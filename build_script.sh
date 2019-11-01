#!/usr/bin/env bash


OLD_BUILD=$(grep -E -o "'CFBundleVersion': '[!0-9]*'," maestral_macos.spec | grep -E -o '[0-9]+')
NEW_BUILD=$(($OLD_BUILD+1))

OLD="'CFBundleVersion': '$OLD_BUILD',"
NEW="'CFBundleVersion': '$NEW_BUILD',"

echo "*********** INCREMENTING BUILD NUMBER TO $NEW_BUILD ************"


sed -i "" "s/$OLD/$NEW/g" maestral_macos.spec

echo "********************* BUILDING ************************"

pyinstaller  -y --clean -w /Users/samschott/Documents/Python/maestral-dropbox/pyinstaller_macos.spec

echo "*************** REMOVING QML MODULES ******************"

python3 /Users/samschott/Documents/Python/maestral-dropbox/post_build.py

echo "********************** SIGNING ************************"

codesign -s "Mac Developer: sam.schott@outlook.com (FJNXBRUVWL)" --deep dist/Maestral.app

echo "*********************** DONE **************************"
