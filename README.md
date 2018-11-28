# SisyphosDBX
Open-source Dropbox command line client for macOS and Linux

## About
SisyphosDBX is an open-source Dropbox client written in Python. The project's main goal is to provide an open-source desktop Dropbox client for platforms that aren't supported. SisyphosDBX is script-based which makes it platform-independent. It's written using the Python SDK for Dropbox API v2.

*Usage:*

After installation, in a Python command prompt, run
```Python
>>> from sisyphosdbx import SisyphosDBX
>>> sdbx = SisyphosDBX()
```
On initial use, SisyphosDBX will ask you to link your dropbox account, give the location of your Dropbox folder on the local drive, and to specify excluded folders. It will then start syncing.

SisyphosDBX remembers its last settings and resumes syncing after a restart. You can also pause and resume syncing while SisyphosDBX is running, add and remove exluded folders, and change the Dropnox location on the local drive:

```Python
>>> sdbx.pause_sync()  # pause syncing
>>> sdbx.resume_sync()  # resume syncing
>>> path = '/FolderOnDropbox'  # path relative to Dropbox folder
>>> sdbx.exclude_folder(path)  # exclude Dropbox folder from sync, delete locally
>>> sdbx.include_folder(path)  # inlcude Dropbox folder in sync, download its contents
>>> sdbx.set_dropbox_directory('~/Dropbox')  # give path for local dropbox folder
```


*IMPORTANT:*
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
