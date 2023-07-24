#### Changed:

* Allow limiting the upload and download bandwidth used for syncing, either by setting the config file values, by using the CLI `maestral bandwidth-limit up|down`, or through the Settings pane in the GUI.
* Add config file items for the maximum number of parallel file transfers.
* Speed up querying the sync status of folders.
* Added support for Python 3.12.

#### Fixed:

* Fixes the download sync of remote symlinks. The local item now is an actual symlink instead of a 0 KB file.
* Fixes an issue where the Login Items entry for Maestral would incorrectly be listed with the developer name instead of the app name in macOS Ventura's System Settings.
* Fixes an issue which would prevent periodic reindexing.
* Fixes an issue with interrupted downloads of folders which are newly included by selective sync not automatically resuming when Maestral restarts.
* Fixes an issue with detect the init system on some Linux distributions, a prerequisite for the autostart functionality.

#### Removed:

* Removed support for access token authentication. Users who linked Maestral to their Dropbox account before September 2020 will be asked to reauthenticate so that Maestral can retrieve a refresh token instead.