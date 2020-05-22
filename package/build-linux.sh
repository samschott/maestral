#!/usr/bin/env bash

SPEC_FILE=maestral_linux.spec

if [ "$1" = "--dev" ]; then
    BRANCH="develop"
else
    BRANCH="master"
fi

echo "**** INSTALLING DEPENDENCIES ****************************"

pip install -U pyinstaller

git clone https://github.com/samschott/maestral build/maestral
cd build/maestral
git checkout $BRANCH
git pull
pip install .
cd ../..

git clone https://github.com/samschott/maestral-qt build/maestral-qt
cd build/maestral-qt
git checkout $BRANCH
git pull
pip install .
cd ../..

echo "**** BUILDING *******************************************"

python3 -m PyInstaller  -y --clean -w $SPEC_FILE

echo "**** RUNNING POST-BUILD SCRIPTS *************************"

# pass

echo "**** SIGNING ********************************************"

# todo

echo "**** DONE ***********************************************"
