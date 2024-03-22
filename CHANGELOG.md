## v1.9.1:

#### Fixed:

* Fixes a regression in v1.9.0 in the macOS GUI which prevented several buttons and menu
  items from reacting to clicks. This was a side effect from a new GUI framework API.

## v1.9.0:

#### Changed:

* Allow passing multiple paths to `maestral excluded add | remove` CLI commands.

#### Fixed:

* Fixes an issue where syncing remote changes would lead to the modified time of a file
  being set to the download time instead of the time of original modification.

## v1.8.0

#### Changed:

* Dropped support for Python 3.7 which was reached end-of-life on 27 Jun 2023.

#### Fixed:

* Fixes the default systemd service name when enabling autostart on Linux. This was a
  regression in v1.7.3. Autostart entries created with `maestral autostart -Y` prior to
  v1.7.3 will continue to work.
* Fixes an issue where data transport errors that are retried could result in zero byte
  files being created in the cloud if the local file size is smaller than 4 MB.
* Fixes an issue where renaming a local file by making unicode normalisation changes
  only could lead to a cycle of deletion and re-creation on Dropbox servers when syncing
  with Maestral from multiple machines.

## v1.7.3

This is the last release that supports Python 3.7 which will reach end-of-life on 27
Jun 2023. The macOS app always ships with its own Python runtime, but custom
installations will require at least Python 3.8 starting with the next release.

#### Changed:

* Preparation for upcoming API changes in `watchdog` dependency.
* No more automatic reindexing: Maestral would automatically rebuild its index  every 14
  days. This could occasionally lead to conflicting copies if a file was modified 
  remotely during this process. This reindexing is not necessary, especially as syncing
  has become very reliable. Starting with this release, reindexing needs to be triggered
  manually through the GUI or CLI if required.

#### Fixed:

* Fixes autostart entries for the GUI being malformed when Maestral is installed in a
  Python virtual environment.
* Fixes autostart entries for the daemon being malformed for the macOS app bundle. This
  applies to autostart entries created with `maestral autostart -Y` and not using the
  "Start on login" checkbox in the GUI.
* The `filestatus` now is case-sensitive when checking on a case-sensitive file system.
* Fixes an issue where renaming a file by changing the casing only would not be picked
  up if Maestral was not running during the rename.

## v1.7.2

#### Changed:

* Improved support for systems where some file system calls don't accept a
 `follow_symlinks = False` option, notably `chmod` and `utime`.
* Abort uploads if the file is modified between the upload of individual chunks. This
  saves some bandwidth and prevents us from ever committing an inconsistent file to
  Dropbox's version history.
* Show desktop notifications when a conflicting copy is created both during upload and
  download sync. Unlike regular notifications, those notifications are shown for each
  conflicting copy instead of giving a summary count.
* Append the username and date to the file name of a conflicting copy, for example
  `myfile (Sam's conflicting copy 2022-08-30).pdf`.

#### Fixed:

* Fixes an issue for systems that do not provide /sys/class/power_supply such as 
  Synology devices. Power supply state is periodically queried to prevent automatic
  reindexing when on battery power.
* Fixes potentially inconsistent error messages if Maestral does not have permissions to
  perform file moves within the local Dropbox folder.
* Fixes a regression with some icon buttons in the macOS GUI not being displayed.

## v1.7.1

#### Fixed:

* Fixes running the app bundle version on Intel Macs.

## v1.7.0

#### Changed:

* Allow limiting the upload and download bandwidth used for syncing, either by setting
  the config file values, by using the CLI `maestral bandwidth-limit up|down`, or
  through the Settings pane in the GUI.
* Add config file items for the maximum number of parallel file transfers.
* Speed up querying the sync status of folders.
* Added support for Python 3.12.

#### Fixed:

* Fixes the download sync of remote symlinks. The local item now is an actual symlink
  instead of a 0 KB file.
* Fixes an issue where the Login Items entry for Maestral would incorrectly be listed
  with the developer name instead of the app name in macOS Ventura's System Settings.
* Fixes an issue which would prevent periodic reindexing.
* Fixes an issue with interrupted downloads of folders which are newly included by
  selective sync not automatically resuming when Maestral restarts.
* Fixes an issue with detect the init system on some Linux distributions, a prerequisite
  for the autostart functionality.

#### Removed:

* Removed support for access token authentication. Users who linked Maestral to their
  Dropbox account before September 2020 will be asked to reauthenticate so that Maestral
  can retrieve a refresh token instead.

## v1.6.5

#### Fixed:

* Fixes starting the gui with `maestral gui` from Python 3.9 and lower. This does not
  affect the macOS app bundle.

## v1.6.4

#### Changed:

* Drops support for macOS 10.13 and 10.14. macOS 10.15 or later is now required,
  reflecting the support status from Apple. In the future, we will drop support for 
  macOS versions once they no longer receive security updates.
* Improved status messages: the sync count is now updated as items are uploaded or 
  downloaded instead of with a delay.
* `maestral activity` now shows animating progress bars to view upload and download
  progress.
* `maestral sharelink list` now outputs plain urls, separated by new lines. Use
  the `--long | -l` option if you would like a pretty-printed table with additional
  information about each link.
* `maestral history` now shows where the listed changes occurred (local vs remote).
* When multiple GUIs are installed (for example Qt and Cocoa), ask the user to choose
  when running `maestral gui` instead of selecting a default.

#### Fixed:

* Fixes an error which would prevent running Maestral on Python 3.7.
* Fixes a regression where the CLI command `maestral activity` would fail when run from
  a macOS app bundle.
* Fixes an issue where reauthorizing Maestral's Dropbox access could hang indefinitely.
* Fixes an issue on macOS where changing the casing of two local file names in the same
  folder in quick succession could result in the second file being deleted during sync.

#### Dependencies:

* Introduced `rich` dependency.
* Removed `sdnotify` dependency.

## v1.6.3

#### Changed:

* The macOS app bundle now uses Python 3.10 instead of 3.9. This comes with some
  performance improvements.

#### Fixed:

* Work around transitory server-side errors when refreshing access tokens by retrying
  the refresh up to three times.
* Fixed a segfault on startup for a small number of macOS users.
* Fixed an issue where files which contain decomposed unicode characters could be
  deleted after renaming them locally on some versions of macOS.
* Fixes an issue where the `maestral gui` command would fail for macOS app bundles.

## v1.6.2

#### Changed:

* Improved error message for file names with incompatible characters that are rejected
  by Dropbox servers, e.g., emoji or slashes at the end of a file name.
* Capture Dropbox SDK logs in Maestral's log output. This will log which API endpoints
  are called and any retries on errors or rate limiting.

#### Fixed:

* Fixes intermittent failures to show a file associated with a sync issue in the Linux
  GUI.
* Fixes an issue where the macOS app bundle would use a system-wide installation of the
  Sparkle framework if available instead of the one bundled with Maestral. This could
  lead to unexpected issues if the system-wide installation would have an incompatible
  version.
* Fixes an issue where the access level of shared links may be incorrectly reported.
* Resume interrupted downloads after a shutdown when including new items with selective
  sync.
* Fixes occasional conflicting copies of folders during initial sync due to a race
  condition when a child item is synced before its parent folder.
* Fixes the display of timestamps in the CLI from `maestral ls` and `maestral history`
  commands. The former would show times in UTC instead of the device's timezone and the
  latter would show Unix timestamps instead of formatted output.

## v1.6.1

#### Fixed:

* Fixes an error when querying space usage from Dropbox servers for team accounts.
* Fixes reading from the database on SQLite versions pre 3.23.0 (2018-04-02), for
  example on macOS High Sierra.

## v1.6.0

#### Changed:

* Sync errors are now stored in a SQlite database table instead of a config file.
* The CLI command `maestral filestatus PATH` will now return `error` if there is a sync
  error for any child of the given path. This brings it in line with the `syncing` status.
* Re-enabled updating from versions older than 1.5.0.
* Improved file integrity checks after upload or download.
* Better parallelize CPU intensive work when indexing local changes. This improves
  performance on multicore CPUs.
* Migrate the Linux GUI from PyQt5 to PyQt6.

#### Fixed:

* Fixes an issue where upload sync errors could continue to be reported after the local
  file was deleted if the deletion occurred while sync was not running.
* Fixes an issue with the Linux Qt GUI where aborting the setup dialog after linking but
  before choosing a local Dropbox folder would result in an inconsistent state.
* Fixes an issue when storing 64-bit inode numbers in our database.
* Fixes occasional crashes of the macOS GUI when running on Apple Silicon.

## v1.5.3

#### Changed:

* The Dock icon no longer appears while the app is launching in macOS.
* The Dock icon no longer appears when clicking on a desktop notification on macOS.
* Clicking on a desktop notification will now show the file or folder which triggered the
  notification. Previously, only clicking on the "Show" button of the notification
  would open the file browser.
* Removed update notifications by the CLI.
* Proper symlink handling: Remote items which are symlinks will now be synced as symlinks
  instead of empty files. Local symlinks will no longer be followed during indexing or
  silently ignored when syncing. Instead, attempting to upload a symlink will raise an
  error because uploading symlinks is not currently supported by the public Dropbox API.

#### Fixed:

* Fixes a crash of the `maestral activity` CLI command when run from the macOS App Bundle
  due to a missing packaged library.
* Fixes an issue which prevented the `maestral gui` command from working with the macOS
  app bundle.
* Fixes an issue where moving a local file to overwrite another file, for example with mv
  in the terminal, could generate an incorrect conflicting copy during upload sync.
* Properly handle when the local Dropbox directory is renamed by changing the casing only
  on case-insensitive file systems such as APFS on macOS.
* Fixes an issue which could result in sync errors not being cleared after the successful
  sync of an item under some circumstances.
* Relative paths passed to `maestral move-dir` are now interpreted relative to the
  working directory where the command is run instead of the working directory of the sync
  daemon.

## v1.5.2

#### Changed:

* Improved dialog flow on Linux when the local Dropbox folder is missing.
* Improved error handling when determining the change time (ctime) of a local file fails.

#### Fixed:

* Fixes an issue where the output of CLI commands would get truncated to 80 characters
  when piped to another command and not attached to an interactive stream such as a
  terminal.
* Fixes Python 3.10 compatibility of Linux (Qt) GUI, thanks to @raffaem.
* Fixes an issue where the CLI fails to install on Apple Silicon Macs.
* Fixes a startup loop of the Linux GUI when the local Dropbox folder is missing.

## v1.5.1

#### Changed:

* Handle Dropbox server errors in the same way as connection errors by retrying the sync
  job.

#### Fixed:

* Fixed issues when trying to abort the CLI setup dialog with ctrl+C.
* Fixes an issue which could under some circumstances result in deleted folder content
  after performing the initial indexing and download. This would mostly occur for shared
  folders.
* Fixes an issue where launchd or systemd might start the sync daemon with a non-UTF-8
  encoding set in the environment.
* Fixes an issue where deleting the local Dropbox folder during startup indexing may
  result in some files being deleted from the remote Dropbox.

## v1.5.0

#### Added:

* Added support for Dropbox Business accounts with a Team Space. Shared folders in a Team
  Space will now be synced at the top level, next to the user's personal folder.

#### Changed:

* Reorganised config file sections.
* Brought back support for macOS High Sierra in the macOS app bundle.

#### Fixed:

* Fixed a crash when running the CLI command `maestral config-file --clean`.

## v1.4.8

#### Added:

* Added automatic updates with Sparkle for the macOS app bundle.

#### Changed:

* Improved performance when processing local file events.
* Improved error messages when the system keyring cannot be accessed despite being
  unlocked, for example because the executable (app bundle or Python) has an invalid
  signature.
* Improved error messages on startup for the macOS app bundle.
* Improved error message in the CLI when setting a config value fails because the new
  value has the wrong type.
* Improved handling of more exotic file system or device related errors when opening
  local files.

#### Fixed:

* Fixed a crash on startup of the daemon when the log level is set to WARNING.
* Fixed an issue which could result in an unresponsive daemon during startup on macOS.
* Fixed an issue which could result in the CLI or GUI to stall indefinitely if the
  daemon process is unresponsive.
* Fixed an issue in the macOS GUI where passing the "missing Dropbox folder" flow by
  selecting a new location would lead to duplicate menu entries in the status bar menu.
* Fixed an issue where the Linux / Qt GUI would hang indefinitely after unlinking.
* Fixed an issue where moving / removing the local Dropbox folder during a download
  could lead to unhandled exceptions instead of useful error messages.
* Fixed handling of 503 and other raw HTTP errors from the Dropbox SDK, for instance
  when Dropbox servers have temporary outages or are undergoing planned maintenance.
* Fixed periodic connection checking for connections over proxy using
  `http_proxy` environment variable.
* Fixed an issue where some uploaded items would not register as synced after aborting
  or pausing during an upload sync.
* Fixed a compatibility issue with watchdog v2.1.4 and higher.

#### Removed:

* Removed an unneeded prompt when revoking a shared link.

## v1.4.6

#### Changed:

* Performance improvements to selective sync dialog in GUI.

#### Fixed:

* Fixed an `IndexError` during conflict resolution when the local item is a non-empty
  folder.

## v1.4.5

#### Added:

* Support CMD+Q keyboard shortcut to quit app on macOS.

#### Changed:

* Clarified the "merge local folder" option in the GUI's setup dialog.

#### Fixed:

* Fixed an issue where erroneous file conflicts could occur when a file name contains
  decomposed unicode characters such as "é" represented by "e" + "́" instead of a single
  character.
* Fixed an issue where center-aligned text would appear right-aligned on Apple Silicon
  computers.

#### Dependencies:

* Bumped desktop-notifier to v3.3.0. This fixes a segfault for non-framework
  distributions of Python such as Anaconda Python.

## v1.4.4

This release introduces support for tab-completion in the shell, allows you to choose
the actual Dropbox folder instead of its parent folder / location from the GUI, and
further reduces memory usage during startup indexing. As usual, several bugs have
been squashed.

The website has also moved to its own domain at [maestral.app](https://maestral.app).
The source still lives in the `website` branch of the GitHub repo and contributions are
welcome.

#### Added:

* Added support for shell completion. Completion is available for the commands
  themselves and for several arguments, notably paths relative to the Dropbox folder and
  choices from a set of fixed options. Use the command `maestral completion` to generate
  shell completion scripts for bash, zsh or fish. For bash or zsh, save the returned
  script in a location of you choice and source it in '\~/.bashrc' or '\~/.zshrc',
  respectively. For fish, save the returned script at
  '\~/.config/fish/completions/maestral.fish'. Completion is available for the base
  commands themselves and several arguments, notably paths relative to the Dropbox
  folder and choices from a set of fixed options.

#### Changed:

* Improved error messages when inotify limits are reached on Linux.
* GUI dialogs to select a local Dropbox folder now ask for the actual folder name
  instead of the location only.
* Local indexing on startup is now carried out without loading the entire folder tree
  into memory. This further reduces peak memory usage and fragmentation.
* Permission errors when scanning the contents of a local folder during startup indexing
  are now treated as fatal errors instead of skipping its content. This prevents items
  from being deleted on the server when they are still present locally but inaccessible.
* Improved logging during daemon startup: Logging is now initialised immediately after
  the main imports and therefore captures potential errors early during the startup
  process.
* Loggers are scoped per configuration instead of globally. This enables separating logs
  for Maestral instances for different configs which are running in the same process.
* Improved performance and appearance of the selective sync dialog. Folder content is
  no longer pre-fetched but will be loaded on-demand when expanding a folder.

#### Fixed:

* Fixes a rare issue where throttling of sync threads would be disabled if the
  "max_cpu_percent" config value would be set to 100% divided by the number of CPU cores
  (e.g, 25% on a 4-core CPU).
* Fixes an issue where a local permission error would be treated as a fatal error
  instead of as a sync issue.
* Moving the Dropbox folder between partitions no longer triggers a full resync.
* Fixes an error when running the `diff` CLI command and selecting the local file as the
  base version.
* Fixes download links in the update dialog.
* Fixes an unexpected error which may occur when creating a conflicting copy.

#### Dependencies:

* Bumped click to >= v8.0.0 to support shell completion.
* Bumped survey to >= v3.4.3 for Python 3.6 support.

## v1.4.3

This release improves performance and memory usage by switching from sqlalchamey to our
own database interaction layer.

Maestral now also has a website with a detailed documentation of the command line
interface, released with github pages at
[https://samschott.github.io/maestral](https://samschott.github.io/maestral).

#### Changed:

* We now use our own ORM layer instead of sqlalchemy. This improves both baseline memory
  usage and peak memory usage during startup and indexing.
* Use a new network session for each thread and clean up network resources before the
  thread stops.
* The macOS GUI will now show a dock icon if there is an open window.
* The CLI will print full tracebacks to the console in case of unexpected errors.

#### Fixed:

* Fixed detecting local changes when saving an MS Office on macOS with recent versions
  of the Office suite.

#### Dependencies:

* Bumped desktop-notifier to >=3.2.2
* Bumped watchdog to >=2.0.1
* Removed sqlalchemy
* Removed alembic

## v1.4.2

Fixes an issue where the daemon might restart syncing even though it should be paused
when an internet connection is reestablished.

## v1.4.1

Fixes an issue where the daemon status may incorrectly report "Connecting..." even
though the daemon is connected.

## v1.4.0

This release brings significant extensions to the command line interface: It introduces
commands to create and manage shared links, to compare older version of a file and print
the diff output to the terminal, and commands for direct access to config values (note
the warning below). It also adds optional one-way syncing, for instance to keep a mirror
of a remote Dropbox folder while ignoring local changes.

Several bugs have been fixed which could occur when resuming the sync activity after the
connection had been lost while indexing a remote folder.

Finally, this release removes automatic error reporting via Bugsnag. Please file any bug
reports as issues on GitHub where it is possible to follow up.

#### Added:

* Added a command `maestral diff` to compare different versions of a text file. The
  resulting diff is printed to the console. Credit goes to @OrangeFran.
* Resurrected the command `maestral revs` to list previous versions (revisions) of a
  file.
* Added a command group `maestral sharelink` to create and manage shared links.
  Subcommands are:

  * `create`: Create a shared link for a file or folder, optionally with password
    protection and an expiry date on supported accounts (business and professional).
  * `list`: List shared links, either for a specific file or folder or for all items
    in your Dropbox.
  * `revoke`: Revoke a shared link.

* Added a command group `maestral config` to provide direct access to config values.
  Subcommands are:

  * `get`: Gets the config value for a key.
  * `set`: Sets the config value for a key.

  This provides access to previously inaccessible config values such as
  `reindex_interval` or `max_cpu_percent`. Please refer to a Wiki for an overview of all
  config values. Use the `set` command with caution: setting some config values may
  leave the daemon in an inconsistent state (e.g., changing the location of the Dropbox
  folder). Always use the equivalent command from the Settings group (e.g., `maestral
  move-dir`).
* Added the ability to disable a single sync direction, for instance to enable download
  syncs only. This can be useful when you want to mirror a remote folder while ignoring
  local changes or when syncing to a file system which does not support inotify. To use
  this, set the respective config values for `upload` or `download` to False. Note that
  conflict resolution remains unaffected. For instance, when an unsynced local change
  would be overwritten by a remote change, the local file will be moved to a
  "conflicting copy" first. However, the conflicting copy will not be uploaded.

#### Changed:

* Changes to indexing:

  * Avoid scanning of objects matching an  `.mignore` pattern (file watches will still
    be added however). This results in performance improvements during startup and
    resume. A resulting behavioral change is that **maestral will remove files matching
    an ignore pattern from Dropbox**. After this change it will be immaterial if an
    `.mignore` pattern is added before or after having matching files in Dropbox. Credit
    goes to @andrewsali.
  * If Maestral is quit or interrupted during indexing, for instance due to connection
    problems, it will later resume from the same position instead of restarting from the
    beginning.
  * Indexing will no longer skip excluded folders. This is necessary for the above
    change.
  * Defer periodic reindexing, typically carried out weekly, if the device is not
    connected to an AC power supply. This prevents draining the battery when hashing
    file contents.

* Changes to CLI:

  * Moved linking and unlinking to a new command group `maestral auth` with subcommands
    `link`, `unlink` and `status`.
  * Renamed the command `file-status` to `filestatus`.
  * Added a `--yes, -Y` flag to the `unlink` to command to skip the confirmation prompt.
  * Renamed the `configs` command to list config files to `config-files`.
  * Added an option `--clean` to `config-files` to remove all stale config files (those
    without a linked Dropbox account).

* Improved the error message when the user is running out of inotify watches: Recommend
  default values of `max_user_watches = 524288` and `max_user_instances = 1024` or
  double the current values, whichever is higher. Advise to apply the changes with
  `sysctl -p`.

#### Fixed:

* Fixes an issue with the CLI on Python 3.6 where commands that print dates to the
  console would raise an exception.
* Properly handle a rare OSError "[Errno 41] Protocol wrong type for socket" on macOS,
  see https://bugs.python.org/issue33450.
* Allow creating local files even if we cannot set their permissions, for instances on
  some mounted NTFS drives.
* Fixes an issue with the selective sync dialog in the Qt / Linux GUI where the "Update"
  button could be incorrectly enabled or disabled.
* Fixes an issue where a lost internet connection while starting the sync could lead to
  a stuck sync thread or an endless indexing cycle.
* Fixes an issue where a lost internet connection during the download of a folder newly
  included in selective sync could result in the download never being completed.
* Fixes an issue where pausing the sync during the download of a folder newly included
  in selective sync could result in the download never being completed.

#### Removed:

* Removed automatic error reporting via bugsnag.
* Removed from CLI:

  * The `maestral restart` command. Use `stop` and `start` instead.
  * The `maestral account-info` command. Use `maestral auth status` instead.

* Removed the public API methods `Maestral.resume_sync` and `Maestral.pause_sync`. Use
  `Maestral.start_sync` and `Maestral.stop_sync` instead.

#### Dependencies:

* Bumped survey to version >=3.2.2,<4.0.
* Bumped keyring to version >=22.
* Bumped watchdog to version >= 2.0.
* Added `desktop-notifier` dependency. This is spin-off project from Maestral, built on
  the code previously in the `notify` module.
* Removed the bugsnag dependency.

## v1.3.1

#### Fixed:

* Fixes an incorrect entry point for the Qt GUI.

## v1.3.0

This release features an overhaul of the command line interface: commands are grouped by
sections in the help output, dialogs and output formatting have been improved and many
commands have become significantly faster.

This release also significantly reduces the CPU usage when idle and provides a whole
series of bug fixes for GUI and daemon.

#### Added:

* Desktop notifications for sync errors are now clickable and will show the related file
  or folder either on Dropbox or locally.
* Desktop notifications now have a "Show" button to show a recently changed file.
* Added a public API `Maetral.status_change_longpoll` for frontends to wait for status
  changes without frequent polling. `status_change_longpoll` blocks until there is a
  change in status and then returns `True`. The default timeout is 60 sec.

#### Changed:

* Significant improvements to the command line interface:
    * Overhauled all CLI dialogs with nicer formatting and more interactive prompts
      using the `survey` package.
    * Improved output of many CLI commands, including `ls`, `activity`, and `restore`.
    * Increased speed of many CLI commands by importing only necessary modules.
    * Shortened help texts for CLI commands.
    * Group help output by function.
* Reduced the CPU usage of daemon and GUIs in the idle state:
    * Increased timeouts for all event queues.
    * Decreased the frequency of daemon housekeeping tasks.
    * GUIs now use longpoll APIs to wait for state changes instead of frequent polling.
* Improved performance when syncing a large number of remote deletions.
* The `Maestral.include_item()` API now accepts paths which lie inside an excluded
  folder. When called with such a path, all immediate parents will be included as well.
  This change also applies to the `maestral excluded remove`.
* The `Maestral.excluded_items` property is no longer read-only.
* Some refactoring of the `cli` module to prepare for shell completion support.

#### Fixed:

* Fixes an issue where all newly downloaded files would be created with 755 permissions.
  They are now created with the user's default permissions for new files instead.
* Fixes an unexpected crash when the list of `pending_downloads` or `download_errors`
  would contain an invalid path, i.e., a Dropbox path for which we cannot get any
  current or deleted metadata.
* Fixes an error when a local file name contains bytes which cannot be decoded by
  reported file system encoding. This now raises a sync error instead of crashing and
  all log handlers have been updated to deal with the resulting surrogate escapes.
* Fixes possible loss of data when excluding an item from syncing while it is
  downloaded. This is no longer possible and will raise a `BusyError` instead.
* Fixes an issue where `maestral ls` would fail when run with the `-l, --long` flag.
* Fixes an occasional `IndexError` during a download sync when trying to query past
  versions of a deleted item.
* Fixes an issue which could cause a segfault of the selective sync dialog on macOS.
* Fixes an issue where the selective sync dialog on Linux would not load the contents of
  more than 10 folders.
* Fixes a regression with the autostart functionality of the Linux GUI. Autostart
  entries created with v1.2.2 will need be reset by toggling the checkbox "start on
  login" off and on.
* Fixes an issue where two configs linked to the same Dropbox account would both be
  unlinked when trying to unlink only one of them.
* Fixes an import error with v11.0 of the Dropbox SDK.

#### Removed:

* Removed the `maestral rev` command to list old file revisions. Instead
  `maestral restore` will list possible revisions to restore.

#### Deprecated:

* Deprecated the `Maestral.set_excluded_items` API. Use the setter for
  `Maestral.excluded_items` instead.

#### Development:

* Updated tests and migrated fully to pytest.
* Improved API documentation, including sections on the sync logic and on logging.
* Added contributing guidelines.

#### Dependencies:

* Require `watchdog<=10.3` because of an unresolved issue in watchdog 0.10.4 on macOS.
* Pin `dropbox<12.0` to avoid bad surprises in case of breaking changes.
* Add `survey>=2.1.0` for an interactive CLI.

## v1.2.2

This release focuses on bug fixes and performance improvements. In particular, memory
usage has been improved when syncing a Dropbox folder with a large number of items.

#### Changed:

- `maestral file-status` now accepts relative paths.
- Runs the daemon in a Python interpreter with -OO flags. This strips docstrings and
  saves a few MB of memory.
- Moves from `pkg_resources` to locate entry points and other metadata to the faster and
  more light-weight `importlib.metadata`.
- Update scripts are no longer run after a fresh install or for a new config.
- Significantly reduces memory usage during the initial sync of a Dropbox folder with
  many (> 10,000) items and when downloading a large set of changes. To achieve this,
  new APIs have been added to `SyncEngine` and `DropboxClient` that return iterators
  over remote changes. Dropbox servers are queried on every iteration.
- `Maestral.get_history` now returns only the last 100 sync events by default. This can
  be increased by setting the `limit` argument manually.
- The total sync history kept in out database is limited to the last 1,000 events.
- Switch from PyInstaller to [briefcase](https://github.com/beeware/briefcase) for
  packaging on macOS.

#### Fixed:

- Fixes an issue which would prevent the daemon from starting on macOS when running with
  Python 3.6.
- Fixes a segfault of the macOS GUI on macOS High Sierra.
- Fixes an issue with the macOS GUI becoming unresponsive when opening the selective
  sync dialog if one of the displayed folders contains a large number (> 2k) of
  immediate children.
- Fixes an issue with the Qt GUI crashing when opening the selective sync dialog if one
  of the folders contains a large number (> 2k) of immediate children.
- Fixes an issue where `Mastral.excluded_status` would return "included" for items
  inside an excluded folder.

## v1.2.1

This update provides bug fixes and some improvements to error handling. Major changes
don't regard Maestral itself but its distribution: a Docker image is now available,
thanks to @aries1980, and the macOS app bundle has been rebuilt with the macOS 11 SDK,
providing full compatibility from macOS 10.13 High Sierra to macOS 11.0 Big Sur.

#### Added:

- Added a Docker image, thanks to @aries1980. The docker image is based on Linux and
  does not currently include a GUI.
- Added `-V, --version` option to the command line interface to show the version and
  exit.

#### Changed:

- Improves handling of database related errors such as database integrity, missing read
  / write permissions for the database file, etc.
- Improves handling of errors when the keyring cannot be unlocked to delete credentials
  during an unlink.
- Improves handling of errors when the keyring where Dropbox credentials are stored
  becomes unavailable, e.g., has been uninstalled.
- Never start a subprocess when maestral is run with the `-f, --foreground` option.
  Previously, any required setup such as linking, etc, would still be performed in a
  subprocess.
- Minor tweaks and improvements to the macOS GUI.
- Allow sending desktop notifications in Linux before the daemon's event loop has
  started. This is useful for error messages which occur early during the
  initialization.
- Improves log messages when the connection to Dropbox is lost.
- Performance improvements to `maestral activity` in case of very large sync queues.

#### Fixed:

- Fixes a database integrity error due to an unfulfilled unique constraint.
- Fixes an issue when the daemon is launched with systemd where systemd would
  unexpectedly receive notifications from a subprocess instead of the main process.
- Fixes an issue which would prevent syncing from automatically resuming after moving the
  local Dropbox directory with `maestral move-dir` or through the GUI.
- Fixed a green background for sync issue views in the macOS GUI.
- Fixes an issue where the system tray icon in KDE Plasma could fall back to the regular
  app icon or not show up at all,
- Fixes an issue where the user may be asked to unlock or grant access to the system
  keyring twice on startup if access denied the first time.

#### Dependencies:

- Adds `alembic` dependency for database migrations.

## v1.2.0

The local file index and sync history are now stored in a SQLite database. After the
update, Maestral will first reindex your Dropbox to populate the new index.

This change enables several improvements to the command line interface and GUI: The
command `maestral activity` now shows the progress of individual uploads or downloads.
`maestral history` has been added to list recent sync events. In the GUI, the recent
changes menu now has been replaced by a "Activity" window which shows all sync events of
the past week.

This release also introduces clickable desktop notifications, performance improvements
to indexing of local file changes, and bug fixes and smaller changes listed below.

Finally, this release introduces support for macOS 11 (Big Sur).

#### Added:

- Added an option `-e, --external` to `maestral log show` to open the log in the
  platform's default program instead of showing it in the console.
- Added a CLI command `history` to show all sync events of the past week.
- Added a "Activity" window to show all sync events of the past week.
- Desktop notifications are now clickable: for a single file change, clicking the
  notification will show the file in the platform's file manager. For a deletion, the
  Dropbox website is opened to provide options for restoring the file or folder.
- Use entry points to discover GUI frontends. 3rd party GUIs can register a
  `maestral_gui` entry point to be launched with the `maestral gui` CLI command. If
  installed, `maestral gui` will default to the 1st party `maestral-cocoa` or `maestral-
  qt` GUIs on macOS and Linux, respectively.

#### Changed:

- Transition to short-lived auth tokens for newly linked accounts.
- Transition to OAuth scopes for app permissions.
- Save all sync history and local index in SQLite database.
- Reduce unnecessary path conversions during indexing of local changes.
- Improved performance on case-sensitive file systems.
- Sync remote changes in filename even if they are only a change in casing. Those
  changes where previously ignored.
- Attempt to preserve local file permissions when syncing unless the file id has
  changed. Dropbox servers do store file permissions but don't make them available
  through the public API. We therefore cannot sync file permissions and instead choose
  not to overwrite locally set permissions on every download.
- Changed return type of `Maestral.get_activity` from namedtuple to dict for better
  consistency throughout the API. Every uploading or downloading item will have 'size'
  and 'completed' entries to monitor the progress of syncing individual items.
- The CLI command `maestral activity` now shows the progress of uploads and downloads
  for individual files.
- Introduced type annotations throughout and fixed a few type-related bugs.
- Added a field "Sync threads" to the output of the CLI command `maestral status`.
- The output of `maestral ls` is now printed in a grid, similar to the `ls` command
  included with most platforms.
- The macOS app bundle now uses Python 3.8, leading to some performance improvements
  when moving or copying file system trees.
- Prepared the GUI for changes in macOS Big Sur: use native alerts and dialogs wherever
  possible and refactor loading of libraries.
- Use an asyncio event loop instead of Pyro's event loop to run the daemon. This enables
  integration with the Cocoa run loop and callbacks when clicking notifications.

#### Fixed:

- Fixes a bug where throttling of sync threads would raise an error when we cannot
  determine the CPU count.
- Fixes a bug where sending SIGTERM to the daemon process would raise an error when we
  cannot determine its PID. Now, `Stop.Failed` is returned instead.
- Fixes a bug which would result in incorrect systemd unit files for non-default config
  file names. Please disable and re-enable autostart with `maestral autostart -Y|-N` to
  replace old unit files.
- Fixes a possible race condition when creating the cache directory.
- Fixes error handling when a file is changed while uploading.

#### Removed:

- Support for config names with spaces. Spaces could cause issues with autostart entries
  on some platforms.
- The ability to run the daemon in a separate thread. The daemon must now always be run
  in its own process.

#### Dependencies:

- Replaced `jeepney` dependency on Linux with `dbus-next`.

## v1.1.0

This release expands the CLI functionality and improves the handling of file
modification times during upload and download (used for display purposes only). It also
fixes bugs with the "start on login" functionality of the macOS app bundle. After
updating, please toggle "start on login" in the GUI or `maestral autostart` in the CLI
to replace any old login items.

#### Added:

- Added `--include-deleted` option to `maestral ls`.
- Added `-l, --long` option to `maestral ls` to include metadata in listing.
- Added `maestral revs` command to list revisions of a file.
- Added `maestral restore` command to restore an old revision of a file.

#### Changed:

- Always create config directory if it does not exist.
- Improved performance of converting Dropbox paths to correctly cased local paths.
- Renamed macOS executable inside app bundle from "main" to "Maestral". This results in
  more informative process names.
- Local files are now created with the "last modified" time provided by Dropbox servers.
  This only applies to new downloads. To update existing modified times, you will need
  to delete and redownload your Dropbox folder.

#### Fixed:

- Fixes a thread-safety issue with desktop notifications.
- Fixes a thread-safety issue when two frontends try to start or stop syncing at the
  same time.
- Fixes an issue where Maestral could incorrectly identify a file system as case
  sensitive if the Dropbox folder and temporary directory are on partitions with
  different file systems.
- Fixes incorrect file modification times uploaded to Dropbox for timezones outside of
  UTC. Those times are used for display purposes only.
- Fixes an issue where the `maestral autostart -Y` CLI command would start the GUI on
  on login in case of the macOS app bundle.

## v1.0.3

#### Changed:

- Both "-h" and "--help" can now be used to print help output for a command.
- Show both the daemon and GUI version in the settings window.
- The command line tool bundled with the macOS app now provides proper help output.
- Significantly reduced CPU usage of the GUI on macOS.
- The macOS app now uses a hardened runtime and is properly signed and notarized.

#### Fixed:

- Fixes an issue which could lead to the local Dropbox folder being moved before syncing
  has been paused.
- Fixes an issue where download errors would show a rev number instead of the Dropbox
  path.
- Fixes a race condition when two processes try to start a sync daemon at the same time.
- Fixes an issue in the macOS GUI where updating the displayed sync issues could fail.
- Fixes truncated text in the macOS setup dialog.
- Fixes an issue on fresh macOS installs where creating autostart entries could fail if
  /Library/LaunchAgents does not yet exist.
- Fixes an issue in the macOS app bundle where installing the command line could tool
  would fail if /usr/local/bin is owned by root (as is default on a fresh install). Now,
  the user is asked for permission instead.

#### Dependencies:

- Removed `lockfile` dependency.
- Added `fasteners` dependency.

## v1.0.2

This release fixes bugs in the command line interface.

#### Fixed:

- Fixes a crash of the CLI when an update is available due to incorrect formatting of
  the update message.
- Fixes an error when listing the contents of an empty directory with `maestral ls`.

## v1.0.0

This is the first stable release of Maestral. There have been numerous bug fixes to
error handling and platform integration as well as a few bug fixes to syncing itself.
There are also a few outward facing changes: Pausing Maestral now cancels any pending
sync jobs instead of waiting for them to be completed. The macOS GUI switches from Qt to
using a native Cocoa interface and the macOS app bundle finally includes a full command
line interface.

#### Added:

- Command line tools are now bundled with the macOS app bundle and can be installed from
  the settings window.
- Added support for config names with spaces.
- Switch from Qt to native Cocoa GUI on macOS.
- Expanded test suite to include sync tests.

#### Changed:

- Added '.dropbox' and '.dropbox.cache' to always excluded paths.
- Pausing sync now cancels all pending uploads and downloads.
- Quicker detection of connection problems.
- Faster sync of local deletions.
- The GUI now always launches a separate daemon process instead of an in-process daemon.
- Temporary files during a download are now stored inside the Dropbox directory at
  '.maestral.cache'. This guarantees that temporary files always reside on the same
  partition as the Dropbox folder itself.
- System tray icons are no longer installed in the platform theme in Linux. This is part
  of a workaround for a Qt issue on Linux desktops which causes unnecessarily large
  pixmap transfers over dBus when HiDPI support is enabled. Manually installed icons
  will still be respected.
- Switch from implicit grant to PKCE OAuth2 flow.
- Added public API to link a Dropbox account: `Maestral.get_auth_url` and
  `Maestral.link`. Frontends no longer need to import `maestral.oauth`.
- Moved all command line dialogs from the main API to the CLI module.
- Bumped watchdog requirement to >= 10.0.0 for more consistent error handling.
- Added explicit jeepny dependency for Linux. This is a dependency of keyring but we use
  it by itself as well.
- Improved the reliability of ignoring file system events caused by Maestral itself.

#### Fixed:

- Fixes an issue where a dropped internet connection during startup could result in
  continuous retries until the connection is finally established.
- Fixes an issue where downloads of newly included folders would not resume after being
  interrupted.
- Fixes an issue which could lead to false conflicting copies of folders in some cases.
- Fixes the handling of inofify limit and permission errors when starting a file system
  watch.
- Fixes handling of errors from too long file names.
- Handle errors due to file names which are not allowed on the local file system.
- Fixes handling of some uncaught insufficient disk space errors.
- Fixes incorrect autostart entries on macOS.
- Fixes a crash when running Maestral as a systemd service without python-systemd
  installed.
- Fixes an issue when checking for updates if the list of releases from Github includes
  dev releases.
- Fixes an issue where only remote changes would be listed in 'Recent changes' in the
  GUI.
- Fixes the alignment of comboboxes in the Qt GUI on macOS.
- Fixes a crash on macOS when no notification center is available, for instance in a
  headless session or on Github test runners.
- Fixes a crash on Linux when the command line tool `notify-send` is not available.
- Fixes an issue where sync errors would have incomplete path information.
- Resolves an issue where indexing a large Dropbox folder with > 100,000 items would
  continuously timeout and restart in some cases.

#### Removed:

- Removed migration code for versions < 0.6.3. If you want to update to v1.0.0, please
  make sure to upgrade to at least version 0.6.3 first or unlink your Dropbox before
  updating to v1.0.0.
- Removed u-msgpack dependency.

## v0.6.4

The release provides bug fixes and minor improvements to the command line and graphical
user interfaces. Importantly, it fixes an issue where some files could accidentally
ubecome n-indexed, resulting in incorrect conflict resolution.

#### Added:

- Config option to set the keyring backend. This defaults to 'automatic' but can be used
  to specify a preferred backend such as `keyrings.backends.kwallet.DBusKeyring`. You
  will need to migrate your credentials manually to the new keyring if you change this
  setting.
- Added a `-v, --verbose` flag to `maestral start` and `maestral restart` commands to
  print log output to stdout.
- Added an API documentation for developers, available on
  [Read the Docs](https://maestral-dropbox.readthedocs.io).

#### Changed:

- During initial CLI setup, give the option to sync the entire Dropbox without
  paginating through individual folders to exclude.
- Limit the number of notifications to keep in the notification center. This will only
  work for some desktop environments.
- Fall back to plain text credential storage if neither Gnome Keyring, KWallet or any
  other storage implementing the Secret Service API can be found. A warning is shown
  when plain text storage is used.
- Settings and setup windows are no longer always kept on top in Linux.
- `maestral start --foreground` no longer prints log messages to stdout by default.

#### Fixed:

- Properly handle errors when moving files, for instance for sync conflicts.
- Fixes an issue where some files could accidentally become un-indexed, resulting in
  incorrect conflict resolution.
- Fixes an issue with macOS app bundles where the migration of configuration files was
  omitted after an update. This would result in a failure to start the daemon.
- Correctly specify the required version of `six` to work around an upstream issue in
  Dropbox.
- Fixes an issue where stdout would end up in the systemd journal in addition to the
  structured log messages.
- Fixed a bug where XDG_DATA_HOME was ignored.

## v0.6.3

This release fixes a critical error introduced when updating to v9.5 of the Dropbox
Python SDK which prevented any remote changes from being downloaded.

#### Changed:

- Show release notes from all releases since last update in update dialog.
- Use our own method instead of the `psuitl` package to determine the CPU usage. This
  eliminates the `psuitl` dependency which can be difficult to install on some systems.

#### Fixed:

- Fixes an issue with downloads failing because Dropbox Metadata is longer hashable from
  v9.5 of the Dropbox Python SDK.
- Fixed a StopIteration exception on startup when the location of the maestral CLI
  script cannot be found in the package metadata.
- Fixes an error when restarting the daemon with the "foreground" option.
- Fixed incorrect button labels in the setup dialog when choosing whether to replace or
  keep an old Dropbox folder. The labels "Replace" and "Cancel" where switched.
- Fixes a bug where the option "Unlink & Quit" in the "Revoked Access" error dialog
  would unlink but not quit Maestral.

## v0.6.2

This release enables excluding individual files from syncing and fixes an issue which
led to continuously retrying failed downloads. It also contains significant performance
improvements to indexing, reduces the CPU usage when syncing a large number of files and
introduces weekly re-indexing.

This release also introduces support for an ".mignore" file with the same syntax as
[gitignore](https://git-scm.com/docs/gitignore). This feature is considered 'alpha' and
may change in the future. Feedback is welcome.

#### Added:

- Support excluding files from sync. This uses the same 'selective sync' interface as
  excluding folders. Excluded files will be removed from the local Dropbox folder.
- Introduces an ".mignore" file to specify files that Maestral should ignore. The
  ".mignore" file must be saved in the local Dropbox folder. When excluding files or
  folders with selective sync (`maestral exclude`), they will be removed from the local
  folder and kept in the cloud only. The ".mignore" file enables the reverse: files or
  folders which exist locally will not be uploaded to Dropbox. It uses the same syntax
  as [gitignore files](https://git-scm.com/docs/gitignore) and, similar to gitignore,
  files which are already tracked by Maestral will not be affected. More details are
  given in the [Wiki](https://github.com/SamSchott/maestral-dropbox/wiki/mignore).
- Added a config option "max_cpu_percent" to adjust the target maximum CPU usage per CPU
  core. This defaults to 20%, i.e., 80% total for a quad core CPU. Maestral will aim to
  remain below that percentage but this is not guaranteed.

#### Changed:

- Replaced the `excluded_files` and `excluded_folders` settings from the config file
  with a unified `excluded_items` setting. Entries from `excluded_folders` will be
  migrated to the `excluded_items` setting.
- Renamed methods which exclude / include folders to `exclude_item` etc.
- Speed up creation of local folders.
- When trying to create a file or folder with the same path as an item excluded by
  selective sync, the new item is now renamed by appending "selective sync conflict"
  instead of raising a sync issue. This is closer the behaviour of the official client.
- Significant performance improvements to indexing and file event processing. Indexing a
  remote Dropbox with 20,000 to 30,000 files and comparing it a local folder now takes
  ~ 5 min, depending on on the average file size.
- Introduced periodic reindexing every week. This has been made possible by the above
  performance improvements.

#### Fixed:

- Don't immediately retry when a download fails. Instead, save failed downloads and
  retry only on pause / resume or restart.
- Fixes missing cursor and resulting unexpected `ValidationError` during sync startup.
- Wait until all sync activity has stopped before moving the Dropbox folder. This avoids
  errors when trying to convert local to dropbox paths and vice versa during the move.
- Fixes an issue which would prevent some conflicting copies created by Dropbox from
  being downloaded.
- Correctly handle when a local item is renamed to an always excluded file name such as
  ".DS_STORE": the item is now deleted from Dropbox.
- Fixes an issue where sharing an existing folder from the Dropbox website would result
  in the folder being deleted locally. This is because Dropbox actually removes the
  shared folder from the user's Dropbox and then re-mounts it as a shared drive / file
  system. We handle this correctly now by leaving the local folder alone or deleting and
  re-downloading it, depending on the time elapsed between removal and re-mounting.
- Improves conflict resolution when a folder has been been replaced with a file or vice
  versa and both the local and remote item have un-synced changes.
- Fixes an issue where `maestral stop` would block until all pending syncs have
  completed. This could potentially take a *very* long time for large downloads.
  Instead, any interrupted downloads will be restarted on next launch.

#### Removed:

- Removed the `excluded_files` and `excluded_folders` settings from the config file.

## v0.6.1

This release improves desktop notifications: Notifications will now only appear for
remote file changes and you can chose between different notification levels (CLI only)
and snooze notifications temporarily. It also reintroduces the `maestral autostart`
command to start the sync daemon on login (requires systemd on Linux). This works
independently of the GUI option "Start on login".

There have also been significant changes in package structure: the GUI has been split
off into a separate package `maestral-qt` which will be installed with the gui extra
`pip3 install -U maestral[gui]` or directly with `pip3 install -U maestral-qt`. A native
Cocoa GUI (`maestral-cocoa`) for macOS is currently in testing and will likely be
released with the next update.

Other changes include improved error handling, cleaned up config files and some tweaks
to CLI commands. As always, there are several bug fixes. Thank you for all your
feedback!

#### Added:

- New CLI command `maestral autostart` to start the daemon on login. This requires
  systemd on Linux. The "Start on login" option of the GUI remains independent and the
  GUI will attach to an existing daemon if it finds one.
- Added desktop notifications for errors: Serious errors such as revoked Dropbox access,
  deleted Dropbox folder, etc, were previously only shown in the GUI as an alert window
  or printed as warnings when invoking a CLI command.
- Support for different levels of desktop notifications (CLI only). You can now select
  between FILECHANGE, SYNCISSUE, ERROR and NONE with `maestral notify LEVEL`.
- Added an option to snooze notifications. In the CLI, use `maestral notify snooze N` to
  snooze notifications for N minutes. In the GUI, use the "Snooze Notifications" menu.
- Support using an existing directory when setting up Maestral through the CLI. This was
  previously only supported in the GUI. Files and folders in the existing directory will
  be merged with your Dropbox.
- The CLI command `maestral restart` now supports restarting Maestral into the current
  process instead of spawning a new process. This is enabled by passing the
  `-f, --foreground` option.
- Added a native Cocoa GUI for macOS. This removes the PyQt5 dependency for macOS and
  reduces the size of the bundled app from 50 MB to 15 MB. It also eliminates a few
  inconsistencies in GUI appearance. Especially the sync issues window looks a lot
  better (hopefully you won't see it too often).

#### Changed:

- Split off GUI into separate python packages (`maestral-qt`, `maestral-cocoa`).
- Notify only for remote changes and not for those which originated locally. This
  should significantly reduce the number of unwanted notifications.
- Renamed `maestral notifications` to `maestral notify` for brevity.
- Renamed the `set-dir` command to `move-dir` to emphesize that it moves the local
  Dropbox folder to a new location.
- Configurations are now tied to a Dropbox account:
    - New configurations are now created on-demand when calling `maestral gui` or
      `maestral start` with a new configuration name.
    - A configuration is automatically removed when unlinking a Dropbox account.
    - All configurations can be listed together with the account emails with
      `maestral configs`. This replaces `maestral config list`.
- For app bundles on macOS, you can now pass a config option `-c, --config-name` to the
  bundle's executable ("Maestral.app/Contents/MacOS/main"). It will then use the
  specified configuration if it already exists or to create a new one.
- The GUI no longer restarts after completing the setup dialog.
- Removed sync and application state info from the config file. Sync and application
  states are now  saved separately in '~/.local/share/maestral/CONFIG_NAME.state' on
  Linux and '~/Library/Application Support/maestral/CONFIG_NAME.state' on macOS.
- Use atomic save to prevent corruption of the sync index if Maestral crashes or is
  killed during a save.
- Moved the sync index to the same folder as the application state.
- Improved conflict detection and resolution when changing files which are currently
  being uploaded or downloaded.

#### Fixed:

- Fixes an issue where local changes while maestral was not running could be overwritten
  by remote changes instead of resulting in a conflicting copy.
- Fixes an issue where local file events could be ignored while a download is in
  progress.
- Fixes an issue where a new local file could be incorrectly deleted if it was created
  just after a remote item at the same path was deleted.
- Fixes an issue where `maestral stop` and `maestral restart` would not interrupt
  running sync jobs but instead wait for them to be completed. Now, aborted jobs will be
  resumed when starting Maestral again.
- Correctly handle when a folder is replaced by a file and vice versa.
- Correctly handle additional error types: internal Dropbox server error, insufficient
  space on local drive, file name too long for local file system and out-of-memory
  error.
- Automatically resume upload in case of dropped packages instead of raising a sync
  issue.
- Set the log level for the systemd journal according to user settings instead of always
  using logging.DEBUG.
- Run checks for Dropbox folder location and link status when invoking `maestral
  restart`.
- Notify the user through the GUI when moving the Dropbox directory fails instead of
  silently keeping the old directory.
- Fixes an issue where the environment variable XDG_DATA_DIR would not be respected in
  Linux.

#### Removed:

- Removed "-a" option from `maestral ls`. List all entries by default, even if they
  start with a period.
- Removed the `maestral config` command group. Configurations are now created and
  deleted on-demand and can be listed with `maestral configs`.

## v0.5.2

#### Added:

- Added automatic crash and error reporting with [bugsnag](https://www.bugsnag.com).
  This is *disabled* by default and can be enabled in the Settings pane or with the
  command `maestral analytics -Y`. The information sent with the bug report contains a
  traceback, the Python version, basic platform information (e.g,
  'Darwin-19.2.0-x86_64-i386-64bit') and potentially the version of PyQt5 and the user's
  desktop environment. No personal information will be shared.

#### Changed:

- Improved the code which handles multiple configurations: Explicitly pass the config
  name to classes instead of keeping it as a global variable.
- Improved starting of the daemon: ensure that the right python executable is used.
- Order of commands returned by `maestral --help` by importance instead of
  alphabetically.
- Sync errors will now be listed by `maestral status` if present.
- Live updates to the Settings window when settings are changed from the command line.

#### Fixed:

- Fixed an issue on macOS where some directory deletions could be ignored in case of
  rapid successive deletions.
- Fixed an unexpected exception when attempting to create a directory that already
  exists. Do not rely on the `exists_ok` parameter in `os.makedirs` but catch
  `FileExistsError` explicitly (see https://bugs.python.org/issue13498).
- Fixed an `AttributeError` when a local folder is replaced by file: the Dropbox
  metadata of the folder will not have a content hash. This mostly occurs when modifying
  a folder structure programmatically, for instance with git.
- Fixed an `AttributeError` when a remote file has been replaced by a folder before its
  changes could be downloaded: the Dropbox metadata of the folder will not have a
  content hash.
- Fixed an bug introduced in v0.5.0 which would cause rebuilding the index to block
  indefinitely.
- Fixed a crash of the GUI when closing the settings window shortly after closing the
  "Chose folders to sync..." dialog. This was caused by QThreads being destroyed while
  the threads were still running.
- Fixed an issue where the local revision number of a file could be set to 'folder',
  resulting in an exception from the Dropbox API.
- Fixed a bug when the "relink dialog" (shown when Maestral's Dropbox access has
  expired) might use the wrong Dropbox account when syncing multiple accounts.
- Fixed an issue with imports in Pyro5 5.7 which prevented the daemon from starting.

#### Removed:

- Removed the command `maestral errors` from the CLI.

## v0.5.0

This release improves the sync reliability in case of rapid successive changes to the
local Dropbox folder. It also improves error handling and includes other bug fixes. This
may be considered the first release candidate for a stable v1.0.0.

#### Added:

- Show a small bell on top of system tray icon in case of sync issues.
- Notify the user when the local Dropbox folder contains too many items to watch and
  recommend increasing the maximum number of inotify watches (Linux only).
- Notify the user when an upload fails because a file exceeds the size limit of 350 GB.
- Notify the user when an upload fails due to dropped network packages.
- Adds a command line option `maestral link -r` to relink to an existing account without
  resetting the sync state. This is the equivalent of the GUI 'relink dialog'.

#### Changed:

- Refines some error messages.
- Improves error handling in CLI: avoid printing full Python tracebacks to the console.
  Print concise and actionable error messages instead if possible.
- Improves formatting of `maestral ls` output.
- Improves status notifications for large uploads: dynamically adapt the unit to show up
  to four significant digits (e.g., "16MB/1.6GB" instead of "0/1.6GB").
- Reduces memory footprint of macOS app by stripping doc strings (at least 5MB in
  dropbox package only).

#### Fixed:

- Fixes multiple sync issues and corner cases due to rapid and successive file changes:
  The algorithm which combines successive changes of a local file to a single file event
  (created / deleted / modified / moved) has been simplified and improved.
- Fixes an issue which could cause the watchdog thread to crash silently on case-
  sensitive file systems when saving changes to a file.
- Removes sip import because it may fail depending on how PyQt was installed.
- Fixed an issue where user notifications would not appear for certain implementations
  of 'notify-send'.
- Fixes an error when setting the log level from the CLI.
- Fixes an error when relinking Maestral through the GUI after its Dropbox access has
  been revoked.

## v0.4.4

This updates focuses on bug fixes and performance improvements. Notably, it reduces the
memory usage of the GUI by ~ 30MB. If you are upgrading from v0.2.4 or earlier, please
perform an incremental update to v0.4.3 first (see Removed section).

#### Changed:

- Show a progress dialog while checking for updates when requested by the user.
- Show an error message when the GUI cannot connect to or start a sync daemon.
- Reduces the memory footprint of the GUI by ~ 30 MB by avoiding Dropbox API imports and
  deleting QtWidgets when they are not visible.
- Changing the log level (e.g., `maestral log level DEBUG`) no longer requires a restart
  of the maestral daemon to become effective.
- `maestral set-dir` now takes the new path as an argument: `maestral set-dir PATH`. If
  not given, the user will be prompted to input a path or use the default.
- Migrated from Pyro4 to Pyro5 for communication with sync daemon.

#### Fixed:

- Fixes an unhandled error when trying to upload changes to a file which is not currently
  indexed by Maestral.
- Fixes an unhandled error when attempting to calculate the content hash of a file which
  has been deleted locally. This can occur after Maestral has been notified of remote
  changes to a file which is deleted locally before comparing file contents.
- Fixes a bug which could result in multiple false "conflicting copies" of a file when
  the user modifies the file while it is being uploaded.
- Fixes a regression bug which would prevent the creation and selection of new configs
  for different Dropbox accounts.
- Fixes a bug that would prevent Maestral from properly shutting down a sync daemon
  which was started from the GUI. This was a result of the daemon's sync threads not
  exiting as long as a parent process from the same process group is still alive (the
  GUI in our case). We prevent this by using "double-fork" magic to properly orphan the
  daemon process so that init will perform its cleanup. See Stevens' "Advanced
  Programming in the UNIX Environment" for details (ISBN 0201563177).
- Fixes an issue where the application launcher which is used to start Maestral on login
  in Linux may be untrusted.
- Fixes an issue where `maestral set-dir` would fail if the new directory is the same as
  the old directory.

#### Removed:

- Removed code to migrate config files and sync indices from Maestral versions prior to
  v0.2.5.
- Removed code to migrate authentication keys to the system keyring when upgrading from
  v0.1.2 or earlier.

## v0.4.3

#### Fixed:

- Fixes a bug which would prevent periodic update checks from running.
- Fixes an issue where the system tray icon would not be displayed on Qt 5.13.1 with
  enabled HighDpi support.
- Fixes an issue which would prevent system tray icons from loading on Gnome 3.
- Fixes and issue which would prevent macOS binaries from running due to the team ID
  missing in the a code-signing certificate. Note that, even though the macOS binary is
  code-signed, the certificate is not from Apple's Developer Program. Therefore, to run
  the app, you will have to "right-lick -> Open".

#### Changed:

- Tweaked system tray icon design.
- Use NSUserNotificationCenter when running from Python outside of an app bundle.

#### Removed:

- Removed automatic detection of Gnome screen scaling factors because it caused problems
  on a few desktop environments. Set the environment variable `QT_SCREEN_SCALE_FACTORS`
  instead to enable it manually if required.

## v0.4.2

#### Added:

- Added a command `maestral activity` which gives a live view of all items queued for
  syncing or currently being synced.

#### Fixed:

- Fixes crash of the sync thread when attempting to download a file from Dropbox which
  has been deleted after it has been queued for download but before the actual download
  attempt.
- Fixes crash of the sync thread when attempting to upload a file to Dropbox which has
  been deleted after it has been queued for upload but before the actual upload attempt.
- Fixes a bug where the revision number of a file could be incorrectly set to "folder".
- Fixes a crash of the sync thread while indexing local changes (after a restart) if an
  indexed item has been deleted before we could check if it is a file or a folder.
- Fixes a bug where newly downloaded files could be immediately re-uploaded in some
  cases.
- Fixes a crash on startup when started as systemd service with watchdog.

## v0.4.1

This release focuses on bug fixes and performance improvements. Notable changes are:

- You can now rebuild Maestral's index from the command line with `maestral rebuild-
  index`.
- Communication between the sync daemon and frontend (GUI or CLI) is faster and more
  secure.
- Improved system tray notifications.

Here is the list of all changes:

#### Added:

- Added `maestral rebuild-index` command to CLI.
- Added support for systemd software watchdog (see #55).

#### Changed:

- Renamed command `maestral config new` to `maestral config add`.
- Renamed command `maestral config delete` to `maestral config remove`.
- Improved system tray notifications:
    - Display the name of the user who changed a file
    - Added app-icon and and -name to Linux notifications.
    - Migrated macOS notifications from `NSUserNotificationCenter` (deprecated) to
      `UNUserNotificatioCenter` for macOS Mojave and higher.
- Improved appearance of unlink dialog: show spinning progress indicator and perform
  unlink in the background.
- Show menu entry "No recent files" when there are no recently changed files to display.
- Use Unix domain sockets instead of TCP/IP sockets for communication with daemon. This
  means that communication is lighter, faster and more secure (other users on the same
  PC can no longer connect to your sync daemon).
- Use NSTemporaryDirectory on macOS as runtime dir.
- Simplified code for the initial sync.

#### Fixed:

- Fixes a bug where the CLI setup dialog could fail when choosing to replace an existing
  Dropbox folder.
- Fixes a bug which would cause `maestral start` to hang indefinitely if the daemon is
  not created successfully (see #57).
- Fixes a bug which would cause `maestral unlink` to fail when the Maestral daemon is
  still running.
- Fixes a bug where the Maestral GUI would show a paused icon during the initial sync
  after setup.
- Fixes a bug where the menu bar item "Pause Syncing" would not change to "Resume
  Syncing" when pausing sync through the CLI via `maestral pause` (and vice versa).
- Catch unexpected exceptions in sync threads and display to user instead of crashing.
- Do not upload changes to an excluded folder but raise a sync issue instead.
- Fixes wrong color of system tray / menu bar icon on macOS when clicked in light-mode.
- Fixes a regression bug from v0.4.0 which caused the creation of new configs for
  separate Dropbox accounts to fail silently.
- Fixes a bug which could result in a missing sync cursor when running the Maestral
  thafter e initial setup. This would come from parallel access to the config files from
  tha read spawned by the setup dialog and the Maestral daemon itself. We now make sure
  ththat e setup dialog leaves no threads behind after exiting.
- Fixes a bug which could cause false sync errors when adding a nested folder structure
  to the local Dropbox folder.
- Fixes bug in converting Dropbox `DeleteError`s due to an invalid path to
  `MaestralApiError`s.
- Fixes a bug which would prevent Maestral from detecting local changes to files that are
  part of a batch which is currently being downloaded.
- Fixes a bug where the user may be asked to create a new keyring in a non-default
  wallet if multiple wallets are available on first start (see #56).
  See https://github.com/jaraco/keyring/issues/391 for the current behaviour of Python
  keyring.
- Fixes a bug which could cause the Maestral daemon to be started with a different PATH
  than the invoking command (see #57).
- Fixes a bug where changes to a file which is not synced locally would trigger "file
  added" instead of "file modified" notifications.

## v0.4.0

Main changes are:

- Support the exclusion of subfolders.
- Check and notify if updates are available.
- Decoupled GUI and sync daemon.
- Cleaned up the command line interface. Use `maestral start` instead of
  `maestral daemon start` and `maestral start --foreground` instead of `maestral sync`.
- Bug fixes and performance improvements.

Details are given below.

#### Added:

- Method to get the sync status of individual files or folders. This is also accessible
  through the CLI via `maestral file-status LOCAL_PATH`. In the future, this could be
  used by file manager plugins to overlay the sync status of files.
- Support to exclude subfolders in the main API, CLI and GUI.
- Added a command group `maestral excluded` to view and manage excluded folders.
  Available commands are `add`, `remove` and `show`.
- For case-sensitive file systems: Automatically rename created items which have the
  same name as an existing item, but with a different case. This avoids possible issues
  on case-sensitive file systems since Dropbox itself is not case-sensitive.
- GUI notifications when a new version of Maestral is available, configurable to daily,
  weekly, monthly or never.
- A new "Check for updates..." menu entry.
- Better integration with systemd: When the daemon is started from systemd, status
  updates and ready / stopping signals are sent to systemd and the log is sent to the
  journal instead of stdout. This requires the installation of the systemd extra as
  `pip3 install -U maestral[systemd]`, which will install `sdnotify` and `systemd-
  python`. The latter may require you install additional packages through your system's
  package manager first. See [here](https://github.com/systemd/python-systemd) for
  installation instructions.

#### Changed:

- Separated daemon and CLI code into different modules.
- Simplified CLI:
    - Moved commands from `maestral daemon` to main command group, i.e.,
      `maestral daemon start` is now `maestral start`.
    - Removed `maestral sync`. Use `maestral start --foreground` instead.
- GUI now uses only the main Maestral API which is exposed over sockets.
- Changed returned values of the Maestral API to Python types only for better
  serialisation.
- GUI now starts its own daemon on demand or attaches to an existing one. This daemon
  will run in a separate process, unless started from a macOS App bundle.
- Improved startup time for large folders: Moved indexing of local files after a restart
  to the `upload_thread`.
- Sync engine moved to a submodule.
- Setup dialog no longer returns a Maestral instance on success but just ``True``. It
  is up to the GUI to create or attach to a Maestral daemon.

#### Fixed:

- Fixed an incorrect error being raised for a corrupted rev file, which could lead to a
  crash or misleading error message.
- Fixed a bug which would cause a renamed file with a previously invalid name not to
  sync to Dropbox.
- Fixed a bug in the GUI which would cause clicking on a recently changed file to reveal
  the wrong item in the file manager.
- Fixed a bug which would cause the sync thread to crash when attempting to follow a
  broken symlink (#50). Now, the error will be reported to the user as a sync issue.
  Thanks to @michaelbjames for the fix.
- Fixes a bug where the Dropbox path is not reset when aborting the setup dialog.

#### Removed:

- Removed the CLI command `maestral sync`. Use `maestral start --foreground` instead.


## v0.3.2

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
- Reduced the startup time by downloading profile picture in a thread. Periodically
  update in the background (every 20 min).
- Check hashes before uploading modified files. This speeds up re-linking an old folder
  by orders of magnitude.
- Enable the creation of multiple autostart entries for different configurations.
- Fall back to PNG tray icons if the platform may not support our svg format.

#### Fixed:

- Fixed a bug which would not allow running maestral for the first time before
  explicitly adding a configuration with `maestral config new`. Now, a default
  configuration is created automatically on first run.
- Prevent the GUI and a daemon from syncing the same folder at the same time.
- Fixed the creation of multiple daemons. A new daemon will no longer overwrite an old
  one and `maestral daemon start` will do nothing if a daemon for the given
  configuration is already running.
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

## v0.3.1

#### Fixed:

- Fixes a bug when calling the command line script `maestral daemon errors`. This bug
  was the result of an error in pickling our MaestralApiExceptions (see
  [https://bugs.python.org/issue1692335#msg310951](https://bugs.python.org/issue1692335#msg310951)
  for a discussion).

## v0.3.0

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
- Improved logic to detect system tray color and set icons accordingly. This is mostly
  for KDE which, unlike Gnome, does not handle automatically adapting its tray icon
  colours.

#### Changed:

- Animated setup dialog.
- Redesigned the settings window to show more prominent account information.
- Improved command line and GUI flows for setting or moving the Dropbox folder location.
- Moved to an Implicit Grant OAuth2 flow. This does not require an app secret to be
  stored in the client source code. Maestral will therefore no longer require the user
  to get their own API keys or to use the precompiled oauth binaries hosted on PyPI.
- Improved the user massages given by command line scripts.
- Improved status messages given in RebuildIndexDialog.
- Unified and improved the creation of QThreads by the GUI to perform background tasks.
  This fixes an issue with occasional segfaults RebuildIndexDialog and improves the
  reliability of the UI.
- Started to work on providing a top-level API in `Maestral` for all functionality that
  is required by the UI. There should be no need to interact with `Monitor` or
  `UpDownSync` directly for high-level functionality.

#### Fixed:

- Fixed a crash on startup if the Meastral's Dropbox access has expired or has been
  revoked.
- Fixed handling of `ListFolder` errors. Those will only occur when the user gives an
  incorrect folder name to list and will (hopefully) never be caused my Maestral itself.

## v0.2.6

This release fixes a critical bug which would cause Maestral to get stuck after the
initial sync. This does not affect users who have already performed the initial sync
with a previous version of Maestral.

#### Added:

- Added a context menu entry to the "Sync issues" window to show a file on dropbox.com.

#### Changed:

- Move logs to '$XDG_CACHE_HOME/maestral' on Linux and '~/Library/Logs/maestral' on
  macOS.
- Reduce the number of Dropbox API calls during initial sync.

#### Fixed:

- Fixed a bug which would cause Maestral to get stuck after the initial download.
- Fixes an issue in macOS where modal dialogs in the settings window would sometimes
  appear behind the window instead of in front of it.

## v0.2.5

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
- Fixed an issue where an interrupted upload would not resume without restarting
  Maestral.
- Fixed an issue where file changes while "offline" would sometimes not be synced to
  Dropbox when a connection is reestablished.
- Fixed an issue where errors from `requests` would inadvertently get caught instead of
  being raised.

## v0.2.4

This version mainly improves the appearance and responsiveness of the GUI specifically
on Linux platforms with a Gnome desktop. It also introduces a dialog to handle a deleted
or moved Dropbox folder.

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

## v0.2.3

This release mainly fixes crashes of the setup dialog and contains tweaks to the UI.

#### Changed:

- Launch into setup dialog if no Dropbox authentication token can be found in keychain.
- Only log messages of level ERROR or higher to file.
- Show account email in the system tray menu above space usage.
- Unified the code for error dialogs and added an app icon to all dialogs.

#### Fixed:

- Fixed a bug which could could result in the user being asked to re-authenticate when
  no Dropbox folder is detected on startup.
- Fixed a bug which could cause Maestral to crash during the setup dialog, immediately
  after user authentication.

## v0.2.2

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

## v0.2.1

#### Changed:

- Reload all file and folder icons when the system appearance changes: the system may
  provide different icons (e.g., darker folder icons in "dark mode" on macOS Mojave).
- Improved notification centre alerts in macOS: when installed as a bundled app,
  notifications are now properly sent from the Maestral itself, showing the Maestral
  icon, instead of through apple script.
- Improved layout of the "Rebuild index" dialog.

#### Fixed:

- Fixes a bug which would prevent Meastral from starting on login: the correct startup
  script is now called.

## v0.2.0

#### Major changes

#### Added:

- Proper handling of sync errors. Dropbox API errors are converted to a more informative
  `MaestralApiError` and a log of sync errors is kept. This log is cleared as sync
  errors are resolved. Errors are now handled as follows:
      - Individual file sync errors are indicated by the system tray icon changing. The
        can listed by the user through the GUI.
      - Unexpected errors or major errors which prevent Maestral from functioning (e.g.,
        a corrupted index) trigger an error dialog.

- Introduced a new panel "View Sync Issues..." to show an overview of sync issues and
  their cause (invalid file name, insufficient space on Dropbox, etc...)
- Added a new function to rebuild Maestral's file index which is accessible through the
  GUI.
- Added "Recently Changed Files" submenu to the system tray menu. "Recently Changed
  Files" shows entries for the 30 last-changed files (synced folders only) and navigates
  to the respective file in the default file manager when an entry is clicked.

#### Changed:

- Refactored sync code: Collected all sync functionality in a the new class
  `monitor.UpDownSync`. `MaestralClient` now only handles access to the Dropbox API
  itself but is no longer concerned with version tracking, etc. `MaestralClient` no
  longer catches Dropbox API errors but raises them, augmented with useful information,
  as `MaestralApiError`.
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
- Truncate entries in the "Recently Changed Files" menu if their width exceeds 200
  pixels.
- Fixed a bug which would cause Maestral to crash when clicking "Choose folders to
  sync..." while Maestral cannot connect to Dropbox servers.

## v0.1.2

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
  multiple times between the "Select Folders to Sync" and "Select Dropbox location"
  panels.
- Do not upload files that have identical content on Dropbox. Previously: files were
  always uploaded and conflict checking was left to do by the Dropbox server.

## v0.1.1

#### Fixed:

- Fixes an issue which would prevent newly created empty folders from being synced.
- Remove references to conda in startup script.
