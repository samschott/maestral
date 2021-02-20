---
layout: single
permalink: /docs/mignore
sidebar:
  nav: "docs"
---

# Mignore

When excluding files or folders with selective sync (`maestral exclude` in the CLI),
they will be removed from the local folder and kept in the cloud only. However, there
also is a valid use case for excluding files from syncing which exist only in the local
Dropbox directory and not in the cloud. Those could be for instance build artefacts or
system files which Maestral does not already exclude by default.

Maestral supports excluding local files from syncing by using a ".mignore" file to
specify patterns of file names to be ignored. The ".mignore" file must be saved in the
top-level Dropbox folder. It uses the same syntax as [gitignore files](https://git-
scm.com/docs/gitignore) but behaves slightly differently:

- If you add a new mignore pattern, **any matching files or folder will be removed from
  the remote Dropbox** and will only exist locally. Currently, pattern matching is case
  sensitive. A pattern `Foo.txt` in mignore will match the file name "Foo.txt" but not
  "foo.txt". This may change in the future since Dropbox itself is case-insensitive.
- If you remove a pattern from mignore, you will need to pause and resume syncing to index
  and upload any newly included items.
- If a local file or folder is moved to an ignored path, it will be deleted from Dropbox.
- If an item is moved from an ignored path to an included one, it will be uploaded to
  Dropbox.
- When a file is excluded by mignore and a file with the same name is created in the
  cloud, the remote file may be downloaded and included in syncing temporally. However,
  the next time syncing is paused and resumed, it will removed from the cloud.

<p>Warning: A long list of patterns in mignore may impact performance when Maestral is
indexing a large number of local file changes.</p>{: .notice--danger}
