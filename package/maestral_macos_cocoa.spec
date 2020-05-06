# -*- mode: python ; coding: utf-8 -*-

block_cipher = None


import time
import pkg_resources as pkgr
from maestral import __version__, __author__


try:
    with open('bundle_version_macos.txt', 'r') as f:
        bundle_version = str(int(f.read()) + 1)
except FileNotFoundError:
    bundle_version = '1'

with open('bundle_version_macos.txt', 'w') as f:
    f.write(bundle_version)


def Entrypoint(dist, group, name, **kwargs):

    packages = []

    kwargs.setdefault('pathex', [])
    # get the entry point
    ep = pkgr.get_entry_info(dist, group, name)
    # insert path of the egg at the verify front of the search path
    kwargs['pathex'] = [ep.dist.location] + kwargs['pathex']
    # script name must not be a valid module name to avoid name clashes on import
    script_path = os.path.join(workpath, name + '-script.py')
    print("creating script for entry point", dist, group, name)
    with open(script_path, 'w') as fh:
        print("import", ep.module_name, file=fh)
        print("%s.%s()" % (ep.module_name, '.'.join(ep.attrs)), file=fh)
        for package in packages:
            print("import", package, file=fh)

    return Analysis(
        [script_path] + kwargs.get('scripts', []),
        **kwargs
    )


a = Entrypoint(
    'maestral_cocoa', 'console_scripts', 'maestral_cocoa',
    binaries=None,
    datas= [
        (pkgr.resource_filename('maestral_cocoa', 'resources/*.icns'), 'maestral_cocoa/resources'),
        (pkgr.resource_filename('maestral_cocoa', 'resources/*.pdf'), 'maestral_cocoa/resources'),
        (pkgr.resource_filename('maestral', 'resources/*.plist'), 'maestral/resources'),
        (pkgr.resource_filename('maestral', 'resources/*.desktop'), 'maestral/resources'),
        (pkgr.resource_filename('maestral', 'resources/*.png'), 'maestral/resources'),
        (pkgr.resource_filename('maestral', 'resources/*.service'), 'maestral/resources'),
    ],
    hiddenimports=['pkg_resources.py2_warn'],
    hookspath=['hooks'],
    runtime_hooks=[],
    excludes=['_tkinter'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)

pyz = PYZ(
    a.pure, a.zipped_data,
    cipher=block_cipher
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='main'
)

app = BUNDLE(
    coll,
    name='Maestral.app',
    icon=pkgr.resource_filename('maestral_cocoa', 'resources/maestral.icns'),
    bundle_identifier='com.samschott.maestral',
    info_plist={
        'NSHighResolutionCapable': 'True',
        'LSUIElement': '1',
        'CFBundleVersion': bundle_version,
        'CFBundleShortVersionString': __version__,
        'NSHumanReadableCopyright': 'Copyright © {} {}. All rights reserved.'.format(time.strftime('%Y'), __author__),
        'LSMinimumSystemVersion': '10.13.0',
    },
)
