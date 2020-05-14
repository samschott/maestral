#!/usr/bin/env bash

SPEC_FILE=maestral_macos_cocoa.spec
BUILD_NO=$(grep -E -o "[0-9]*" bundle_version_macos.txt)

echo "**** INSTALLING DEPENDENCIES ***************************"

git clone https://github.com/pyinstaller/pyinstaller.git build/pyinstaller
cd build/pyinstaller
git checkout master
git pull
cd bootloader
export MACOSX_DEPLOYMENT_TARGET=10.13
export CFLAGS=-mmacosx-version-min=10.13
export CPPFLAGS=-mmacosx-version-min=10.13
export LDFLAGS=-mmacosx-version-min=10.13
export LINKFLAGS=-mmacosx-version-min=10.13
python ./waf all
cd ..
pip install .
cd ../..

git clone https://github.com/samschott/maestral build/maestral
cd build/maestral
git checkout develop
git pull
pip install .
cd ../..

git clone https://github.com/samschott/maestral-cocoa build/maestral-cocoa
cd build/maestral-cocoa
git checkout develop
git pull
pip install .
cd ../..

echo "**** BUILD NUMBER $BUILD_NO ****************************"

python3 -m PyInstaller  -y --clean -w $SPEC_FILE

echo "**** COPY CLI ENTRY POINT ******************************"

cp bin/maestral_cli dist/Maestral.app/Contents/MacOS/maestral_cli

echo "**** SIGNING *******************************************"

codesign -s "Apple Development: sam.schott@outlook.com (FJNXBRUVWL)" \
  --entitlements entitlements.plist --deep -o runtime dist/Maestral.app

echo "**** CREATING DMG **************************************"

test -f dist/dmg-folder && rm -Rf dist/dmg-folder
mkdir dist/dmg-folder
cd dist/dmg-folder
ln -s /Applications
cd ..
cd ..
cp -R dist/Maestral.app dist/dmg-folder/
hdiutil create -volname "Maestral" \
  -srcfolder dist/dmg-folder -ov -format UDBZ dist/Maestral.dmg
rm -Rf dist/dmg-folder

codesign --verify --sign "Apple Development: sam.schott@outlook.com (FJNXBRUVWL)" dist/Maestral.dmg
md5 -r dist/Maestral.dmg

echo "**** DONE **********************************************"
