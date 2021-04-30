---
title: Symlinks
permalink: /docs/symlinks
---

Maestral will follow local symlinks, even if they point to locations outside of your
Dropbox folder, and upload the contents of the destination. It will raise a sync issue
in case of broken symlinks. Depending on your platform, you may experience slow indexing
and high CPU usage with symlinks, or the linked items may only be synced when restarting
Maestral or pausing and resuming sync. Under certain circumstances, the symlink may be
silently replaced by the item it points to.

Dropbox has changed how its official client handles symlinks: it will no longer follow
symlinks itself but rather upload a placeholder file with symlink metadata. This is
currently not supported by Maestral which will simply download the empty placeholder
file.

It is therefore not recommended to use symlinks in your Dropbox folder, either with the
official client or Maestral. In a future version, Maestral may no longer follow symlinks
but sync them as actual files instead. This change will be announced in the release
notes.

