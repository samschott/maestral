---
layout: single
title: Selective sync
permalink: /docs/selective-sync
sidebar:
  nav: "docs"
---

With selective sync, you can select files and folders to remove from your hard drive but keep in your online account. Maestral will ask you during setup, before the initial download, which files or folder you would like to include in syncing. You can also select which items to sync later, through the GUI in the settings pane, or through the CLI with the `maestral excluded` command group. If you want to keep files or folder inside the Dropbox directory on your hard drive but not sync them to the cloud, you can use an [mignore]({{ site.baseurl }}/docs/mignore) file instead.

### Example GUI usage

Selective sync settings are accessible through the settings pane:

{% include figure
image_path="/assets/images/selective-sync.png"
image_path_dark="/assets/images/selective-sync-dark.png"
alt="Selective sync" %}

### Example CLI usage

To exclude a folder `Pictures` in your Dropbox from syncing, you can run:

```shell
$ maestral excluded add Pictures
```

To include the folder again, run:

```shell
$ maestral excluded remove Pictures
```

Items which have been excluded from syncing will be removed from your hard drive and items which have been newly included will be downloaded. The command `maestral excluded remove` will therefore only work when the sync daemon is running.

To list all currently excluded files and folders, run:

```shell
$ maestral excluded list
```

Finally, the command `maestral ls -l` will list all files and folders in your Dropbox or together with the any of its directories included state. For example:

```shell
$ maestral ls

Name                  Type      Size  Shared   Syncing   Last Modified
Data upload           file    24.0 B  private  ✓         18 Jan 2021
Pictures              folder       -  shared   ✓         -
quadruplet_states.nb  folder       -  shared   ✓         -
Work                  folder       -  shared   excluded  -
```