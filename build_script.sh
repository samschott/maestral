#!/usr/bin/env bash

SPEC_FILE=pyinstaller_macos.spec

OLD_BUILD=$(grep -E -o "'CFBundleVersion': '[!0-9]*'," $SPEC_FILE | grep -E -o '[0-9]+')
NEW_BUILD=$(($OLD_BUILD+1))

OLD="'CFBundleVersion': '$OLD_BUILD',"
NEW="'CFBundleVersion': '$NEW_BUILD',"

echo "*********** INCREMENTING BUILD NUMBER TO $NEW_BUILD ************"

sed -i "" "s/$OLD/$NEW/g" $SPEC_FILE

echo "********************* BUILDING ************************"

pyinstaller  -y --clean -w /Users/samschott/Documents/Python/maestral-dropbox/$SPEC_FILE

echo "*************** REMOVING QML MODULES ******************"

python3 /Users/samschott/Documents/Python/maestral-dropbox/post_build.py

echo "********************** SIGNING ************************"

codesign -s "Apple Development: sam.schott@outlook.com (FJNXBRUVWL)" --deep dist/Maestral.app

echo "*********************** DONE **************************"
