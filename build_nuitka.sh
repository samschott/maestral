#!/usr/bin/env bash

python3 -OO -m nuitka --follow-imports --standalone --enable-plugin=qt-plugins=sensible,styles --plugin-enable=multiprocessing --lto maestral/gui/main.py

# python3 post_build.py
