# SisyphosDBX
Open-source Dropbox command line client for macOS and Linux

## About
SisyphosDBX is an open-source Dropbox client written in Python. The project's main goal is to provide an open-source desktop Dropbox client for platforms that aren't supported. SisyphosDBX is script-based which makes it platform-independent. It's written using the Python SDK for Dropbox API v2.

*Usage:*

After installtion, in a Python command prompt, run
```Python
>>> from sisyphosdbx import SisyphosDBX
>>> sdbx = SisyphosDBX()
```
On initial use, SisyphosDBX will ask you to link your dropbox account, give the location of your Dropbox folder on the local drive, and to specify excluded folders. It will then start syncing.

SisyphosDBX remembers its last settings and resumes syncing after a restart. Use

```Python
>>> sdbx.stop_sync()  # pause syncing
>>> sdbx.start_sync()  # resume syncing
```

to start and resume syncing.

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
