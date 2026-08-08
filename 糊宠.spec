# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['D:\\ZCodeProject\\desktop_pet\\desktop_pet.py'],
    pathex=[],
    binaries=[],
    datas=[('D:\\ZCodeProject\\desktop_pet\\assets\\app.ico', 'app.ico'), ('assets', 'assets'), ('skins', 'skins')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5.Qt3DCore', 'PyQt5.Qt3DRender', 'PyQt5.Qt3DAnimation', 'PyQt5.Qt3DInput', 'PyQt5.Qt3DLogic', 'PyQt5.QtQuick', 'PyQt5.QtQml', 'PyQt5.QtSql', 'PyQt5.QtMultimedia', 'PyQt5.QtWebEngineWidgets'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='糊宠',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='D:\\ZCodeProject\\desktop_pet\\version_info.txt',
    icon=['D:\\ZCodeProject\\desktop_pet\\assets\\app.ico'],
)
