[![PyPi Release](https://img.shields.io/pypi/v/maestral.svg?color=blue)](https://pypi.org/project/maestral/)

# Maestral <img src="https://raw.githubusercontent.com/SamSchott/maestral-dropbox/master/maestral/gui/resources/Maestral.png" align="right" title="Maestral" width="110" height="110">

A light-weight and open-source Dropbox client for macOS and Linux.

## About

Maestral is an open-source Dropbox client written in Python. The project's main goal is to
provide a client for platforms and file systems that are no longer directly supported by
Dropbox.

Currently, Maestral does not support Dropbox Paper, the management of Dropbox teams and
the management of shared folder settings. If you need any of this functionality, please
use the Dropbox website or the official client.

The focus on file syncing does come with advantages: the Maestral App on macOS is 80%
smaller than the official Dropbox App (50 MB vs 290 MB) and uses 70% less memory. The app
size and memory footprint can be further reduced when installing and running Maestral
without a GUI and using the Python installation provided by your OS. The Maestral code
itself and its Python dependencies take up less than 3 MB,  making a headless install
ideal for systems with tight resources.

## Installation

A binary is provided for macOS Mojave and can be downloaded from the Releases tab. On
other platforms, download and install the Python package from PyPI by running
```console
$ python3 -m pip install --upgrade maestral
```
in the command line. If you intend to use the graphical user interface, you also need to
install PyQt5. I highly recommend installing PyQt5 through your distribution's package
manager (e.g, yum, dnf, apt-get, homebrew). Alternatively, you can also get it from PyPI:
```console
$ python3 -m pip install --upgrade PyQt5
```
However, in this case the interface style may not follow your selected system appearance
(e.g., "dark mode" on macOS or "Adwaita-dark" on Gnome).

If you clone and install the version from GitHub, you will need to provide your own
Dropbox API keys as environment variables `DROPBOX_API_KEY` and `DROPBOX_API_SECRET`. You
can get those keys [here](https://www.dropbox.com/developers/apps/create).

## Usage

Run `maestral gui` in the command line (or open the Maestral app on macOS) to start
Maestral with a graphical user interface. On its first run, Maestral will guide you
through linking and configuring your Dropbox and will then start syncing.

![screenshot macOS](https://raw.githubusercontent.com/SamSchott/maestral-dropbox/master/screenshots/macOS.png)
![screenshot Fedora](https://raw.githubusercontent.com/SamSchott/maestral-dropbox/master/screenshots/Fedora.png)

## Command line usage

After installation, Maestral will be available as a command line script by typing
`maestral` in the command prompt. Command line functionality resembles that of the
interactive client. Type `maestral --help` to get a full list of available commands.
Invoking `maestral sync` will configure Maestral on first run and then automatically start
syncing.

## Interactive usage (Python shell)

After installation, in a Python command prompt, run
```Python
>>> from maestral import Maestral
>>> m = Maestral()
```
On initial use, Maestral will ask you to link your Dropbox account, give the location of
your Dropbox folder on the local drive, and to specify excluded folders. It will then
start syncing. Supported commands include:

```Python
>>> m.pause_sync()  # pause syncing
>>> m.resume_sync()  # resume syncing

>>> path = '/Folder/On/Dropbox'  # path relative to Dropbox folder
>>> m.exclude_folder(path)  # exclude Dropbox folder from sync, delete locally
>>> m.include_folder(path)  # include Dropbox folder in sync, download its contents

>>> m.set_dropbox_directory('~/Dropbox')  # give path for local Dropbox folder
>>> m.unlink()  # unlinks your Dropbox account but keeps all your files
```

## Structure

`maestral.client` handles all the interaction with the Dropbox API such as authentication,
uploading and downloading files and folders, getting metadata and listing folder contents.

`maestral.monitor` handles the actual syncing. It monitors the local Dropbox folders and
the remote Dropbox for changes and applies them using the interface provided by
`maestral.client`.

`maestral.main` provides the main programmatic user interface. It links your Dropbox
account and sets up your local folder and lets you select which folders to sync.

`maestral.gui` contains all graphical user interfaces for `Maestral`.

## Contribute

The following tasks could need your help:

- [ ] Write tests for maestral.
- [ ] Detect and warn in case of unsupported Dropbox folder locations (network drives,
      external hard drives, etc) and when the Dropbox folder is deleted by the user.
- [ ] Speed up downloads of large folders and initial sync: Download zip files if possible.
- [ ] Native Cocoa and GTK interfaces. Maestral currently uses PyQt5.
- [ ] Packaging: improve packing for macOS (reduce app size) and package for other platforms.

## Warning:

- Maestral does not have production status yet, so only 500 accounts can use the API keys.
- Maestral is still in beta status. Even through highly unlikely, using it may potentially
  result in loss of data.
- Known issues:
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


# Acknowledgements

- The config module uses code from the [Spyder IDE](https://github.com/spyder-ide).
- The MaestralApiClient is based on the work from [Orphilia](https://github.com/ksiazkowicz/orphilia-dropbox).
