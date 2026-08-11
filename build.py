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

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ICON_SOURCE = os.path.join(ROOT, "assets", "source_icon.png")
ICON_OUTPUT = os.path.join(ROOT, "assets", "app.ico")
VERSION_FILE = os.path.join(ROOT, "version_info.txt")
ENTRY = os.path.join(ROOT, "desktop_pet.py")
VERSION_JSON = os.path.join(ROOT, "version.json")
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


# ---------------------------------------------------------------------------
# E1：版本号一致性预检（打包前阻断"三处不一致"）
# 三处 = desktop_pet.APP_VERSION / version.json.version / download_url(s) 的 tag。
# git tag 是 Should（不可用就 warn，不 fail）。
# 任一硬断言不过 → sys.exit(1)，阻断打包。
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"/releases/download/(v?[\d.]+)/", re.IGNORECASE)


def _extract_release_tag(url: str):
    """从 GitHub Release URL 提取 tag 段，如 /releases/download/v1.1.0/... → v1.1.0
    无匹配返回 None。"""
    if not url:
        return None
    m = _TAG_RE.search(url)
    return m.group(1) if m else None


def _assert_version_consistent(app_version: str):
    """打包前预检：APP_VERSION 与 version.json / download_url(s) tag 必须一致。
    git tag 不可用时仅 warn。任一硬断言不过 sys.exit(1)。
    """
    print("\n========== [预检] 版本号一致性 ==========")
    errors = []

    # 1) 读 version.json
    try:
        with open(VERSION_JSON, "r", encoding="utf-8") as f:
            vj = json.load(f)
    except Exception as e:
        print(f"[失败] 无法读取 version.json：{e}")
        sys.exit(1)

    # 2) APP_VERSION == version.json.version
    json_ver = vj.get("version", "")
    if json_ver != app_version:
        errors.append(
            f"  · APP_VERSION({app_version}) != version.json.version({json_ver})"
        )

    # 3) download_url / download_urls 的 GitHub Release tag == APP_VERSION
    #    兼容两种 tag 写法：v1.1.0 或 1.1.0（统一比较数字部分）
    expected_num = app_version
    expected_tag_variants = {app_version, f"v{app_version}"}
    urls_to_check = []
    if vj.get("download_urls"):
        urls_to_check.extend(vj["download_urls"])
    if vj.get("download_url"):
        urls_to_check.append(vj["download_url"])
    # 仅校验 GitHub Release URL（加速镜像也含 /releases/download/vX.Y.Z/ 路径，会被同一条正则捕获）
    checked_url_count = 0
    for url in urls_to_check:
        if "releases/download" not in url:
            continue
        checked_url_count += 1
        tag = _extract_release_tag(url)
        if tag is None or tag.lstrip("v") != expected_num:
            errors.append(
                f"  · download_url tag({tag}) 与 APP_VERSION({app_version}) 不一致：{url}"
            )
    if checked_url_count == 0:
        # 没有任何 GitHub Release URL 也是问题（无权威源）
        errors.append("  · version.json 中没有任何 GitHub Release /releases/download/ URL")

    # 4) git tag（Should，不可用 warn 不 fail）
    try:
        res = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        if res.returncode == 0:
            git_tag = res.stdout.strip()
            if git_tag.lstrip("v") != expected_num:
                errors.append(
                    f"  · git describe --tags 最近 tag({git_tag}) 与 APP_VERSION({app_version}) 不一致"
                )
        # git 不可用 / 无 tag：不 fail（开发者环境可能没打 tag）
    except FileNotFoundError:
        print("[警告] 未找到 git，跳过 git tag 校验（不阻断）")
    except Exception as e:
        print(f"[警告] git tag 校验失败：{e}（不阻断）")

    # 结论
    if errors:
        print("[失败] 版本号不一致，已阻断打包：")
        for e in errors:
            print(e)
        print("\n修复提示：")
        print("  1) 改 desktop_pet.py 的 APP_VERSION")
        print("  2) 改 version.json 的 version + download_url(s) 的 tag")
        print("  3) 打标签：git tag vX.Y.Z")
        sys.exit(1)
    print(f"[OK] 版本号一致：{app_version}"
          + (f"（已校验 {checked_url_count} 个 Release URL）" if checked_url_count else ""))


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
    # E1：打包前先做版本号一致性预检（APP_VERSION / version.json / Release tag / git tag）
    _assert_version_consistent(version)
    gen_icon()
    gen_version_info(version)
    run_pyinstaller(version)
    # E6：发布流程提示（publish.py 是独立工具，由作者手动决定何时跑）
    print("\n下一步：python publish.py  生成 version.json.draft 并打印发布清单")


if __name__ == "__main__":
    main()
