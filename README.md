# Meastral
An open-source Dropbox client for macOS and Linux.

## About
Meastral is an open-source Dropbox client written in Python. The project's main goal is to provide a client for platforms and file systems that are not directly supported by Dopbox. Meastral uses the Python SDK for the Dropbox API v2.

Meastral remembers its last settings and resumes syncing after a restart. You can also pause and resume syncing while Meastral is running, inlcude and exclude folders in the sync, and change the Dropbox location on your local drive. External storage devices are however not supported as Dropbox locations.

## Usage
Run `meastral --gui` in the command line to start Meastral with a graphical user interface. On first sync, Birdbox will run you through linking and configuring your Dropbox and then start syncing. The user interface is based on a status bar (menu bar) icon showing the current syncing status and a preference pane for configuration.

<p align="centred">
    <img src="/screenshots/menu_bar.png" height="300" title="Menu bar icon">
    <img src="/screenshots/settings.png" height="300" title="Preference pane">
</p>


## Interactive usage (Python shell)

After installation, in a Python command prompt, run
```Python
>>> from meastral import Meastral
>>> m = Meastral()
```
On initial use, Meastral will ask you to link your dropbox account, give the location of your Dropbox folder on the local drive, and to specify excluded folders. It will then start syncing. Supported commands are:

```Python
>>> m.pause_sync()  # pause syncing
>>> m.resume_sync()  # resume syncing

>>> path = '/Folder/On/Dropbox'  # path relative to Dropbox folder
>>> m.exclude_folder(path)  # exclude Dropbox folder from sync, delete locally
>>> m.include_folder(path)  # inlcude Dropbox folder in sync, download its contents

>>> m.set_dropbox_directory('~/Dropbox')  # give path for local dropbox folder
>>> m.unlink()  # unlinks your Dropbox account but keeps are your files
```

You can get information about your Dropbox account and direct access to uploading, downloading and moving items on your Dropbox through the Meastral API client `BidBoxClient`. Some example commands include:

```Python
>>> from meastral import MeastralClient
>>> client = MeastralClient()

>>> client.upload(local_path, dropbox_path)  # uploads file form local_path to Dropbox
>>> client.download(dropbox_path, local_path)  # downloads file from Dropbox to local_path
>>> client.move(old_path, new_path)  # moved file or folder from old_path to new_path on Dropbox
>>> client.make_dir(dropbox_path)  # created folder 'dropbox_path' on Dropbox

>>> client.list_folder(dropbox_path)  # lists content of a folder on Dropbox
>>> client.get_metadata(dropbox_path)  # returns metadata for a file or folder on Dropbox
>>> client.get_space_usage()  # returns your Dropbox space usage
>>> client.get_account_info()  # returns your Dropbox account info
```

MeastralClient does not inlcude any syncing functionality.

## Command line usage
After installation, Meastral will be available as a command line script by typing `meastral` in the command prompt. Command line functionality resembles that of the interactive client. Type `meastral --help` to get a full list of available commmands. Invoking `meastral` by itself will configure Meastral on first run and then automatically start syncing.

## Warning:
- Meastral does not have production status yet, so only 500 accounts can use the API keys.
- Meastral is still in beta status and may potentially result in loss of data. Only sync folders with non-essential files.

## Installation
Download and install the Python package by running
```console
$ pip git+https://github.com/SamSchott/meastral
```
in the command line.

## Dependencies
*System:*
- Python 3.6 or higher
- macOS or Linux

*Python:*
- dropbox
- watchdog
- blinker
- PyQt5 (for GUI only)
