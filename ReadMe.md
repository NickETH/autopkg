AutoPkg for Windows!
====================

Fully working Windows release is [here](https://github.com/NickETH/autopkg/releases).

This became possible thanks to the fundamental work of [Nick McSpadden](https://github.com/nmcspadden/autopkg/tree/win) !

AutoPkg is an automation framework for macOS and now Windows! software packaging and distribution, oriented towards the tasks one would normally perform manually to prepare third-party software for mass deployment to managed clients.

These tasks typically involve at least several of the following steps:

* downloading an application and/or updates for it, usually via a web browser
* extracting them from a multitude of archive formats
* adding site-specific configuration
* adding sane versioning information
* "fixing" poorly-written installer scripts
* saving these modifications back to a compressed disk image or installer package
* importing these into a software distribution system like Munki, Jamf Pro or SCCM, Baramundi on Windows etc.
* customizing the associated metadata for such a system with site-specific data, post-installation scripts, version info or other metadata

Often these tasks follow similar patterns for each individual application, and when managing many applications this becomes a daily task full of sub-tasks that one must remember (and/or maintain documentation for) about exactly what had to be done for a successful deployment of every update for every managed piece of software.

With AutoPkg, we define these steps in a "Recipe" plist-based format, run automatically instead of by hand, and shared with others.


Installation on Windows
-----------------------

Download the [actual Windows release](https://github.com/NickETH/autopkg/releases).  Download the MSI.
But first, install all the prequisites!

Recommended installation is per user into the user profile, which is used to run AutoPkg. For this to work, the MSI must be advertised with admin rights and the following command:

msiexec /jm AutoPkgWin.msi

CAUTION: This needs an elevated CMD-shell! PS-console does not work!

After this, the Installer can be run with standard user rights.

AutoPkg for Windows requires Windows 7 or newer, 32 or 64bit and to have Git installed is highly recommended, so managing recipe repositories is possible. Knowledge of Git itself is not required but helps.
Tested only on 64bit!

**The following software and tools are needed as prequisites to run AutoPkg on Windows:**

* Python 3.8.x: [Download](https://www.python.org/downloads/release/python-3810/) (Caution: pythonnet is still not compatible with Python 3.9/3.10)
  * Although Python 3.10.x should work with pythonnet v3.0.0-alpha2 with: pip install pythonnet --pre
  * Needed libraries: pyyaml, appdirs, msl.loadlib, pythonnet, comtypes, pywin32, certifi
  * If Python is present, those libs are automatically installed by the AutoPkg installer.
* Git (highly recomended): [Download](https://git-scm.com/download/win)
* 7zip: [Download](https://www.7-zip.org/)
* Windows-Installer-SDK: [Download](https://developer.microsoft.com/en-us/windows/downloads/sdk-archive), You have to select the version, that fits your OS. This is necessary for some of the MSI-related processors.
  * Download the webinstaller, choose a download directory and select at least: "MSI Tools", "Windows SDK for Desktop C++ x86 Apps" and on x64 systems also "Windows SDK for Desktop C++ x64 Apps", (there will be some additional selections).
  * Then install at minimum: "Windows SDK Desktop Tools x86-x86_en-us.msi" and "Windows SDK Desktop Tools x64-x86_en-us.msi" (x64 only).
  * Find the install location (Somewhere under C:\Program Files (x86)\Windows Kits\...)
  * Copy the Wi*.vbs and Msi*.exe files over to your MSITools folder.
  * Register the 64bit mergemod DLL: regsvr32 "C:\Program Files (x86)\Windows Kits\10\bin\xxx\x64\mergemod.dll"
  * If the SDK is present, this COM DLL is automatically registered by the AutoPkg installer.
* Wix-Toolset: [Download](https://wixtoolset.org/releases/), version 3.11 should do it. Although, i always use the latest development version.
* MSBuild: [Download] (https://visualstudio.microsoft.com/thank-you-downloading-visual-studio/?sku=BuildTools&rel=16#), THE Windows Make!
  * Install commandline: vs_buildtools.exe --add Microsoft.VisualStudio.Workload.MSBuildTools --quiet
  * [Install HowTo] (https://stackoverflow.com/questions/42696948/how-can-i-install-the-vs2017-version-of-msbuild-on-a-build-server-without-instal)
  * See the AutoPkg build itself for a jump start. Wix-based stuff should use it to build/make.
* NANT: [Download](http://nant.sourceforge.net/) (Deprecated), this is one of the predecessors of MS-Build (which you should use, when starting with a new build-enviroment).
  * i know: This tool is hopelessly outdated, but i use it around WIX since ages. Just did not find the time to move over to MS-Build so far. Transition is on its way...
  * Download the ZIP package, extract it and copy the "nant-0.92" folder to the MSITools dir.
* ResourceHacker: [Download](http://www.angusj.com/resourcehacker/#download), Download the ZIP install, extract it and copy ResourceHacker.exe to your tools folder.


Usage
-----

A getting started guide is available [here](https://github.com/autopkg/autopkg/wiki/Getting-Started).

Frequently Asked Questions (and answers!) are [here](https://github.com/autopkg/autopkg/wiki/FAQ).

See [the wiki](https://github.com/autopkg/autopkg/wiki) for more documentation.

See [recipes-win](https://github.com/NickETH/recipes-win/tree/master/SharedProcessors) for a basic set of processors for Windows.

Discussion
----------

Discussion of the use and development of AutoPkg is [here](http://groups.google.com/group/autopkg-discuss).
