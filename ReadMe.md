AutoPkg for Windows!
====================

Early, experimental Windows release is [here](https://github.com/NickETH/autopkg/tree/win).

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

Download the [very first Windows release](https://github.com/NickETH/autopkg/tree/win). Choose "Clone or download", "Download ZIP".

Extract the ZIP and copy the "Code" folder to the place, where your AutoPkg should run from. Rename it to AutoPkg. Rename the file "autopkg" in it to AutoPkg.py.

AutoPkg for Windows requires Windows 7 or newer, 32 or 64bit, and Git is highly recommended to have installed so that it can manage recipe repositories. Knowledge of Git itself is not required but helps.

Git can be installed [from](https://git-scm.com/download/win).

**The following software and tools are needed as prequisites to run AutoPkg on Windows:**

* Python 2.7.x: [Download](https://www.python.org/downloads/)
* 7zip: [Download](https://www.7-zip.org/)
* Windows-Installer-SDK: [Download](https://developer.microsoft.com/en-us/windows/downloads/sdk-archive), You have to select the version, that fits your OS.
  * Download the webinstaller, choose a download directory and select at least: "MSI Tools" and "Windows SDK for Desktop C++ x86 Apps", (there will be some additional selections).
  * Then install at minimum: "Windows SDK Desktop Tools x86-x86_en-us.msi". If know how to do it, an admin install will do.
  * Find the install location (Somewhere under C:\Program Files (x86)\Windows Kits\...)
  * Copy the Wi*.vbs and Msi*.exe files over to your tools folder.
* Wix-Toolset: [Download](https://wixtoolset.org/releases/), version 3.11 should do it. Although, i always use the latest development version.
* NANT: [Download](http://nant.sourceforge.net/), this is one of the predecessors of MS-Build, which you should use, when starting with a new build-enviroment.
  * i know: This tool is hopelessly outdated, but i use it around WIX since ages. Just did not find the time to move over to MS-Build so far. If someone likes to step in...
* Edit the "AutoPkg-default.reg" regfile and alter the paths to your needs. Then apply it to the system on the working account for AutoPkg.

Usage
-----

A getting started guide is available [here](https://github.com/autopkg/autopkg/wiki/Getting-Started).

Frequently Asked Questions (and answers!) are [here](https://github.com/autopkg/autopkg/wiki/FAQ).

See [the wiki](https://github.com/autopkg/autopkg/wiki) for more documentation.


Discussion
----------

Discussion of the use and development of AutoPkg is [here](http://groups.google.com/group/autopkg-discuss).
