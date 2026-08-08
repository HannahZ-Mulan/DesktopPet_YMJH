# -*- coding: utf-8 -*-
"""
build_icon.py — 把一张 PNG/JPG 自动转成圆角白底多尺寸 Windows 图标 (.ico)
==========================================================================

图标样式：
  圆角方形纯白卡片 + 角色居中放大（仿 iOS/现代应用图标风格）。
  - 智能裁剪：按角色实际包围盒（getbbox）紧凑裁掉四周透明留白，
    再补成正方形，角色天然撑满、居中，不会偏移或偏小。
  - 角色占卡片的比例由 CHARACTER_RATIO 控制（默认 0.92 = 撑满 92%）。
  - 圆角半径由 CARD_RADIUS_RATIO 控制。
  - 适用任意形状的源图（透明背景最佳；非正方形会自动处理）。

为什么需要多尺寸：
  Windows 在不同场景取用不同尺寸 —— 资源管理器大图标 256px、
  任务栏 32px、小图标 16px。只塞一张大图，小尺寸会被系统缩放得发糊。
  本脚本把源图压成 256/128/64/48/32/16 六个尺寸，嵌进同一个 .ico。

用法：
  python build_icon.py                  # 默认读 assets/source_icon.png
  python build_icon.py 我的图.png       # 指定其它源图
  python build_icon.py 我的图.png out.ico

换图标流程：
  1. 把你的图片覆盖 assets/source_icon.png（任意 PNG/JPG，建议≥256x256）
  2. python build_icon.py
  3. python build.py    # 重新打包 EXE
"""

import os
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("[错误] 缺少 Pillow，请先安装：  pip install Pillow")
    raise

# 输出 ico 内嵌的所有尺寸（从大到小）
ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]

# —— 圆角白底卡片样式 ——
CARD_BG = (255, 255, 255, 255)      # 卡片底色：纯白不透明
CARD_RADIUS_RATIO = 0.22            # 圆角半径 / 边长（≈iOS 图标圆角比例）
# 角色在卡片里的占比：0.92 = 撑满卡片 92%，四周各留 4% 白边。
# 调大→角色更大（更接近边缘）；调小→留白更多。
CHARACTER_RATIO = 0.92

# 项目根目录（本脚本所在目录）
ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SOURCE = os.path.join(ROOT, "assets", "source_icon.png")
DEFAULT_OUTPUT = os.path.join(ROOT, "assets", "app.ico")


def _round_corner_mask(size: int, radius: int) -> "Image.Image":
    """生成圆角矩形的 alpha 蒙版（白色区域=保留，透明=裁掉）。"""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def _make_card(source_path: str, target: int) -> "Image.Image":
    """
    读源图 → 提取角色 → 合成到圆角白底卡片上。

    关键：用 getbbox() 按"角色实际包围盒"紧凑裁剪，而不是盲裁整图中心正方形。
    这样角色天然撑满裁剪区、居中，避免源图大量留白导致角色在图标里偏小偏移。
    """
    img = Image.open(source_path).convert("RGBA")
    w, h = img.size

    # 1) 按角色实际非透明区域裁剪（紧凑包围盒，去掉四周空透明像素）
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
        w, h = img.size

    # 2) 把裁剪区补成正方形（以角色中心为基准，短边方向补透明），
    #    保证后续等比缩放不变形；用 max 维度做边长，角色完全保留。
    side = max(w, h)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((side - w) // 2, (side - h) // 2), img)
    img = square

    # 3) 缩放到目标"角色区"尺寸（CHARACTER_RATIO 控制占卡片比例）
    char_size = max(1, int(target * CHARACTER_RATIO))
    img = img.resize((char_size, char_size), Image.LANCZOS)

    # 3) 画白底卡片，再把角色居中贴上去
    card = Image.new("RGBA", (target, target), CARD_BG)
    offset = (target - char_size) // 2
    card.paste(img, (offset, offset), img)   # 第三参用角色自身 alpha 当蒙版

    # 4) 应用圆角：用圆角蒙版裁掉卡片四角（透明，露出桌面）
    radius = max(1, int(target * CARD_RADIUS_RATIO))
    mask = _round_corner_mask(target, radius)
    rounded = Image.new("RGBA", (target, target), (0, 0, 0, 0))
    rounded.paste(card, (0, 0), mask)
    return rounded


def build_icon(source_path: str = DEFAULT_SOURCE,
               output_path: str = DEFAULT_OUTPUT) -> str:
    """生成多尺寸 ico，返回输出路径。"""
    if not os.path.isfile(source_path):
        raise FileNotFoundError(
            f"找不到图标源图：{source_path}\n"
            f"请把你的图片放到 assets/source_icon.png（任意 PNG/JPG）。"
        )

    # 256 是 Windows 现代图标的"母尺寸"，以此为基础生成其余小尺寸。
    # PIL 的 ico 保存：给 sizes=ICO_SIZES，会自动从 base 重新采样生成各尺寸。
    base = _make_card(source_path, 256)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    base.save(
        output_path,
        format="ICO",
        sizes=ICO_SIZES,
    )

    # 友好提示：列出实际写入的尺寸
    print(f"[OK] 已生成多尺寸图标：{output_path}")
    print(f"     源图：{source_path}")
    print(f"     内嵌尺寸：{', '.join(f'{w}x{h}' for w, h in ICO_SIZES)}")
    return output_path


def main():
    args = sys.argv[1:]
    source = args[0] if len(args) >= 1 else DEFAULT_SOURCE
    output = args[1] if len(args) >= 2 else DEFAULT_OUTPUT
    build_icon(source, output)


if __name__ == "__main__":
    main()
