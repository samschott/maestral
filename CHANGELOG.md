### v0.2.2-dev1 (2019-07-19)

_Added_:

- Added support for file and folder names with two or more periods.
- Temporary autosave files that are created by macOS are now detected by their extension
  and excluded from syncing.
- More fine-grained errors, subclassed from `MaestralApiError`.
- Log all events of level INFO and higher to a rotating file in '~/.maestral/logs'. The
  log folder will never exceed 6 MB.

_Changed_:

- Better handling when Dropbox resets a cursor: retry any `files_list_folder` calls and
  prompt the user to rebuild the index on `files_list_folder_longpoll` calls.
- Prepare for G-suite Dropbox integration: G-suite files such as Google docs and sheets
  will not be downloadable but can only be exported. Maestral will ignore such files.
- Moved deprecated API calls to v2.
- Better handling of `OSErrors` on download.

_Fixed_:

- Fixed a bug which would prevent some error dialogs from being shown to the user.

### v0.2.1 (2019-07-18)

_Changed_:

- Reload all file and folder icons when the system appearance changes: the system may
  provide different icons (e.g., darker folder icons in "dark mode" on macOS Mojave).
- Improved notification center alerts in macOS: when installed as a bundled app,
  notifications are now properly sent from the Maestral itself, showing the Maestral icon,
  instead of through apple script.
- Improved layout of the "Rebuild index" dialog.

_Fixed_:

- Fixes a bug which would prevent Meastral from starting on login: the correct startup
  script is now called.

### v0.2.0 (2019-07-17)

#### Major changes

_Added_:

- Proper handling of sync errors. Dropbox API errors are converted to a more informative
  `MaestralApiError` and a log of sync errors is kept. This log is cleared as sync errors
  are resolved. Errors are now handled as follows:
      - Individual file sync errors are indicated by the system tray icon changing. The
        can listed by the user through the GUI.
      - Unexpected errors or major errors which prevent Maestral from functioning (e.g., a
        corrupted index) trigger an error dialog.

- Introduced a new panel "View Sync Issues..." to show an overview of sync issues and
  their cause (invalid file name, insufficient space on Dropbox, etc...)
- Added a new function to rebuild Maestral's file index which is accessible through the
  GUI.
- Added "Recently Changed Files" submenu to the system tray menu. "Recently Changed Files"
  shows entries for the 30 last-changed files (synced folders only) and navigates to the
  respective file in the default file manager when an entry is clicked.

_Changed_:

- Refactored sync code: Collected all sync functionality in a the new class
  `monitor.UpDownSync`. `MaestralClient` now only handles access to the Dropbox API itself
  but is no longer concerned with version tracking, etc. `MaestralClient` no longer
  catches Dropbox API errors but raises them, augmented with useful information, as
  `MaestralApiError`.
- Moved storage of user authentication tokens from a text file to the system keyring. As a
  result, authentication tokens will be encrypted on the hard drive and only decrypted
  when the user logs in. On some systems, this may cause problems in headless mode, when
  the Gnome keyring is not loaded. The
  [keyring documentation](https://keyring.readthedocs.io/en/latest/#using-keyring-on-headless-linux-systems)
  provides help for such cases.

#### Minor changes

_Added:_

- Added progress messages for uploads and downloads, e.g., "Downloading 3/98...". These
  are output as info messages and shown in the status field of the system tray menu.
- When unlinking your Dropbox account through the GUI, Maestral is restarted to enter the
  setup dialog.
- Refinements for dark interface themes such as Dark Mode in macOS Mojave

_Changed:_

- Use native system icons instead of macOS icons to represent files and folders.
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
- Adapted code to correctly load resources in case Maestral is packaged as a macOS app
  bundle.

_Fixed:_

- Fixed a bug which may result in a removed folder not being deleted locally if it
  contains subfolders.
- Fixed a bug which may result in file modifications not being uploaded, depending on
  how the changes were saved by the program which was used to edit the file.
- Fixed a bug which would incorrectly list top level files as folders in the "Exclude
  folders" dialog.
- Truncate entries in the "Recently Changed Files" menu if their width exceeds 200 pixels.
- Fixed a bug which would cause Maestral to crash when clicking "Choose folders to sync..."
  while Maestral cannot connect to Dropbox servers.

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
