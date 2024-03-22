#### Changed:

* Improved support for systems where some file system calls don't accept a `follow_symlinks = False` option, notably `chmod` and `utime`.
* Abort uploads if the file is modified between the upload of individual chunks. This saves some bandwidth and prevents us from ever committing an inconsistent file to Dropbox's version history.
* Show desktop notifications when a conflicting copy is created both during upload and download sync. Unlike regular notifications, those notifications are shown for each conflicting copy instead of giving a summary count.
* Append the username and date to the file name of a conflicting copy, for example `myfile (Sam's conflicting copy 2022-08-30).pdf`.

#### Fixed:

* Fixes an issue for systems that do not provide /sys/class/power_supply such as Synology devices. Power supply state is periodically queried to prevent automatic reindexing when on battery power.
* Fixes potentially inconsistent error messages if Maestral does not have permissions to perform file moves within the local Dropbox folder.
* Fixes a regression with some icon buttons in the macOS GUI not being displayed.