[![PyPi Release](https://img.shields.io/pypi/v/maestral.svg)](https://pypi.org/project/maestral/)
[![Pyversions](https://img.shields.io/pypi/pyversions/maestral.svg)](https://pypi.org/pypi/maestral/)
[![Documentation Status](https://readthedocs.org/projects/maestral/badge/?version=latest)](https://maestral.readthedocs.io/en/latest/?badge=latest)
[![codecov](https://codecov.io/gh/SamSchott/maestral/branch/develop/graph/badge.svg?token=V0C7IQ1MAU)](https://codecov.io/gh/SamSchott/maestral)

# Maestral <img src="https://raw.githubusercontent.com/SamSchott/maestral/master/src/maestral/resources/maestral.png" align="right" title="Maestral" width="110" height="110">

A light-weight and open-source Dropbox client for macOS and Linux.

## About

Maestral is an open-source Dropbox client written in Python. The project's main goal is to
provide a client for platforms and file systems that are no longer directly supported by
Dropbox.

Maestral currently does not support Dropbox Paper, the management of Dropbox teams and
the management of shared folder settings. If you need any of this functionality, please
use the Dropbox website or the official client. Maestral does support syncing
multiple Dropbox accounts and excluding local files from sync with a ".mignore" file.

The focus on "simple" file syncing does come with advantages: on macOS, the Maestral App
bundle is significantly smaller than the official Dropbox app (20 MB vs 290 MB) and uses
much less memory (100 MB vs 800 MB for a medium sized Dropbox on macOS). The memory usage
will depend on the size of your synced Dropbox folder and can be further reduced when
running Maestral without a GUI.

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
Releases tab. On other platforms, please download and install the Python package from PyPI:

```console
$ python3 -m pip install --upgrade maestral
```

If you intend to use the graphical user interface, you also need to specify the GUI option
during installation or upgrade. This will install the `maestral-qt` frontend and `PyQt5`
on Linux and `maestral-cocoa` on macOS:

```console
$ python3 -m pip install --upgrade maestral[gui]
```

More detailed installation instructions are given in the
[Wiki](https://github.com/SamSchott/maestral-dropbox/wiki/Installation-Requirements).

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
- `maestral file-status LOCAL_PATH`: Gets the sync status of an individual file or folder.
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
respectively. Configs will be automatically cleared when unlinking an account and you can
list all currently linked accounts with `maestral configs`:

```console
$ maestral configs

Config name  Account
personal     user@gmail.com
work         user@mycorp.org

```

By default, the Dropbox folder names will contain the capitalised config-name in braces.
In the above case, this will be "Dropbox (Personal)" and "Dropbox (Work)".

### Docker usage

The Docker image is available for x86, arm/v7 (32bit) and arm64 platforms. You can do
everything that you supposed to do in the command line, except running the GUI.

For the first run, get access to the shell within the Docker container 

```console
$ docker run -it -v /mnt/dropbox:/dropbox maestraldbx/maestral:latest ash
```

where `/mnt/dropbox` is the directory that which contains the `Dropbox` directory.
Maestral runs with `UID` 1000, make sure that the user owns `/mnt/dropbox` and the
contents within (`chown -R 1000 /mnt/dropbox`).

Later, if you want just a `maestral start`, just execute

```console
$ docker run \
  -d \
  --name maestral \
  --rm \
  -v /mnt/dropbox:/dropbox \
  maestraldbx/maestral:latest
```

- To step into the Maestral container: `docker exec -it maestral ash`
- List the logs of the container: `docker logs maestral`
- Get the build info of a running container: `docker inspect maestral | jq ".[].Config.Labels"`


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

# Acknowledgements

- The config module uses code from the [Spyder IDE](https://github.com/spyder-ide)
- The DropboxClient is inspired by work from [Orphilia](https://github.com/ksiazkowicz/orphilia-dropbox)
- Error reporting is powered by bugsnag.
