#!/usr/bin/env bash

SPEC_FILE=maestral_macos.spec
BUILD_NO=$(grep -E -o "[0-9]*" bundle_version_macos.txt)

echo "**** BUILD NUMBER $BUILD_NO ****************************"

python3 -m PyInstaller  -y --clean -w $SPEC_FILE

echo "**** COPY ENTRY POINT **********************************"

cp bin/maestral_gui dist/Maestral.app/Contents/MacOS/maestral_gui

echo "**** REMOVING UNNEEDED MODULES *************************"

python3 post_build_macos.py

echo "**** SIGNING ******************************************"

codesign -s "Apple Development: sam.schott@outlook.com (FJNXBRUVWL)" --deep dist/Maestral.app

echo "**** DONE *********************************************"
