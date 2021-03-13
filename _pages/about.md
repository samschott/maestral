---
layout: single
title: About
permalink: /about/
---

Maestral is an open-source Dropbox client written in Python. The project's main goal is
to provide a client for platforms and file systems that are no longer directly supported
by Dropbox. This was motivated by Dropbox temporarily dropping support for many Linux
file systems but extends to systems that no longer meet Dropbox's minimum requirement of
glibc >= 2.19, such as CentOS 6 and 7.

## Limitations

Currently, Maestral does not support Dropbox Paper, the management of Dropbox teams and
the management of shared folder settings. If you need any of this functionality, please
use the Dropbox website or the official client.

Maestral uses the public Dropbox API which, unlike the official client, does not support
transferring only those parts of a file which changed ("binary diff"). Maestral may
therefore use more bandwidth that the official client. However, it will avoid uploading
or downloading a file if it already exists with the same content locally or in the
cloud.

## Features

The focus on "simple" file syncing does come with advantages: on macOS, the Maestral App
bundle is smaller than the official Dropbox app (40 MB vs 420 MB) and uses less memory
(100 MB for a medium sized Dropbox on macOS vs 500 GB). The memory usage will depend on
the size of your synced Dropbox folder and can be further reduced when running Maestral
without a GUI.

Maestral also supports syncing multiple Dropbox accounts by running multiple instance in
parallel.

Finally, since Maestral is not an official Dropbox App but just a third-party
application, it will not count towards the three devices limit for basic Dropbox
accounts.

## Privacy policy

Maestral does not collect any user data, anonymous or otherwise. It only communicates
with Dropbox servers and the Dropbox privacy policy of course applies.

## Acknowledgements

Maestral directly uses code from the following projects:

- The config module uses code from the [Spyder IDE](https://github.com/spyder-ide)
- The DropboxClient module is inspired by work from [Orphilia](https://github.com/ksiazkowicz/orphilia-dropbox)

It also would not be possible without the following excellent Python packages:

- Communication between sync daemon and frontends uses [Pyro5](https://github.com/irmen/Pyro5).
- The command line interface is built with [click](https://github.com/pallets/click) and
  uses interactive prompts by [survey](https://github.com/Exahilosys/survey).
- The Cocoa GUI is built using [toga](https://github.com/beeware/toga) and the macOS app
  bundle is built using [briefcase](https://github.com/beeware/briefcase), both part of
  the [beeware](https://beeware.org) project for writing cross-platform Python applications.
- Credential storage uses system keychains via [keyring](https://github.com/jaraco/keyring).
- [watchdog](https://github.com/gorakhargosh/watchdog) allows us to receive local file
  system events.
- Many more well known libraries that have become the backbone of Python projects
  such as requests, sqlalchemy, etc.
