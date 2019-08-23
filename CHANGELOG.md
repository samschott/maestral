### v0.3.3-dev

_Added:_

- Methods to get the sync status of individual files or folders, including CLI support.
  In the future, this could be used by file manager plugins to overlay the sync status of
  files.
- Added experimental support to exclude subfolders.
- Added a command group `maestral excluded` to view and manage excluded folders.
- Automatically rename created items which have the same name as an existing item, but
  with a different case. This avoids possible issues on case-sensitive file systems since
  Dropbox itself is not case-sensitive.

_Changed:_

- Separated daemon and CLI code into different modules.
- Created a submodule for the sync engine.
- Setup dialog no longer returns a Maestral instance on success but just ``True``. It
  is up to the GUI to create its own instance or attach to a daemon.
- Moved indexing of local files after a restart to the `upload_thread`. This improves the
  apparent startup time for a large local Dropbox folder.
- Removed many direct calls of Maestral attributes from the GUI. Try to limit required
  methods to the main API (Maestral's methods and properties) which is exposed by the
  daemon.
- Changed returned values of the main API to Python types only. This provides safer
  serialization.

_Fixed:_

- Fixed incorrect error being raised for a corrupted rev file, which could lead to a
  crash or misleading error message.

### v0.3.2

This release fixes a bug that could result in only changes of top-level items being
synced. This affects users who carried out the initial linking with Maestral v0.2.5 or
later and selected to exclude folders before the first download. Users affected by this
should rebuild Maestral's index by selecting "Rebuild index..." in the main menu.

Other improvements include expanded command line scripts with more useful output, minor
bug fixes and small tweaks to the UI.

#### Added:

- Added a "status" property to `maestral.main` which shows the last log message.
- Added a command group `maestral log` to view and clear the log as well set the logging
  level. Commands are:
    - `maestral log show`: Shows the logs in terminal.
    - `maestral log clear`: Clears the logs.
    - `maestral log level`: Returns the current log level.
    - `maestral log level [DEBUG|INFO|WARNING|ERROR]`: Sets the log level to the given
       value. Affects both stdout and file logs.
- Added an option "-a" to `maestral ls` to include hidden files.
- Added tooltips for system tray icon when not on macOS.

#### Changed:

- Made log levels persistent between sessions.
- Changed the name of `maestral list` to `maestral ls` and, by default, do not list
  "hidden" items that start with a dot. Added an option "-a" to explicitly list all
  files in a directory.
- Improved output from command line scripts:
    - Wrap all long outputs in empty lines.
    - Show more informative status.
    - Show Dropbox folder location in account-info.
    - Add colours to outputs like "[OK]" and "[FAILED]".
- Set minimum version requirement for click package.
- Reduced the startup time by downloading profile picture in a thread. Periodically update
  in the background (every 20 min).
- Check hashes before uploading modified files. This speeds up re-linking an old folder by
  orders of magnitude.
- Enable the creation of multiple autostart entries for different configurations.
- Fall back to PNG tray icons if the platform may not support our svg format.

#### Fixed:

- Fixed a bug which would not allow running maestral for the first time before explicitly
  adding a configuration with `maestral config new`. Now, a default configuration is
  created automatically on first run.
- Prevent the GUI and a daemon from syncing the same folder at the same time.
- Fixed the creation of multiple daemons. A new daemon will no longer overwrite an old
  one and `maestral daemon start` will do nothing if a daemon for the given configuration
  is already running.
- Automatic allocation of ports for the communication between daemon and client.
- Show the (Dropbox) file path in the string representation of `MaestralApiError`.
  Previously, one could not see from the traceback which file caused the error.
- Fixed a bug that would result in only changes of top-level items being synced. This
  affects users who carrier out the initial linking with Maestral v0.2.5 or later
  (commit 40be316b49f2198a01cc9ce9b804f8e6336e36f8) and selected to exclude folders
  before the initial sync. Users affected by this bug should rebuild Maestral's index by
  selecting "Rebuild index..." in the main menu.

#### Removed:

- No longer install a script "maestral-gui". Use "maestral gui" instead.

### v0.3.1 (2019-08-14)

#### Fixed:

- Fixes a bug when calling the command line script `maestral daemon errors`. This bug
  was the result of an error in pickling our MaestralApiExceptions (see
  [https://bugs.python.org/issue1692335#msg310951](https://bugs.python.org/issue1692335#msg310951)
  for a discussion).

### v0.3.0 (2019-08-14)

This release includes several significant changes. The largest are:

1) Support for multiple Dropbox accounts (via the command line)
2) A Maestral daemon for the command line
3) A redesigned settings window with more prominent account information

The detailed list of changes is:

#### Added:

- Maestral can now be started as a daemon from the command line. A new command group
  `maestral daemon` has been introduced to manage this.
- Added support for custom Dropbox folder names. The folder name must be set with the
  command line scripts.
- Added a new command group `maestral config` to manage multiple Maestral configurations
  for different Dropbox accounts.
- Added a new command line option `--config-name` or `-c` to select the configuration
  file to use.
- Improved grouping and naming of command line scripts.
- Added a "relink" dialog which is shown when Maestral's Dropbox access has expired or
  has been revoked by the user.
- Improved logic to detect system tray color and set icons accordingly. This is mostly for
  KDE which, unlike Gnome, does not handle automatically adapting its tray icon colors.

#### Changed:

- Animated setup dialog.
- Redesigned the settings window to show more prominent account information.
- Improved command line and GUI flows for setting or moving the Dropbox folder location.
- Moved to an Implicit Grant OAuth2 flow. This does not require an app secret to be
  stored in the client source code. Maestral will therefore no longer require the user to
  get their own API keys or to use the precompiled oauth binaries hosted on PyPI.
- Improved the user massages given by command line scripts.
- Improved status messages given in RebuildIndexDialog.
- Unified and improved the creation of QThreads by the GUI to perform background tasks.
  This fixes an issue with occasional segfaults RebuildIndexDialog and improves the
  reliability of the UI.
- Started to work on providing a top-level API in `Maestral` for all functionality that is
  required by the UI. There should be no need to interact with `Monitor` or `UpDownSync`
  directly for high-level functionality.

#### Fixed:

- Fixed a crash on startup if the Meastral's Dropbox access has expired or has been
  revoked.
- Fixed handling of `ListFolder` errors. Those will only occur when the user gives an
  incorrect folder name to list and will (hopefully) never be caused my Maestral itself.

### v0.2.6 (2019-08-08)

This release fixes a critical bug which would cause Maestral to get stuck after the
initial sync. This does not affect users who have already performed the initial sync
with a previous version of Maestral.

#### Added:

- Added a context menu entry to the "Sync issues" window to show a file on dropbox.com.

#### Changed:

- Move logs to '$XDG_CACHE_HOME/maestral' on Linux and '~/Library/Logs/maestral' on macOS.
- Reduce the number of Dropbox API calls during initial sync.

#### Fixed:

- Fixed a bug which would cause Maestral to get stuck after the initial download.
- Fixes an issue in macOS where modal dialogs in the settings window would sometimes
  appear behind the window instead of in front of it.

### v0.2.5 (2019-08-07)

This release fixes several sync issues which could occur when the internet connection is
lost during a sync. It also notifies the user if Maestral's access to their Dropbox has
been revoked.

#### Added:

- Handle expired or invalidated Dropbox access.
- Ask the user before overriding an existing folder in the setup dialog.
- Added status updates for large file uploads (e.g., "Uploading 10/545MB...").

#### Changed:

- Significant speedup of initial indexing. Excluded folders or subfolders will no
  longer be indexed.
- Save config files in the systems default location: '$XDG_CONFIG_HOME/maestral' or
  '.config/maestral' in Linux and '~/Library/Application Support/maestral' on macOS.

#### Fixed:

- Fixed a false "Dropbox folder cannot be found" message which would appear when
  quitting and restarting Maestral during the first sync. Now, the initial download is
  quietly resumed when relaunching Maestral.
- Fixed an issue where an interrupted upload would not resume without restarting Maestral.
- Fixed an issue where file changes while "offline" would sometimes not be synced to
  Dropbox when a connection is reestablished.
- Fixed an issue where errors from `requests` would inadvertently get caught instead of
  being raised.

### v0.2.4 (2019-08-05)

This version mainly improves the appearance and responsiveness of the GUI specifically on
Linux platforms with a Gnome desktop. It also introduces a dialog to handle a deleted or
moved Dropbox folder.

#### Added:

- Added a "Select all" option when choosing which folders to sync.
- Handle deleted or moved Dropbox folder in setup dialog.
- Handle deleted or moved Dropbox folder while Maestral is running.

#### Changed:

- Improved performance of the GUI on some Gnome systems in case of many rapid status
  changes.
- Show system tray icon already during the setup dialog.

#### Fixed:

- Fixed size of the system tray icon in Gnome desktops with high-DPI scaling.
- Fixed a bug which would result in an error dialog being shown for "normal" sync errors
  such as an invalid file name.
- Fixed missing line-breaks in the traceback shown by the error dialog.
- Updated console scripts to reflect changes in MaestralMonitor and MaestralApiClient.

### v0.2.3 (2019-07-22)

This release mainly fixes crashes of the setup dialog and contains tweaks to the UI.

#### Changed:

- Launch into setup dialog if no Dropbox authentication token can be found in keychain.
- Only log messages of level ERROR or higher to file.
- Show account email in the system tray menu above space usage.
- Unified the code for error dialogs and added an app icon to all dialogs.

#### Fixed:

- Fixed a bug which could could result in the user being asked to re-authenticate when no
  Dropbox folder is detected on startup.
- Fixed a bug which could cause Maestral to crash during the setup dialog, immediately
  after user authentication.

### v0.2.2 (2019-07-19)

#### Added:

- Added support for file and folder names with two or more periods.
- Temporary autosave files that are created by macOS are now detected by their extension
  and excluded from syncing.
- More fine-grained errors, subclassed from `MaestralApiError`.
- Log all events of level INFO and higher to a rotating file in '~/.maestral/logs'. The
  log folder size will never exceed 6 MB.

#### Changed:

- Better handling when Dropbox resets a cursor: retry any `files_list_folder` calls and
  prompt the user to rebuild the index on `files_list_folder_longpoll` calls.
- Prepare for G-suite Dropbox integration: G-suite files such as Google docs and sheets
  will not be downloadable but can only be exported. Maestral will ignore such files.
- Moved deprecated API calls to v2.
- Better handling of `OSErrors` on download.
- Tweaks to logo.

#### Fixed:

- Fixed a bug which would prevent some error dialogs from being shown to the user.
- Fixed a bug which would cause the setup dialog to crash after linking to Dropbox.

### v0.2.1 (2019-07-18)

#### Changed:

- Reload all file and folder icons when the system appearance changes: the system may
  provide different icons (e.g., darker folder icons in "dark mode" on macOS Mojave).
- Improved notification centre alerts in macOS: when installed as a bundled app,
  notifications are now properly sent from the Maestral itself, showing the Maestral icon,
  instead of through apple script.
- Improved layout of the "Rebuild index" dialog.

#### Fixed:

- Fixes a bug which would prevent Meastral from starting on login: the correct startup
  script is now called.

### v0.2.0 (2019-07-17)

#### Major changes

#### Added:

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

#### Changed:

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

#### Added:

- Added progress messages for uploads and downloads, e.g., "Downloading 3/98...". These
  are output as info messages and shown in the status field of the system tray menu.
- When unlinking your Dropbox account through the GUI, Maestral is restarted to enter the
  setup dialog.
- Refinements for dark interface themes such as Dark Mode in macOS Mojave

#### Changed:

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

#### Fixed:

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

#### Added:

- Added new command line option 'autostart' to automatically start Maestral on login.

#### Changed:

- Limit notifications to remote changes only and only notify for changes in folders that
  currently being synced, unless more than 100 files have changed.
- Detect colour of system tray and invert icon colour automatically if not on macOS.
- Shut down immediately and kill threads instead of waiting for timeout.
- Improve appearance of Settings window in GTK 3 style.

#### Fixed:

- Fixed a bug which would cause uploads to fail if they are split into multiple chunks.
- Fixed a bug that would prevent Maestral from quitting if the setup dialog is aborted.
- Fixed a bug that would cause Maestral to crash during the setup dialog when switching
  multiple times between the "Select Folders to Sync" and "Select Dropbox location" panels.
- Do not upload files that have identical content on Dropbox. Previously: files were
  always uploaded and conflict checking was left to do by the Dropbox server.

### v0.1.1 (2019-06-23)

#### Fixed:

- Fixes an issue which would prevent newly created empty folders from being synced.
- Remove references to conda in startup script.
