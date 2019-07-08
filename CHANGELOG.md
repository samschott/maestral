### v0.1.3-dev2 (2019-07-07)

This version includes some GUI improvements: A more informative setup dialog, a new menu
item which lists recently changed files and an improved unlinking flow. More notably, 
the upload syncing of local changes has been refactored.

_Added:_

- Added progress messages for uploads and downloads, e.g., "Downloading 3/98...". These
  are output as info messages and shown in the status field of the system tray menu.
- Added "Recently Changed Files" submenu to the system tray menu. "Recently Changed Files"
  shows entries for the 30 last-changed files (synced folders only) and navigates to the
  respective file in the default file manager when an entry is clicked.
- When unlinking your Dropbox account through the GUI, Maestral is restarted to enter the 
  setup dialog.

_Changed:_

- Some programs save file changes by deleting the old file and creating a new file. This 
  is now correctly combined to a single `FileModified` event.
- Some programs create temporary files when saving changes. Those temporary files are 
  deleted again after the save is completed. Those `FileCreated` and `FileDeleted` 
  events, which occur in quick succession, are now ignored by Maestral.
- The following file names have been added to the exclusion list:
    - Files that start with "\~$" or ".~"
    - Files that start with "~" and end with ".tmp"
- Cleaned up some of the config module code: removed Spyder specific functions and
  obsolete Python 2 compatibility.
- Refactored sync code: Moved all download sync functionality from `client` to `monitor`.
  Upload and download syncing are now both handled by the new class `monitor.UpDownSync`
  and `MaestralClient` only handles access to the Dropbox API.

_Fixed:_

- Fixed a bug which may result in a removed folder not being deleted locally if it 
  contains subfolders.
- Fixed a bug which may result in file modifications not being uploaded, depending on 
  how the changes were saved by the program which was used to edit the file.
- Fixed a bug which would incorrectly list top level files as folders in the "Exclude
  folders" dialog.
- Truncate entries in the "Recently Changed Files" menu if their width exceeds 200 pixels.

### v0.1.2 (2019-06-25)

_Added:_

- Added new command line option 'autostart' to automatically start Maestral on login.

_Changed:_

- Limit notifications to remote changes only and only notify for changes in folders that
  currently being synced, unless more than 100 files have changed.
- Detect color of system tray and invert icon color automatically if not on macOS.
- Shut down immediately and kill threads instead of waiting for timeout.
- Improve appearance of Settings window in GTK 3 style.

_Fixed:_

- Fixed a bug which would cause uploads to fail if they are split into multiple chunks.
- Fixed a bug that would prevent Maestral from quitting if the setup dialog is aborted.
- Fixed a bug that would cause Maestral to crash during the setup dialog when switching
  multiple times between the "Select Folders to Sync" and "Select Dropbox location" panels.
- Do not upload files that have identical content on Dropbox. Previously: files were
  always uploaded and conflict checking was left to do by the Dropbox server.

### v0.1.1 (2019-06-23)

_Fixed:_

- Fixes an issue which would prevent newly created empty folders from being synced.
- Remove references to conda in startup script.
