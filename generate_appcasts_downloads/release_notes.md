#### Changed:

* Drops support for macOS 10.13 and 10.14. macOS 10.15 or later is now required, reflecting the support status from Apple. In the future, we will drop support for macOS versions once they no longer receive security updates.
* Improved status messages: the sync count is now updated as items are uploaded or downloaded instead of with a delay.
* `maestral activity` now shows animating progress bars to view upload and download progress.
* `maestral sharelink list` now outputs plain urls, separated by new lines. Use the `--long | -l` option if you would like a pretty-printed table with additional information about each link.
* `maestral history` now shows where the listed changes occurred (local vs remote).
* When multiple GUIs are installed (for example Qt and Cocoa), ask the user to choose when running `maestral gui` instead of selecting a default.

#### Fixed:

* Fixes an error which would prevent running Maestral on Python 3.7.
* Fixes a regression where the CLI command `maestral activity` would fail when run from a macOS app bundle.
* Fixes an issue where reauthorizing Maestral's Dropbox access could hang indefinitely.
* Fixes an issue on macOS where changing the casing of two local file names in the same folder in quick succession could result in the second file being deleted during sync.

#### Dependencies:

* Introduced `rich` dependency.
* Removed `sdnotify` dependency.