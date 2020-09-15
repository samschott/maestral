#!/usr/bin/env bash

SPEC_FILE=maestral_macos.spec
BUILD_NO=$(grep -E -o "[0-9]*" bundle_version_macos.txt)

if [ "$1" = "--dev" ]; then
    BRANCH="develop"
else
    BRANCH="master"
fi

export MACOSX_DEPLOYMENT_TARGET=10.13
export CFLAGS=-mmacosx-version-min=10.13
export CPPFLAGS=-mmacosx-version-min=10.13
export LDFLAGS=-mmacosx-version-min=10.13
export LINKFLAGS=-mmacosx-version-min=10.13

echo "**** INSTALLING DEPENDENCIES ***************************"

git clone https://github.com/pyinstaller/pyinstaller.git build/pyinstaller
cd build/pyinstaller
git checkout master
git pull
git apply ../../patch/pyinstaller_macos_11.patch
cd bootloader
python3 ./waf all
cd ..
python3 -m pip install .
cd ../..

git clone https://github.com/samschott/maestral build/maestral
cd build/maestral
git checkout $BRANCH
git pull
python3 -m pip install .
cd ../..

git clone https://github.com/samschott/maestral-cocoa build/maestral-cocoa
cd build/maestral-cocoa
git checkout $BRANCH
git pull
python3 -m pip install .
cd ../..

echo "**** BUILD NUMBER $BUILD_NO ****************************"

python3 -m PyInstaller  -y --clean -w $SPEC_FILE

echo "**** COPY CLI ENTRY POINT ******************************"

cp bin/maestral_cli dist/Maestral.app/Contents/MacOS/maestral_cli

echo "**** SIGNING APP ***************************************"

echo "removing xattr"
xattr -cr dist/Maestral.app

echo "signing app"
codesign -s "Developer ID Application: Sam Schott" \
  --entitlements entitlements.plist --deep -o runtime dist/Maestral.app

echo "**** CREATING DMG **************************************"

test -f dist/Maestral.dmg && rm dist/Maestral.dmg

create-dmg \
  --volname "Maestral" \
  --window-size 300 150 \
  --icon-size 64 \
  --text-size 11 \
  --icon "Maestral.app" 75 75 \
  --app-drop-link 225 75 \
  "dist/Maestral.dmg" \
  "dist/Maestral.app"

echo "signing dmg"
codesign --verify --sign "Developer ID Application: Sam Schott" dist/Maestral.dmg



if ! [ "$1" = "--dev" ]; then
    echo "**** NOTARISING DMG ************************************"
    ./macos-notarize-dmg.sh dist/Maestral.dmg
fi

echo "**** DONE **********************************************"
