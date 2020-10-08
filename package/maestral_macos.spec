# -*- mode: python ; coding: utf-8 -*-

block_cipher = None


import os
import time
import pkg_resources as pkgr
from maestral import __version__, __author__


def Entrypoint(dist, group, name, **kwargs):

    packages = []

    kwargs.setdefault("pathex", [])
    # get the entry point
    ep = pkgr.get_entry_info(dist, group, name)
    # insert path of the egg at the verify front of the search path
    kwargs["pathex"] = [ep.dist.location] + kwargs["pathex"]
    # script name must not be a valid module name to avoid name clashes on import
    script_path = os.path.join(workpath, name + "-script.py")
    print("creating script for entry point", dist, group, name)
    with open(script_path, "w") as fh:
        print("import", ep.module_name, file=fh)
        print("%s.%s()" % (ep.module_name, ".".join(ep.attrs)), file=fh)
        for package in packages:
            print("import", package, file=fh)

    return Analysis([script_path] + kwargs.get("scripts", []), **kwargs)


a = Entrypoint(
    "maestral_cocoa",
    "console_scripts",
    "maestral_cocoa",
    binaries=None,
    datas=[
        (pkgr.resource_filename("maestral_cocoa", "resources/*.icns"), "."),
        (pkgr.resource_filename("maestral_cocoa", "resources/*.pdf"), "."),
        (pkgr.resource_filename("maestral", "resources/*"), "."),
    ],
    hiddenimports=["pkg_resources.py2_warn", "alembic"],
    hookspath=["hooks"],
    runtime_hooks=[],
    excludes=["_tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Maestral",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="main",
)

app = BUNDLE(
    coll,
    name="Maestral.app",
    icon=pkgr.resource_filename("maestral_cocoa", "resources/maestral.icns"),
    bundle_identifier="com.samschott.maestral",
    info_plist={
        "NSHighResolutionCapable": "True",
        "LSUIElement": "1",
        "CFBundleExecutable": "Maestral",
        "CFBundleVersion": os.environ.get("BUNDLE_VERSION", "1"),
        "CFBundleShortVersionString": __version__,
        "NSHumanReadableCopyright": "Copyright © {} {}. All rights reserved.".format(
            time.strftime("%Y"), __author__
        ),
        "LSMinimumSystemVersion": "10.13.0",
    },
)
