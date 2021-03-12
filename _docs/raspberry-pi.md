---
layout: single
title: Notes on Raspberry Pi
permalink: /docs/raspberry-pi
sidebar:
  nav: "docs"
---

Maestral has been shown to run on Raspberry Pi but requires at least Python 3.6 while,
as of this writing, Raspbian comes preinstalled with versions 2.7 and 3.5. You will
therefore need to build Python 3.6 yourself. Build instructions can be found for example
[here](https://medium.com/@isma3il/install-python-3-6-or-3-7-and-pip-on-raspberry-
pi-85e657aadb1e).

If you would like to use the GUI, you will also need to build PyQt5 since (for the time
being) there is no pre-built wheel for arm64. Due to the low amount of memory on a Pi 3,
the build will fail unless you temporary disable the Desktop GUI. To do that, run `sudo
raspi-config`, select `Boot Options`, `Desktop/CLI` and then choose the second option.
After rebooting the system, you should have enough memory to build `PyQt5`. So just run

```console
$ python3.6 -m pip install --upgrade maestral[gui]
```

Building `PyQt5` may take _a couple of hours_. If the build keeps failing for low
memory, you might need to temporary stop some services running in the background (like
webserver, squid, etc). After everything is finished successfully, run the `raspi-
config` again, reboot and enjoy your Maestral installation!

Currently, the "Start on login" option of the GUI does not support the Raspbian Desktop.
If you want the GUI to run on startup, first make an executable sh file containing
`maestral gui`. Then edit `/home/pi/.config/lxsession/LXDE-pi/autostart` and add to a
new line @ followed by the sh file path.
