#### Changed:

* Improved error message for file names with incompatible characters that are rejected by Dropbox servers, e.g., emoji or slashes at the end of a file name.
* Capture Dropbox SDK logs in Maestral's log output. This will log which API endpoints are called and any retries on errors or rate limiting.

#### Fixed:

* Fixes intermittent failures to show a file associated with a sync issue in the Linux GUI.
* Fixes an issue where the macOS app bundle would use a system-wide installation of the Sparkle framework if available instead of the one bundled with Maestral. This could lead to unexpected issues if the system-wide installation would have an incompatible version.
* Fixes an issue where the access level of shared links may be incorrectly reported.
* Resume interrupted downloads after a shutdown when including new items with selective sync.
* Fixes occasional conflicting copies of folders during initial sync due to a race condition when a child item is synced before its parent folder.
* Fixes the display of timestamps in the CLI from `maestral ls` and `maestral history` commands. The former would show times in UTC instead of the device's timezone and the latter would show Unix timestamps instead of formatted output.