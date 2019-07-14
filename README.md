# Meastral

A light-weight and open-source Dropbox client for macOS and Linux.

## About

Meastral is an open-source Dropbox client written in Python. The project's main goal is to
provide a client for platforms and file systems that are not directly supported by
Dropbox. Meastral uses the Python SDK for the Dropbox API v2.

Currently, Maestral does not support Dropbox Paper, the management of shared folder / file
settings or the management of Dropbox teams. If you need any of this functionality, you
must use the Dropbox website.

## Installation

Download and install the Python package by running
```console
$ pip install --upgrade git+https://github.com/SamSchott/maestral
```
in the command line. If you intend to use the graphical user interface, you also need to
install PyQt5. It is recommended to install PyQt5 through your distribution's package manager
(e.g, yum, dnf, apt-get, homebrew). Alternatively, you can also install PyQt5 from PyPI:
```console
$ pip install --upgrade PyQt5
```
However, in this case the interface style may not follow your selected system appearance
(e.g., "dark mode" on macOS or "Adwaita-dark" on Gnome). 

## Usage

Run `meastral gui` in the command line to start Meastral with a graphical user interface.
On its first run, Meastral will guide you through linking and configuring your Dropbox and
will then start syncing. The user interface is based on a status bar icon which shows the
current syncing status and a preference pane for configuration.

<img src="/screenshots/macOS.png" height="500" />
<img src="/screenshots/Fedora.png" height="500" />

## Command line usage

After installation, Meastral will be available as a command line script by typing
`meastral` in the command prompt. Command line functionality resembles that of the
interactive client. Type `meastral --help` to get a full list of available commands.
Invoking `meastral sync` will configure Meastral on first run and then automatically start
syncing.

## Interactive usage (Python shell)

After installation, in a Python command prompt, run
```Python
>>> from meastral import Meastral
>>> m = Meastral()
```
On initial use, Meastral will ask you to link your Dropbox account, give the location of
your Dropbox folder on the local drive, and to specify excluded folders. It will then
start syncing. Supported commands are:

```Python
>>> m.pause_sync()  # pause syncing
>>> m.resume_sync()  # resume syncing

>>> path = '/Folder/On/Dropbox'  # path relative to Dropbox folder
>>> m.exclude_folder(path)  # exclude Dropbox folder from sync, delete locally
>>> m.include_folder(path)  # include Dropbox folder in sync, download its contents

>>> m.set_dropbox_directory('~/Dropbox')  # give path for local Dropbox folder
>>> m.unlink()  # unlinks your Dropbox account but keeps are your files
```

## Structure
`maestral.client` handles all the interaction with the Dropbox API such as
authentication, uploading and downloading files and folders, getting metadata and listing
folder contents.

`maestral.monitor` handles the actual syncing. It monitors the local Dropbox
folders and the remote Dropbox for changes and applies them using the interface provided
by `maestral.client`.

`maestral.main` provides the main programmatic user interface. It links your Dropbox
account and sets up your local, lets you select which folders to sync and can pause and
resume syncing.

`maestral.gui` contains all graphical user interfaces for `Maestral`.

## Contribute

The following tasks could need your help:

- [x] Test robustness if internet connection is slow or lost, maestral process is killed
      during sync, user is logged out during sync, etc.
- [x] More efficient and robust tracking of local revisions. Possibly using xattr, even
      though this would limit file system compatibility.
- [x] Better handling of network errors.
- [ ] Better handling of certain API errors.
- [ ] Detect and warn in case of unsupported Dropbox folder locations (network drives,
      external hard drives, etc) and when the Dropbox folder is deleted by the user.
- [ ] Speed up download of large folders and initial sync: Download zip files if possible.
- [ ] Native Cocoa and GTK interfaces. Maestral currently uses PyQt5.

## Warning:

- Meastral does not have production status yet, so only 500 accounts can use the API keys.
- Meastral is still in beta status. Even through highly unlikely, using it may potentially
  result in loss of data.
- Known issues:
  - File and folder names with two periods are currently not supported. This prevents
    syncing of temporary files which are created during the save process on some file
    systems.
  - Network drives and some external hard drives are not supported as locations for the
    Dropbox folder.

## Dependencies

*System:*
- macOS or Linux
- Python 3.6 or higher
- [gnome-shell-extension-appindicator](https://github.com/ubuntu/gnome-shell-extension-appindicator)
  on Gnome 3.26 and higher
- PyQt 5.9 or higher (for GUI only).

*Python:*
- click
- dropbox
- watchdog
- blinker
- requests
- u-msgpack-python

