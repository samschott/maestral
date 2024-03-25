This is the last release that supports Python 3.7 which will reach end-of-life on 27 Jun 2023. The macOS app always ships with its own Python runtime, but custom installations will require at least Python 3.8 starting with the next release.

#### Changed:

* Preparation for upcoming API changes in `watchdog` dependency.
* No more automatic reindexing: Maestral would automatically rebuild its index  every 14 days. This could occasionally lead to conflicting copies if a file was modified remotely during this process. This reindexing is not necessary, especially as syncing has become very reliable. Starting with this release, reindexing needs to be triggered manually through the GUI or CLI if required.

#### Fixed:

* Fixes autostart entries for the GUI being malformed when Maestral is installed in a Python virtual environment.
* Fixes autostart entries for the daemon being malformed for the macOS app bundle. This applies to autostart entries created with `maestral autostart -Y` and not using the "Start on login" checkbox in the GUI.
* The `filestatus` command now is case-sensitive when checking on a case-sensitive file system.
* Fixes an issue where renaming a file by changing the casing only would not be picked up if Maestral was not running during the rename.