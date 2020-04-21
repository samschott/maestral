#!/usr/bin/env bash

SPEC_FILE=maestral_linux.spec

echo "**** INSTALLING DEPENDENCIES ****************************"

pip install -U pyinstaller

git clone https://github.com/samschott/maestral-dropbox build/maestral-dropbox
cd build/maestral-dropbox
git checkout develop
pip install .
cd ../..

git clone https://github.com/samschott/maestral-cocoa build/maestral-cocoa
cd build/maestral-cocoa
git checkout develop
pip install .
cd ../..

echo "**** BUILDING *******************************************"

python3 -m PyInstaller  -y --clean -w $SPEC_FILE

echo "**** RUNNING POST-BUILD SCRIPTS *************************"

# pass

echo "**** SIGNING ********************************************"

# todo

echo "**** DONE ***********************************************"
