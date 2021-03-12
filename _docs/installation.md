---
layout: single
title: Installation
permalink: /docs/installation
sidebar:
  nav: "docs"
---

An app bundle is currently provided only for macOS. On other platforms, you can either
install the Docker image or the Python package from PyPI:

```console
$ python3 -m pip install --upgrade maestral
```

If you intend to use the graphical user interface, you also need to specify the GUI option
during installation or upgrade. This will install the `maestral-qt` frontend and `PyQt5`
on Linux and `maestral-cocoa` on macOS:

```console
$ python3 -m pip install --upgrade maestral[gui]
```

Please refer to the [download]({{ site.baseurl }}/download) page for links and a
comparison of installation sizes.

## System requirements

The basic requirements to run the daemon are:

- Mac OS X Mojave or higher
- Linux 2.6 or higher
- For the Python package: Python 3.6 or higher

### GUI

The Linux GUI requires PyQt 5.9 or higher. While the GUI will run with Qt 5.9, support
of some platform features such as high-dpi scaling will be limited. Qt 5.12 or higher is
recommended for newer platforms.

To install the GUI, you can either specify the gui extra when install Maestral

```console
$ python3 -m pip install --upgrade maestral[gui]
```

or directly pip-install `maestral-qt` / `maestral-cocoa `.

Since the GUI is a system tray / menu bar app, it does require a desktop environment
with a system tray. This is no longer the case in Gnome 2.6+, although Ubuntu ships with
its own system tray extension. For Gnome desktop environments that do not provide a
system tray, the
[gnome-shell-extension-appindicator](https://extensions.gnome.org/extension/615/appindicator-support/)
is recommended.

The GUI is regularly tested on the platforms which I have access to:

- macOS 10.14 Mojave and macOS 11 Big Sur
- Ubuntu 20.04
- CentOS 6
- Raspbian on Raspberry Pi 3B+

### systemd journal support

Logging to the systemd journal requires
[python-systemd](https://github.com/systemd/python-systemd) which is currently not a
dependency but will be installed when specifying the 'syslog' extra in the installation
command:

```console
$ python3 -m pip install --upgrade maestral[syslog]
```

Note however that a pip installation will build python-systemd from source and requires
gcc, systemd headers and python headers and may therefore fail on some systems. It is
recommended to install python-systemd from your distribution's package manager instead:

On Fedora/RHEL/CentOS:

```console
dnf install python3-systemd
```

On Debian/Ubuntu/Mint:

```console
apt-get install python3-systemd
```

## Docker image

A Docker image is available for x86, arm/v7 (32bit) and arm64 platforms. You can do
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

To step into the Maestral container use `docker exec -it maestral ash`. list the logs of
To the container use `docker logs maestral` get the build info of a running container:
To get the build info of a running container:
`docker inspect maestral | jq ".[].Config.Labels"`.
