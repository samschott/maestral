#!/usr/bin/env bash

SPEC_FILE=pyinstaller_macos.spec

OLD_BUILD=$(grep -E -o "'CFBundleVersion': '[!0-9]*'," $SPEC_FILE | grep -E -o '[0-9]+')
NEW_BUILD=$(($OLD_BUILD+1))

OLD="'CFBundleVersion': '$OLD_BUILD',"
NEW="'CFBundleVersion': '$NEW_BUILD',"

echo "**** INCREMENTING BUILD NUMBER TO $NEW_BUILD ***********"

sed -i "" "s/$OLD/$NEW/g" $SPEC_FILE

echo "**** BUILDING ******************************************"

python3 -OO -m PyInstaller  -y --clean -w $SPEC_FILE

echo "**** REMOVING UNNEEDED MODULES *************************"

python3 post_build.py

echo "**** MOVING COCOA LIBS *********************************"

mv dist/Maestral.app/Contents/MacOS/Foundation/_Foundation.cpython-37m-darwin.so dist/Maestral.app/Contents/MacOS/Foundation.so
mv dist/Maestral.app/Contents/MacOS/CoreFoundation/_CoreFoundation.cpython-37m-darwin.so dist/Maestral.app/Contents/MacOS/CoreFoundation.so
mv dist/Maestral.app/Contents/MacOS/AppKit/_AppKit.cpython-37m-darwin.so dist/Maestral.app/Contents/MacOS/AppKit.so

rm -R -f dist/Maestral.app/Contents/MacOS/Foundation/
rm -R -f dist/Maestral.app/Contents/MacOS/CoreFoundation/
rm -R -f dist/Maestral.app/Contents/MacOS/AppKit/

echo "**** SIGNING ******************************************"

codesign -s "Apple Development: sam.schott@outlook.com (FJNXBRUVWL)" --deep dist/Maestral.app

echo "**** DONE *********************************************"
