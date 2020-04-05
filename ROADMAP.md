
#### Short term:

* Switch from implicit grant to PKCE OAuth flow as soon as Dropbox supports it.
* Snap package once core20 is released.
* CLI autocomplete for paths once there is better support from upstream `click`.
* Update macOS app bundle to Python 3.8 and Qt 5.14.

#### Long term:

* deb and rpm packages: either with Pyinstaller executable or as Python package.
* GUI support for multiple Dropbox accounts.
* Option to install command line scripts from macOS app bundle.
* Work with upstream `toga` to fix remaining issues for native macOS GUI,
  notably memory leak in `rubicon.objc`.
