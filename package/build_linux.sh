#!/usr/bin/env bash

SPEC_FILE=maestral_linux.spec

echo "**** BUILDing *****************************************"

python3 -OO -m PyInstaller  -y --clean -w $SPEC_FILE

echo "**** SIGNING ******************************************"

# todo

echo "**** DONE *********************************************"
