# -*- coding: utf-8 -*-
"""
publish.py — 桌宠发布助手（E6 发布脚本化）
==========================================

职责：在 build.py 打包出 dist/糊宠.exe 之后，辅助作者完成"发版清单"。
本脚本 **不自动调 GitHub API**、**不自动 git push**（避免引入 token 管理
和误操作风险），只做两件事：
  1. 计算 EXE 的 SHA256
  2. 生成 version.json.draft（不覆盖 version.json）+ 打印发布清单

用法：
  python build.py        # 先打包
  python publish.py      # 再生成 draft

产出：
  · version.json.draft   （含 sha256 / download_urls，给作者过目后覆盖 version.json）
  · 终端打印 4 步发布清单

依赖：仅 Python 标准库（hashlib / json / re），不引入新依赖。
"""

import hashlib
import json
import os
import re
import sys
import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(ROOT, "desktop_pet.py")
VERSION_JSON = os.path.join(ROOT, "version.json")
DRAFT_PATH = os.path.join(ROOT, "version.json.draft")
EXE_PATH = os.path.join(ROOT, "dist", "糊宠.exe")

# E4 默认镜像模板（PM 约束 3：第 0 项是 GitHub 权威源）
# {owner}/{repo}/{exe} 从现有 version.json 推断，不硬编码
DEFAULT_MIRROR_TEMPLATES = [
    "https://github.com/{owner}/{repo}/releases/download/v{ver}/{exe}",
    "https://gh-proxy.com/https://github.com/{owner}/{repo}/releases/download/v{ver}/{exe}",
    "https://ghfast.top/https://github.com/{owner}/{repo}/releases/download/v{ver}/{exe}",
]


def _read_app_version() -> str:
    """从 desktop_pet.py 读 APP_VERSION（与 build.py 同实现，避免循环依赖）。"""
    with open(ENTRY, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r'\s*APP_VERSION\s*=\s*["\']([^"\']+)["\']', line)
            if m:
                return m.group(1)
    print("[警告] 未在 desktop_pet.py 找到 APP_VERSION")
    return None


def _compute_sha256(path):
    """流式计算大文件 SHA256（避免一次性读 80MB EXE 进内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(64 * 1024)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _infer_owner_repo_exe(version_json_path):
    """从现有 version.json 推断 owner/repo/exe，避免硬编码。
    解析 download_url 里的 GitHub Release 模板：
      https://github.com/{owner}/{repo}/releases/download/vX.Y.Z/{exe}
    """
    try:
        with open(version_json_path, "r", encoding="utf-8") as f:
            vj = json.load(f)
    except Exception:
        return None, None, None
    url = vj.get("download_url") or (vj.get("download_urls") or [""])[0]
    # 严格匹配 GitHub Release URL
    m = re.search(
        r"https?://github\.com/([^/]+)/([^/]+)/releases/download/v?[\d.]+/(.+)$",
        url,
    )
    if not m:
        return None, None, None
    return m.group(1), m.group(2), m.group(3)


def _read_existing_changelog(version_json_path):
    """保留现有 changelog / update_date，作者改起来更轻松。"""
    try:
        with open(version_json_path, "r", encoding="utf-8") as f:
            vj = json.load(f)
        return vj.get("changelog", ""), vj.get("update_date", "")
    except Exception:
        return "", ""


def main():
    print("=" * 60)
    print("publish.py — 糊宠发布助手")
    print("=" * 60)

    # 1) 校验 EXE 存在
    if not os.path.isfile(EXE_PATH):
        print(f"\n[失败] 未找到 EXE：{EXE_PATH}")
        print("请先运行：python build.py")
        sys.exit(1)
    exe_size = os.path.getsize(EXE_PATH)
    print(f"\n[1/3] 找到 EXE：{EXE_PATH}（{exe_size:,} bytes ≈ {exe_size/1024/1024:.1f} MB）")

    # 2) 算 SHA256
    print("\n[2/3] 计算 SHA256 ...")
    sha = _compute_sha256(EXE_PATH)
    print(f"      sha256 = {sha}")

    # 3) 读 APP_VERSION + 推断 owner/repo/exe
    version = _read_app_version()
    if not version:
        print("\n[失败] 无法读取 APP_VERSION")
        sys.exit(1)
    owner, repo, exe_name = _infer_owner_repo_exe(VERSION_JSON)
    if not owner:
        # 兜底：用现有数据里的占位（默认 GitHub 账号）
        print("\n[警告] 无法从 version.json 推断 owner/repo/exe，请在 draft 里手动改")
        owner, repo, exe_name = "HannahZ-Mulan", "DesktopPet_YMJH", "糊宠.exe"
    print(f"      version = {version}")
    print(f"      owner/repo/exe = {owner}/{repo}/{exe_name}")

    # 4) 生成 download_urls
    mirror_urls = [
        tpl.format(owner=owner, repo=repo, ver=version, exe=exe_name)
        for tpl in DEFAULT_MIRROR_TEMPLATES
    ]

    # 5) 保留旧 changelog + 自动 update_date
    old_changelog, _ = _read_existing_changelog(VERSION_JSON)
    today = datetime.date.today().strftime("%Y-%m-%d")

    draft = {
        "version": version,
        "update_date": today,
        "download_url": mirror_urls[0],   # v1 兼容：单值 = 列表第 0 项
        "download_urls": mirror_urls,
        "sha256": sha,
        "changelog": old_changelog or "（请作者填写本次更新说明）",
    }

    # 6) 写 draft（不覆盖 version.json）
    with open(DRAFT_PATH, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)
    print(f"\n[3/3] 已生成 draft：{DRAFT_PATH}")
    print("      （未覆盖 version.json，请作者过目后再覆盖）")

    # 7) 打印发布清单
    print("\n" + "=" * 60)
    print("发布清单（请按顺序手动执行）")
    print("=" * 60)
    print(f"""
[1] 上传 EXE 到 GitHub Release
    · 标签：v{version}
    · 文件：{EXE_PATH}
    · 页面：https://github.com/{owner}/{repo}/releases/new

[2] 确认 version.json.draft 无误后覆盖 version.json
    · 重点是 sha256（已自动计算）和 download_urls（默认 3 个镜像，可增减）
    · 命令：copy /Y version.json.draft version.json  （Windows）
            cp version.json.draft version.json        （其他）

[3] git 提交并推送
    git add version.json
    git commit -m "release v{version}"
    git push
    git tag v{version}
    git push origin v{version}

[4] 在群内/社区通知网友更新
    模板：糊宠 v{version} 已发布。{old_changelog or ''}
    启动旧版桌宠后会自动检测到新版本。

sha256（用于核对）：{sha}
""")
    print("[完成] publish.py 输出完毕。下一步是上面 [1]-[4] 手动步骤。")


if __name__ == "__main__":
    main()
