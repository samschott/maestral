---
layout: single
title: Sync issues
permalink: /docs/sync-issues
sidebar:
  nav: "docs"
---

Generally, the same limitations as outlined in this Dropbox [support
article](https://help.dropbox.com/installs-integrations/sync-uploads/files-not-syncing)
apply with additional constraints depending on your local file system. In addition,
Maestral cannot download DMCA protected files from Dropbox servers, this is a limitation
of the public Dropbox API.

## Ignored files

The following system files will be ignored by Maestral, following the behaviour of the
official Dropbox client:

### System files

* desktop.ini
* thumbs.db
* ds_store
* icon\r

### Maestral or Dropbox config files

* .maestral
* .dropbox
* .dropbox.cache
* .dropbox.attr

### Temporary files

* Names that start with "\~$" or ".\~"
* Names start with "\~" and end with ".tmp"

## Sync issues

Some file names may result in sync issues, depending on the platform.
You can view sync issues with [`maestral status`]({{ site.baseurl }}/cli/status) from
the CLI will be notified of sync issues through the GUI with the tray icon showing a bell.
The GUI also features a panel to show all sync issues:

{% include figure image_path="/assets/images/syncissue.png" alt="Sync issue" width="500px"%}

### Incompatible characters for all operating systems

If sync isn't working and your file name includes one of these characters, the easiest solution is to rename the original file without these characters.

* / (forward slash)
* \ (backslash)

Note: Some emojis can also cause sync issues.

### Maximum path length

File paths with more than 300 components won't sync. This is a Dropbox limitation.

### Maximum file size

Single files larger than 350 GB cannot be synced with Dropbox.

### Incompatible characters with Windows

Files that contain the following characters may not be synced to your Windows machine
but do not cause any issues with Dropbox or Maestral.

* < (less than)
* \> (greater than)
* : (colon)
* " (double quote)
* \| (vertical bar or pipe)
* ? (question mark)
* \* (asterisk)
* . (period) or a space at the end of a file or folder name

