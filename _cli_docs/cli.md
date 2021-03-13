---
layout: single
title: Command line interface
permalink: /cli
sidebar:
  nav: "cli-docs"
---

The `maestral` command will be automatically added to your PATH when installing the
Python package with pip. When using an app bundle on macOS, the command line interface
can be installed from the settings panel. This will create the appropriate symlink in
/usr/local/bin, prompting for your admin password if this directory is owned by root.

The command line interface provides access to a wide number of features, including some
that are not available from the GUI. This includes creating shared links for files and
folders, listing and restoring old versions of a file, and low-level access to Maestral's
configuration such as maximum CPU usage, reindex intervals, etc.

Running `maestral` without any arguments or `maestral --help` will print an overview over
all available commands with help texts. Help on a specific command is available through
`maestral COMMAND --help` which will list allowed arguments and options.

### Dropbox paths

When a command shows paths relative to your Dropbox folder in its output, those paths
may be in lower case. This is because Dropbox itself is case-insensitive and Maestral
internally converts all Dropbox paths to lower case. Formatting of path output may
change in the future.

Likewise, when a command takes a Dropbox path as input, it may be specified with arbitrary
casing. Dropbox paths may be specified as relative paths or optionally with a leading `/`
and will always be interpreted relative to the Dropbox root.

### Config names

Several commands take a `-c, --config-name` option to support multiple configurations of
Maestral which are linked to different Dropbox accounts. For instance,
`maestral start -c work` will start the sync daemon for the "work" config. If the given
config name does not exist, it will be created on-demand when running `maestral start` or
`maestral auth link`. Other commands must operate on an existing config name. You can
get a list of all current configs with `maestral config-files`.

If no config name option is given, the default configuration "maestral" will be used.
