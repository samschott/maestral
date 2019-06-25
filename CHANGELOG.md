### v0.1.2-dev0

_Changes:_

- Detect color of system tray and invert icon color automatically if not on macOS.
- Added new command line option 'autostart'.
- Shut down immediately and kill threads instead of waiting for timeout.
- Do not upload files that have identical content on Dropbox. Previously: files were 
  always uploaded and conflict checking was left to do by the Dropbox server.

_Fixes:_

- Fixed a bug that would prevent Maestral from quitting if the setup dialog is aborted.
- Fixed a bug that would cause Maestral to crash during the setup dialog when switching
  multiple times between the "Select Folders to Sync" and "Select Dropbox location" panes.
- Fixed a bug which would cause uploads that are split into multiple chunks to fail.

### v0.1.1 (2019-06-23)

_Fixes:_

- Fixes an issue which would prevent newly created empty folders from being synced.
- Remove references to conda in startup script.