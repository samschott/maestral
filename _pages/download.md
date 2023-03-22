---
layout: single
title: Download
permalink: /download/
---

There are several ways to install Maestral. If you are running macOS, the easiest route
is to download the signed and notarized app bundle. It will run natively on both Intel
Macs and Apple Silicon. On other platforms, you can install the Python package or a
Docker image based on Alpine Linux.

<p>
<a href="https://github.com/SamSchott/maestral/releases/latest" class="btn btn--small btn--warning"><i class="icon fab fa-apple"></i>App Bundle</a>
<a href="https://pypi.org/project/maestral/" class="btn btn--small btn--primary"><i class="icon fas fa-cubes"></i>PyPI</a>
<a href="https://hub.docker.com/r/maestraldbx/maestral" class="btn btn--small btn--info"><i class="icon fab fa-docker"></i>Docker</a>
</p>

Please refer to the [Documentation]({{ site.baseurl }}/docs/installation) for a
comprehensive guide to installing Maestral from PyPI or Docker Hub. The options compare as
follows:

| Package          | GUI included | Size        |
| :---             | :---         |        ---: |
| macOS App bundle | yes          | 65 MB       |
| PyPI             | optional     | 15 - 160 MB |
| Docker image     | no           | 75 MB       |

The install from PyPI will vary in size, depending on the platform. It will require about
15 MB on macOS, including all dependencies. For the Linux GUI, the largest dependency is
PyQt5 with the bundled Qt libraries at about 140 MB, bumping the total install size to
160 MB. Note that you may already have PyQt5 and other dependencies installed on you
system.
