#!/usr/bin/env bash

# Flags:
# --dev: build from dev branch instead of master
# --clean: clean build cache and donwnload from github

stringContain() { [ -z "$1" ] || { [ -z "${2##*$1*}" ] && [ -n "$2" ];};}

ARGS="$@"
SPEC_FILE=maestral_linux.spec

if stringContain "--dev" "$ARGS"; then
    BRANCH="develop"
else
    BRANCH="master"
fi

echo "**** INSTALLING DEPENDENCIES ****************************"

if stringContain "--clean" "$ARGS"; then
    rm -r -f build
    mkdir build
fi

python3 -m pip install -U pyinstaller

git clone https://github.com/samschott/maestral build/maestral
cd build/maestral
git checkout $BRANCH
git pull
python3 -m pip install .
cd ../..

git clone https://github.com/samschott/maestral-qt build/maestral-qt
cd build/maestral-qt
git checkout $BRANCH
git pull
python3 -m pip install .
cd ../..

echo "**** BUILDING *******************************************"

python3 -m PyInstaller  -y --clean -w $SPEC_FILE

echo "**** RUNNING POST-BUILD SCRIPTS *************************"

# pass

echo "**** SIGNING ********************************************"

# todo

echo "**** DONE ***********************************************"
