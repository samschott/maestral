### v0.1.2 (2019-06-25)

_Changes:_

- Limit notifications to remote changes only and only notify for changes in folders that
  currently beeing synced, unless more than 100 files have changed.
- Detect color of system tray and invert icon color automatically if not on macOS.
- Added new command line option 'autostart'.
- Shut down immediately and kill threads instead of waiting for timeout.
- Improve appearance of Settings window in GTK 3 style.

_Fixes:_

- Fixed a bug which would cause uploads to fail if they are split into multiple chunks.
- Fixed a bug that would prevent Maestral from quitting if the setup dialog is aborted.
- Fixed a bug that would cause Maestral to crash during the setup dialog when switching
  multiple times between the "Select Folders to Sync" and "Select Dropbox location" panels.
- Do not upload files that have identical content on Dropbox. Previously: files were 
  always uploaded and conflict checking was left to do by the Dropbox server.

### v0.1.1 (2019-06-23)

_Fixes:_

- Fixes an issue which would prevent newly created empty folders from being synced.
- Remove references to conda in startup script.
