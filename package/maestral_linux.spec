# -*- mode: python ; coding: utf-8 -*-

import pkg_resources as pkgr


block_cipher = None


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
    'maestral', 'console_scripts', 'maestral',
    binaries=[],
    datas= [
        (pkgr.resource_filename('maestral_qt', 'resources/tray-icons-svg/*.svg'), './tray-icons-svg'),
        (pkgr.resource_filename('maestral_qt', 'resources/tray-icons-png/*.png'), './tray-icons-png'),
        (pkgr.resource_filename('maestral_qt', 'resources/*'), '.'),
        (pkgr.resource_filename('maestral', 'resources/*'), '.'),
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='maestral',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True
)

