# Sisyphos DBX
Open-source Dropbox command line client for macOS and Linux.

## About
Sisyphos DBX is an open-source Dropbox client written in Python. The project's main goal is to provide an open-source desktop Dropbox client for platforms that aren't supported. SisyphosDBX is script-based which makes it platform-independent. It's written using the Python SDK for Dropbox API v2.

Sisyphos DBX remembers its last settings and resumes syncing after a restart. You can also pause and resume syncing while SisyphosDBX is running, add and remove exluded folders, and change the Dropbox location on the local drive.

## Interactive usage (Python shell)

After installation, in a Python command prompt, run
```Python
>>> from sisyphosdbx import SisyphosDBX
>>> sdbx = SisyphosDBX()
```
On initial use, SisyphosDBX will ask you to link your dropbox account, give the location of your Dropbox folder on the local drive, and to specify excluded folders. It will then start syncing. Supported commands are:

```Python
>>> sdbx.pause_sync()  # pause syncing
>>> sdbx.resume_sync()  # resume syncing

>>> path = '/Folder/On/Dropbox'  # path relative to Dropbox folder
>>> sdbx.exclude_folder(path)  # exclude Dropbox folder from sync, delete locally
>>> sdbx.include_folder(path)  # inlcude Dropbox folder in sync, download its contents

>>> sdbx.set_dropbox_directory('~/Dropbox')  # give path for local dropbox folder
>>> sdbx.unlink()  # unlinks your Dropbox account but keeps are your files
```

You can get information about your Dropbox account and direct access uploading, downloading and moving files / folders on your Dropbox through the SisyphosDBX API client. Some example commands include:

```Python
>>> from sisyphosdbx.client import SisyphosClient
>>> client = SisyphosClient()

>>> client.upload(local_path, dropbox_path)  # uploads file form local_path to Dropbox
>>> client.download(dropbox_path, local_path)  # downloads file from Dropbox to local_path
>>> client.move(old_path, new_path)  # moved file or folder from old_path to new_path on Dropbox
>>> client.make_dir(dropbox_path)  # created folder 'dropbox_path' on Dropbox

>>> client.list_folder(dropbox_path)  # lists content of a folder on Dropbox
>>> client.get_metadata(dropbox_path)  # returns metadata for a file or folder on Dropbox
>>> client.get_space_usage()  # returns your Dropbox space usage
>>> client.get_account_info()  # returns your Dropbox account info
```

## Command line usage
After installation, Sisyphos DBX will be available as a command line script by typing `sisyphosdbx` in the command prompt. Command line functionality resembles that of the interactive client. Type `sisyphosdbx --help` to get a full list of available commmands. Invoking `sisyphosdbx` by itself will configure Sisyphos DBX on first run and then automatically start syncing.

## Warning:
- SisyphosDBX doesn't have production status yet, so only 500 accounts can use the API keys.
- SisyphosDBX is still in beta status and may potentially result in loss of data. Only sync folders with non-essential files.

## Installation
Download and install the package by running
```console
$ pip git+https://github.com/SamSchott/sisyphosdbx
```
in the command line.

## Dependencies
*System:*
- Python >= 3.7
- macOS or Linux

*Python:*
- dropbox
- watchdog
- blinker
- PyQt5 (for GUI only)
