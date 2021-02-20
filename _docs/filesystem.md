---
layout: single
permalink: /docs/filesystem
sidebar:
  nav: "docs"
---

# Case-sensitive file systems

Dropbox is case-insensitive: it does not allow files "Readme.txt" and "readme.txt" to
exist at the same location. This likely is a deliberate choice to enable syncing between
case-sensitive and -insensitive file systems. However, Dropbox is case-preserving so
that a file created as "readme.txt" is shown as "readme.txt" and a file created as
"Readme.txt" is shown as "Readme.txt".

This may cause issues when your local file system  *is* case sensitive. On such systems,
when you create a new file or folder with a name that already exists but with different
casing, Maestral will rename it by appending "(case conflict)" before it is uploaded.
