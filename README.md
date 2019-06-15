# Meastral
A light-weight and open-source Dropbox client for macOS and Linux.

## About
Meastral is an open-source Dropbox client written in Python. The project's main goal is to
provide a client for platforms and file systems that are not directly supported by
Dropbox. Meastral uses the Python SDK for the Dropbox API v2.

## Installation
Download and install the Python package by running
```console
$ pip install git+https://github.com/SamSchott/maestral
```
in the command line.

## Usage
Run `meastral-gui` in the command line to start Meastral with a graphical user interface.
On first sync, Meastral will run you through linking and configuring your Dropbox and then
start syncing. The user interface is based on a status bar (menu bar) icon showing the
current syncing status and a preference pane for configuration.

![Screenshot macOS](/screenshots/full.png)

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
`client.MaestralClient` handles all the interaction with the Dropbox API such as
authentication, uploading and downloading files and folders, getting metadata and listing
folder contents. It also includes utilities to convert between local and Dropbox file
paths, to keep track of local revisions and to check for sync conflicts between local and
remote files.

`monitor.MaestralMonitor` handles the actual syncing. It monitors the local Dropbox
folders and the remote Dropbox for changes and applies them using the interface provided
by `MaestralClient`.

`main.Maestral` provides the main programmatic user interface. It links your Dropbox
account and sets up your local, lets you select which folders to sync and can pause and
resume syncing.

`gui` contains all user interfaces for `Maestral`.

## Contribute
The follwing tasks could need your help:

- [ ] Native Cocoa and GTK interfaces. Maestral currently uses PyQt.
- [ ] Better handling of network errors and API errors.
- [ ] More efficient and robust tracking of local revisions. Possibly using xattr, even
      though this would limit file system compatibility.
- [ ] Detect and warn in case of unsupported Dropbox folders locations (network drives,
      external hard drives, etc).
- [ ] Speed up initial sync: Download whole folders as zip files if possible.
- [ ] Test robustness if internet connection is slow or lost, maestral process is killed
      during sync, user is logged out during sync, etc.

## Warning:
- Meastral does not have production status yet, so only 500 accounts can use the API keys.
- Meastral is still in beta status and may potentially result in loss of data. Only sync
  folders with non-essential files.
- Known issues:
  - File and folder names with two periods are currently not supported. This prevents
    syncing of temporary files which are created during the save process on some file
    systems.
  - Rare falsely detected sync conflicts may occur on startup.
  - Network drives and some external hard drives are not supported as locations for the
    Dropbox folder.

## Dependencies
*System:*
- Python 3.6 or higher
- macOS or Linux

*Python:*
- dropbox
- watchdog
- blinker
- PyQt 5.9 or higher (for GUI only)
