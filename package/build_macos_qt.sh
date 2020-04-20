#!/usr/bin/env bash

SPEC_FILE=maestral_macos_qt.spec
BUILD_NO=$(grep -E -o "[0-9]*" bundle_version_macos.txt)

echo "**** BUILD NUMBER $BUILD_NO ****************************"

python3 -m PyInstaller  -y --clean -w $SPEC_FILE

echo "**** COPY ENTRY POINT **********************************"

cp bin/maestral_cli dist/Maestral.app/Contents/MacOS/maestral_cli

echo "**** RUNNING POST-BUILD SCRIPTS ************************"

python3 post_build_macos_qt.py

echo "**** SIGNING ******************************************"

codesign -s "Apple Development: sam.schott@outlook.com (FJNXBRUVWL)" --deep dist/Maestral.app

echo "**** DONE *********************************************"
