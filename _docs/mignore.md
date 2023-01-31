---
title: Mignore
permalink: /docs/mignore
---

When excluding files or folders with selective sync (`maestral exclude` in the CLI),
they will be removed from the local folder and kept in the cloud only. However, there
also is a valid use case for excluding files from syncing which exist only in the local
Dropbox directory and not in the cloud. Those could be for instance build artefacts or
system files which Maestral does not already exclude by default.

Maestral supports excluding local files from syncing by using a ".mignore" file to
specify patterns of file names to be ignored. The ".mignore" file must be saved in the
top-level Dropbox folder. It uses the same syntax as [gitignore files](https://git-
scm.com/docs/gitignore) but behaves a bit differently:

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

Mignore patterns will be interpreted relative to the Dropbox folder. A few examples,
copied from the gitingore documentation:

- The pattern `hello.*` matches any file or directory whose name begins with `hello.`.
  If one wants to restrict this only to the directory and not in its subdirectories,
  one can prepend the pattern with a slash, i.e. `/hello.*`; the pattern now matches
  `hello.txt`, `hello.c` but not `folder/hello.java`.
- The pattern `foo` will match a file `foo` or a directory `foo` and paths underneath it.
- The pattern `foo/` will match a directory foo and paths underneath it, but will not
  match a regular file or a symbolic link `foo`.
- The patterns `doc/frotz` and `/doc/frotz` have the same effect in any `.mignore` file.
  In other words, a leading slash is not relevant if there is already a middle slash in
  the pattern.

For a comprehensive overview of rules, please refer to the [gitignore documentation](https:
//git-scm.com/docs/gitignore).

<p><b>Warning:</b> A long list of patterns in mignore may impact performance when Maestral
is indexing a large number of local file changes.</p>{: .notice--danger}
