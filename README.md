[![PyPi Release](https://img.shields.io/pypi/v/maestral.svg)](https://pypi.org/project/maestral/)
[![Pyversions](https://img.shields.io/pypi/pyversions/maestral.svg)](https://pypi.org/pypi/maestral/)
[![Documentation Status](https://readthedocs.org/projects/maestral/badge/?version=latest)](https://maestral.readthedocs.io/en/latest/?badge=latest)
[![codecov](https://codecov.io/gh/SamSchott/maestral/branch/master/graph/badge.svg?token=V0C7IQ1MAU)](https://codecov.io/gh/SamSchott/maestral)

# Maestral <img src="https://raw.githubusercontent.com/SamSchott/maestral/master/src/maestral/resources/maestral.png" align="right" title="Maestral" width="110" height="110">

A light-weight and open-source Dropbox client for macOS and Linux.

## About

Maestral is an open-source Dropbox client written in Python. The project's main goal is to
provide a client for platforms and file systems that are no longer directly supported by
Dropbox.

Maestral currently does not support Dropbox Paper, the management of Dropbox teams, and
the management of shared folder settings. If you need any of this functionality, please
use the Dropbox website or the official client. Maestral does support syncing
multiple Dropbox accounts and excluding local files from sync with a ".mignore" file.

The focus on "simple" file syncing does come with advantages: on macOS, the Maestral App
bundle is significantly smaller than the official Dropbox app and uses less memory. The
exact memory usage will depend on the size of your synced Dropbox folder and can be further
reduced when running Maestral without a GUI.

Maestral uses the public Dropbox API which, unlike the official client, does not support
transferring only those parts of a file which changed ("binary diff"). Maestral may
therefore use more bandwidth that the official client. However, it will avoid uploading
or downloading a file if it already exists with the same content locally or in the cloud.

## Warning

- Never sync a local folder with both the official Dropbox client and Maestral at the same
  time.
- Network drives and some external hard drives are not supported as locations for the
  Dropbox folder.

## Installation

An app bundle is provided for macOS High Sierra and higher and can be downloaded from the
Releases tab. This app Bundle is also package as a Homebrew cask.

On other platforms, you can download and install Maestral as a Python package from PyPI or
as a Docker image from Docker Hub.

For more detailed information on the installation, setup and system requirements, please
check the [documentation](https://maestral.app/docs/installation).


### Homebrew

The official Maestral releases are also available as Homebrew casks. If you have
[Homebrew](https://brew.sh) on your system, you can install using:

```console
$ brew install maestral
```

### Python package using PyPI

Please download and install the Python package from PyPI:

```console
$ python3 -m pip install --upgrade maestral
```

If you intend to use the graphical user interface, you also need to specify the GUI option
during installation or upgrade. This will install the `maestral-qt` frontend and `PyQt5`
on Linux and `maestral-cocoa` on macOS:

```console
$ python3 -m pip install --upgrade maestral[gui]
```

### Docker image

A Docker image is available for x86, arm/v7 (32bit) and arm64 platforms and can be
installed with:

```colsole
$ docker pull maestraldbx/maestral
```

## Usage

Run `maestral gui` in the command line (or open the Maestral app on macOS) to start
Maestral with a graphical user interface. On its first run, Maestral will guide you
through linking and configuring your Dropbox and will then start syncing.

<img src="https://raw.githubusercontent.com/SamSchott/maestral-dropbox/master/screenshots/macOS_dark.png" alt="screenshot macOS" width="840"/>
<img src="https://raw.githubusercontent.com/SamSchott/maestral-dropbox/master/screenshots/Ubuntu.png" alt="screenshot Fedora" width="840"/>

### Command line usage

After installation, Maestral will be available as a command line script by typing
`maestral` in the command prompt. Type `maestral --help` to get a full list of available
commands. The most important are:

- `maestral gui`: Starts the Maestral GUI. Creates a sync daemon if not already running.
- `maestral start|stop`: Starts or stops the Maestral sync daemon.
- `maestral pause|resume`: Pauses or resumes syncing.
- `maestral autostart -Y|-N`: Sets the daemon to start on log in.
- `maestral status`: Gets the current status of Maestral.
- `maestral filestatus LOCAL_PATH`: Gets the sync status of an individual file or folder.
- `maestral excluded add|remove|list`: Command group to manage excluded folders.
- `maestral ls DROPBOX_PATH`: Lists the contents of a directory on Dropbox.
- `maestral notify snooze N`: Snoozes desktop notifications for N minutes.

Maestral supports syncing multiple Dropbox accounts by running multiple instances
with different configuration files. This needs to be configured from the command
line by passing the option `--config-name` to `maestral start` or `maestral gui`.
Maestral will then select an existing config with the given name or create a new one.
For example:

```console
$ maestral start --config-name="personal"
$ maestral start --config-name="work"
```

This will start two instances of Maestral, syncing a private and a work account, 
respectively. Configs will be automatically cleared when unlinking an account. You can
list all currently linked accounts with `maestral config-files`. The above setup for
example will return the following on macOS:

```console
$ maestral config-files

Config name  Account          Path
maestral     user@gmail.com   ~/Library/Application Support/maestral/maestral.ini
private      user@mycorp.org  ~/Library/Application Support/maestral/private.ini
```

By default, the Dropbox folder names will contain the capitalised config-name in braces.
In the above case, this will be "Dropbox (Personal)" and "Dropbox (Work)".

A full documentation of the CLI is available on the
[website](https://samschott.github.io/maestral/cli/).

## Contribute

There are multiple topics that could use your help. Some of them are easy, such as adding
new CLI commands, others require more experience, such as packaging for non-macOS
platforms. Look out for issues marked with "good first issue" or "help wanted". Pull
requests should be made against the develop branch.

Relevant resources are:

- [Maestral API docs](https://maestral.readthedocs.io)
- [Dropbox API docs](https://www.dropbox.com/developers/documentation/http/documentation)
- [Dropbox Python SDK docs](https://dropbox-sdk-python.readthedocs.io/en/latest/)

[CONTRIBUTING.md](CONTRIBUTING.md) contains detailed information on the expected code
style and test format.

If you are using the macOS app bundle, please consider sponsoring the project with £1 per 
month to offset the cost of an Apple Developer account to sign and notarize the bundle.

## System requirements

- macOS 10.14 Mojave or higher or Linux
- Python 3.6 or higher
- For the system tray icon on Linux:
  - [gnome-shell-extension-appindicator](https://github.com/ubuntu/gnome-shell-extension-appindicator)
    on Gnome 3.26 and higher
