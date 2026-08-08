# -*- coding: utf-8 -*-
"""
build.py — 桌宠一键打包脚本
============================

一条命令完成：生成图标 → 生成版本资源 → PyInstaller 打包。

用法：
  python build.py

产出：
  dist/糊宠.exe   （图标、属性、任务栏图标全部就位）

换图标流程：
  1. 把你的图片覆盖 assets/source_icon.png（任意 PNG/JPG，建议≥256x256）
  2. python build.py
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ICON_SOURCE = os.path.join(ROOT, "assets", "source_icon.png")
ICON_OUTPUT = os.path.join(ROOT, "assets", "app.ico")
VERSION_FILE = os.path.join(ROOT, "version_info.txt")
ENTRY = os.path.join(ROOT, "desktop_pet.py")
APP_NAME = "糊宠"               # EXE 文件名
APP_TITLE = "糊宠"              # 显示名（属性面板里的"产品名"）
COMPANY = "红烧茄子"

# 与 desktop_pet.py 中 APP_VERSION 同步：自动读取，无需手动改两处
EXCLUDE_MODULES = [
    "PyQt5.Qt3DCore", "PyQt5.Qt3DRender", "PyQt5.Qt3DAnimation",
    "PyQt5.Qt3DInput", "PyQt5.Qt3DLogic", "PyQt5.QtQuick",
    "PyQt5.QtQml", "PyQt5.QtSql", "PyQt5.QtMultimedia",
    "PyQt5.QtWebEngineWidgets",
]


def _read_app_version() -> str:
    """从 desktop_pet.py 里读出 APP_VERSION，避免手动维护两份版本号。"""
    with open(ENTRY, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r'\s*APP_VERSION\s*=\s*["\']([^"\']+)["\']', line)
            if m:
                return m.group(1)
    print("[警告] 未在 desktop_pet.py 找到 APP_VERSION，默认用 1.0.0")
    return "1.0.0"


def _version_tuple(ver: str):
    """'1.2.3' -> (1, 2, 3, 0)，PyInstaller 版本资源要求 4 段数字。"""
    parts = [int(x) for x in re.findall(r"\d+", ver)]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def gen_icon():
    """调用 build_icon 生成多尺寸 ico。"""
    from build_icon import build_icon
    print("\n========== [1/3] 生成图标 ==========")
    build_icon(ICON_SOURCE, ICON_OUTPUT)


def gen_version_info(version: str):
    """生成 PyInstaller 版本资源文件（右键属性→详细信息里的字段）。"""
    print("\n========== [2/3] 生成版本资源 ==========")
    v = _version_tuple(version)
    content = f"""# UTF-8
#
# PyInstaller 版本资源文件（由 build.py 自动生成，请勿手动编辑）
# 这些字段会出现在 EXE 右键 → 属性 → 详细信息 面板。

VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={v},
    prodvers={v},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,       # NT_WINDOWS32
    fileType=0x1,     # VFT_APP
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',   # 0x0409(英文) + 0x04B0(Unicode)
        [
          StringStruct(u'CompanyName', u'{COMPANY}'),
          StringStruct(u'FileDescription', u'{APP_TITLE}'),
          StringStruct(u'FileVersion', u'{version}'),
          StringStruct(u'InternalName', u'{APP_NAME}'),
          StringStruct(u'OriginalFilename', u'{APP_NAME}.exe'),
          StringStruct(u'ProductName', u'{APP_TITLE}'),
          StringStruct(u'ProductVersion', u'{version}'),
        ]
      ),
    ]),
    VarFileInfo([VarStruct(u'Translation', [0x0409, 1200])]),
  ]
)
"""
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] 版本资源：{VERSION_FILE}（产品名：{APP_TITLE}，版本：{version}）")


def run_pyinstaller(version: str):
    """执行 PyInstaller 打包。"""
    print("\n========== [3/3] PyInstaller 打包 ==========")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", APP_NAME,
        "--icon", ICON_OUTPUT,
        "--version-file", VERSION_FILE,
        # ico 内置到 EXE，运行时 resource_path("app.ico") 能取到
        "--add-data", f"{ICON_OUTPUT}{os.pathsep}app.ico",
        # 道具图标内置（状态栏 <img> 显示用）
        "--add-data", f"assets{os.pathsep}assets",
        "--add-data", f"skins{os.pathsep}skins",
        ENTRY,
    ]
    for mod in EXCLUDE_MODULES:
        cmd += ["--exclude-module", mod]

    print("运行命令：")
    print("  " + " ".join(cmd))
    print()
    # 直接继承当前 stdout，PyInstaller 进度条原样可见
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print(f"\n[失败] PyInstaller 退出码 {result.returncode}")
        sys.exit(result.returncode)

    exe = os.path.join(ROOT, "dist", f"{APP_NAME}.exe")
    if os.path.isfile(exe):
        print(f"\n[完成] 产出：{exe}")
        print(f"       版本：{version}　产品名：{APP_TITLE}")
    else:
        print(f"\n[失败] 未找到产出 EXE：{exe}")
        sys.exit(1)


def main():
    version = _read_app_version()
    print(f"准备打包 {APP_TITLE} v{version}")
    gen_icon()
    gen_version_info(version)
    run_pyinstaller(version)


if __name__ == "__main__":
    main()
