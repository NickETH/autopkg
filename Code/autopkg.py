#!/usr/local/autopkg/python
#
# Copyright 2013 Greg Neagle
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""autopkg tool. Runs autopkg recipes and also handles other
related tasks"""


import copy
import difflib
import glob
import hashlib
import os
import plistlib
import pprint
import shutil
import subprocess
import sys
import time
import traceback
from base64 import b64decode
from typing import Optional
from urllib.parse import quote, urlparse

import yaml
from autopkgcmd import common_parse, gen_common_parser, search_recipes
from autopkglib import (
    RECIPE_EXTS,
    AutoPackager,
    AutoPackagerError,
    PreferenceError,
    core_processor_names,
    extract_processor_name_with_recipe_identifier,
    find_binary,
    find_recipe_by_identifier,
    get_all_prefs,
    get_autopkg_version,
    get_identifier,
    get_pref,
    get_processor,
    is_mac,
    log,
    log_err,
    processor_names,
    recipe_from_file,
    remove_recipe_extension,
    set_pref,
    version_equal_or_greater,
	is_windows,
)
from autopkglib.github import GitHubSession, print_gh_search_results
# Warning disabled on Windows version
#f sys.platform != "darwin":
#    print(
#        """
#--------------------------------------------------------------------------------
#-- WARNING: AutoPkg is not completely functional on platforms other than OS X --
#--------------------------------------------------------------------------------
#"""
#    )

# Catch Python 2 wrappers with an early f-string. Message must be on a single line.
_ = f"""{sys.version_info.major} It looks like you're running the autopkg tool with an incompatible version of Python. Please update your script to use autopkg's included Python (/usr/local/autopkg/python). AutoPkgr users please note that AutoPkgr 1.5.1 and earlier is NOT compatible with autopkg 2. """  # noqa

# If any recipe fails during 'autopkg run', return this exit code
RECIPE_FAILED_CODE = 70


def print_version(argv):
    """Prints autopkg version"""
    _ = argv[1]
    print(get_autopkg_version())


def recipe_has_step_processor(recipe, processor):
    """Does the recipe object contain at least one step with the
    named Processor?"""
    if "Process" in recipe:
        processors = [step.get("Processor") for step in recipe["Process"]]
        if processor in processors:
            return True
    return False


def has_munkiimporter_step(recipe):
    """Does the recipe have a MunkiImporter step?"""
    return recipe_has_step_processor(recipe, "MunkiImporter")


def has_check_phase(recipe):
    """Does the recipe have a "check" phase?"""
    return recipe_has_step_processor(recipe, "EndOfCheckPhase")


def builds_a_package(recipe):
    """Does this recipe build any packages?"""
    return recipe_has_step_processor(recipe, "PkgCreator")


def valid_recipe_dict_with_keys(recipe_dict, keys_to_verify):
    """Attempts to read a dict and ensures the keys in
    keys_to_verify exist. Returns False on any failure, True otherwise."""
    if recipe_dict:
        for key in keys_to_verify:
            if key not in recipe_dict:
                return False
        # if we get here, we found all the keys
        return True
    return False


def valid_recipe_dict(recipe_dict):
    """Returns True if recipe dict is a valid recipe,
    otherwise returns False"""
    return (
        valid_recipe_dict_with_keys(recipe_dict, ["Input", "Process"])
        or valid_recipe_dict_with_keys(recipe_dict, ["Input", "Recipe"])
        or valid_recipe_dict_with_keys(recipe_dict, ["Input", "ParentRecipe"])
    )


def valid_recipe_file(filename):
    """Returns True if filename contains a valid recipe,
    otherwise returns False"""
    recipe_dict = recipe_from_file(filename)
    return valid_recipe_dict(recipe_dict)


def valid_override_dict(recipe_dict):
    """Returns True if the recipe is a valid override,
    otherwise returns False"""
    return valid_recipe_dict_with_keys(
        recipe_dict, ["Input", "ParentRecipe"]
    ) or valid_recipe_dict_with_keys(recipe_dict, ["Input", "Recipe"])


def valid_override_file(filename):
    """Returns True if filename contains a valid override,
    otherwise returns False"""
    override_dict = recipe_from_file(filename)
    return valid_override_dict(override_dict)


def find_recipe_by_name(name, search_dirs):
    """Search search_dirs for a recipe by file/directory naming rules"""
    # drop extension from the end of the name because we're
    # going to add it back on...
    name = remove_recipe_extension(name)
    # search by "Name", using file/directory hierarchy rules
    for directory in search_dirs:
        # TODO: Combine with similar code in get_recipe_list() and find_recipe_by_identifier()
        normalized_dir = os.path.abspath(os.path.expanduser(directory))
        patterns = [os.path.join(normalized_dir, f"{name}{ext}") for ext in RECIPE_EXTS]
        patterns.extend(
            [os.path.join(normalized_dir, f"*/{name}{ext}") for ext in RECIPE_EXTS]
        )
        for pattern in patterns:
            matches = glob.glob(pattern)
            for match in matches:
                if valid_recipe_file(match):
                    return match

    return None


def find_recipe(id_or_name, search_dirs):
    """find a recipe based on a string that might be an identifier
    or a name"""
    return find_recipe_by_identifier(id_or_name, search_dirs) or find_recipe_by_name(
        id_or_name, search_dirs
    )


def get_identifier_from_override(override):
    """Return the identifier from an override, falling back with a
    warning to just the 'name' of the recipe."""
    # prefer ParentRecipe
    identifier = override.get("ParentRecipe")
    if identifier:
        return identifier
    identifier = override["Recipe"].get("identifier")
    if identifier:
        return identifier
    else:
        name = override["Recipe"].get("name")
        log_err(
            "WARNING: Override contains no identifier. Will fall "
            "back to matching it by name using search rules. It's "
            "recommended to give the original recipe identifier "
            "in the override's 'Recipes' dict to ensure the same "
            "recipe is always used for this override."
        )
    return name


def get_repository_from_identifier(identifier: str):
    """Get a repository name from a recipe identifier."""
    results = GitHubSession().search_for_name(identifier)
    # so now we have a list of items containing file names and URLs
    # we want to fetch these so we can look inside the contents for a matching
    # identifier
    # We just want to fetch the repos that contain these
    # Is the name an identifier?
    identifier_fragments = identifier.split(".")
    if identifier_fragments[0] != "com":
        # This is not an identifier
        return
    correct_item = None
    for item in results:
        file_contents_raw = do_gh_repo_contents_fetch(
            item["repository"]["name"], item.get("path")
        )
        file_contents_data = plistlib.loads(file_contents_raw)
        if file_contents_data.get("Identifier") == identifier:
            correct_item = item
            break
    # Did we get correct item?
    if not correct_item:
        return
    print(f"Found this recipe in repository: {correct_item['repository']['name']}")
    return correct_item["repository"]["name"]


def locate_recipe(
    name,
    override_dirs,
    recipe_dirs,
    make_suggestions=True,
    search_github=True,
    auto_pull=False,
):
    """Locates a recipe by name. If the name is the pathname to a file on disk,
    we attempt to load that file and use it as recipe. If a parent recipe
    is required we first add the child recipe's directory to the search path
    so that the parent can be found, assuming it is in the same directory.

    Otherwise, we treat name as a recipe name or identifier and search first
    the override directories, then the recipe directories for a matching
    recipe."""

    recipe_file = None
    if os.path.isfile(name):
        # name is path to a specific recipe or override file
        # ignore override and recipe directories
        # and attempt to open the file specified by name
        if valid_recipe_file(name):
            recipe_file = name

    if not recipe_file:
        # name wasn't a filename. Let's search our local repos.
        recipe_file = find_recipe(name, override_dirs + recipe_dirs)

    if not recipe_file and make_suggestions:
        print(f"Didn't find a recipe for {name}.")
        make_suggestions_for(name)

    if not recipe_file and search_github:
        indef_article = "a"
        if name[0].lower() in ["a", "e", "i", "o", "u"]:
            indef_article = "an"
        if not auto_pull:
            answer = input(
                f"Search GitHub AutoPkg repos for {indef_article} {name} recipe? "
                "[y/n]: "
            )
        else:
            answer = "y"
        if answer.lower().startswith("y"):
            identifier_fragments = name.split(".")
            repo_names = []
            if identifier_fragments[0] == "com":
                # Filter out "None" results if we don't find a matching recipe
                parent_repo = get_repository_from_identifier(name)
                repo_names = [parent_repo] if parent_repo else []

            if not repo_names:
                results_items = GitHubSession().search_for_name(name)
                print_gh_search_results(results_items)
                # make a list of unique repo names
                repo_names = []
                for item in results_items:
                    repo_name = item["repository"]["name"]
                    if repo_name not in repo_names:
                        repo_names.append(repo_name)

            if len(repo_names) == 1:
                # we found results in a single repo, so offer to add it
                repo = repo_names[0]
                if not auto_pull:
                    print()
                    answer = input(f"Add recipe repo '{repo}'? [y/n]: ")
                else:
                    answer = "y"
                if answer.lower().startswith("y"):
                    repo_add([None, "repo-add", repo])
                    # try once again to locate the recipe, but don't
                    # search GitHub again!
                    print()
                    recipe_dirs = get_search_dirs()
                    recipe_file = locate_recipe(
                        name,
                        override_dirs,
                        recipe_dirs,
                        make_suggestions=True,
                        search_github=False,
                        auto_pull=auto_pull,
                    )
            elif len(repo_names) > 1:
                print()
                print("To add a new recipe repo, use 'autopkg repo-add " "<repo name>'")
                return None

    return recipe_file


def load_recipe(
    name,
    override_dirs,
    recipe_dirs,
    preprocessors=None,
    postprocessors=None,
    make_suggestions=True,
    search_github=True,
    auto_pull=False,
):
    """Loads a recipe, first locating it by name.
    If we find one, we load it and return the dictionary object. If an
    override file is used, it prefers finding the original recipe by
    identifier rather than name, so that if recipe names shift with
    updated recipe repos, the override still applies to the recipe from
    which it was derived."""

    if override_dirs is None:
        override_dirs = []
    if recipe_dirs is None:
        recipe_dirs = []
    recipe = None
    recipe_file = locate_recipe(
        name,
        override_dirs,
        recipe_dirs,
        make_suggestions=make_suggestions,
        search_github=search_github,
        auto_pull=auto_pull,
    )

    if recipe_file:
        # read it
        recipe = recipe_from_file(recipe_file)

        # store parent trust info, but only if this is an override
        if recipe_in_override_dir(recipe_file, override_dirs):
            parent_trust_info = recipe.get("ParentRecipeTrustInfo")
            override_parent = recipe.get("ParentRecipe") or recipe.get("Recipe")
        else:
            parent_trust_info = None

        # does it refer to another recipe?
        if recipe.get("ParentRecipe") or recipe.get("Recipe"):
            # save current recipe as a child
            child_recipe = recipe
            parent_id = get_identifier_from_override(recipe)
            # add the recipe's directory to the search path
            # so that we'll be able to locate the parent
            recipe_dirs.append(os.path.dirname(recipe_file))
            # load its parent, this time not looking in override directories
            recipe = load_recipe(
                parent_id,
                [],
                recipe_dirs,
                make_suggestions=make_suggestions,
                search_github=search_github,
                auto_pull=auto_pull,
            )
            if recipe:
                # merge child_recipe
                recipe["Identifier"] = get_identifier(child_recipe)
                recipe["Description"] = child_recipe.get(
                    "Description", recipe.get("Description", "")
                )
                for key in list(child_recipe["Input"].keys()):
                    recipe["Input"][key] = child_recipe["Input"][key]

                # take the highest of the two MinimumVersion keys, if they exist
                for candidate_recipe in [recipe, child_recipe]:
                    if "MinimumVersion" not in list(candidate_recipe.keys()):
                        candidate_recipe["MinimumVersion"] = "0"
                if version_equal_or_greater(
                    child_recipe["MinimumVersion"], recipe["MinimumVersion"]
                ):
                    recipe["MinimumVersion"] = child_recipe["MinimumVersion"]

                recipe["Process"].extend(child_recipe.get("Process", []))
                if recipe.get("RECIPE_PATH"):
                    if "PARENT_RECIPES" not in recipe:
                        recipe["PARENT_RECIPES"] = []
                    recipe["PARENT_RECIPES"] = [recipe["RECIPE_PATH"]] + recipe[
                        "PARENT_RECIPES"
                    ]
                recipe["RECIPE_PATH"] = recipe_file
            else:
                # no parent recipe, so the current recipe is invalid
                log_err(f"Could not find parent recipe for {name}")
        else:
            recipe["RECIPE_PATH"] = recipe_file

        # re-add original stored parent trust info or remove it if it was picked
        # up from a parent recipe
        if recipe:
            if parent_trust_info:
                recipe["ParentRecipeTrustInfo"] = parent_trust_info
                if override_parent:
                    recipe["ParentRecipe"] = override_parent
                else:
                    log_err(f"No parent recipe specified for {name}")
            elif "ParentRecipeTrustInfo" in recipe:
                del recipe["ParentRecipeTrustInfo"]

    if recipe:
        # store the name the user used to locate this recipe
        recipe["name"] = name

    if recipe and preprocessors:
        steps = []
        for preprocessor_name in preprocessors:
            steps.append({"Processor": preprocessor_name})
        steps.extend(recipe["Process"])
        recipe["Process"] = steps

    if recipe and postprocessors:
        steps = recipe["Process"]
        for postprocessor_name in postprocessors:
            steps.append({"Processor": postprocessor_name})
        recipe["Process"] = steps

    return recipe


def get_recipe_info(
    recipe_name,
    override_dirs,
    recipe_dirs,
    make_suggestions=True,
    search_github=True,
    auto_pull=False,
):
    """Loads a recipe, then prints some information about it. Override aware."""
    recipe = load_recipe(
        recipe_name,
        override_dirs,
        recipe_dirs,
        make_suggestions=make_suggestions,
        search_github=search_github,
        auto_pull=auto_pull,
    )
    if recipe:
        log(
            "Description:         {}".format(
                "\n                     ".join(
                    recipe.get("Description", "").splitlines()
                )
            )
        )
        log(f"Identifier:          {get_identifier(recipe)}")
        log(f"Munki import recipe: {has_munkiimporter_step(recipe)}")
        log(f"Has check phase:     {has_check_phase(recipe)}")
        log(f"Builds package:      {builds_a_package(recipe)}")
        log(f"Recipe file path:    {recipe['RECIPE_PATH']}")
        if recipe.get("PARENT_RECIPES"):
            log(
                "Parent recipe(s):    {}".format(
                    "\n                     ".join(recipe["PARENT_RECIPES"])
                )
            )
        log("Input values: ")
        output = pprint.pformat(recipe.get("Input", {}), indent=4)
        log(" " + output[1:-1])
        return True
    else:
        log_err(f"No valid recipe found for {recipe_name}")
        return False


def git_cmd():
    """Returns a path to a git binary, priority in the order below.
    Returns None if none found.
    1. app pref 'GIT_PATH'
    2. a 'git' binary that can be found in the PATH environment variable
    3. '/usr/bin/git'
    """
    return find_binary("git")


class GitError(Exception):
    """Exception to throw if git fails"""

    pass


def run_git(git_options_and_arguments, git_directory=None):
    """Run a git command and return its output if successful;
    raise GitError if unsuccessful."""
    gitcmd = git_cmd()
    if not gitcmd:
        raise GitError("ERROR: git is not installed!")
    cmd = [gitcmd]
    cmd.extend(git_options_and_arguments)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=git_directory,
            text=True,
        )
        (cmd_out, cmd_err) = proc.communicate()
    except OSError as err:
        raise GitError(
            f"ERROR: git execution failed with error code {err.errno}: "
            f"{err.strerror}"
        )
    if proc.returncode != 0:
        raise GitError(f"ERROR: {cmd_err}")
    else:
        return cmd_out


def get_recipe_repo(git_path):
    """git clone git_path to local disk and return local path"""

    # figure out a local directory name to clone to
    parts = urlparse(git_path)
    domain_and_port = parts.netloc
    # discard port if any
    domain = domain_and_port.split(":")[0]
    reverse_domain = ".".join(reversed(domain.split(".")))
    # discard file extension if any
    url_path = os.path.splitext(parts.path)[0]
    dest_name = reverse_domain + url_path.replace("/", ".")
    recipe_repo_dir = get_pref("RECIPE_REPO_DIR") or "~/Library/AutoPkg/RecipeRepos"
    recipe_repo_dir = os.path.expanduser(recipe_repo_dir)
    dest_dir = os.path.join(recipe_repo_dir, dest_name)
    dest_dir = os.path.abspath(dest_dir)
    gitcmd = git_cmd()
    if not gitcmd:
        log_err("No git binary could be found!")
        return None

    if os.path.exists(dest_dir):
        # probably should attempt a git pull
        # check to see if this is really a git repo first
        if not os.path.isdir(os.path.join(dest_dir, ".git")):
            log_err(f"{dest_dir} exists and is not a git repo!")
            return None
        log(f"Attempting git pull for {dest_dir}...")
        try:
            log(run_git(["pull"], git_directory=dest_dir))
            return dest_dir
        except GitError as err:
            log_err(err)
            return None
    else:
        log(f"Attempting git clone for {git_path}...")
        try:
            log(run_git(["clone", git_path, dest_dir]))
            return dest_dir
        except GitError as err:
            log_err(err)
            return None
    return None


def write_plist_exit_on_fail(plist_dict, path):
    """Writes a dict to a new plist at path, exits the program
    if the write fails."""
    try:
        with open(path, "wb") as f:
            plistlib.dump(plist_dict, f)
    except (TypeError, OverflowError):
        log_err(f"Failed to save plist to {path}.")
        sys.exit(-1)


def print_tool_info(options):
    """Eventually will print some information about the tool
    and environment. For now, just print the current prefs"""
    _ = options
    print("Current preferences:")
    pprint.pprint(get_all_prefs())


def get_repo_info(path_or_url):
    """Given a path or URL, find a locally installed repo and return
    infomation in a dictionary about it"""
    repo_info = {}
    recipe_repos = get_pref("RECIPE_REPOS") or {}

    parsed = urlparse(path_or_url)
    if parsed.netloc:
        # it's a URL, look it up and find the associated path
        repo_url = path_or_url
        for repo_path in list(recipe_repos.keys()):
            test_url = recipe_repos[repo_path].get("URL")
            if repo_url == test_url:
                # found it; copy the dict info
                repo_info["path"] = repo_path
                repo_info.update(recipe_repos[repo_path])
                # get out now!
                return repo_info
    else:
        repo_path = os.path.abspath(os.path.expanduser(path_or_url))
        if repo_path in recipe_repos:
            repo_info["path"] = repo_path
            repo_info.update(recipe_repos[repo_path])
    return repo_info


def save_pref_or_warn(key, value):
    """Saves a key and value to preferences, warning if there is an issue"""
    try:
        set_pref(key, value)
    except PreferenceError as err:
        log_err(f"WARNING: {err}")


def get_search_dirs():
    """Return search dirs from preferences or default list"""
    default = [".", "~/Library/AutoPkg/Recipes", "/Library/AutoPkg/Recipes"]
    if is_windows(): #Added for Windows version
        default = [".", "%APPDATA%\AutoPkg\Recipes", "%ALLUSERSPROFILE%\AutoPkg\Recipes"]                                                                                        
    dirs = get_pref("RECIPE_SEARCH_DIRS")
    if isinstance(dirs, str):
        # convert a string to a list
        dirs = [dirs]
    return dirs or default


def get_override_dirs():
    """Return override dirs from preferences or default list"""
    default = ["~/Library/AutoPkg/RecipeOverrides"]

    dirs = get_pref("RECIPE_OVERRIDE_DIRS")
    if isinstance(dirs, str):
        # convert a string to a list
        dirs = [dirs]
    return dirs or default


def add_search_and_override_dir_options(parser):
    """Several subcommands use these same options"""
    parser.add_option(
        "-d",
        "--search-dir",
        metavar="DIRECTORY",
        dest="search_dirs",
        action="append",
        default=[],
        help=("Directory to search for recipes. Can be specified " "multiple times."),
    )
    parser.add_option(
        "--override-dir",
        metavar="DIRECTORY",
        dest="override_dirs",
        action="append",
        default=[],
        help=(
            "Directory to search for recipe overrides. Can be "
            "specified multiple times."
        ),
    )


########################
# subcommand functions #
########################


def expand_repo_url(url):
    """Given a GitHub repo URL-ish name, returns a full GitHub URL. Falls
    back to the 'autopkg' GitHub org, and full non-GitHub URLs return
    unmodified.
    Examples:
    'user/reciperepo'         -> 'https://github.com/user/reciperepo'
    'reciperepo'              -> 'https://github.com/autopkg/reciperepo'
    'http://some/repo/url     -> 'http://some/repo/url'
    '/some/path               -> '/some/path'
    '~/some/path              -> '~/some/path'
    """
    # Strip trailing slashes
    url = url.rstrip("/")
    # Parse URL to determine scheme
    parsed_url = urlparse(url)
    if url.startswith(("/", "~")):
        # If the URL looks like a file path, return as is.
        pass
    elif not parsed_url.scheme:
        # If no URL scheme was given in the URL, try GitHub URLs
        if "/" in url:
            # If URL looks like 'name/repo' then prepend the base GitHub URL
            url = f"https://github.com/{url}"
        else:
            # Assume it's a repo within the 'autopkg' org
            url = f"https://github.com/autopkg/{url}"

    return url


def repo_add(argv):
    """Add/update one or more repos of recipes"""
    verb = argv[1]
    parser = gen_common_parser()
    parser.set_usage(
        f"""Usage: %prog {verb} recipe_repo_url
Download one or more new recipe repos and add it to the search path.
The 'recipe_repo_url' argument can be of the following forms:
- repo (implies 'https://github.com/autopkg/repo')
- user/repo (implies 'https://github.com/user/repo')
- (http[s]://|git://|user@server:)path/to/any/git/repo

Example: '%prog repo-add recipes'
..adds the autopkg/recipes repo from GitHub."""
    )
    # Parse arguments
    arguments = common_parse(parser, argv)[1]
    if len(arguments) < 1:
        log_err("Need at least one recipe repo URL!")
        return -1

    recipe_search_dirs = get_search_dirs()
    recipe_repos = get_pref("RECIPE_REPOS") or {}
    for repo_url in arguments:
        repo_url = expand_repo_url(repo_url)
        new_recipe_repo_dir = get_recipe_repo(repo_url)
        if new_recipe_repo_dir:
            if new_recipe_repo_dir not in recipe_search_dirs:
                log(f"Adding {new_recipe_repo_dir} to RECIPE_SEARCH_DIRS...")
                recipe_search_dirs.append(new_recipe_repo_dir)
            # add info about this repo to our prefs
            recipe_repos[new_recipe_repo_dir] = {"URL": repo_url}

    # save our updated RECIPE_REPOS and RECIPE_SEARCH_DIRS
    save_pref_or_warn("RECIPE_REPOS", recipe_repos)
    save_pref_or_warn("RECIPE_SEARCH_DIRS", recipe_search_dirs)

    log("Updated search path:")
    for search_dir in get_pref("RECIPE_SEARCH_DIRS"):
        log(f"  '{search_dir}'")


def repo_delete(argv):
    """Delete a recipe repo"""
    verb = argv[1]
    parser = gen_common_parser()
    parser.set_usage(
        f"Usage: %prog {verb} recipe_repo_path_or_url [...]\n"
        "Delete one or more recipe repo and remove it from the search "
        "path."
    )

    # Parse arguments
    arguments = common_parse(parser, argv)[1]
    if len(arguments) < 1:
        log_err("Need at least one recipe repo path or URL!")
        return -1

    recipe_repos = get_pref("RECIPE_REPOS") or {}
    recipe_search_dirs = get_search_dirs()

    for path_or_url in arguments:
        path_or_url = expand_repo_url(path_or_url)
        repo_path = get_repo_info(path_or_url).get("path")
        if not repo_path:
            log_err(f"ERROR: Can't find an installed repo for {path_or_url}")
            continue
        else:
            log(f"Removing repo at {repo_path}...")

        # first, remove from RECIPE_SEARCH_DIRS
        if repo_path in recipe_search_dirs:
            recipe_search_dirs.remove(repo_path)
        # now remove the repo files
        try:
            if is_windows():  #Added for Windows version
                # we need to remove readonly, hidden and system bits
                os.system('attrib.exe -r -h -s ' + repo_path + '\\*.* /S /D')
            shutil.rmtree(repo_path)
        except OSError as err:
            log_err(f"ERROR: Could not remove {repo_path}: {err}")
        else:
            # last, remove from RECIPE_REPOS
            del recipe_repos[repo_path]

    # save our updated RECIPE_REPOS and RECIPE_SEARCH_DIRS
    save_pref_or_warn("RECIPE_REPOS", recipe_repos)
    save_pref_or_warn("RECIPE_SEARCH_DIRS", recipe_search_dirs)


def repo_list(argv):
    """List recipe repos"""
    verb = argv[1]
    parser = gen_common_parser()
    parser.set_usage(f"Usage: %prog {verb}\n" "List all installed recipe repos.")
    _options, _arguments = common_parse(parser, argv)
    recipe_repos = get_pref("RECIPE_REPOS") or {}
    if recipe_repos:
        for key in sorted(recipe_repos.keys()):
            print(f"{key} ({recipe_repos[key]['URL']})")
        print()
    else:
        print("No recipe repos.")


def repo_update(argv):
    """Update one or more recipe repos"""
    verb = argv[1]
    parser = gen_common_parser()
    parser.set_usage(
        f"Usage: %prog {verb} recipe_repo_path_or_url [...]\n"
        "Update one or more recipe repos.\n"
        "You may also use 'all' to update all installed recipe "
        "repos."
    )

    # Parse arguments
    arguments = common_parse(parser, argv)[1]
    if len(arguments) < 1:
        log_err("Need at least one recipe repo path or URL!")
        return -1

    if "all" in arguments:
        # just get all repos
        recipe_repos = get_pref("RECIPE_REPOS") or {}
        repo_dirs = [key for key in list(recipe_repos.keys())]
    else:
        repo_dirs = []
        for path_or_url in arguments:
            path_or_url = expand_repo_url(path_or_url)
            repo_path = get_repo_info(path_or_url).get("path")
            if not repo_path:
                log_err(f"ERROR: Can't find an installed repo for {path_or_url}")
            else:
                repo_dirs.append(repo_path)

    for repo_dir in repo_dirs:
        # resolve ~ and symlinks before passing to git
        repo_dir = os.path.abspath(os.path.expanduser(repo_dir))
        log(f"Attempting git pull for {repo_dir}...")
        try:
            log(run_git(["pull"], git_directory=repo_dir))
        except GitError as err:
            log_err(err)


def do_gh_repo_contents_fetch(
    repo: str, path: str, use_token=False, decode=True
) -> Optional[bytes]:
    """Fetch file contents from GitHub and return as a string."""
    gh_session = GitHubSession()
    if use_token:
        gh_session.setup_token()
    # Do the search, including text match metadata
    (results, code) = gh_session.call_api(
        f"/repos/autopkg/{repo}/contents/{quote(path)}"
    )

    if code == 403:
        log_err(
            "You've probably hit the GitHub's search rate limit, officially 5 "
            "requests per minute.\n"
        )
        if results:
            log_err("Server response follows:\n")
            log_err(results.get("message", None))
            log_err(results.get("documentation_url", None))

        return None
    if results is None or code is None:
        log_err("A GitHub API error occurred!")
        return None
    if decode:
        return b64decode(results["content"])
    return results["content"]


def display_help(argv, subcommands):
    """Display top-level help"""
    main_command_name = os.path.basename(argv[0])
    print(
        f"Usage: {main_command_name} <verb> <options>, where <verb> is one of "
        "the following:"
    )
    print()
    # find length of longest subcommand
    max_key_len = max([len(key) for key in list(subcommands.keys())])
    for key in sorted(subcommands.keys()):
        # pad name of subcommand to make pretty columns
        subcommand = key + (" " * (max_key_len - len(key)))
        print(f"    {subcommand}  ({subcommands[key]['help']})")
    print()
    if len(argv) > 1 and argv[1] not in subcommands:
        print(f"Error: unknown verb: {argv[1]}")
    else:
        print(f"{main_command_name} <verb> --help for more help for that verb")


def get_info(argv):
    """Display info about configuration or a recipe"""
    verb = argv[1]
    parser = gen_common_parser()
    parser.set_usage(f"Usage: {'%prog'} {verb} [options] [recipe]")

    # Parse arguments
    add_search_and_override_dir_options(parser)
    parser.add_option(
        "-q",
        "--quiet",
        action="store_true",
        help=("Don't offer to search Github if a recipe can't be found."),
    )
    parser.add_option(
        "-p",
        "--pull",
        action="store_true",
        help=(
            "Pull the parent repos if they can't be found in the search path. "
            "Implies agreement to search GitHub."
        ),
    )
    (options, arguments) = common_parse(parser, argv)

    override_dirs = options.override_dirs or get_override_dirs()
    search_dirs = options.search_dirs or get_search_dirs()

    make_suggestions = True
    if options.quiet:
        # don't make suggestions if we want to be quiet
        make_suggestions = False

    if len(arguments) == 0:
        # return configuration info
        print_tool_info(options)
        return 0
    elif len(arguments) == 1:
        if get_recipe_info(
            arguments[0],
            override_dirs,
            search_dirs,
            make_suggestions=make_suggestions,
            search_github=make_suggestions,
            auto_pull=options.pull,
        ):
            return 0
        else:
            return -1
    else:
        log_err("Too many recipes!")
        return -1


def processor_info(argv):
    """Display info about a processor"""

    def print_vars(var_dict, indent=0):
        """Print a dict of dicts and strings"""
        for key, value in list(var_dict.items()):
            if isinstance(value, dict):
                print(" " * indent, f"{key}:")
                print_vars(value, indent=indent + 2)
            else:
                print(" " * indent, f"{key}: {value}")

    verb = argv[1]
    parser = gen_common_parser()
    parser.set_usage(f"Usage: {'%prog'} {verb} [options] processorname")
    parser.add_option(
        "-r", "--recipe", metavar="RECIPE", help="Name of recipe using the processor."
    )

    # Parse arguments
    add_search_and_override_dir_options(parser)
    (options, arguments) = common_parse(parser, argv)

    override_dirs = options.override_dirs or get_override_dirs()
    search_dirs = options.search_dirs or get_search_dirs()

    if len(arguments) != 1:
        log_err("Need exactly one processor name")
        return -1

    processor_name = arguments[0]

    recipe = None
    if options.recipe:
        recipe = load_recipe(options.recipe, override_dirs, search_dirs)

    try:
        processor_class = get_processor(processor_name, recipe=recipe)
    except (KeyError, AttributeError):
        log_err(f"Unknown processor '{processor_name}'")
        return -1

    try:
        description = processor_class.description
    except AttributeError:
        try:
            description = processor_class.__doc__
        except AttributeError:
            description = ""
    try:
        input_vars = processor_class.input_variables
    except AttributeError:
        input_vars = {}
    try:
        output_vars = processor_class.output_variables
    except AttributeError:
        output_vars = {}

    print(f"Description: {description}")
    print("Input variables:")
    print_vars(input_vars, indent=2)
    print("Output variables:")
    print_vars(output_vars, indent=2)


def list_processors(argv):
    """List the processors in autopkglib"""
    verb = argv[1]
    parser = gen_common_parser()
    parser.set_usage(f"Usage: %prog {verb} [options]\n" "List the core Processors.")
    _ = common_parse(parser, argv)[0]

    print("\n".join(sorted(processor_names())))


def make_suggestions_for(search_name):
    """Suggest existing recipes with names similar to search name."""
    # trim extension from the end if it exists
    search_name = remove_recipe_extension(search_name)
    (search_name_base, search_name_ext) = os.path.splitext(search_name.lower())
    recipe_names = [os.path.splitext(item["Name"]) for item in get_recipe_list()]
    recipe_names = list(set(recipe_names))

    matches = []
    if len(search_name_base) > 3:
        matches = [
            "".join(item)
            for item in recipe_names
            if (
                search_name_base in item[0].lower()
                and search_name_ext in item[1].lower()
            )
        ]
    if search_name_ext:
        compare_names = [
            item[0].lower()
            for item in recipe_names
            if item[1].lower() == search_name_ext
        ]
    else:
        compare_names = [item[0].lower() for item in recipe_names]

    close_matches = difflib.get_close_matches(search_name_base, compare_names)
    if close_matches:
        matches.extend(
            [
                "".join(item)
                for item in recipe_names
                if ("".join(item) not in matches and item[0].lower() in close_matches)
            ]
        )
        if search_name_ext:
            matches = [
                item for item in matches if os.path.splitext(item)[1] == search_name_ext
            ]

    if len(matches) == 1:
        print(f"Maybe you meant {matches[0]}?")
    elif len(matches):
        print(f"Maybe you meant one of: {', '.join(matches)}?")


def get_recipe_list(
    override_dirs=None, search_dirs=None, augmented_list=False, show_all=False
):
    """Factor out the core of list_recipes for use in other functions"""
    override_dirs = override_dirs or get_override_dirs()
    search_dirs = search_dirs or get_search_dirs()

    recipes = []
    for directory in search_dirs:
        # TODO: Combine with similar code in find_recipe_by_name() and find_recipe_by_identifier()
        normalized_dir = os.path.abspath(os.path.expanduser(directory))
        if not os.path.isdir(normalized_dir):
            continue

        # find all top-level recipes and recipes one level down
        patterns = [os.path.join(normalized_dir, f"*{ext}") for ext in RECIPE_EXTS]
        patterns.extend(
            [os.path.join(normalized_dir, f"*/*{ext}") for ext in RECIPE_EXTS]
        )

        for pattern in patterns:
            matches = glob.glob(pattern)
            for match in matches:
                recipe = recipe_from_file(match)
                if valid_recipe_dict(recipe):
                    recipe_name = os.path.basename(match)

                    recipe["Name"] = remove_recipe_extension(recipe_name)
                    recipe["Path"] = match

                    # If a top level "Identifier" key is not discovered,
                    # this will copy an IDENTIFIER key in the "Input"
                    # entry to the top level of the recipe dictionary.
                    if "Identifier" not in recipe:
                        identifier = get_identifier(recipe)
                        if identifier:
                            recipe["Identifier"] = identifier

                    recipes.append(recipe)

    for directory in override_dirs:
        normalized_dir = os.path.abspath(os.path.expanduser(directory))
        if not os.path.isdir(normalized_dir):
            continue
        patterns = [os.path.join(normalized_dir, f"*{ext}") for ext in RECIPE_EXTS]
        for pattern in patterns:
            matches = glob.glob(pattern)
            for match in matches:
                override = recipe_from_file(match)
                if valid_override_dict(override):
                    override_name = os.path.basename(match)

                    override["Name"] = remove_recipe_extension(override_name)
                    override["Path"] = match
                    override["IsOverride"] = True

                    if augmented_list and not show_all:
                        # If an override has the same Name as the ParentRecipe
                        # AND the override's ParentRecipe matches said
                        # recipe's Identifier, remove the ParentRecipe from the
                        # listing.
                        for recipe in recipes:
                            if recipe["Name"] == override["Name"] and recipe.get(
                                "Identifier"
                            ) == override.get("ParentRecipe"):
                                recipes.remove(recipe)

                    recipes.append(override)
    return recipes


def list_recipes(argv):
    """List all available recipes"""
    verb = argv[1]
    parser = gen_common_parser()
    parser.set_usage(
        f"Usage: %prog {verb} [options]\n"
        "List all the recipes this tool can find automatically.\n"
    )
    parser.add_option(
        "-i",
        "--with-identifiers",
        action="store_true",
        help="Include recipe's identifier in the list.",
    )
    parser.add_option(
        "-p",
        "--with-paths",
        action="store_true",
        help="Include recipe's path in the list.",
    )
    parser.add_option(
        "--plist",
        action="store_true",
        help=(
            "Print recipe list in plist format. This provides "
            "all available key/value pairs of recipes."
        ),
    )
    parser.add_option(
        "-a",
        "--show-all",
        action="store_true",
        help=(
            "Include recipes with duplicate shortnames, "
            "including overrides. Use in conjunction with "
            "'--with-identifiers', '--with-paths', or "
            "'--plist'."
        ),
    )

    # Parse options
    add_search_and_override_dir_options(parser)
    options = common_parse(parser, argv)[0]

    augmented_list = False
    if options.with_identifiers or options.with_paths or options.plist:
        augmented_list = True

    if options.show_all and not augmented_list:
        log_err(
            "The '--show-all' option is only valid when used with "
            "'--with-paths', '--with-identifiers', or '--plist' options."
        )
        return -1
    elif options.plist and (options.with_identifiers or options.with_paths):
        log_err(
            "It is invalid to specify '--with-identifiers' or "
            "'--with-paths' with '--plist'."
        )
        return -1

    override_dirs = options.override_dirs or get_override_dirs()
    search_dirs = options.search_dirs or get_search_dirs()

    recipes = get_recipe_list(
        override_dirs=override_dirs,
        search_dirs=search_dirs,
        augmented_list=augmented_list,
        show_all=options.show_all,
    )

    lowercase_sorted = sorted(recipes, key=lambda s: s["Name"].lower())

    if options.plist:
        print(plistlib.dumps(lowercase_sorted).decode())
    else:
        column_spacer = 1
        max_name_length = 0
        max_identifier_length = 0

        if recipes and augmented_list:
            max_name_length = max([len(r["Name"]) for r in recipes]) + column_spacer

            max_identifier_length = (
                (max([len(r.get("Identifier", "")) for r in recipes]) + column_spacer)
                if options.with_identifiers
                else column_spacer
            )

            name_spacer = f"{{: <{max_name_length}}}"
            ident_spacer = f"{{: <{max_identifier_length}}}"
            path_spacer = "{: <20}"
            format_str = f"{name_spacer} {ident_spacer} {path_spacer}"
        else:
            format_str = "{}{}{}"

        output = []

        for recipe in lowercase_sorted:
            name = recipe["Name"]

            # Only display identifier string if enabled
            identifier = ""
            if options.with_identifiers:
                identifier = recipe.get("Identifier", "")

            # To make display cleaner, switch out a ~ for the user's home.
            recipe_path = ""
            if options.with_paths and "Path" in recipe:
                user_home = os.environ.get("HOME")
                recipe_path = recipe["Path"]
                if user_home:
                    recipe_path = recipe_path.replace(user_home, "~")

            out_string = format_str.format(name, identifier, recipe_path)

            if out_string not in output:
                output.append(out_string)

        print("\n".join(output))


def get_git_commit_hash(filepath):
    """Get the current git commit hash if possible"""
    try:
        git_toplevel_dir = run_git(
            ["rev-parse", "--show-toplevel"], git_directory=os.path.dirname(filepath)
        ).rstrip("\n")
    except GitError:
        return None
    try:
        relative_path = os.path.relpath(filepath, git_toplevel_dir)
        # this was the _wrong_ implementation and essentially is the same
        # as `git hash-object filepath`. It gives us the object hash for the
        # file. Fine for later getting diff info but no good for finding the
        # the commits since the hash was recorded
        #
        # git_hash = run_git(
        #    ['rev-parse', ':' + relative_path],
        #    git_directory=git_toplevel_dir).rstrip('\n')
        #
        # instead, we need to use `rev-list` to find the most recent commit
        # hash for the file in question.
        git_hash = run_git(
            ["rev-list", "-1", "HEAD", "--", relative_path],
            git_directory=git_toplevel_dir,
        ).rstrip("\n")
    except GitError:
        return None
    # make sure the file hasn't been changed locally since the last git pull
    # if git diff produces output, it's been changed, and therefore storing
    # the hash is pointless
    try:
        diff_output = run_git(
            ["diff", git_hash, relative_path], git_directory=git_toplevel_dir
        ).rstrip("\n")
    except GitError:
        return None
    if diff_output:
        return None
    return git_hash


def getsha256hash(filepath):
    """Generate a sha256 hash for the file at filepath"""
    if not os.path.isfile(filepath):
        return "NOT A FILE"
    hashfunction = hashlib.sha256()
    fileref = open(filepath, "rb")
    while 1:
        chunk = fileref.read(2 ** 16)
        if not chunk:
            break
        hashfunction.update(chunk)
    fileref.close()
    return hashfunction.hexdigest()


def find_processor_path(processor_name, recipe, env=None):
    """Returns the pathname to a procesor given a name and a recipe"""
    if env is None:
        env = {}
        env["RECIPE_SEARCH_DIRS"] = get_pref("RECIPE_SEARCH_DIRS") or []
    if recipe:
        recipe_dir = os.path.dirname(recipe["RECIPE_PATH"])
        processor_search_dirs = [recipe_dir]

        # check if our processor_name includes a recipe identifier that
        # should be used to locate the recipe.
        # if so, search for the recipe by identifier in order to add
        # its dirname to the processor search dirs
        (
            processor_name,
            processor_recipe_id,
        ) = extract_processor_name_with_recipe_identifier(processor_name)
        if processor_recipe_id:
            shared_processor_recipe_path = find_recipe_by_identifier(
                processor_recipe_id, env["RECIPE_SEARCH_DIRS"]
            )
            if shared_processor_recipe_path:
                processor_search_dirs.append(
                    os.path.dirname(shared_processor_recipe_path)
                )

        # search recipe dirs for processor
        if recipe.get("PARENT_RECIPES"):
            # also look in the directories containing the parent recipes
            parent_recipe_dirs = list(
                {os.path.dirname(item) for item in recipe["PARENT_RECIPES"]}
            )
            processor_search_dirs.extend(parent_recipe_dirs)

        for directory in processor_search_dirs:
            processor_filename = os.path.join(directory, processor_name + ".py")
            if os.path.exists(processor_filename):
                return processor_filename

    return None


def os_path_compressuser(pathname):
    """Sort of the inverse of os.path.expanduser"""
    home_dir = os.path.expanduser("~")
    if pathname == home_dir:
        return "~"
    elif pathname.startswith(home_dir):
        return "~/" + os.path.relpath(pathname, home_dir)
    else:
        return pathname


def get_trust_info(recipe, search_dirs=None):
    """Gets information from a recipe we use to ensure parent recipes and
    non-core processors have not changed"""
    # generate hashes for each parent recipe
    parent_recipe_paths = recipe.get("PARENT_RECIPES", []) + [recipe["RECIPE_PATH"]]
    parent_recipe_hashes = {}
    for p_recipe_path in parent_recipe_paths:
        p_recipe_hash = getsha256hash(p_recipe_path)
        git_hash = get_git_commit_hash(p_recipe_path)
        p_recipe = load_recipe(p_recipe_path, override_dirs=[], recipe_dirs=search_dirs)
        identifier = get_identifier(p_recipe)
        parent_recipe_hashes[identifier] = {
            "path": os_path_compressuser(p_recipe_path),
            "sha256_hash": p_recipe_hash,
        }
        if git_hash:
            parent_recipe_hashes[identifier]["git_hash"] = git_hash
    # generate hashes for each non-core processor
    recipe_processors = [step["Processor"] for step in recipe["Process"]]
    core_processors = core_processor_names()
    non_core_processors = [
        processor for processor in recipe_processors if processor not in core_processors
    ]
    non_core_processor_hashes = {}
    for processor in non_core_processors:
        processor_path = find_processor_path(processor, recipe)
        if processor_path:
            processor_hash = getsha256hash(processor_path)
            git_hash = get_git_commit_hash(processor_path)
        else:
            log_err(f"WARNING: processor path not found for processor: {processor}")
            processor_path = ""
            processor_hash = "PROCESSOR FILEPATH NOT FOUND"
            git_hash = None
        non_core_processor_hashes[processor] = {
            "path": os_path_compressuser(processor_path),
            "sha256_hash": processor_hash,
        }
        if git_hash:
            non_core_processor_hashes[processor]["git_hash"] = git_hash

    # return a dictionary containing the hashes we generated
    return {
        "non_core_processors": non_core_processor_hashes,
        "parent_recipes": parent_recipe_hashes,
    }


def get_git_diff(filepath, git_hash):
    """Get a git diff of filepath from git_hash"""
    filepath = os.path.expanduser(filepath)
    try:
        git_toplevel_dir = run_git(
            ["rev-parse", "--show-toplevel"], git_directory=os.path.dirname(filepath)
        ).rstrip("\n")
    except GitError:
        return ""
    relative_path = os.path.relpath(filepath, git_toplevel_dir)
    try:
        return run_git(
            ["diff", git_hash, relative_path], git_directory=git_toplevel_dir
        )
    except GitError:
        return ""


def get_git_log(filepath, git_hash):
    """Get log entries for commits for filepath since the commit referred to by
    git_hash"""
    filepath = os.path.expanduser(filepath)
    try:
        git_toplevel_dir = run_git(
            ["rev-parse", "--show-toplevel"], git_directory=os.path.dirname(filepath)
        ).rstrip("\n")
    except GitError:
        return ""
    relative_path = os.path.relpath(filepath, git_toplevel_dir)
    try:
        return run_git(
            ["log", git_hash + "..", "--", relative_path],
            git_directory=git_toplevel_dir,
        )
    except GitError:
        return ""


class TrustVerificationWarning(AutoPackagerError):
    """Exception for trust verification warnings"""

    pass


class TrustVerificationError(AutoPackagerError):
    """Exception for trust verification errors"""

    pass


def recipe_from_external_repo(recipe_path):
    """Returns True if the recipe_path is in a path in RECIPE_REPOS, which contains
    recipes added via repo-add"""
    recipe_repos = get_pref("RECIPE_REPOS") or {}
    for repo in list(recipe_repos.keys()):
        if recipe_path.startswith(repo):
            return True
    return False


def recipe_in_override_dir(recipe_path, override_dirs):
    """Returns True if the recipe is in a path in override_dirs"""
    normalized_recipe_path = os.path.abspath(os.path.expanduser(recipe_path))
    normalized_override_dirs = [
        os.path.abspath(os.path.expanduser(directory)) for directory in override_dirs
    ]
    for override_dir in normalized_override_dirs:
        if normalized_recipe_path.startswith(override_dir):
            return True
    return False


def verify_parent_trust(recipe, override_dirs, search_dirs, verbosity=0):
    """Verify trust info for parent recipes"""
    # warn if trust info is in non-override
    if recipe.get("ParentRecipeTrustInfo") and not recipe_in_override_dir(
        recipe["RECIPE_PATH"], override_dirs
    ):
        warning = "Trust information in non-override recipe."
        if verbosity > 1:
            warning += (
                "\nTrust info should only be stored in local recipe overrides."
                f"\nTrust info found in {recipe['RECIPE_PATH']}"
            )
        raise TrustVerificationWarning(warning)
    # warn if no trust info
    if not recipe.get("ParentRecipeTrustInfo"):
        warning = "No trust information present."
        if recipe_in_override_dir(recipe["RECIPE_PATH"], override_dirs) and recipe.get(
            "PARENT_RECIPES"
        ):
            if verbosity > 1:
                warning += (
                    "\nAudit the parent recipe, then run:\n"
                    f"\tautopkg update-trust-info {recipe['name']}"
                )
            raise TrustVerificationWarning(warning)
        else:
            if verbosity > 1:
                warning += (
                    "\nAudit the recipe, then store trust info by running:\n"
                    f"\tautopkg make-override {recipe['name']}"
                )
            raise TrustVerificationWarning(warning)
    recipe_repo_dir = get_pref("RECIPE_REPO_DIR") or "~/Library/AutoPkg/RecipeRepos"
    recipe_repo_dir = os.path.expanduser(recipe_repo_dir)

    # error if we can't use/trust the trust info (it was provided by another)
    if recipe_from_external_repo(recipe["RECIPE_PATH"]):
        # this recipe is in a cloned recipe repo; IE, not a local recipe
        # ignore any embedded trust info
        raise TrustVerificationError(
            "Recipe from external repo: embedded trust info will be ignored. "
            "Audit the recipe, then create an override to trust it."
        )

    # verify trust of parent recipes
    parent_recipe = load_recipe(
        recipe["ParentRecipe"],
        override_dirs,
        search_dirs,
        make_suggestions=False,
        search_github=False,
    )
    expected_trust_info = recipe["ParentRecipeTrustInfo"]
    actual_trust_info = get_trust_info(parent_recipe, search_dirs)
    if actual_trust_info == expected_trust_info:
        # shortcut if the two dictionaries match perfectly
        return
    # look for differences
    trust_errors = ""
    # get detail on non_core_processors
    expected_processor_names = set(expected_trust_info["non_core_processors"].keys())
    actual_processor_names = set(actual_trust_info["non_core_processors"].keys())
    for processor in expected_processor_names:
        expected_hash = expected_trust_info["non_core_processors"][processor][
            "sha256_hash"
        ]
        git_hash = expected_trust_info["non_core_processors"][processor].get("git_hash")
        actual_hash = (
            actual_trust_info["non_core_processors"]
            .get(processor, {})
            .get("sha256_hash")
        )
        if expected_hash != actual_hash:
            processor_path = find_processor_path(processor, recipe)
            if processor_path:
                trust_errors += (
                    f"Processor {processor} contents differ from expected.\n"
                )
                trust_errors += f"    Path: {processor_path}\n"
            else:
                trust_errors += f"Expected processor {processor} can't be found.\n"
                processor_path = expected_trust_info["non_core_processors"][
                    processor
                ].get("path")
            if verbosity > 1 and processor_path and git_hash:
                trust_errors += get_git_diff(processor_path, git_hash)
                trust_errors += get_git_log(processor_path, git_hash)
                trust_errors += "\n"
    for processor in actual_processor_names:
        if processor not in expected_processor_names:
            trust_errors += f"Unexpected processor found: {processor}\n"
            processor_path = find_processor_path(processor, recipe)
            if processor_path:
                trust_errors += f"    Path: {processor_path}\n"

    # get detail on parent_recipes
    expected_parent_recipes = list(expected_trust_info["parent_recipes"].keys())
    actual_parent_recipes = list(actual_trust_info["parent_recipes"].keys())
    if set(expected_parent_recipes) != set(actual_parent_recipes):
        trust_errors += f"Expected parent recipe list: {[expected_parent_recipes]}\n"
        trust_errors += f"Actual parent recipe list: {[expected_parent_recipes]}\n"
    for p_recipe_id in expected_parent_recipes:
        expected_hash = expected_trust_info["parent_recipes"][p_recipe_id][
            "sha256_hash"
        ]
        git_hash = expected_trust_info["parent_recipes"][p_recipe_id].get("git_hash")
        actual_hash = (
            actual_trust_info["parent_recipes"].get(p_recipe_id, {}).get("sha256_hash")
        )
        if expected_hash != actual_hash:
            p_recipe = load_recipe(
                p_recipe_id,
                override_dirs,
                search_dirs,
                make_suggestions=False,
                search_github=False,
            )
            if p_recipe:
                trust_errors += (
                    f"Parent recipe {p_recipe_id} contents differ from expected.\n"
                )
                trust_errors += f"    Path: {p_recipe['RECIPE_PATH']}\n"
                recipe_path = p_recipe["RECIPE_PATH"]
            else:
                trust_errors += (
                    f"Expected parent recipe {p_recipe_id} can't be found.\n"
                )
                recipe_path = expected_trust_info["parent_recipes"][p_recipe_id].get(
                    "path"
                )
            if verbosity > 1 and recipe_path and git_hash:
                trust_errors += get_git_log(recipe_path, git_hash)
                trust_errors += get_git_diff(recipe_path, git_hash)
                trust_errors += "\n"
    for p_recipe_id in actual_parent_recipes:
        if p_recipe_id not in expected_parent_recipes:
            trust_errors += f"Unexpected parent recipe found: {p_recipe_id}\n"
            p_recipe = load_recipe(
                p_recipe_id,
                override_dirs,
                search_dirs,
                make_suggestions=False,
                search_github=False,
            )
            if p_recipe:
                trust_errors += f"    Path: {p_recipe['RECIPE_PATH']}\n"

    if trust_errors:
        raise TrustVerificationError(trust_errors)


def update_trust_info(argv):
    """Update the parent recipe trust information stored in a recipe override"""
    verb = argv[1]
    parser = gen_common_parser()

    parser.set_usage(
        f"Usage: %prog {verb} [options] recipe_override_name [...]\n"
        "Update or add parent recipe trust information for a "
        "recipe override."
    )

    # Parse arguments
    add_search_and_override_dir_options(parser)
    options, recipe_names = common_parse(parser, argv)

    override_dirs = options.override_dirs or get_override_dirs()
    search_dirs = options.search_dirs or get_search_dirs()

    if not recipe_names:
        log_err("Need at least one recipe name or path!")
        log_err(parser.get_usage())
        return -1

    for recipe_name in recipe_names:
        recipe_path = locate_recipe(
            recipe_name,
            override_dirs,
            search_dirs,
            make_suggestions=True,
            search_github=False,
        )
        if not recipe_path:
            log_err(f"Cannot find a recipe for {recipe_name}.")
            continue
        # normalize recipe path
        recipe_path = os.path.abspath(os.path.expanduser(recipe_path))
        recipe = recipe_from_file(recipe_path)
        if "ParentRecipe" not in recipe:
            log_err(f"{recipe_name} is not a recipe override and has no parent recipe.")
            log_err(f"Path: {recipe_path}")
            continue
        if "ParentRecipeTrustInfo" not in recipe and not recipe_in_override_dir(
            recipe_path, override_dirs
        ):
            log(f"{recipe_name} does not appear to be a recipe override.")
            if recipe_from_external_repo(recipe_path):
                # don't offer to add parent trust info to a recipe from
                # an external repo
                continue
            else:
                # must be a local recipe
                answer = input("Add parent trust info anyway? [y/n]: ")
                if not answer.lower().startswith("y"):
                    continue
        # add trust info
        parent_recipe = load_recipe(recipe["ParentRecipe"], override_dirs, search_dirs)
        if parent_recipe:
            recipe["ParentRecipeTrustInfo"] = get_trust_info(
                parent_recipe, search_dirs=search_dirs
            )
            if recipe_path.endswith(".recipe.yaml"):
                with open(recipe_path, "wb") as f:
                    yaml.dump(recipe, f, encoding="utf-8")
            else:
                with open(recipe_path, "wb") as f:
                    plistlib.dump(recipe, f)
            log(f"Wrote updated {recipe_path}")
        else:
            log_err(
                f"Could not find parent recipe {recipe['ParentRecipe']} for "
                f"{recipe_name}."
            )


def verify_trust_info(argv):
    """Verify the parent recipe trust information stored in a recipe override"""
    verb = argv[1]
    parser = gen_common_parser()

    parser.set_usage(
        f"Usage: %prog {verb} [options] recipe_override_name [...]\n"
        "Verify parent recipe trust information for a "
        "recipe override."
    )
    parser.add_option(
        "-l",
        "--recipe-list",
        metavar="TEXT_FILE",
        help=("Path to a text file with a list of recipes to " "verify."),
    )
    parser.add_option(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Verbose output. May be specified multiple times.",
    )

    # Parse arguments
    add_search_and_override_dir_options(parser)
    options, recipe_names = common_parse(parser, argv)

    override_dirs = options.override_dirs or get_override_dirs()
    search_dirs = options.search_dirs or get_search_dirs()
    return_code = 0

    if options.recipe_list:
        recipe_list = parse_recipe_list(options.recipe_list)
        recipe_names.extend(recipe_list.get("recipes", []))

    if not recipe_names:
        log_err("Need at least one recipe name or path!")
        log_err(parser.get_usage())
        return -1

    for recipe_name in recipe_names:
        recipe = load_recipe(
            recipe_name,
            override_dirs,
            search_dirs,
            make_suggestions=True,
            search_github=False,
        )
        if not recipe:
            log_err(f"{recipe_name}: NOT FOUND")
            return_code = 1
            continue
        try:
            verify_parent_trust(recipe, override_dirs, search_dirs, options.verbose)
        except AutoPackagerError as err:
            log_err(f"{recipe_name}: FAILED")
            if options.verbose > 0 and str(err):
                for line in str(err).splitlines():
                    log_err(f"    {line}")
            return_code = 1
        else:
            log(f"{recipe_name}: OK")
    return return_code


def make_override(argv):
    """Make a recipe override skeleton."""
    verb = argv[1]
    parser = gen_common_parser()

    parser.set_usage(
        f"Usage: %prog {verb} [options] [recipe]\n"
        "Create a skeleton override file for a recipe. It will "
        "be stored in the first default override directory "
        "or that given by '--override-dir'"
    )

    # Parse arguments
    add_search_and_override_dir_options(parser)
    parser.add_option(
        "-n", "--name", metavar="FILENAME", help="Name for override file."
    )
    parser.add_option(
        "-f", "--force", action="store_true", help="Force overwrite an override file."
    )
    parser.add_option(
        "-p",
        "--pull",
        action="store_true",
        help=(
            "Pull the parent repos if they can't be found in the search path. "
            "Implies agreement to search GitHub."
        ),
    )
    parser.add_option(
        "--ignore-deprecation",
        action="store_true",
        help=(
            "Make an override even if the specified recipe or one of "
            "its parents is deprecated."
        ),
    )
    parser.add_option(
        "--format",
        action="store",
        default="plist",
        help=(
            "The format of the recipe override to be created. "
            "Valid options include: 'plist' (default) or 'yaml'"
        ),
    )
    (options, arguments) = common_parse(parser, argv)

    override_dirs = options.override_dirs or get_override_dirs()
    search_dirs = options.search_dirs or get_search_dirs()

    if len(arguments) != 1:
        log_err("Need exactly one recipe to override!")
        return -1

    recipe_name = arguments[0]
    if os.path.isfile(recipe_name):
        log_err(
            f"{verb} doesn't work with absolute recipe paths, "
            "as it may not be able to correctly determine the value "
            "for 'name' that would be searched in recipe directories."
        )
        return -1

    recipe = load_recipe(
        recipe_name,
        override_dirs=[],
        recipe_dirs=search_dirs,
        make_suggestions=True,
        search_github=options.pull,
        auto_pull=options.pull,
    )
    if not recipe:
        log_err(f"No valid recipe found for {recipe_name}")
        log_err("Dir(s) searched:\n\t{}".format("\n\t".join(search_dirs)))
        return 1

    # stop or warn if DeprecationWarning processor is detected
    proc_names = {x.get("Processor") for x in recipe.get("Process", [{}])}
    if "DeprecationWarning" in proc_names:
        if options.ignore_deprecation:
            log_err(
                f"WARNING: {recipe.get('RECIPE_PATH', recipe_name)} or one of "
                "its parents is deprecated. Making an override anyway, "
                "because --ignore-deprecation is specified."
            )
        else:
            log_err(
                f"{recipe.get('RECIPE_PATH', recipe_name)} or one of its parents "
                "is deprecated. Will not make an override. Use --ignore-deprecation "
                "to make an override regardless of deprecation status."
            )
            return 1

    # make sure parent has an identifier
    parent_identifier = get_identifier(recipe)
    if not parent_identifier:
        log_err(
            f"{recipe.get('RECIPE_PATH', recipe_name)} is missing an Identifier. "
            "Cannot make an override."
        )
        return 1

    override_name = options.name or remove_recipe_extension(
        os.path.basename(recipe["RECIPE_PATH"])
    )

    reversed_name = ".".join(reversed(override_name.split(".")))
    override_identifier = "local." + reversed_name

    override_dict = {
        "Identifier": override_identifier,
        "Input": recipe["Input"],
        "ParentRecipe": parent_identifier,
    }

    # add trust info
    override_dict["ParentRecipeTrustInfo"] = get_trust_info(
        recipe, search_dirs=search_dirs
    )

    if "IDENTIFIER" in override_dict["Input"]:
        del override_dict["Input"]["IDENTIFIER"]

    override_dir = os.path.expanduser(override_dirs[0])
    if not os.path.exists(os.path.join(override_dir)):
        try:
            os.makedirs(os.path.join(override_dir))
        except OSError as err:
            log_err(f"Could not create {override_dir}: {err}")
            return -1

    # set file path for override
    if options.format == "yaml":
        override_file = os.path.join(override_dir, f"{override_name}.recipe.yaml")
    else:
        override_file = os.path.join(override_dir, f"{override_name}.recipe")

    # handle existing override at same path
    if os.path.exists(override_file):
        if not options.force:
            log_err(
                f"A recipe override already exists at {override_file}, "
                "will not overwrite it. Use --force to overwrite "
                "anyway."
            )
            return -1
        os.unlink(override_file)

    # write override to file
    if options.format == "yaml":
        with open(override_file, "wb") as f:
            yaml.dump(override_dict, f, encoding="utf-8")
    else:
        with open(override_file, "wb") as f:
            plistlib.dump(override_dict, f)
    log(f"Override file saved to {override_file}")
    return 0


def parse_recipe_list(filename):
    """Parses a recipe list. This can be either a simple list of recipes to run,
    or a plist containing recipes and other key/value pairs"""
    recipe_list = {}
    try:
        with open(filename, "rb") as f:
            plist = plistlib.load(f)
        if not plist.get("recipes"):
            # try to trigger an AttributeError if the plist is not a dict
            pass
        return plist
    except Exception:
        # file does not appear to be a plist containing a dictionary;
        # read it as a plaintext list of recipes
        with open(filename, "r") as file_desc:
            data = file_desc.read()
        recipe_list["recipes"] = [
            line for line in data.splitlines() if line and not line.startswith("#")
        ]
    return recipe_list


def run_recipes(argv):
    """Run one or more recipes. If called with 'install' verb, run .install
    recipe"""
    verb = argv[1]
    parser = gen_common_parser()
    if verb == "install":
        parser.set_usage(
            f"Usage: %prog {verb} [options] [itemname ...]\n"
            "Install one or more items."
        )
    else:
        parser.set_usage(
            f"Usage: %prog {verb} [options] [recipe ...]\n" "Run one or more recipes."
        )

    # Parse arguments.
    parser.add_option(
        "--pre",
        "--preprocessor",
        action="append",
        dest="preprocessors",
        default=[],
        metavar="PREPROCESSOR",
        help=(
            "Name of a processor to run before each recipe. "
            "Can be repeated to run multiple preprocessors."
        ),
    )
    parser.add_option(
        "--post",
        "--postprocessor",
        action="append",
        dest="postprocessors",
        default=[],
        metavar="POSTPROCESSOR",
        help=(
            "Name of a processor to run after each recipe. "
            "Can be repeated to run multiple postprocessors."
        ),
    )
    parser.add_option(
        "-c",
        "--check",
        action="store_true",
        help="Only check for new/changed downloads.",
    )
    parser.add_option(
        "--ignore-parent-trust-verification-errors",
        action="store_true",
        default=False,
        help=("Run recipes even if they fail parent trust " "verification."),
    )
    parser.add_option(
        "-k",
        "--key",
        action="append",
        dest="variables",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Provide key/value pairs for recipe input. "
            "Caution: values specified here will be applied "
            "to all recipes."
        ),
    )
    parser.add_option(
        "-l",
        "--recipe-list",
        metavar="TEXT_FILE",
        help="Path to a text file with a list of recipes to run.",
    )
    parser.add_option(
        "-p",
        "--pkg",
        metavar="PKG_OR_DMG",
        help=(
            "Path to a pkg or dmg to provide to a recipe. "
            "Downloading will be skipped."
        ),
    )
    parser.add_option(
        "--report-plist",
        metavar="OUTPUT_PATH",
        help=("File path to save run report plist."),
    )
    parser.add_option(
        "-v", "--verbose", action="count", default=0, help="Verbose output."
    )
    parser.add_option(
        "-q",
        "--quiet",
        action="store_true",
        help=("Don't offer to search Github if a recipe can't " "be found."),
    )
    add_search_and_override_dir_options(parser)
    (options, arguments) = common_parse(parser, argv)

    override_dirs = options.override_dirs or get_override_dirs()
    search_dirs = options.search_dirs or get_search_dirs()

    # initialize some variables
    summary_results = {}
    failures = []
    error_count = 0
    preprocessors = []
    postprocessors = []

    # get our list of recipes
    recipe_paths = []
    if verb == "install":
        # hold on for syntactic sugar!
        for index, item in enumerate(arguments):
            # if recipe doesn't have an extension, append '.install'
            if not os.path.splitext(item)[1]:
                # no extension!
                arguments[index] = item + ".install"
            elif os.path.splitext(item)[1] != ".install":
                log_err(f"Can't install with a non-install recipe: {item}")
                del arguments[index]

    recipe_paths.extend(arguments)
    recipe_list = {}
    if options.recipe_list:
        recipe_list = parse_recipe_list(options.recipe_list)
        recipe_paths.extend(recipe_list.get("recipes", []))
        preprocessors = recipe_list.get("preprocessors", [])
        postprocessors = recipe_list.get("postprocessors", [])

    if not recipe_paths:
        log_err(parser.get_usage())
        return -1

    # override preprocessors and postprocessors if specified at the CLI
    if options.preprocessors:
        preprocessors = options.preprocessors
    if options.postprocessors:
        postprocessors = options.postprocessors

    # Add variables from environment
    cli_values = {}
    for key, value in list(os.environ.items()):
        if key.startswith("AUTOPKG_"):
            if options.verbose > 1:
                log(f"Using environment var {key}={value}")
            local_key = key[8:]
            cli_values[local_key] = value

    # Add variables from recipe list. These might override those from
    # environment variables
    if recipe_list:
        for key, value in list(recipe_list.items()):
            if key not in ["recipes", "preprocessors", "postprocessors"]:
                cli_values[key] = value

    # Add variables from commandline. These might override those from
    # environment variables and recipe_list
    for arg in options.variables:
        (key, sep, value) = arg.partition("=")
        if sep != "=":
            log_err(f"Invalid variable [key=value]: {arg}")
            log_err(parser.get_usage())
            return 1
        cli_values[key] = value

    if options.pkg:
        cli_values["PKG"] = options.pkg

    if len(recipe_paths) > 1 and options.pkg:
        log_err("-p/--pkg option can't be used with multiple recipes!")
        return -1

    cache_dir = get_pref("CACHE_DIR") or "~/Library/AutoPkg/Cache"
    cache_dir = os.path.expanduser(cache_dir)
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir, 0o755)
    current_run_results_plist = os.path.join(cache_dir, "autopkg_results.plist")

    run_results = []
    try:
        with open(current_run_results_plist, "wb") as f:
            plistlib.dump(run_results, f)
    except OSError as err:
        log_err(f"Can't write results to {current_run_results_plist}: {err.strerror}")

    if options.report_plist:
        results_report = dict()
        write_plist_exit_on_fail(results_report, options.report_plist)

    make_suggestions = True
    if len(recipe_paths) > 1:
        # don't make suggestions or offer to search GitHub
        # if we have a list of recipes
        make_suggestions = False

    if options.quiet:
        # don't make suggestions or search Github if told to be quiet
        make_suggestions = False
    for recipe_path in recipe_paths:
        recipe = load_recipe(
            recipe_path,
            override_dirs,
            search_dirs,
            preprocessors,
            postprocessors,
            make_suggestions=make_suggestions,
            search_github=make_suggestions,
        )
        if not recipe:
            if not make_suggestions:
                log_err(f"No valid recipe found for {recipe_path}")
            error_count += 1
            continue

        if options.check:
            # remove steps from the end of the recipe Process until we find a
            # EndOfCheckPhase step
            while (
                len(recipe["Process"]) >= 1
                and recipe["Process"][-1]["Processor"] != "EndOfCheckPhase"
            ):
                del recipe["Process"][-1]
            if len(recipe["Process"]) == 0:
                log_err(
                    f"Recipe at {recipe_path} is missing EndOfCheckPhase Processor, "
                    "not possible to perform check."
                )
                error_count += 1
                continue

        log(f"Processing {recipe_path}...")

        # Create a local copy of preferences
        prefs = copy.deepcopy(dict(get_all_prefs()))
        # Add RECIPE_PATH and RECIPE_DIR variables for use by processors
        prefs["RECIPE_PATH"] = os.path.abspath(recipe["RECIPE_PATH"])
        prefs["RECIPE_DIR"] = os.path.dirname(prefs["RECIPE_PATH"])
        prefs["PARENT_RECIPES"] = recipe.get("PARENT_RECIPES", [])
        # Update search locations that may have been overridden with CLI or
        # environment variables
        prefs["RECIPE_SEARCH_DIRS"] = search_dirs
        prefs["RECIPE_OVERRIDE_DIRS"] = override_dirs

        # Add our verbosity level
        prefs["verbose"] = options.verbose

        autopackager = AutoPackager(options, prefs)

        fail_recipes_without_trust_info = bool(
            cli_values.get(
                "FAIL_RECIPES_WITHOUT_TRUST_INFO",
                prefs.get("FAIL_RECIPES_WITHOUT_TRUST_INFO"),
            )
        )

        if (
            "ParentRecipeTrustInfo" not in recipe
            and not fail_recipes_without_trust_info
        ):
            log_err(
                f"WARNING: {recipe_path} is missing trust info and "
                "FAIL_RECIPES_WITHOUT_TRUST_INFO is not set. "
                "Proceeding..."
            )

        # we should also skip trust verification if we've been told to ignore
        # verification errors
        skip_trust_verification = options.ignore_parent_trust_verification_errors or (
            "ParentRecipeTrustInfo" not in recipe
            and not fail_recipes_without_trust_info
        )

        try:
            if not skip_trust_verification:
                verify_parent_trust(recipe, override_dirs, search_dirs, options.verbose)
            autopackager.process_cli_overrides(recipe, cli_values)
            autopackager.verify(recipe)
            autopackager.process(recipe)
        except AutoPackagerError as err:
            error_count += 1
            failure = {}
            if isinstance(err, (TrustVerificationWarning, TrustVerificationError)):
                log_err("Failed local trust verification.")
            else:
                log_err("Failed.")
            failure["recipe"] = recipe_path
            failure["message"] = str(err)
            failure["traceback"] = traceback.format_exc()
            failures.append(failure)
            autopackager.results.append({"RecipeError": str(err).rstrip()})

        run_results.append(autopackager.results)
        try:
            with open(current_run_results_plist, "wb") as f:
                plistlib.dump(run_results, f)
        except OSError as err:
            log_err(
                f"Can't write results to {current_run_results_plist}: {err.strerror}"
            )

        # build a pathname for a receipt
        recipe_basename = os.path.splitext(os.path.basename(recipe_path))[0]
        # TO-DO: if recipe processing fails too early,
        # autopackager.env["RECIPE_CACHE_DIR"] is not defined and we can't
        # write a recipt. We should handle this better.
        # for now, just write the receipt to /tmp/receipts
        receipt_dir = os.path.join(
            autopackager.env.get("RECIPE_CACHE_DIR", "/tmp"), "receipts"
        )
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        receipt_name = f"{recipe_basename}-receipt-{timestamp}.plist"

        if not os.path.exists(receipt_dir):
            try:
                os.makedirs(receipt_dir)
            except OSError as err:
                log_err(f"Can't create {receipt_dir}: {err.strerror}")

        # look through results for interesting info
        # and record for later summary and use
        for item in autopackager.results:
            if item.get("Output"):
                # record any summary results
                output_keys = list(item["Output"].keys())
                results_keys = [
                    summary_key
                    for summary_key in output_keys
                    if summary_key.endswith("_summary_result")
                ]
                for key in results_keys:
                    result = item["Output"][key]
                    summary_text = result.get("summary_text", "")
                    data = result.get("data")
                    if key not in summary_results:
                        summary_results[key] = {}
                        summary_results[key]["summary_text"] = summary_text
                        if type(data).__name__ in ["dict", "__NSCFDictionary"]:
                            summary_results[key]["header"] = result.get(
                                "report_fields"
                            ) or list(data.keys())
                        summary_results[key]["data_rows"] = []
                    summary_results[key]["data_rows"].append(data)

        # save receipt
        if os.path.exists(receipt_dir):
            receipt_path = os.path.join(receipt_dir, receipt_name)
            try:
                with open(receipt_path, "wb") as f:
                    plistlib.dump(autopackager.results, f)
                if options.verbose:
                    log(f"Receipt written to {receipt_path}")
            except OSError as err:
                log_err(f"Can't write receipt to {receipt_path}: {err.strerror}")

    # done running recipes, print a summary
    if failures:
        log("\nThe following recipes failed:")
        for item in failures:
            log(f"    {item['recipe']}")
            for line in item["message"].splitlines():
                log(f"        {line}")

    if summary_results:
        for _key, value in list(summary_results.items()):
            log(f"\n{value['summary_text']}")

            # make our table header
            display_header = [
                item.replace("_", " ").title() for item in value["header"]
            ]
            underlines = ["-" * len(item) for item in value["header"]]
            rows = [display_header, underlines]
            for row in value["data_rows"]:
                this_row = []
                for field in value["header"]:
                    this_row.append(row[field])
                rows.append(this_row)

            # calculate the widths of each column
            widths = []
            for column in range(len(value["header"])):
                this_column = [len(row[column]) for row in rows]
                widths.append(max(this_column) + 2)

            # build a format string for each row based on our
            # column widths
            format_str = "    "
            for count, width in enumerate(widths):
                # adding format strings with 'count' for 2.6 compatibility
                format_str += "{" + str(count) + ":<" + str(width) + "}"

            # print each row (which includes the header rows)
            for row in rows:
                log(format_str.format(*row))

    if not summary_results:
        log("\nNothing downloaded, packaged or imported.")

    # save report plist with the summary data
    if options.report_plist:
        results_report["failures"] = failures
        results_report["summary_results"] = summary_results
        write_plist_exit_on_fail(results_report, options.report_plist)
        log(f"\nReport plist saved to {options.report_plist}.")

    if error_count:
        return RECIPE_FAILED_CODE


def printplistitem(label, value, indent=0):
    """Prints a plist item in an 'attractive' way"""
    indentspace = "    "
    if value is None:
        log(indentspace * indent + f"{label}: !NONE!")
    elif type(value) == list or type(value).__name__ == "NSCFArray":
        if label:
            log(indentspace * indent + f"{label}:")
        for item in value:
            printplistitem("", item, indent + 1)
    elif type(value) == dict or type(value).__name__ == "NSCFDictionary":
        if label:
            log(indentspace * indent + f"{label}:")
        for subkey in list(value.keys()):
            printplistitem(subkey, value[subkey], indent + 1)
    else:
        if label:
            log(indentspace * indent + f"{label}: {value}")
        else:
            log(indentspace * indent + f"{value}")


def printplist(plistdict):
    """Prints plist dictionary in a pretty(?) way"""
    keys = list(plistdict.keys())
    keys.sort()
    for key in keys:
        printplistitem(key, plistdict[key], indent=2)


def find_http_urls_in_recipe(recipe):
    """Looks in the Input and Process sections of a recipe for any http URLs.
    Returns a dict."""
    recipe_urls = {}
    for key, value in list(recipe.get("Input", {}).items()):
        if isinstance(value, str) and value.startswith("http:"):
            if "Input" not in recipe_urls:
                recipe_urls["Input"] = {}
            recipe_urls["Input"][key] = value
    for step in recipe.get("Process", []):
        processor = step["Processor"]
        arguments = step.get("Arguments", {})
        for key, value in list(arguments.items()):
            if isinstance(value, str) and value.startswith("http:"):
                if "Process" not in recipe_urls:
                    recipe_urls["Process"] = {}
                if processor not in recipe_urls["Process"]:
                    recipe_urls["Process"][processor] = {}
                recipe_urls["Process"][processor][key] = value
    return recipe_urls


def audit(argv):
    """Audit one or more recipes."""
    verb = argv[1]
    parser = gen_common_parser()

    parser.set_usage(
        f"Usage: %prog {verb} [options] recipe [..]\n" "Audit one or more recipes."
    )

    # Parse arguments
    add_search_and_override_dir_options(parser)
    parser.add_option(
        "-l",
        "--recipe-list",
        metavar="TEXT_FILE",
        help="Path to a text file with a list of recipes to " "audit.",
    )
    parser.add_option(
        "-p",
        "--plist",
        action="store_true",
        default=False,
        help="Output in plist format.",
    )
    (options, arguments) = common_parse(parser, argv)

    override_dirs = options.override_dirs or get_override_dirs()
    search_dirs = options.search_dirs or get_search_dirs()

    # these processors download items
    DOWNLOAD_PROCESSORS = {"CURLDownloader", "URLDownloader"}

    # these processors build packages or dmgs
    CREATOR_PROCESSORS = {"DmgCreator", "FlatPkgPacker", "PkgCreator"}

    # these processors could be used to modify the contents of a vendor
    # distribution (pkg, dmg, etc)
    MODIFICATION_PROCESSORS = [
        "Copier",
        "FileCreator",
        "FileMover",
        "PathDeleter",
        "PkgInfoCreator",
        "PlistEditor",
        "Symlinker",
    ]

    recipe_paths = []
    recipe_paths.extend(arguments)
    if options.recipe_list:
        recipe_list = parse_recipe_list(options.recipe_list)
        recipe_paths.extend(recipe_list.get("recipes", []))

    override_dirs = options.override_dirs or get_override_dirs()
    search_dirs = options.search_dirs or get_search_dirs()

    if not recipe_paths:
        log_err(parser.get_usage())
        return -1

    audit_results = {}
    recipe_issue_count = 0
    recipe_no_issue_count = 0
    for recipe_path in recipe_paths:
        recipe = load_recipe(
            recipe_path,
            override_dirs,
            search_dirs,
            None,
            None,
            make_suggestions=False,
            search_github=False,
        )
        if not recipe:
            log_err(f"No valid recipe found for {recipe_path}")
            continue

        audit_results[recipe_path] = {}
        http_urls = find_http_urls_in_recipe(recipe)
        if http_urls:
            audit_results[recipe_path]["http_urls"] = http_urls
        recipe_processors = [step["Processor"] for step in recipe["Process"]]
        if (
            set(recipe_processors).intersection(DOWNLOAD_PROCESSORS)
            and "CodeSignatureVerifier" not in recipe_processors
        ):
            audit_results[recipe_path]["MissingCodeSignatureVerifier"] = True
        creator_processors = set(recipe_processors).intersection(CREATOR_PROCESSORS)
        core_processors = core_processor_names()
        non_core_processors = [
            processor
            for processor in recipe_processors
            if processor not in core_processors
        ]
        modification_processors = set()
        # only flag modification processors if there is a creator or non-core
        # processor afterwards
        for index in range(len(recipe_processors)):
            if recipe_processors[index] in MODIFICATION_PROCESSORS:
                # are there any creator processors after this one?
                if set(recipe_processors[index + 1 :]).intersection(CREATOR_PROCESSORS):
                    modification_processors.add(recipe_processors[index])
                # are there any non-core processors after this one?
                elif set(recipe_processors[index + 1 :]).intersection(
                    set(non_core_processors)
                ):
                    modification_processors.add(recipe_processors[index])
        if creator_processors or non_core_processors:
            audit_results[recipe_path]["audit_processors"] = list(
                creator_processors
            ) + list(modification_processors)
        if non_core_processors:
            audit_results[recipe_path]["non_core_processors"] = non_core_processors
        if options.plist:
            # skip all the printing; we'll print plist output later.
            continue
        if audit_results[recipe_path]:
            recipe_issue_count += 1
            log(recipe_path)
            log(f"    File path:        {recipe['RECIPE_PATH']}")
            if recipe.get("PARENT_RECIPES"):
                text = "\n                      ".join(recipe["PARENT_RECIPES"])
                log(f"    Parent recipe(s): {text}")
            if audit_results[recipe_path].get("MissingCodeSignatureVerifier"):
                log("    Missing CodeSignatureVerifier")
            if audit_results[recipe_path].get("http_urls"):
                log("    The following http URLs were found in the recipe:")
                printplist(audit_results[recipe_path]["http_urls"])
            if audit_results[recipe_path].get("non_core_processors"):
                log(
                    "    The following processors are non-core and can execute "
                    "arbitrary code, performing any action."
                )
                log(
                    "    Be sure you understand what the processor does and/or "
                    "you trust its source:"
                )
                for processor in audit_results[recipe_path].get("non_core_processors"):
                    log(f"        {processor}")
            if audit_results[recipe_path].get("audit_processors"):
                log(
                    "    The following processors make modifications and their "
                    "use in this recipe should be more closely inspected:"
                )
                for processor in audit_results[recipe_path].get("audit_processors"):
                    log(f"        {processor}")
            log("")
        else:
            recipe_no_issue_count += 1
            log(f"{recipe_path}: no audit flags triggered.")
    if options.plist:
        print(plistlib.dumps(audit_results))
    elif len(recipe_paths) > 1:
        log("\nSummary:")
        log(f"    {len([audit_results.keys()])} recipes audited")
        log(f"    {recipe_issue_count} recipes with audit issues")
        log(f"    {recipe_no_issue_count} recipes triggered no audit flags")


def new_recipe(argv):
    """Makes a new recipe template"""
    verb = argv[1]
    parser = gen_common_parser()

    parser.set_usage(
        f"Usage: %prog {verb} [options] recipe_pathname\n" "Make a new template recipe."
    )

    # Parse arguments
    parser.add_option("-i", "--identifier", help="Recipe identifier")
    parser.add_option(
        "-p", "--parent-identifier", help="Parent recipe identifier for this recipe."
    )
    parser.add_option(
        "--format",
        action="store",
        default="plist",
        help=(
            "The format of the new recipe to be created. "
            "Valid options include: 'plist' (default) or 'yaml'"
        ),
    )
    (options, arguments) = common_parse(parser, argv)

    if len(arguments) != 1:
        log_err("Must specify exactly one recipe pathname!")
        log_err(parser.get_usage())
        return -1

    filename = arguments[0]
    name = os.path.basename(filename).split(".")[0]
    identifier = options.identifier or "local." + name

    recipe = {
        "Description": "Recipe description",
        "Identifier": identifier,
        "Input": {"NAME": name},
        "MinimumVersion": "1.0",
        "Process": [
            {
                "Arguments": {"Argument1": "Value1", "Argument2": "Value2"},
                "Processor": "ProcessorName",
            }
        ],
    }
    if options.parent_identifier:
        recipe["ParentRecipe"] = options.parent_identifier

    try:
        if options.format == "yaml" or filename.endswith(".recipe.yaml"):
            # Yaml recipes require AutoPkg 2.3 or later.
            recipe["MinimumVersion"] = "2.3"
            with open(filename, "wb") as f:
                yaml.dump(recipe, f, encoding="utf-8")
        else:
            with open(filename, "wb") as f:
                plistlib.dump(recipe, f)
        log(f"Saved new recipe to {filename}")
    except Exception as err:
        log_err(f"Failed to write recipe: {err}")


def main(argv):
    """Main routine"""
    # define our subcommands ('verbs')
    subcommands = {
        "help": {"function": display_help, "help": "Display this help"},
        "audit": {"function": audit, "help": "Audit one or more recipes."},
        "info": {
            "function": get_info,
            "help": "Get info about configuration or a recipe",
        },
        "install": {
            "function": run_recipes,
            "help": (
                "Run one or more install recipes. "
                "Example: autopkg install Firefox -- "
                "equivalent to: autopkg run Firefox.install"
            ),
        },
        "list-recipes": {
            "function": list_recipes,
            "help": "List recipes available locally",
        },
        "list-repos": {"function": repo_list, "help": "see repo-list"},
        "list-processors": {
            "function": list_processors,
            "help": "List available core Processors",
        },
        "make-override": {"function": make_override, "help": "Make a recipe override"},
        "new-recipe": {"function": new_recipe, "help": "Make a new template recipe"},
        "processor-info": {
            "function": processor_info,
            "help": "Get information about a specific processor",
        },
        "processor-list": {"function": list_processors, "help": "see list-processors"},
        "repo-add": {
            "function": repo_add,
            "help": "Add one or more recipe repo from a URL",
        },
        "repo-delete": {"function": repo_delete, "help": "Delete a recipe repo"},
        "repo-list": {"function": repo_list, "help": "List installed recipe repos"},
        "repo-update": {
            "function": repo_update,
            "help": "Update one or more recipe repos",
        },
        "run": {"function": run_recipes, "help": "Run one or more recipes"},
        "search": {"function": search_recipes, "help": "Search for recipes on GitHub."},
        "update-trust-info": {
            "function": update_trust_info,
            "help": (
                "Update or add parent recipe trust info for a recipe " "override."
            ),
        },
        "verify-trust-info": {
            "function": verify_trust_info,
            "help": ("Verify parent recipe trust info for a recipe override."),
        },
        "version": {
            "function": print_version,
            "help": "Print the current version of autopkg",
        },
    }

    # Warn against running as root
    if is_mac() and os.getuid() == 0:
        log_err("\n" + "WARNING! " * 8 + "\n")
        log_err(
            "    Running AutoPkg as root or using `sudo` is not recommended!\n"
            "    A mistake in a recipe or processor could modify or delete\n"
            "    important system files.\n"
            "    Please run autopkg as an unprivileged user.\n"
            "    A future release of autopkg may fail with an error if run as\n"
            "    root.\n"
        )
        log_err("WARNING! " * 8 + "\n")

    try:
        verb = argv[1]
    except IndexError:
        verb = "help"
    if verb.startswith("-"):
        # option instead of a verb
        verb = "help"
    if verb == "help" or verb not in subcommands:
        display_help(argv, subcommands)
        return 1

    # Call the command function and pass it the argument list.
    # We leave the verb in the list in case one function can handle
    # multiple verbs.
    return subcommands[verb]["function"](argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
