---
layout: single
title: Download
permalink: /download/
---

There are several ways to install Maestral. If you are running macOS, the easiest route is
download the signed and notarized app bundle. On other platforms, you can install the
Python package or a Docker image based on alpine Linux.

[macOS App Bundle](https://github.com/SamSchott/maestral/releases){: .btn .btn--warning} &nbsp; [PyPI](https://pypi.org/project/maestral/){: .btn .btn--primary} &nbsp; [Docker](https://hub.docker.com/r/maestraldbx/maestral){: .btn .btn--info}

Please refer to the [Documentation]({{ site.baseurl }}/docs/installation) for a
comprehensive guide to installing Maestral from PyPI or Docker Hub. The options compare as
follows:

| Package          | GUI included | Size        |
| :---             | :---         |        ---: |
| macOS App bundle | yes          | 33 MB       |
| PyPI             | optional     | 20 - 160 MB |
| Docker image     | no           | 87 MB       |

The install from PyPI will vary in size, depending on the platform. It will require about
20 MB on macOS, including all dependencies. For the Linux GUI, the largest dependency is
PyQt5 with the bundled Qt libraries at about 140 MB, bumping the total install size to
160 MB. Note that you may already have PyQt5 and other dependencies installed on you
system.

