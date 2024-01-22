---
title: Notes on Raspberry Pi
permalink: /docs/raspberry-pi
---

Maestral has been shown to run on Raspberry Pi, but the installation process is more
involved if you want to install the GUI in addition the daemon and CLI.

When installing the GUI, you will need to build PyQt6 since (at the time of writing)
there is no pre-built wheel for arm64. Due to the low amount of memory on a Pi 3, the
build will fail unless you temporary disable the Desktop GUI. To do that, run `sudo
raspi-config`, select `Boot Options`, `Desktop/CLI` and then choose the second option.
After rebooting the system, you should have enough memory to build `PyQt6`. So just run

```console
$ python3 -m pip install --upgrade maestral[gui]
```

Building `PyQt6` may take _a couple of hours_. If the build keeps failing for low
memory, you might need to temporary stop some services running in the background (like
webserver, squid, etc). After everything is finished successfully, run the `raspi-
config` again, reboot and enjoy your Maestral installation!

Currently, the "Start on login" option of the GUI does not support the Raspbian Desktop.
If you want the GUI to run on startup, first make an executable sh file containing
`maestral gui`. Then edit `/home/pi/.config/lxsession/LXDE-pi/autostart` and add to a
new line @ followed by the sh file path.
