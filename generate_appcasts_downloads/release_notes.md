This release fixes several crashes of the GUI when running on Apple Silicon. It also refactors the handling of sync errors, resulting in more reliable clearing of sync errors after a file was successfully synced, features some performance improvements when indexing local changes and further decouples the sync logic from specifics of the Dropbox API.

#### Changed:

* Sync errors are now stored in a SQlite database table instead of a config file.
* The CLI command `maestral filestatus` will now return `error` if there is a sync error for any child of the given path.
* Re-enabled updating from versions older than 1.5.0.
* Improved file integrity checks after upload or download.
* Better parallelize CPU intensive work when indexing local changes. This improves performance on multicore CPUs.
* Migrate the Linux GUI from PyQt5 to PyQt6.

#### Fixed:

* Fixes an issue where upload sync errors could continue to be reported after the local file was deleted if the deletion occurred while sync was not running.
* Fixes an issue with the Linux Qt GUI where aborting the setup dialog after linking but before choosing a local Dropbox folder would result in an inconsistent state.
* Fixes an issue when storing 64-bit inode numbers in our database.
* Fixes occasional crashes of the macOS GUI when running on Apple Silicon.