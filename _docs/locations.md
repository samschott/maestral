---
title: Supported folder locations
permalink: /docs/locations
---

Network drives and some external hard drives are not fully supported as locations for
the Dropbox folder because Maestral will not be notified of file changes in those
locations. The same holds for distributed filesystems or other filesystems where inotify
events may be disabled. In such cases, Maestral will only upload changes when it runs a
full indexing of the local folder, for instance when resuming the sync.

In such cases, it may be preferable to configure one-way syncing only so that local
changes are always ignored. This can be done through the CLI with `maestral config set
upload False` and will take effect once the daemon is restarted.
