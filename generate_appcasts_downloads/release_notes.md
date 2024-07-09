#### Changed:

* Allow passing multiple paths to `maestral excluded add | remove` CLI commands.

#### Fixed:

* Fixes an issue where syncing remote changes would lead to the modified time of a file being set to the download time instead of the time of original modification.