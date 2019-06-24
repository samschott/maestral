### v0.1.2-dev0

_Changes:_

- Added new command line option 'autostart'.
- Shut down immediately and kill threads instead of waiting for timeout.

_Fixes:_

- Fixed a bug that would prevent Maestral from quitting if the setup dialog is aborted.
- Fixed a bug that would cause Maestral to crash during the setup dialog when swtiching
  multiple times between the "Select Folders to Sync" and "Select Dropbox location" panes.

### v0.1.1 (2019-06-23)

_Fixes:_

- Fixes an issue which would prevent newly created empty folders from beeing synced.
- Remove references to conda in startup script.