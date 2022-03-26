---
title: Symlinks
permalink: /docs/symlinks
---

While the Dropbox client fully supports uploading and downloading symlinks, the public
API does not allow creating symlinks on the server, only getting information about
existing symlinks. Maestral is therefore limited to downloading symlinks from the server
but cannot upload them. You will see a sync issue when trying to upload a newly created
symlink from your local Dropbox folder.

Some background about why we do not simply follow symlinks:

In mid-2019 Dropbox changed how its client handles symlinks: It will no longer sync the
symlink's destination but rather upload the symlink file itself together with the
information of where it points to. Symlinks are therefore now synced as what they
actually are: files with metadata pointing to a destination.

This means that symlinks pointing to another file or folder *outside* of your Dropbox
may be broken when synced to a different machine if the destination does not exist
there.

Treating symlinks this way has the advantages of preserving folder structures with
internal relative symlinks, for example some macOS app bundles.
