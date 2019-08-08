### v0.2.6 (2019-08-08)

This release fixes a critical bug which would cause Maestral to get stuck after the
initial sync. This does not affect users who have already performed the initial sync
with a previous version of maestral.

_Added:_

- Added a context menu entry to the "Sync issues" window to show a file on dropbox.com.

_Changed:_

- Move logs to '$XDG_CACHE_HOME/maestral' on Linux and '~/Library/Logs/maestral' on macOS.
- Reduce the number of Dropbox API calls during initial sync.

_Fixed:_

- Fixed a bug which would cause Maestral to get stuck after te initial download.

### v0.2.5 (2019-08-07)

This release fixes several sync issues which could occur when the internet connection is
lost during a sync. It also notifies the user if Maestral's access to their Dropbox has
been revoked.

_Added:_

- Handle expired or invalidated Dropbox access.
- Ask the user before overriding an existing folder in the setup dialog.
- Added status updates for large file uploads (e.g., "Uploading 10/545MB...").

_Changed:_

- Significant speedup of initial indexing. Excluded folders or subfolders will no
  longer be indexed.
- Save config files in the systems default location: '$XDG_CONFIG_HOME/maestral' or
  '.config/maestral' in Linux and '~/Library/Application Support/maestral' on macOS.
  
_Fixed:_

- Fixed a false "Dropbox folder cannot be found" message which would appear when
  quitting and restarting Maestral during the first sync. Now, the initial download is
  quietly resumed when relaunching Maestral.
- Fixed an issue where an interrupted upload would not resume without restarting Maestral.
- Fixed an issue where file changes while "offline" would sometimes not be synced to
  Dropbox when a connection is reestablished.
- Fixed an issue where errors from `requests` would inadvertently get caught instead of
  being raised.
- Fixes an issue in macOS where modal dialogs in the settings window would sometimes
  appear behind the window instead of in front of it.

### v0.2.4 (2019-08-05)

This version mainly improves the appearance and responsiveness of the GUI specifically on
Linux platforms with a Gnome desktop. It also introduces a dialog to handle a deleted or
moved Dropbox folder.

_Added:_

- Added a "Select all" option when choosing which folders to sync.
- Handle deleted or moved Dropbox folder in setup dialog.
- Handle deleted or moved Dropbox folder while Maestral is running.

_Changed:_

- Improved performance of the GUI on some Gnome systems in case of many rapid status
  changes.
- Show system tray icon already during the setup dialog.

_Fixed:_

- Fixed size of the system tray icon in Gnome desktops with high-DPI scaling.
- Fixed a bug which would result in an error dialog being shown for "normal" sync errors
  such as an invalid file name.
- Fixed missing line-breaks in the traceback shown by the error dialog.
- Updated console scripts to reflect changes in MaestralMonitor and MaestralApiClient.

### v0.2.3 (2019-07-22)

This release mainly fixes crashes of the setup dialog and contains tweaks to the UI.

_Changed:_

- Launch into setup dialog if no Dropbox authentication token can be found in keychain.
- Only log messages of level ERROR or higher to file.
- Show account email in the system tray menu above space usage.
- Unified the code for error dialogs and added an app icon to all dialogs.

_Fixed:_

- Fixed a bug which could could result in the user being asked to re-authenticate when no
  Dropbox folder is detected on startup.
- Fixed a bug which could cause Maestral to crash during the setup dialog, immediately
  after user authentication.

### v0.2.2 (2019-07-19)

_Added_:

- Added support for file and folder names with two or more periods.
- Temporary autosave files that are created by macOS are now detected by their extension
  and excluded from syncing.
- More fine-grained errors, subclassed from `MaestralApiError`.
- Log all events of level INFO and higher to a rotating file in '~/.maestral/logs'. The
  log folder size will never exceed 6 MB.

_Changed_:

- Better handling when Dropbox resets a cursor: retry any `files_list_folder` calls and
  prompt the user to rebuild the index on `files_list_folder_longpoll` calls.
- Prepare for G-suite Dropbox integration: G-suite files such as Google docs and sheets
  will not be downloadable but can only be exported. Maestral will ignore such files.
- Moved deprecated API calls to v2.
- Better handling of `OSErrors` on download.
- Tweaks to logo.

_Fixed_:

- Fixed a bug which would prevent some error dialogs from being shown to the user.
- Fixed a bug which would cause the setup dialog to crash after linking to Dropbox.

### v0.2.1 (2019-07-18)

_Changed_:

- Reload all file and folder icons when the system appearance changes: the system may
  provide different icons (e.g., darker folder icons in "dark mode" on macOS Mojave).
- Improved notification centre alerts in macOS: when installed as a bundled app,
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
- Detect colour of system tray and invert icon colour automatically if not on macOS.
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
