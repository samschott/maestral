#### Changed:

* The macOS app bundle now uses Python 3.10 instead of 3.9. This comes with some performance improvements.

#### Fixed:

* Work around transitory server-side errors when refreshing access tokens by retrying the refresh up to five times.
* Fixed a segfault on startup for a small number of macOS users.
* Fixed an issue where files which contain decomposed unicode characters could be deleted after renaming them locally on some versions of macOS.
* Fixes an issue where the `maestral gui` command would fail for macOS app bundles.