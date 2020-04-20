#!/usr/local/bin/python3

import shutil
from pathlib import Path


bundle_path = Path(__file__).parent / Path('dist/Maestral.app/Contents/macOS')

items_to_remove = [
    'QtQml',
    'QtQuick',
    'QtNetwork',
    'QtWebSockets',
    'QtQmlModels',
    'PyQt5/Qt/translations',
    'PyQt5/Qt/plugins/imageformats/libqgif.dylib',
    'PyQt5/Qt/plugins/imageformats/libqtiff.dylib',
    'PyQt5/Qt/plugins/imageformats/libqwebp.dylib',
    'PyQt5/Qt/plugins/platforms/libqwebgl.dylib',
    'PyQt5/Qt/plugins/platforms/libqoffscreen.dylib',
    'PyQt5/Qt/plugins/platforms/libqminimal.dylib',
    'libsqlite3.0.dylib',
]

print("Removing unneeded Qt modules...")

for path in items_to_remove:
    lib_path = bundle_path / path
    if lib_path.is_file():
        lib_path.unlink()
    elif lib_path.is_dir():
        shutil.rmtree(lib_path)

print("Done.")
