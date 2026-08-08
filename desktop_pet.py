# -*- coding: utf-8 -*-
"""
糊宠 HuChong 2.0
========================
作者：红烧茄子

功能：
  · 自主生命感 —— 呼吸/眨眼/闲逛/睡觉/偷看
  · 物理玩具   —— 拖拽抛掷 + 重力弹跳 + 边界碰撞
  · 环境感知   —— 闲置检测 / 前台窗口识别 / 时间提醒（纯 ctypes，零依赖）
  · 换肤系统   —— skins/ 文件夹 + 右键菜单 + 运行时加图 + 配置持久化
  · 新奇玩法   —— 躲猫猫（边缘探头）+ 吃文件（拖放）

所有 Windows API 通过 ctypes 调用，不依赖 pywin32。
"""

import os
import sys
import math
import json
import time
import random
import datetime
import ctypes
# —— DPI 感知：必须在 PyQt5/QtGui 导入并创建 QApplication 之前声明。
#    否则高缩放屏幕(150%/200%)下，ctypes 拿到的窗口坐标(物理像素)与
#    Qt move()(逻辑像素)不一致，导致站窗口位置全错（跳到看不见的地方）。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE_V2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
import urllib.request
import urllib.error
import ssl
import subprocess
import tempfile
import shutil
from ctypes import wintypes

from PyQt5.QtCore import (
    Qt, QTimer, QPropertyAnimation, QPointF, QEasingCurve, QRectF, QSize,
    QThread, pyqtSignal
)
from PyQt5.QtGui import (
    QPixmap, QPainter, QColor, QFont, QFontMetrics, QTransform, QPen, QIcon
)
from PyQt5.QtWidgets import (
    QWidget, QApplication, QMenu, QAction, QGraphicsOpacityEffect,
    QFileDialog, QMessageBox, QProgressDialog, QLabel, QInputDialog,
    QWidgetAction, QGraphicsDropShadowEffect
)


# ---------------------------------------------------------------------------
# 版本与更新配置
# ---------------------------------------------------------------------------
APP_VERSION = "1.1.0"
# 版本检查文件 URL（放在 GitHub raw，国内通常可读；你可改用自己的仓库）
# 格式：https://raw.githubusercontent.com/用户名/仓库名/main/version.json
VERSION_CHECK_URL = (
    "https://raw.githubusercontent.com/HannahZ-Mulan/DesktopPet_YMJH/main/version.json"
)
# 备用：jsDelivr CDN（国内更快），格式同上
VERSION_CHECK_URL_CDN = (
    "https://cdn.jsdelivr.net/gh/HannahZ-Mulan/DesktopPet_YMJH@main/version.json"
)


# ===========================================================================
# 路径工具：兼容 PyInstaller 打包与直接运行
# ===========================================================================
def app_dir() -> str:
    """程序所在目录。打包后为 EXE 所在目录，便于读写外部 skins/config。"""
    if getattr(sys, "frozen", False):          # PyInstaller 打包
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(relative: str) -> str:
    """内置资源（仅打包时使用的只读资源）绝对路径。"""
    if hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative)


def skins_dir() -> str:
    """皮肤目录：优先 EXE/脚本同级的 skins/（可读写），其次内置。"""
    ext = os.path.join(app_dir(), "skins")
    if os.path.isdir(ext):
        return ext
    return resource_path("skins")


def config_path() -> str:
    return os.path.join(app_dir(), "config.json")


# ===========================================================================
# Windows API（纯 ctypes，零依赖）
# ===========================================================================
class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# 设置函数签名，避免 64 位下 HWND 被截断
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetForegroundWindow.argtypes = []
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetLastInputInfo.restype = wintypes.BOOL
_user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
# 窗口矩形感知（阶段3：站窗口顶边）
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
_user32.IsIconic.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.restype = wintypes.BOOL
_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
# 鼠标坐标→窗口（窗口玩耍模式：站到鼠标下方的窗口，而非"前台"）
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.WindowFromPoint.argtypes = [_POINT]            # POINT 按值传
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
# 进程归属判定（过滤桌宠自身窗口：winId() 不可靠，改比对 PID）
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def idle_seconds() -> float:
    """距离上一次键鼠输入的秒数（系统级）。"""
    lii = _LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not _user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0
    now = _kernel32.GetTickCount()
    return max(0.0, (now - lii.dwTime) / 1000.0)


def foreground_window_title() -> str:
    """当前前台窗口标题（Unicode）。"""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return ""
    n = _user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def foreground_window_rect():
    """返回前台窗口的屏幕矩形 (left, top, right, bottom)，失败或最小化返回 None。"""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    if _user32.IsIconic(hwnd):      # 最小化的窗口不能站
        return None
    # 前台是桌面/无窗口时，hwnd 可能是 0 或返回 32768 这种"隐藏坐标"
    if hwnd == _user32.GetDesktopWindow():
        return None
    rect = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    # 过滤 Windows 的异常坐标（最小化残留 -32000 / 隐藏窗口 32768）
    if (rect.left < -1000 or rect.left > 10000 or
            rect.top < -1000 or rect.top > 10000):
        return None
    # 过滤过小的矩形
    if rect.right - rect.left < 100 or rect.bottom - rect.top < 80:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def _is_self_hwnd(hwnd):
    """判断 hwnd 是否属于本进程（用于过滤桌宠自身）。

    不用 winId()：PyQt5 在 Windows 上 winId() 返回值常与 WindowFromPoint 看到的
    原生顶层 hwnd 不一致。改用进程 PID 比对，最可靠。
    """
    if not hwnd:
        return False
    pid_out = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_out))
    return pid_out.value == _kernel32.GetCurrentProcessId()


def window_at_point(x, y):
    """
    返回 (x,y) 屏幕坐标下"顶层应用窗口"的矩形 (left, top, right, bottom)。

    与 foreground_window_rect 的关键区别：用 WindowFromPoint 取鼠标下的窗口，
    再用 GetAncestor(GA_ROOT) 溯源到顶层（WindowFromPoint 会返回按钮等子窗口）。
    这样即便桌宠自己置顶抢了"前台"，也能正确找到用户鼠标下方的真正目标窗口。
    失败/落在桌面或桌宠自身/最小化时返回 None。
    """
    pt = _POINT(x, y)
    hwnd = _user32.WindowFromPoint(pt)
    if not hwnd:
        return None
    # 溯源到顶层窗口（WindowFromPoint 可能返回子窗口，如浏览器的渲染区）
    hwnd = _user32.GetAncestor(hwnd, 2) or hwnd   # GA_ROOT = 2
    # 过滤：桌面 / 桌宠自身（同进程）/ 不可见 / 最小化
    if hwnd == _user32.GetDesktopWindow():
        return None
    if _is_self_hwnd(hwnd):
        return None
    if not _user32.IsWindowVisible(hwnd):
        return None
    if _user32.IsIconic(hwnd):
        return None
    rect = _hwnd_rect(hwnd)
    if not rect:
        return None
    # 过滤过小窗口（站不上去）
    left, top, right, bottom = rect
    if right - left < 100 or bottom - top < 80:
        return None
    return rect, hwnd


def is_window_alive(hwnd):
    """所站窗口是否还可见（未关闭/未最小化）。用于站姿持续跟踪。"""
    if not hwnd:
        return False
    return bool(_user32.IsWindow(hwnd) and
                _user32.IsWindowVisible(hwnd) and
                not _user32.IsIconic(hwnd))


def _hwnd_rect(hwnd):
    """读指定窗口的屏幕矩形 (left, top, right, bottom)，失败返回 None。"""
    if not hwnd:
        return None
    rect = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    # 过滤异常坐标（与 window_at_point 一致的兜底）
    if (rect.left < -1000 or rect.left > 10000 or
            rect.top < -1000 or rect.top > 10000):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def cursor_pos():
    """当前鼠标屏幕坐标 (x, y)。"""
    pt = _POINT()
    if not _user32.GetCursorPos(ctypes.byref(pt)):
        return None
    return (pt.x, pt.y)


# —— 开机自启（注册表 HKCU\...\Run，无需管理员权限）——
_AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_NAME = "糊宠"


def is_autostart_enabled():
    """当前是否已设置开机自启。"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0, winreg.KEY_READ)
        try:
            winreg.QueryValueEx(key, _AUTOSTART_NAME)
            return True
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


def set_autostart(enable):
    """开启/关闭开机自启。返回 (成功?, 错误信息)。"""
    try:
        import winreg
        if enable:
            # 打包后用 exe 路径；开发模式用 pythonw + 脚本
            if getattr(sys, "frozen", False):
                target = f'"{sys.executable}"'
            else:
                target = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, _AUTOSTART_NAME, 0, winreg.REG_SZ, target)
            winreg.CloseKey(key)
        else:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE)
            try:
                winreg.DeleteValue(key, _AUTOSTART_NAME)
            except FileNotFoundError:
                pass    # 本来就没有，算成功
            winreg.CloseKey(key)
        return True, ""
    except Exception as e:
        return False, str(e)


# —— 进程路径检测（用于识别启动器/游戏等无固定窗口标题的程序）——
TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
    ]


_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.Process32FirstW.restype = wintypes.BOOL
_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.Process32NextW.restype = wintypes.BOOL
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]


def any_process_under(dir_path: str) -> bool:
    """是否有进程的可执行文件位于 dir_path 目录下（大小写不敏感）。

    用进程快照 + 完整路径查询，能识别启动器及其拉起的子进程，
    不依赖窗口标题（即使程序没有可见窗口也能检测到）。
    """
    target = os.path.normcase(os.path.abspath(dir_path))
    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap:
        return False
    try:
        pe = _PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        buf = ctypes.create_unicode_buffer(1024)
        if _kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                # 先用进程名做粗筛，减少 OpenProcess 开销
                if "launcher" in pe.szExeFile.lower() or \
                   pe.szExeFile.lower().endswith((".exe",)):
                    h = _kernel32.OpenProcess(
                        _PROCESS_QUERY_LIMITED_INFORMATION, False, pe.th32ProcessID)
                    if h:
                        n = wintypes.DWORD(1024)
                        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                            path = os.path.normcase(os.path.abspath(buf.value))
                            if path.startswith(target):
                                _kernel32.CloseHandle(h)
                                return True
                        _kernel32.CloseHandle(h)
                if not _kernel32.Process32NextW(snap, ctypes.byref(pe)):
                    break
    finally:
        _kernel32.CloseHandle(snap)
    return False


# ===========================================================================
# 动画系统：关键帧动画 + 多源变换合成
# ===========================================================================
class KeyAnim:
    """关键帧动画。keyframes: [(t, sx, sy, ox, oy, rot_deg), ...]"""

    def __init__(self, name, keyframes, duration_ms):
        self.name = name
        self.keyframes = keyframes
        self.duration_ms = duration_ms

    def sample(self, t):
        kf = self.keyframes
        if t <= kf[0][0]:
            return kf[0][1:]
        if t >= kf[-1][0]:
            return kf[-1][1:]
        for i in range(len(kf) - 1):
            t0 = kf[i][0]
            t1 = kf[i + 1][0]
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                a = kf[i][1:]
                b = kf[i + 1][1:]
                return tuple(a[k] + (b[k] - a[k]) * f for k in range(5))
        return kf[-1][1:]


def _kf(*frames):
    """便捷构造，每帧 (t, sx, sy, ox, oy, rot)。"""
    return list(frames)


# —— 互动动画（瞬时动作层）——
ANIM_JUMP = KeyAnim("跳跃", _kf(
    (0.00, 1.00, 1.00, 0, 0, 0),
    (0.12, 1.12, 0.85, 0, 8, 0),
    (0.42, 0.92, 1.12, 0, -52, 0),
    (0.68, 1.06, 0.90, 0, 4, 0),
    (0.84, 0.97, 1.04, 0, -2, 0),
    (1.00, 1.00, 1.00, 0, 0, 0),
), 900)

ANIM_SQUISH = KeyAnim("压扁回弹", _kf(
    (0.00, 1.00, 1.00, 0, 0, 0),
    (0.22, 1.28, 0.72, 0, 16, 0),
    (0.50, 0.90, 1.12, 0, -6, 0),
    (0.74, 1.06, 0.96, 0, 2, 0),
    (1.00, 1.00, 1.00, 0, 0, 0),
), 650)

ANIM_SHAKE = KeyAnim("左右抖动", _kf(
    (0.00, 1.00, 1.00, 0, 0, 0),
    (0.12, 1.02, 1.00, 10, 0, 4),
    (0.30, 1.02, 1.00, -10, 0, -4),
    (0.48, 1.02, 1.00, 9, 0, 3),
    (0.66, 1.02, 1.00, -7, 0, -2),
    (0.82, 1.01, 1.00, 3, 0, 1),
    (1.00, 1.00, 1.00, 0, 0, 0),
), 600)

ANIM_POP = KeyAnim("缩身弹跳", _kf(
    (0.00, 1.00, 1.00, 0, 0, 0),
    (0.25, 0.86, 0.88, 0, 10, 0),
    (0.55, 1.10, 1.08, 0, -4, 0),
    (1.00, 1.00, 1.00, 0, 0, 0),
), 550)

ANIM_SPIN = KeyAnim("转圈", _kf(
    (0.00, 1.00, 1.00, 0, 0, 0),
    (0.25, 0.95, 0.95, 0, -4, 90),
    (0.50, 0.92, 0.92, 0, 0, 180),
    (0.75, 0.95, 0.95, 0, -4, 270),
    (1.00, 1.00, 1.00, 0, 0, 360),
), 800)

ANIM_CHOMP = KeyAnim("啊呜吃掉", _kf(
    (0.00, 1.00, 1.00, 0, 0, 0),
    (0.15, 1.20, 0.78, 0, 14, 0),    # 张嘴/下蹲
    (0.30, 0.95, 1.15, 0, -8, 0),    # 仰头吞
    (0.50, 1.25, 0.76, 0, 16, 0),    # 再嚼
    (0.70, 0.96, 1.10, 0, -6, 0),
    (1.00, 1.00, 1.00, 0, 0, 0),
), 750)

ANIM_PEEK = KeyAnim("探头", _kf(
    (0.00, 1.00, 1.00, 0, 0, 0),
    (0.40, 1.00, 1.00, 0, 0, 0),
    (0.70, 1.05, 1.02, 0, -4, 2),
    (1.00, 1.00, 1.00, 0, 0, 0),
), 600)

INTERACT_ANIMS = [ANIM_JUMP, ANIM_SQUISH, ANIM_SHAKE, ANIM_POP]


def ease_inout(t):
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    return 0.5 * (1 - math.cos(math.pi * t))


# ===========================================================================
# 行为状态
# ===========================================================================
S_IDLE, S_WANDER, S_SLEEP, S_LOOK, S_PEEK, S_DRAG, S_FLY, S_DANCE, S_STAND, S_ALERT, S_FAINT = range(11)
STATE_NAMES = {S_IDLE: "待机", S_WANDER: "闲逛", S_SLEEP: "睡觉",
               S_LOOK: "观察", S_PEEK: "偷看", S_DRAG: "拖拽", S_FLY: "飞行",
               S_DANCE: "跳舞", S_STAND: "站窗口", S_ALERT: "健康提醒",
               S_FAINT: "昏迷"}


# ===========================================================================
# 对话气泡
# ===========================================================================
GREETINGS = [
    "{master}，你好呀！", "嘿嘿，你来啦～", "今天也要元气满满哦！",
    "你的小可爱上线啦！", "想我了吗？", "最喜欢你了！",
]
POKE_TEXTS = [
    "嘿嘿，戳我干嘛！", "哎呀，好痒！", "再戳我就生气啦！哼～",
    "咕噜咕噜～", "我超可爱的对不对？", "嗷呜～",
]
INTERACT_TEXTS = [
    "哇哦～", "好高！", "转晕啦～", "再来一次！", "嘻嘻！", "好玩好玩！",
]
SLEEP_TEXTS = ["Zzz…好困…", "困了，先睡一会儿～", "梦崽我呀～", "(打呼噜)"]
WAKE_TEXTS = ["啊！你回来啦！", "哇，吓我一跳！", "嗯…醒啦～", "终于等到你！"]
WANDER_TEXTS = ["走走走～", "这边看看…", "溜达溜达", "嗯哼～"]
CAUGHT_TEXTS = ["被抓到啦！", "哎呀，被发现啦！", "嘿嘿，被你找到啦～", "我没有在偷看哦！"]
CODE_TEXTS = ["又在写 bug？我盯着你哦", "代码写完了吗？", "加油加油，debug 必胜！"]
BROWSE_TEXTS = ["在查资料吗？", "小心摸鱼被抓～", "这个网页好看吗？"]
VIDEO_TEXTS = ["一起看！嗑瓜子不？", "这个好看！嗷呜～", "休闲时间到～"]
CHAT_TEXTS = ["在聊天呀～", "和谁说话呢？", "叽叽喳喳的～"]
GAME_TEXTS = ["带我一起玩嘛！", "游戏游戏！嗷呜！", "加油冲冲冲！"]
WORK_TEXTS = ["辛苦啦！", "老板没看见吧？", "工作工作，认真脸！"]
EAT_TEXTS = {
    "default": ["啊呜！吃了你的东西～", "真好吃！嗝～", "嘎嘣脆！"],
    ".txt": ["好多字…噎着了 >_<", "啃不完的字～"],
    ".zip": ["硬硬的…咬不动", "里面装的啥呀？"],
    ".exe": ["这个…能吃吗？(狐疑)", "看起来不好吃…"],
    ".png": ["图片脆脆的！", "这个我认识！"],
    ".jpg": ["图片脆脆的！", "这个我认识！"],
    ".pdf": ["好正式的文件！嗷呜", "啃得动啃得动～"],
    ".py": ["代码的味道！能量++", "嗷呜，bug 味儿的！"],
}
MEAL_TEXTS = ["肚子饿啦～有吃的吗？", "咕噜咕噜…该吃饭了吧？", "饭点到了哦！"]
NIGHT_TEXTS = ["这么晚了，该睡啦～", "熬夜伤身体哦！", "陪我一起睡好不好？"]
# 休息提醒（久坐/定时放松）—— 集中管理
BREAK_TEXTS = [
    "坐太久啦，站起来动一动！", "该休息一下眼睛啦～", "久坐不好哦，伸个懒腰吧！",
    "休息一下吧~", "记得伸个懒腰~", "别忘记放松喔",
]
# 偶发闲聊（待机时随机冒泡，增加陪伴感）
IDLE_CHATTER = [
    "海蟑螂就是海蟑螂呀", "梦崽我呀~", "今天玩糊了吗",
    "咕噜咕噜~", "哼哼，发呆中~",
]
# 一梦江湖主题语料（玩家梗/段子，文案较长，show_smart 会按字数给足显示时长）
JIANGHU_TEXTS = [
    "当时我正在打本，你突然的一句“想我了吗”让我彻底慌了神，连招全乱了，后来才知道你在做入梦任务…",
    "看得出来你喜欢一梦江湖，有空我带带你，我2w，发挥好可以10分钟打完日常，你知道临危吗，我当过奖励号",
    "你是为了追我才玩一梦江湖的吗？谢谢你的喜欢，但是不必如此。你可以找到更好的，而不是我这种最好的",
    "好无聊，你说我是去沧海抓大头吃呢还是去华山踢华子的碗？要不去金顶问武当你乖不乖吧？",
    "那天和你牢到很晚，心脏一直怦怦跳，闷闷的，涨涨的。我以为我心动了，结果是打本快猝死了。",
    "情缘dd，早上不能2/5我要去喂鸡，上午不能因为我要松土，下午不能因为我要放牛，晚上不能因为我家猪叫",
    "华子你记住，满堂花醉三千客，一剑霜寒十四州，半堂花醉一千五百客，两剑霜寒二十八州",
    "一梦江湖我不玩了，我没借到钱，试了183家贷款app，根本批不下来，他们问我经济来源，我填的帮派分红",
    "一梦江湖其实是个三a大作，情缘a了，结义a了，师傅也a了",
    "上一世我是垃圾千修，却爱上暗仔，表白却被骂癞蛤蟆。这一世重生，我怒充六块新手礼，却显示余额不足",
    "但是有一说一沧海确实强，特别是那个滕什么沧海，好像是藤椒吧，味道很棒",
    "#P[开物·神力·复]#W触发进入神力·复状态，持续1秒。使他人进入复读状态，触发间隔至多为30秒。",
    "你不要让别人毁了你的人生，你得把人生掌握在自己手里。这是一梦江湖，拿去玩吧，你得亲手毁了自己的人生",
    "那天登你号，好多人叫你宝宝，你说你会断的，七天过去了，怎么断的是咱俩的锁？[破旧的祈愿锁]",
    "什么《一梦江湖》？没听说过（整理龙袍），我说了没听说过（从口袋里掏出摔碎的玉玺），哎呀你烦不烦呐说了我没听说过（拿出方思明娃娃紧紧抱住），我只是一个平平无奇的少侠罢了（身后的喇叭花发出马步谣的声音）（匆忙关掉）。",
    "哥，你今天输出好高，比平时还高，你的连招也比以前猛烈，四面八方都是你凛冽的剑气，是因为她没理你吗",
    "他命真好，一句新赛季不会玩，你连夜上号打完了所有副本还调了号，我问你可以带我吗，你说鼠标吃老鼠药死了",
    "有没有一种可能我正在汤池挂机忽然有个漂亮云梦过来拉我进队说一眼就看中了修为不高衣柜空空脑袋笨笨的我呢",
    "我本来想今天挂锁的，但是我到三生树排了一天队，柳明望告诉我挂锁要两个人，我一下子就迷茫了",
    "游戏圈有个人尽皆知的秘密，我爱一梦江湖如命，这是谁都不能碰的禁忌，纵然外面也染指过王者荣耀，和平精英，原神贪吃蛇，但是它们都不敢闹到一梦江湖面前来",
    "小女子不才，做不了公子心尖尖上的人",
    "呆头鹅，你就站在大门外",
    "恨天地生万物独缺你我同归舟~~",
    "冠军不是踏月我…",
    "辣个穿绿衣服的女孩子，一直在打我QAQ",
]
# 启动器专属（D:\wyclx\Launcher.exe 检测到时随机）
LAUNCHER_TEXTS = ["今天你肝了吗", "大韭菜呼呼"]
# 跳舞专属（触发逐帧舞蹈动画时）
DANCE_TEXTS = ["看我跳舞！", "啦啦啦～一起跳！", "跟着节奏摇起来！", "嘿嘿，跳得好吧？", "再来一段！"]

# ===========================================================================
# 养成系统：道具 & 台词
# ===========================================================================
# 道具定义：hp 加血量, mp 加内力, mood 加心情
# icon: 图片路径（状态栏 rich text 用）；emoji: 菜单/兜底用
ITEMS = {
    "shenshou_dan": {
        "name": "神授丹", "emoji": "💊",
        "icon": resource_path("assets/shenshou_dan.png"),
        "hp": 40, "mp": 0, "mood": 10,
    },
    "yidizui": {
        "name": "一滴醉", "emoji": "🍶",
        "icon": resource_path("assets/yidizui.png"),
        "hp": 0, "mp": 40, "mood": 10,
    },
}
DEFAULT_INVENTORY = {"shenshou_dan": 3, "yidizui": 2}   # 初始库存

FEED_TEXTS = {
    "shenshou_dan": ["啊呜！神授丹真管用～", "血量回来啦！谢谢{master}", "神授丹，嘎嘣脆！", "舒服多了～"],
    "yidizui": ["哈～一滴醉下肚，内力涌上来！", "好酒！内力满满～", "微醺微醺，真舒服～"],
}
NO_ITEM_TEXTS = ["没有{item}啦，专注工作去赚吧！", "包包空空的…先去赚点{item}？", "{item}用光啦～"]
FOCUS_REWARD_TEXTS = ["专注奖励！+{n} {item} 🎁", "你专心工作的样子真帅，+{n}颗{item}！", "叮咚～专注{min}分钟，{n}{item}到手！"]
POMODORO_DONE_TEXTS = ["番茄完成！专注真棒，+{n} {item} 🍅", "专注达成！奖励{n}颗{item}～", "番茄钟结束！你超棒的，+{n}{item}！"]
POMODORO_BREAK_TEXTS = ["番茄中断了…下次加油哦", "专注被打断啦，丹药飞走了…", "记得专注完整个番茄哦～"]
# 分心主动提醒（P1）：切换窗口 / 闲置走神时随机冒泡
DISTRACT_SWITCH_TEXTS = ["哎呀，刚专注到一半就切走啦？回到主线吧~",
                         "咦？任务还没完成哦，要不要继续？",
                         "喂喂~说好的专注呢，回来回来！"]
DISTRACT_IDLE_TEXTS = ["发呆中...任务还在等你呢",
                       "伸个懒腰也好，但别忘了回来继续呀~",
                       "走神啦？任务进度可没跟着走哦"]
# 取消番茄钟时随机冒泡（不奖励，区别于正常完成）
POMODORO_CANCEL_TEXTS = ["番茄取消啦～随时可以重来哦", "好吧，这次先到这儿～", "番茄停了，休息一下也好~"]
LOW_HP_TEXTS = ["呜…我不不太舒服…", "血量好低，能给我颗神授丹吗？", "头好晕…需要治疗…"]
LOW_MP_TEXTS = ["内力不足了…给我来口一滴醉？", "灵力快枯竭了…", "丹田空虚，需要补补内力…"]
# 昏迷台词（hp 归零时触发）
FAINT_TEXTS = ["呜…我倒下了…", "眼前一黑…需要神授丹…", "撑不住了…呜呜…"]
# 方案一·三角联动的阈值常量
MOOD_HIGH = 70       # 心情≥此值 → hp/mp 衰减减慢
MOOD_LOW = 30        # 心情≤此值 → hp/mp 衰减加快
DECAY_BUFF = 0.7     # 心情好时衰减系数（×0.7 = 减慢 30%）
DECAY_DEBUFF = 1.5   # 心心情差时衰减系数（×1.5 = 加快 50%）
FULL_HP_MP = 70      # hp/mp 均≥此值 → 心情自然回升
WEAK_HP = 20         # hp≤此值 → 虚弱（移动变慢等）

# 方案三·昼夜节律常量
NIGHT_WARN_HOUR = 22   # 这个小时开始提醒睡觉
MORNING_BONUS = 10     # 早晨首次启动 hp/mp/mood 各加的量
NIGHT_TIPS = ["夜深了，还不睡吗…", "好困…陪我去睡觉好不好？", "熬夜伤身体哦！明天再玩吧～"]
MORNING_TIPS = ["早安！今天元气满满～", "早上好呀～跟你一起迎接朝阳！", "新的一天，心情好好！"]


def _hour_decay_mult(hour):
    """按真实时段返回衰减倍率（与三角联动 buff 叠加使用）。"""
    if 6 <= hour < 9:
        return 0.5     # 清晨：恢复快
    elif 9 <= hour < 18:
        return 1.0     # 白天：正常
    elif 18 <= hour < 22:
        return 0.9     # 黄昏：略慢（放松时段）
    elif 22 <= hour or hour < 2:
        return 1.8     # 深夜：加速（熬夜伤身）
    else:
        return 2.5     # 凌晨 2-6：严重透支


def _hour_mood_bonus(hour):
    """按真实时段返回心情每分钟额外变化量（正=回升，负=下降）。"""
    if 6 <= hour < 9:
        return 1.0     # 清晨：朝气蓬勃
    elif 9 <= hour < 18:
        return 0.0     # 白天：基础
    elif 18 <= hour < 22:
        return 0.5     # 黄昏：放松，微涨
    elif 22 <= hour or hour < 2:
        return -1.0    # 深夜：疲惫下降
    else:
        return -1.5    # 凌晨：严重透支

# 健康提醒台词（阶段：预提醒轻提示 / 久坐强提醒 / 喝水强提醒）
HEALTH_PRE_TEXTS = ["快起来活动一下吧～", "该喝水啦，别忘了～", "坐太久啦，伸个懒腰？"]
SIT_ALERT_TEXTS = ["🪑 久坐警报！站起来走两步！", "坐太久啦！跟我一起动一动！", "你的腰在抗议啦！快起来！", "久坐伤身，起来活动 1 分钟吧！"]
DRINK_ALERT_TEXTS = ["💧 喝水时间到！咕嘟咕嘟～", "该喝水啦！补充水分很重要！", "别忘喝水哦！身体需要你！", "喝水喝水！我盯着你喝！"]
HEALTH_ANIM_MAP = {"jump": ANIM_JUMP, "shake": ANIM_SHAKE, "pop": ANIM_POP}
HEALTH_ANIM_LABELS = {"jump": "跳动", "shake": "抖动", "pop": "挥手"}

# 状态衰减速率（每分钟下降量）
DECAY_PER_MIN = {"hp": 0.2, "mp": 0.3, "mood": 0.8}

# ===========================================================================
# 动态情感系统（阶段4）
# ===========================================================================
# 四种基础情感，值 0-100，取最高者为"主导情感"
EMOTIONS = ["happy", "sad", "excited", "bored"]
EMOTION_NAMES = {"happy": "开心", "sad": "悲伤", "excited": "兴奋", "bored": "无聊"}
EMOTION_EMOJI = {"happy": "😊", "sad": "😢", "excited": "🤩", "bored": "😑"}
# 情感衰减速率（每分钟）
EMOTION_DECAY = {"happy": 1.2, "sad": 1.5, "excited": 3.0, "bored": 0.6}
# 无聊随时间自然增长（没人理时）
BORED_GROWTH_PER_MIN = 1.5

# 各情感对应的台词风格
EMOTION_TEXTS = {
    "happy": ["今天心情超好！", "嘿嘿，开心～", "生活真美好呀！", "嗷呜～好心情！"],
    "sad": ["呜…有点难过…", "今天不太开心…", "能抱抱我吗？", "叹气…"],
    "excited": ["哇哦哇哦！太兴奋啦！", "冲冲冲！能量满满！", "嗷嗷嗷！开心到转圈！", "耶耶耶！"],
    "bored": ["好无聊啊…", "陪我玩会儿嘛…", "发呆中…", "唔，没什么意思…"],
}

# 启动器所在目录（检测该目录下任意进程运行即触发）
LAUNCHER_DIR = r"D:\wyclx"


class BubbleLabel(QWidget):
    """不透明背景对话气泡，位于角色上方。"""
    MAX_W = 360   # 气泡最大宽度（超过则文字自动换行，避免撑出屏幕）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._text = ""
        self._master = "主人"   # 玩家称呼，show_text 时替换 {master}
        # 统一字体：_update_size 与 paintEvent 共用，保证尺寸计算和绘制一致
        self._font = QFont("Microsoft YaHei", 10)
        self._font.setBold(True)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self._fade = QGraphicsOpacityEffect(self)
        self._fade.setOpacity(0.0)
        self.setGraphicsEffect(self._fade)

        self._anim = QPropertyAnimation(self._fade, b"opacity", self)
        self._anim.setDuration(180)

    def show_text(self, text, duration=2600):
        # 自动替换 {master} 为玩家称呼
        if "{master}" in text:
            text = text.replace("{master}", self._master)
        self._text = text
        self._update_size()
        self._reposition()
        self.show()
        self.raise_()
        self._anim.stop()
        self._fade.setOpacity(0.0)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()
        self._timer.start(duration)
        QTimer.singleShot(max(duration - 200, 200), self._fade_out)

    def show_random(self, texts, duration=2600):
        self.show_text(random.choice(texts), duration)

    def show_smart(self, text):
        """按字数自动算显示时长：每字约 160ms（≈6字/秒，舒适阅读），下限 2.5s，上限 16s。

        长文案（如江湖语料上百字）给足时间读完，短文案不过快消失。
        """
        n = len(text)
        duration = min(16000, max(2500, int(n * 160)))
        self.show_text(text, duration)

    def show_random_smart(self, texts):
        """随机抽一条 + 按字数智能调时长。"""
        self.show_smart(random.choice(texts))

    def _fade_out(self):
        if not self.isVisible():
            return
        self._anim.stop()
        self._anim.setStartValue(self._fade.opacity())
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.InCubic)
        self._anim.start()

    def _update_size(self):
        """按文字内容算气泡尺寸：短文单行，长文限宽自动换行成多行。"""
        fm = QFontMetrics(self._font)
        text_w = fm.horizontalAdvance(self._text)
        pad_x, pad_y = 28, 18
        # 单行能放下（且不超最大宽度）→ 单行；否则限定最大宽度换行
        if text_w + pad_x <= self.MAX_W:
            self._box_w = text_w + pad_x
            self._box_h = fm.height() + pad_y
        else:
            # 用 boundingRect 算"限定最大内容宽度"下的多行包围盒
            inner_w = self.MAX_W - pad_x
            br = fm.boundingRect(0, 0, inner_w, 0, Qt.TextWordWrap | Qt.AlignCenter, self._text)
            self._box_w = self.MAX_W
            self._box_h = br.height() + pad_y
        self.resize(self._box_w, self._box_h + 12)   # +12 给小三角留位置

    def _reposition(self):
        host = self.parent()
        if host is None:
            return
        hr = host.geometry()
        cx = hr.center().x()
        by = hr.top() - self._box_h - 12
        # 上方放不下则放下方
        screen = QApplication.primaryScreen().availableGeometry()
        if by < screen.top() + 4:
            by = hr.bottom() + 12
            self._below = True
        else:
            self._below = False
        self.move(cx - self._box_w // 2, by)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0, 0, self._box_w, self._box_h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 255))
        p.drawRoundedRect(rect, 14, 14)
        p.setPen(QColor(255, 138, 175, 255))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        # 小三角
        tri = self.width() // 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 255))
        if getattr(self, "_below", False):
            p.drawPolygon(
                QPointF(tri - 7, 12), QPointF(tri + 7, 12), QPointF(tri, 2)
            )
        else:
            p.drawPolygon(
                QPointF(tri - 7, self._box_h),
                QPointF(tri + 7, self._box_h),
                QPointF(tri, self._box_h + 10),
            )
        p.setPen(QColor(80, 80, 80, 255))
        p.setFont(self._font)
        # 文字画在内容区（去掉四周 padding），支持自动换行
        text_rect = rect.adjusted(14, 6, -14, -6)
        p.drawText(text_rect, Qt.AlignCenter | Qt.TextWordWrap, self._text)


class StatusTip(QWidget):
    """鼠标悬停时显示的状态浮窗（支持 rich text 进度条）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._label = QLabel(self)
        self._label.setStyleSheet(
            "QLabel { background-color: rgba(255,250,252,245);"
            "border:1.5px solid #f0a0c0; border-radius:10px;"
            "padding:10px 14px; font-size:12px; color:#444; }"
        )
        self._label.setTextFormat(Qt.RichText)
        # 淡入淡出
        self._fade = QGraphicsOpacityEffect(self)
        self._fade.setOpacity(0.0)
        self.setGraphicsEffect(self._fade)
        self._anim = QPropertyAnimation(self._fade, b"opacity", self)
        self._anim.setDuration(200)

    def show_html(self, html):
        self._label.setText(html)
        self._label.adjustSize()
        self.resize(self._label.size())
        self.show()
        self.raise_()
        # 淡入
        self._anim.stop()
        self._fade.setOpacity(0.0)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()

    def fade_out(self):
        if not self.isVisible():
            return
        self._anim.stop()
        self._anim.setStartValue(self._fade.opacity())
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.InCubic)
        self._anim.start()

    def reposition(self, host_rect):
        """贴在宿主窗口正上方居中。"""
        cx = host_rect.center().x()
        x = cx - self.width() // 2
        y = host_rect.top() - self.height() - 8
        screen = QApplication.primaryScreen().availableGeometry()
        if y < screen.top() + 4:
            y = host_rect.bottom() + 8
        if x < screen.left():
            x = screen.left()
        elif x + self.width() > screen.right():
            x = screen.right() - self.width()
        self.move(x, y)


class TaskBanner(QWidget):
    """常驻任务横幅（专注监督 P0）。

    与 BubbleLabel 的差异：
      · 不自动隐藏——由宿主依据「有任务 / 番茄进行中」决定显隐；
      · 文案动态：番茄进行中显示「🍅 24:59  ·  🎯 当前任务」，否则只显示任务；
      · 鼠标穿透（WA_TransparentForMouseEvents），不挡下方点击；
      · 位置跟随桌宠 move 事件同步。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)   # 鼠标穿透

        self._task = ""
        self._rem = 0            # 番茄剩余秒
        self._pomo = False       # 番茄是否进行中
        self._below = False      # 是否放到桌宠下方
        self._box_w = 0
        self._box_h = 0

    def update_content(self, task, remaining_sec, pomodoro_active):
        """刷新横幅文案并重绘。"""
        self._task = task or ""
        self._rem = max(0, int(remaining_sec))
        self._pomo = bool(pomodoro_active)
        self._update_size()
        self.update()

    def _full_text(self):
        """完整显示文本（含 emoji）。"""
        if self._pomo:
            mm = self._rem // 60
            ss = self._rem % 60
            timer = f"🍅 {mm:02d}:{ss:02d}"
            if self._task:
                return f"{timer}  ·  🎯 {self._task}"
            return timer
        return f"🎯 {self._task}" if self._task else ""

    def _update_size(self):
        text = self._full_text()
        if not text:
            self._box_w = 0
            self._box_h = 0
            return
        f = QFont("Microsoft YaHei", 9)
        f.setBold(True)
        self._font = f
        fm = QFontMetrics(f)
        self._box_w = fm.horizontalAdvance(text) + 30
        self._box_h = fm.height() + 14
        self.resize(self._box_w, self._box_h + 10)

    def reposition(self, host_rect):
        """贴在宿主窗口正上方居中，空间不足则翻到下方。"""
        if self._box_w == 0:
            return
        cx = host_rect.center().x()
        x = cx - self._box_w // 2
        y = host_rect.top() - self._box_h - 10
        screen = QApplication.primaryScreen().availableGeometry()
        if y < screen.top() + 4:
            y = host_rect.bottom() + 10
            self._below = True
        else:
            self._below = False
        if x < screen.left():
            x = screen.left()
        elif x + self._box_w > screen.right():
            x = screen.right() - self._box_w
        self.move(x, y)

    def paintEvent(self, _):
        if self._box_w == 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0, 0, self._box_w, self._box_h)
        # 白底圆角胶囊 + 粉色描边（对齐 BubbleLabel 配色）
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 248))
        p.drawRoundedRect(rect, 12, 12)
        p.setPen(QPen(QColor(255, 138, 175, 220), 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect.adjusted(0.7, 0.7, -0.7, -0.7), 12, 12)
        # 小三角指针
        tri = self._box_w // 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 248))
        if self._below:
            p.drawPolygon(
                QPointF(tri - 6, 11), QPointF(tri + 6, 11), QPointF(tri, 2)
            )
        else:
            p.drawPolygon(
                QPointF(tri - 6, self._box_h),
                QPointF(tri + 6, self._box_h),
                QPointF(tri, self._box_h + 8),
            )
        # 文本
        p.setPen(QColor(70, 70, 70, 255))
        p.setFont(self._font)
        p.drawText(rect, Qt.AlignCenter, self._full_text())


# ===========================================================================
# 主宠物窗口
# ===========================================================================
class PetWindow(QWidget):
    DEFAULT_H = 260
    MIN_H = 50
    MAX_H = 600

    GRAVITY = 2400.0       # px/s^2
    BOUNCE = 0.55          # 弹跳能量保留
    FRICTION = 0.92        # 落地水平摩擦

    def __init__(self):
        super().__init__()
        # —— 皮肤 ——
        self._skins = []
        self._skin_index = 0
        self._skin_anims = {}       # 当前皮肤的逐帧动画缓存 {name: [QPixmap]}
        self._pixmap = QPixmap()
        self._load_skins()

        # —— 缩放 / 朝向 ——
        self._scale = 1.0
        self._facing = 1           # 1 朝右, -1 朝左

        # —— 动画状态 ——
        self._anim = None          # 瞬时动作动画
        self._anim_start = 0.0
        self._interact_idx = 0
        self._interact_cd = QTimer(self)
        self._interact_cd.setSingleShot(True)

        # —— 行为状态机 ——
        self._state = S_IDLE
        self._state_until = 0.0    # 当前状态结束时间
        self._wander_target = QPointF(0, 0)
        self._wander_origin = QPointF(0, 0)
        # —— 逐帧动画（如舞蹈）——
        self._frame_seq = []       # 当前逐帧动画的 QPixmap 列表
        self._frame_fps = 12       # 播放帧率
        self._frame_start = 0.0    # 开始时间
        self._frame_loops = 0      # 剩余循环次数（0=无限直到被打断）
        self._blink_t = 0.0
        self._next_blink = random.uniform(2.5, 5.5)
        self._blink_open = True
        self._peek_dir = "right"   # 躲猫猫方向

        # —— 物理 ——
        self._vx = 0.0
        self._vy = 0.0
        self._grounded = True
        self._drag_last_pos = QPointF(0, 0)
        self._drag_last_t = 0.0

        # —— 鼠标交互 ——
        self._dragging = False
        self._drag_offset = QPointF()
        self._press_pos = QPointF()
        self._moved = False
        self._last_click_t = 0.0
        self._last_click_pos = QPointF()

        # —— 环境感知 ——
        self._quiet_mode = False
        self._last_fg_title = ""
        self._last_react_topic = {}    # topic -> 上次触发时间
        self._last_active_idle = 0.0   # 上一次的 idle 值，用于检测"刚回来"
        self._was_sleeping = False
        self._launcher_was_running = False   # 启动器上次是否在运行（边沿触发）
        self._last_pet_interact = time.perf_counter()   # 上次与宠物互动的时间

        # —— 健康提醒状态 ——
        self._sit_interval = 45 * 60     # 久坐提醒间隔（秒，0=关闭）
        self._drink_interval = 60 * 60   # 喝水提醒间隔（秒，0=关闭）
        self._pre_alert = 2 * 60         # 预提醒时间（秒，0=关闭）
        self._health_anim = "jump"       # 提醒动画类型
        self._last_drink_t = time.perf_counter()   # 上次喝水提醒时间
        self._alert_active = False       # 是否正在强提醒中
        self._alert_reason = ""          # 当前提醒原因
        self._pre_alert_sent = {"sit": False, "drink": False}  # 预提醒是否已发（防重复）

        # —— 养成状态 ——
        self._hp = 100.0            # 血量 0-100
        self._mp = 100.0            # 内力 0-100
        self._mood = 100.0          # 心情 0-100
        # —— 情感状态（阶段4）——
        self._emotions = {"happy": 60.0, "sad": 0.0, "excited": 0.0, "bored": 0.0}
        self._inventory = dict(DEFAULT_INVENTORY)   # 道具库存 {item_id: 数量}
        self._last_decay_t = time.perf_counter()    # 上次状态衰减时间
        self._last_low_warn = 0.0   # 上次低状态警告时间（冷却）
        # —— 专注奖励 ——
        self._focus_window = ""     # 当前正在专注的窗口标题
        self._focus_start = 0.0     # 该窗口持续专注的开始时间
        self._focus_rewarded_min = 0   # 已奖励过的专注分钟数（避免重复）
        # —— 番茄钟 ——
        self._pomodoro_end = 0.0    # 番茄钟结束时间（0=未进行）
        self._pomodoro_active = False
        self._pomodoro_custom_min = 0   # 自定义番茄时长（分钟，0=用默认 POMODORO_MIN）
        # —— 当前任务（专注监督 P0）——
        self._current_task = ""        # 当前任务文本
        self._task_start = 0.0         # 任务开始时间戳
        self._show_task_banner = True  # 任务横幅总开关（右键可关）

        # —— 配置 ——
        self._always_on_top = True
        self._load_config()

        # —— 气泡 ——
        self.bubble = BubbleLabel(self)
        self.bubble._master = self._master   # 同步玩家称呼（语料 {master} 替换）
        self.bubble.hide()

        # —— 悬停状态浮窗 ——
        self.status_tip = StatusTip(self)
        self.status_tip.hide()

        # —— 常驻任务横幅（专注监督 P0）——
        self.task_banner = TaskBanner(self)
        self.task_banner.hide()
        self._hover_timer = QTimer(self)        # 悬停计时
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._show_hover_status)
        self._status_hide_timer = QTimer(self)  # 显示后自动隐藏
        self._status_hide_timer.setSingleShot(True)
        self._status_hide_timer.timeout.connect(self._hide_hover_status)

        # —— 更新模块 ——
        self.updater = Updater(self)

        # —— 构建 UI ——
        self._build_ui()

        # —— 定时器 ——
        # 60fps 动画/物理/行为
        self._last_frame_t = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

        # 1Hz 环境感知
        self._sense_timer = QTimer(self)
        self._sense_timer.timeout.connect(self._sense)
        self._sense_timer.start(1000)
        self._sense()

        # 行为决策（每 3 秒评估一次）
        self._behavior_timer = QTimer(self)
        self._behavior_timer.timeout.connect(self._decide_behavior)
        self._behavior_timer.start(3000)

    # -------------------------------------------------------------------
    # 皮肤
    #   每个皮肤 = 一个基础图（用于通用形变动作）。
    #   若皮肤是子目录，则可含：同名 .png 基础图 + 动画子文件夹（如 dance/）。
    #   例：skins/小狼/小狼.png（基础） + skins/小狼/dance/*.png（逐帧舞蹈）
    # -------------------------------------------------------------------
    def _load_skins(self):
        sd = skins_dir()
        skins = []          # list of dict: {name, base, anims:{name:[frames]}}
        if os.path.isdir(sd):
            for entry in sorted(os.listdir(sd)):
                full = os.path.join(sd, entry)
                # 情况 A：直接是图片文件
                if os.path.isfile(full) and entry.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".bmp", ".gif")):
                    skins.append({
                        "name": os.path.splitext(entry)[0],
                        "base": full,
                        "anims": {},
                    })
                # 情况 B：子目录皮肤
                elif os.path.isdir(full):
                    name = entry
                    base = None
                    anims = {}
                    # 找基础图：同名 png 优先，否则目录里第一张图
                    same = os.path.join(full, entry + ".png")
                    if os.path.isfile(same):
                        base = same
                    # 扫描动画子文件夹
                    for sub in sorted(os.listdir(full)):
                        subfull = os.path.join(full, sub)
                        if os.path.isdir(subfull):
                            frames = sorted(
                                os.path.join(subfull, f) for f in os.listdir(subfull)
                                if f.lower().endswith((".png", ".jpg", ".jpeg"))
                            )
                            if frames:
                                anims[sub] = frames
                    # 没有同名基础图，用第一个动画的首帧或目录内任一图
                    if not base:
                        flat = sorted(
                            os.path.join(full, f) for f in os.listdir(full)
                            if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif"))
                        )
                        if flat:
                            base = flat[0]
                        elif anims:
                            base = next(iter(anims.values()))[0]
                    if base:
                        skins.append({"name": name, "base": base, "anims": anims})
        if not skins:
            skins.append({
                "name": "character",
                "base": resource_path("skins/character.png"),
                "anims": {},
            })
        self._skins = skins

    def _apply_skin(self, index):
        if not self._skins:
            return
        index = index % len(self._skins)
        self._skin_index = index
        skin = self._skins[index]
        pm = QPixmap(skin["base"])
        if not pm.isNull():
            self._pixmap = pm
            # 预加载该皮肤的逐帧动画（按需缓存 QPixmap）
            self._skin_anims = {}
            for aname, files in skin["anims"].items():
                frames = []
                for f in files:
                    qpm = QPixmap(f)
                    if not qpm.isNull():
                        frames.append(qpm)
                if frames:
                    self._skin_anims[aname] = frames
            self._resize_window()

    def skin_name(self, index=None):
        if index is None:
            index = self._skin_index
        if 0 <= index < len(self._skins):
            return self._skins[index]["name"]
        return "?"

    def skin_has_anim(self, name, index=None):
        """该皮肤是否拥有某个逐帧动画。"""
        if index is None:
            index = self._skin_index
        if 0 <= index < len(self._skins):
            return name in self._skins[index]["anims"]
        return False

    # -------------------------------------------------------------------
    # 配置持久化
    # -------------------------------------------------------------------
    def _load_config(self):
        self._first_run = True        # 默认首次运行
        self._idle_peek = 0           # 闲置触发躲猫猫的秒数（0=关闭）
        self._pet_name = "糊糊"        # 宠物昵称
        self._master = "主人"          # 玩家称呼（语料 {master} 替换用）
        self._stand_mode = False      # 站窗口顶边开关
        try:
            with open(config_path(), "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self._scale = float(cfg.get("scale", 1.0))
            self._always_on_top = bool(cfg.get("on_top", True))
            self._quiet_mode = bool(cfg.get("quiet", False))
            self._first_run = bool(cfg.get("first_run", True))
            self._idle_peek = int(cfg.get("idle_peek", 0))
            name = cfg.get("pet_name", "").strip()
            if name:
                self._pet_name = name[:12]   # 最多 12 字
            master = cfg.get("master", "").strip()
            if master:
                self._master = master[:8]    # 称呼最多 8 字
            self._stand_mode = bool(cfg.get("stand_mode", False))
            # 番茄钟自定义时长（0=用默认值，1-120 分钟）
            self._pomodoro_custom_min = max(0, min(120, int(cfg.get("pomodoro_min", 0))))
            # 当前任务 & 任务横幅开关（专注监督 P0）
            self._current_task = str(cfg.get("current_task", ""))
            self._show_task_banner = bool(cfg.get("show_task_banner", True))
            # 健康提醒配置
            health = cfg.get("health", {})
            self._sit_interval = int(health.get("sit_interval", 45 * 60))
            self._drink_interval = int(health.get("drink_interval", 60 * 60))
            self._pre_alert = int(health.get("pre_alert", 2 * 60))
            self._health_anim = str(health.get("health_anim", "jump"))
            saved_skin = cfg.get("skin", "")
            pos = cfg.get("pos")
            self._pending_pos = (pos[0], pos[1]) if pos else None
            # 养成状态
            pet_state = cfg.get("pet_state", {})
            self._hp = float(pet_state.get("hp", 100.0))
            self._mp = float(pet_state.get("mp", 100.0))
            self._mood = float(pet_state.get("mood", 100.0))
            inv = pet_state.get("inventory", {})
            self._inventory = {k: int(v) for k, v in inv.items()}
            # 补全默认道具：老配置没有的新物品按默认数量补给（版本升级兼容）
            for k, default_cnt in DEFAULT_INVENTORY.items():
                self._inventory.setdefault(k, default_cnt)
            # 清理已废弃的旧物品（如灵果）
            for old_k in list(self._inventory):
                if old_k not in ITEMS:
                    del self._inventory[old_k]
            # 找到同名皮肤
            for i, s in enumerate(self._skins):
                if s["name"] == saved_skin:
                    self._skin_index = i
                    break
        except Exception:
            self._pending_pos = None

    def _save_config(self):
        try:
            cfg = {
                "scale": self._scale,
                "on_top": self._always_on_top,
                "quiet": self._quiet_mode,
                "first_run": self._first_run,
                "idle_peek": self._idle_peek,
                "pet_name": self._pet_name,
                "master": self._master,
                "skin": self._skins[self._skin_index]["name"] if self._skins and self._skin_index < len(self._skins) else ""
                        if self._skins else "",
                "stand_mode": self._stand_mode,
                "pomodoro_min": self._pomodoro_custom_min,
                "current_task": self._current_task,
                "show_task_banner": self._show_task_banner,
                "health": {
                    "sit_interval": self._sit_interval,
                    "drink_interval": self._drink_interval,
                    "pre_alert": self._pre_alert,
                    "health_anim": self._health_anim,
                },
                "pos": [self.x(), self.y()],
                "pet_state": {
                    "hp": round(self._hp, 1),
                    "mp": round(self._mp, 1),
                    "mood": round(self._mood, 1),
                    "inventory": self._inventory,
                },
            }
            with open(config_path(), "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # -------------------------------------------------------------------
    # UI 构建
    # -------------------------------------------------------------------
    def _build_ui(self):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self._always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)

        self._apply_skin(self._skin_index)
        if getattr(self, "_pending_pos", None):
            self.move(*self._pending_pos)
            self._pending_pos = None
        else:
            self._center_on_screen()

    def _resize_window(self):
        if self._pixmap.isNull():
            return
        h = max(self.MIN_H, min(self.MAX_H, int(self.DEFAULT_H * self._scale)))
        self._disp_h = h
        ar = self._pixmap.width() / max(1, self._pixmap.height())
        w = int(h * ar)
        self._disp_w = w
        pad = int(h * 0.20)
        self._pad = pad
        ww, wh = w + pad * 2, h + pad * 2
        if self.isVisible():
            cx = self.geometry().center().x()
            cy = self.geometry().center().y()
            self.resize(ww, wh)
            self.move(cx - ww // 2, cy - wh // 2)
        else:
            self.resize(ww, wh)

    def moveEvent(self, e):
        """桌宠移动时同步常驻任务横幅位置（专注监督 P0）。"""
        super().moveEvent(e)
        self.task_banner.reposition(self.geometry())

    def _center_on_screen(self):
        g = QApplication.primaryScreen().availableGeometry()
        self.move(g.center().x() - self.width() // 2,
                  g.bottom() - self.height() - 40)

    def _screen_geom(self):
        """角色中心所在屏幕的可用区域（逻辑坐标）。"""
        c = self.geometry().center()
        screen = None
        for s in QApplication.screens():
            if s.geometry().contains(c):
                screen = s
                break
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen.availableGeometry()

    # -------------------------------------------------------------------
    # 行为决策
    # -------------------------------------------------------------------
    def _decide_behavior(self):
        """周期性决定自主行为（仅在非交互状态下生效）。"""
        try:
            self._decide_behavior_inner()
        except Exception:
            pass    # 行为决策异常忽略，下次定时器再试

    def _decide_behavior_inner(self):
        # 交互中 / 躲猫猫中（手动躲藏，等用户点击才结束）不插手
        if self._state in (S_DRAG, S_FLY, S_DANCE, S_PEEK, S_STAND, S_ALERT):
            return
        idle = idle_seconds()
        # 设了闲置躲猫猫 → 不走睡觉逻辑（由 _sense 统一管躲猫猫/睡觉）
        if self._idle_peek > 0:
            if self._state == S_SLEEP:
                return
            # 闲置中不随机走动，安静等躲猫猫
            if idle > 60:
                return
        else:
            # 未设闲置躲猫猫：闲置超过 3 分钟 → 睡觉
            if idle > 180 and self._state != S_SLEEP:
                self._enter_sleep()
                return
            if self._state == S_SLEEP:
                return  # 睡觉中不主动切换
        # 安静模式：几乎不动，每 10 分钟左右才沿边缘走一次
        if self._quiet_mode:
            if self._state == S_IDLE and random.random() < 0.005 and not self.underMouse():
                self._enter_wander()
            else:
                self._set_state(S_IDLE, random.uniform(8, 16))
            return
        # 随机行为，受主导情感影响：
        # 兴奋→更爱跳舞；无聊→更爱待机/发呆；开心→更爱闲逛
        dom = self.dominant_emotion()
        emo_lvl = self._emotions[dom]
        r = random.random()
        # 情感强烈时调整概率权重
        dance_bonus = 0.20 if (dom == "excited" and emo_lvl > 50 and self.skin_has_anim("dance")) else 0.0
        wander_bonus = 0.15 if (dom == "happy" and emo_lvl > 50) else 0.0
        bored_stay = (dom == "bored" and emo_lvl > 50)
        if r < (0.25 + wander_bonus) and not self.underMouse():
            self._enter_wander()
        elif r < (0.25 + wander_bonus + 0.15 + dance_bonus) and self.skin_has_anim("dance"):
            self._enter_dance(loops=random.randint(1, 2))
            self._boost_emotion("happy", 3)
        elif r < (0.55 + wander_bonus + dance_bonus) and not bored_stay:
            self._enter_look()
        elif bored_stay and random.random() < 0.3:
            # 无聊时偶尔抱怨
            self.bubble.show_random(EMOTION_TEXTS["bored"], 2200)
        else:
            self._set_state(S_IDLE, random.uniform(8, 16))

    def _set_state(self, s, duration=0):
        self._state = s
        self._state_until = time.perf_counter() + duration if duration > 0 else 0

    def _enter_wander(self):
        g = self._screen_geom()
        cur_x, cur_y = self.x(), self.y()
        ww, wh = self.width(), self.height()
        if self._quiet_mode:
            # 安静模式：沿屏幕边缘走（贴底边/左边/右边），不走到中间
            margin = 10   # 离屏幕边的留白
            edge = random.choice(["bottom", "left", "right"])
            if edge == "bottom":
                tx = random.randint(g.left() + margin, g.right() - ww - margin)
                ty = g.bottom() - wh - margin
            elif edge == "left":
                tx = g.left() + margin
                ty = random.randint(g.top() + margin, g.bottom() - wh - margin)
            else:
                tx = g.right() - ww - margin
                ty = random.randint(g.top() + margin, g.bottom() - wh - margin)
        else:
            # 正常模式：在当前附近随机走
            tx = cur_x + random.randint(-250, 250)
            ty = cur_y + random.randint(-60, 60)
        tx = max(g.left(), min(g.right() - ww, tx))
        ty = max(g.top(), min(g.bottom() - wh, ty))
        self._wander_origin = QPointF(cur_x, cur_y)
        self._wander_target = QPointF(tx, ty)
        dist = math.hypot(tx - cur_x, ty - cur_y)
        self._facing = 1 if tx >= cur_x else -1
        dur = max(1.5, dist / 120.0)   # 120 px/s
        self._set_state(S_WANDER, dur)
        if not self._quiet_mode and random.random() < 0.4:
            self.bubble.show_random(WANDER_TEXTS, 1800)

    def _enter_sleep(self):
        self._set_state(S_SLEEP, 99999)
        self._was_sleeping = True
        if not self._quiet_mode:
            self.bubble.show_random(SLEEP_TEXTS, 2500)

    def _enter_look(self):
        self._set_state(S_LOOK, random.uniform(3, 6))

    def _enter_peek(self):
        """躲猫猫：躲到屏幕边缘，只露触角/头顶偷看。

        方向 bottom=从屏幕底部往上探头（露触角），left/right=从侧边露半边。
        """
        g = self._screen_geom()
        d = random.choice(["bottom", "bottom", "left", "right"])  # 底部更常出现
        self._peek_dir = d
        cur_x, cur_y = self.x(), self.y()
        ww, wh = self.width(), self.height()

        if d == "bottom":
            # 从屏幕底部探头：窗口大部分沉到任务栏下方，只露顶部触角
            # 触角约占角色高度 6%，只露触角尖 ≈ 角色高度 5%
            reveal_h = int(self._disp_h * 0.05)
            # 窗口顶部留 reveal_h 在屏幕内，其余在屏幕外(下方)
            target_x = max(g.left(), min(g.right() - ww, cur_x))
            target_y = g.bottom() - reveal_h
            self._peek_target_x = target_x
            self._peek_target_y = target_y
        else:
            # 从左右侧露半边
            reveal_w = int(self._disp_w * 0.30)
            if d == "left":
                target_x = g.left() - ww + reveal_w
            else:
                target_x = g.right() - reveal_w
            self._peek_target_x = target_x
            self._peek_target_y = max(g.top(), min(g.bottom() - wh, cur_y))

        self._peek_origin_x = cur_x
        self._peek_origin_y = cur_y
        self._peek_phase = "out"
        self._peek_timer = time.perf_counter()
        # 手动/闲置躲猫猫都不自动结束，直到用户点击或拖拽才退出
        self._set_state(S_PEEK, 0)
        if d == "left":
            self._facing = 1
        elif d == "right":
            self._facing = -1

    def _enter_dance(self, loops=2):
        """开始跳逐帧舞蹈（仅当前皮肤拥有 dance 动画时有效）。"""
        frames = self._skin_anims.get("dance")
        if not frames:
            return False
        self._frame_seq = frames
        self._frame_fps = 12
        self._frame_start = time.perf_counter()
        self._frame_loops = loops
        dur = len(frames) / self._frame_fps * max(1, loops)
        self._set_state(S_DANCE, dur)
        if not self._quiet_mode:
            self.bubble.show_random(DANCE_TEXTS, 2200)
        return True

    def _pick_edge(self, rect):
        """从窗口四条边里随机选一条边。

        四条边都可选（贴屏的边由 _tick_stand 自动走"内侧"，不会跑到屏外）。
        这样最大化窗口也能四边走动。
        """
        return random.choice(["top", "bottom", "left", "right"])

    def _enter_stand(self):
        """跳到【鼠标当前所悬停的窗口】某条边上，进入窗口玩耍模式。

        关键修复：旧版用 GetForegroundWindow，但桌宠是置顶窗口，前台永远是
        桌宠自己/它的菜单 → 永远站到错误目标。改用鼠标下方的窗口（WindowFromPoint），
        彻底绕开"前台被桌宠抢"的死结。

        四边支持：从窗口四条边随机选一条可站边（跳过贴屏的边），
        站上去后沿该边走动，到角有概率转相邻边。
        """
        cp = cursor_pos()
        if not cp:
            return False
        result = window_at_point(*cp)
        if not result:
            return False
        rect, hwnd = result
        self._stand_rect = rect
        self._stand_hwnd = hwnd        # 记住所站窗口句柄，供 _tick_stand 跟踪
        self._stand_edge = self._pick_edge(rect)   # 当前站哪条边
        self._stand_walk_dir = random.choice([-1, 1])
        self._stand_sub = "walk"        # 玩耍子行为：walk/peek
        self._stand_sub_t = 0.0         # 当前子行为已持续时间
        self._stand_next_switch = random.uniform(3, 6)   # 下次切换子行为的时间
        # 到角换边冷却：防止在角上反复横跳
        self._stand_corner_t = 0.0
        # 鼠标切换节流：在新窗口上的停留计时 + 状态
        self._stand_mouse_t = 0.0
        self._stand_on_new = False
        self._stand_new_result = None
        self._set_state(S_STAND, 0)
        self._play(ANIM_JUMP)
        return True

    def _maybe_turn_corner(self, edge, rect):
        """走到边的端点（角）时，有概率转到相邻的垂直边继续走。

        返回 True 表示已换边（调用方应跳过本帧后续）。
        带 _stand_corner_t 冷却（≥1.5s），防止在角上反复横跳。
        """
        if getattr(self, "_stand_corner_t", 0) < 1.5:
            return False
        if random.random() > 0.45:   # 45% 概率换边，55% 原边转身继续
            return False
        # 角→相邻边映射：水平边走到端点转相邻垂直边，反之亦然
        # （贴屏的边也可站，_tick_stand 会自动走内侧，故不再过滤）
        left, top, right, bottom = rect
        # 水平边走到端点 → 转左/右垂直边
        if edge in ("top", "bottom"):
            cand = "left" if self._stand_walk_dir == -1 else "right"
        else:  # left/right 垂直边走到端点 → 转上/下水平边
            cand = "top" if self._stand_walk_dir == -1 else "bottom"
        # 换边：重置走动方向（新边从头走），抖一下表示"翻过去"
        self._stand_edge = cand
        self._stand_walk_dir = random.choice([-1, 1])
        self._stand_sub = "walk"
        self._stand_sub_t = 0
        self._stand_corner_t = 0
        self._play(ANIM_JUMP)
        return True

    def _tick_stand(self, dt):
        """窗口玩耍模式：站【所站窗口】某条边走动 / 探头 / 到角换边 / 鼠标停新窗口则跳过去。

        跟踪逻辑（修复核心）：不再查 GetForegroundWindow（永远是桌宠自己），
        而是用 _enter_stand 记下的 _stand_hwnd 判断所站窗口是否还可见——
        在就读它最新矩形（窗口可能移动/缩放），不可见才回桌面。
        切窗口靠鼠标停留判定（节流，避免快速划过乱跳）。
        """
        hwnd = getattr(self, "_stand_hwnd", None)
        # —— 所站窗口消失/最小化/关闭 → 回 IDLE 等待（不关模式）——
        # 模式只在菜单手动取消时关闭；窗口没了就回 IDLE，
        # 主循环的自动重站逻辑会找下一个鼠标下的窗口站上去。
        if not is_window_alive(hwnd):
            self._set_state(S_IDLE, 0)
            self._stand_hwnd = None
            if not self._quiet_mode:
                self.bubble.show_text("窗户关掉啦，我等下一个～", 1600)
            return

        # —— 读所站窗口的最新矩形（窗口可能正在被拖动/缩放）——
        rect = _hwnd_rect(hwnd)
        if not rect:
            self._set_state(S_IDLE, 0)
            self._stand_hwnd = None
            return
        self._stand_rect = rect
        left, top, right, bottom = rect

        # —— 鼠标切换节流：鼠标停在【另一个】窗口上超过阈值 → 跳过去 ——
        # 节流策略：用实例变量 _stand_on_new 保持"鼠标是否在新窗口上"状态
        # （查询降频到每 ~0.2s 一次，但状态在非查询帧保持，避免计时被误清零）。
        # 鼠标在新窗口区域持续累计真实 dt，离开则清零；累计 >= 0.5s 才切。
        self._stand_mouse_query = getattr(self, "_stand_mouse_query", 0) + dt
        if self._stand_mouse_query >= 0.2:
            self._stand_mouse_query = 0
            cp = cursor_pos()
            if cp:
                result = window_at_point(*cp)
                if result and result[1] != hwnd:
                    self._stand_on_new = True
                    self._stand_new_result = result
                else:
                    self._stand_on_new = False
                    self._stand_new_result = None
        # 计时：在新窗口上 → 累加真实 dt；否则清零
        if getattr(self, "_stand_on_new", False):
            self._stand_mouse_t = getattr(self, "_stand_mouse_t", 0) + dt
            if self._stand_mouse_t >= 0.5:
                new_result = self._stand_new_result
                self._stand_hwnd = new_result[1]
                self._stand_rect = new_result[0]
                self._stand_edge = self._pick_edge(new_result[0])  # 新窗口重选边
                self._stand_sub = "walk"
                self._stand_sub_t = 0
                self._stand_mouse_t = 0.0
                self._stand_on_new = False
                self._play(ANIM_JUMP)
                self._boost_emotion("excited", 6)
                return
        else:
            self._stand_mouse_t = 0.0

        g = self._screen_geom()
        edge = getattr(self, "_stand_edge", "top")
        dw, dh = self._disp_w, self._disp_h
        pad = self._pad

        # —— 按 edge 计算站姿固定坐标 + 走动轴的范围 ——
        # 每条边先按"站外侧（露出窗口外）"算；若会出屏（贴屏），改"站内侧"
        # （身体压在窗口边缘内侧，像 top 趴标题栏那样），并钳到屏幕可见区。
        # 这样最大化窗口（四边贴屏）也能正常四边站。
        if edge == "top":
            stand_y = top - dh + pad          # 外侧：脚踩顶边，身体露在顶边上方
            if stand_y < g.top():
                stand_y = top - pad           # 贴屏 → 趴标题栏内侧
            stand_y = max(g.top(), stand_y)   # 钳到屏幕内
            stand_x = None
            walk_lo, walk_hi = left + pad, right - dw - pad
            axis = "x"
        elif edge == "bottom":
            stand_y = bottom - pad            # 外侧：脚踩底边，身体露在底边下方
            if stand_y + dh > g.bottom():
                stand_y = bottom - dh         # 贴屏 → 站窗口底部内侧
            stand_y = min(g.bottom() - dh, stand_y)   # 钳到屏幕内
            stand_y = max(g.top(), stand_y)
            stand_x = None
            walk_lo, walk_hi = left + pad, right - dw - pad
            axis = "x"
        elif edge == "left":
            stand_x = left - dw + pad         # 外侧：右侧靠左边框，身体露在左边框左方
            if stand_x < g.left():
                stand_x = left                # 贴屏 → 站窗口左侧内侧
            stand_x = max(g.left(), stand_x)  # 钳到屏幕内
            stand_y = None
            walk_lo, walk_hi = top + pad, bottom - dh - pad
            axis = "y"
        else:  # right
            stand_x = right - pad             # 外侧：左侧靠右边框，身体露在右边框右方
            if stand_x + dw > g.right():
                stand_x = right - dw          # 贴屏 → 站窗口右侧内侧
            stand_x = min(g.right() - dw, stand_x)   # 钳到屏幕内
            stand_x = max(g.left(), stand_x)
            stand_y = None
            walk_lo, walk_hi = top + pad, bottom - dh - pad
            axis = "y"
        if walk_hi <= walk_lo:
            walk_hi = walk_lo + 1

        # —— 到角换边冷却计时（防角上反复横跳）——
        self._stand_corner_t = getattr(self, "_stand_corner_t", 0) + dt

        # —— 子行为计时，到点随机切换 ——
        self._stand_sub_t = getattr(self, "_stand_sub_t", 0) + dt
        self._stand_next_switch = getattr(self, "_stand_next_switch", 5)
        if self._stand_sub_t > self._stand_next_switch:
            self._stand_sub_t = 0
            self._stand_next_switch = random.uniform(3, 7)
            r = random.random()
            if r < 0.65:
                self._stand_sub = "walk"
                self._stand_walk_dir = random.choice([-1, 1])
            else:
                # 偶尔原地"探头"：水平边上下晃，垂直边左右晃
                self._stand_sub = "peek"
                self._peek_phase = 0.0

        # —— 执行子行为 ——
        cx = self.x()
        cy = self.y()
        if self._stand_sub in ("walk", None):
            speed = 60
            if axis == "x":
                # 水平边：沿 x 走，y 固定
                nx = cx + self._stand_walk_dir * speed * dt
                at_corner = False
                if nx < walk_lo:
                    nx = walk_lo; self._stand_walk_dir = 1; at_corner = True
                elif nx > walk_hi:
                    nx = walk_hi; self._stand_walk_dir = -1; at_corner = True
                self._facing = self._stand_walk_dir
                self.move(int(nx), int(stand_y))
                # 到角 → 有概率转相邻垂直边
                if at_corner and self._maybe_turn_corner(edge, rect):
                    return
            else:
                # 垂直边：沿 y 走，x 固定
                ny = cy + self._stand_walk_dir * speed * dt
                at_corner = False
                if ny < walk_lo:
                    ny = walk_lo; self._stand_walk_dir = 1; at_corner = True
                elif ny > walk_hi:
                    ny = walk_hi; self._stand_walk_dir = -1; at_corner = True
                # 上下走时朝向：向上=看上，向下=看下（这里简单用方向值）
                self.move(int(stand_x), int(ny))
                if at_corner and self._maybe_turn_corner(edge, rect):
                    return
        elif self._stand_sub == "peek":
            # 原地探头：水平边上下晃，垂直边左右晃（沿"离开边框"方向小幅晃）
            self._peek_phase = getattr(self, "_peek_phase", 0) + dt
            dip = abs(math.sin(self._peek_phase * 3.0)) * 8
            if axis == "x":
                self.move(int(cx), int(stand_y + dip))
            else:
                # 垂直边：朝窗口外侧晃（top/left 边向外负，bottom/right 向外正）
                outward = -1 if edge in ("left", "top") else 1
                self.move(int(stand_x + outward * dip), int(cy))


    def _current_frame_pixmap(self, now):
        """若正在播放逐帧动画，返回当前应显示的 QPixmap；否则返回 None。"""
        if self._state != S_DANCE or not self._frame_seq:
            return None
        elapsed = now - self._frame_start
        frame_i = int(elapsed * self._frame_fps) % len(self._frame_seq)
        return self._frame_seq[frame_i]

    # -------------------------------------------------------------------
    # 主循环
    # -------------------------------------------------------------------
    def _tick(self):
        """主循环：单帧异常不杀进程（try-except 兜底）。"""
        try:
            self._tick_inner()
        except Exception:
            # 单帧异常忽略，避免偶发崩溃杀掉整个程序
            import traceback
            try:
                with open(os.path.join(app_dir(), "crash.log"), "a", encoding="utf-8") as f:
                    f.write(f"\n[{datetime.datetime.now()}] _tick 异常:\n")
                    traceback.print_exc(file=f)
            except Exception:
                pass

    def _tick_inner(self):
        now = time.perf_counter()
        dt = min(0.05, now - self._last_frame_t)
        self._last_frame_t = now

        # —— 状态更新 ——
        # 昏迷时冻结所有行为（趴着不动），只保留绘制
        if self._state == S_FAINT:
            self.update()
            return
        if self._state == S_FLY:
            self._tick_physics(dt)
        elif self._state == S_WANDER:
            self._tick_wander(dt)
        elif self._state == S_PEEK:
            self._tick_peek(dt)
        elif self._state == S_SLEEP:
            self._tick_sleep(dt)
        elif self._state == S_STAND:
            self._tick_stand(dt)
        elif self._state == S_ALERT:
            self._tick_alert(dt)
        # —— 站窗口开关：开启且当前非交互/特殊状态时自动站上去 ——
        # 带冷却：_enter_stand 依赖鼠标下的窗口，鼠标若停在桌面/桌宠自身会失败，
        # 失败后等 1 秒再试，避免每帧无效调用 ctypes。
        elif self._stand_mode and not self._quiet_mode and self._state in (S_IDLE, S_LOOK):
            self._stand_retry_t = getattr(self, "_stand_retry_t", 0) + dt
            if self._stand_retry_t >= 1.0:
                self._stand_retry_t = 0
                self._enter_stand()

        # 眨眼计时
        self._blink_t += dt
        if self._blink_open and self._blink_t > self._next_blink:
            self._blink_open = False
            self._blink_t = 0.0
        elif not self._blink_open and self._blink_t > 0.12:
            self._blink_open = True
            self._blink_t = 0.0
            self._next_blink = random.uniform(2.5, 5.5)

        # 状态到期检查
        if self._state_until and now > self._state_until and \
           self._state not in (S_DRAG, S_FLY, S_SLEEP):
            self._set_state(S_IDLE, 0)

        # 位置保险：防止窗口跑出屏幕找不到（躲猫猫/站窗口除外，它们故意在边缘/窗口上）
        if self._state not in (S_PEEK, S_DRAG, S_STAND, S_ALERT):
            self._clamp_to_screen()

        self.update()

    def _clamp_to_screen(self):
        """把窗口拉回屏幕可见区，防止跑丢。"""
        g = self._screen_geom()
        x, y = self.x(), self.y()
        ww, wh = self.width(), self.height()
        nx = max(g.left(), min(g.right() - ww, x))
        ny = max(g.top(), min(g.bottom() - wh, y))
        if nx != x or ny != y:
            self.move(nx, ny)

    def _tick_physics(self, dt):
        """飞行 + 重力 + 弹跳。"""
        self._vy += self.GRAVITY * dt
        new_x = self.x() + self._vx * dt
        new_y = self.y() + self._vy * dt

        g = self._screen_geom()
        ground_y = g.bottom() - self.height()
        # 水平边界
        if new_x < g.left():
            new_x = g.left()
            self._vx = -self._vx * self.BOUNCE
        elif new_x + self.width() > g.right():
            new_x = g.right() - self.width()
            self._vx = -self._vx * self.BOUNCE
        # 落地
        if new_y >= ground_y:
            new_y = ground_y
            if abs(self._vy) > 200:
                self._vy = -self._vy * self.BOUNCE
                self._vx *= self.FRICTION
                self._play(ANIM_SQUISH)   # 落地压扁
            else:
                self._vy = 0
                self._vx *= 0.8
                if abs(self._vx) < 20:
                    self._vx = 0
                    self._grounded = True
                    self._set_state(S_IDLE, 0)
        self.move(int(new_x), int(new_y))

    def _tick_wander(self, dt):
        """闲逛：朝目标移动，带步态上下颠簸。玩耍模式开启时遇窗口边缘转向。"""
        tx, ty = self._wander_target.x(), self._wander_target.y()
        cx, cy = self.x(), self.y()
        dx, dy = tx - cx, ty - cy
        dist = math.hypot(dx, dy)
        speed = 120.0
        step = speed * dt
        if dist <= step or dist < 3:
            self._set_state(S_IDLE, random.uniform(2, 5))
        else:
            nx = cx + dx / dist * step
            ny = cy + dy / dist * step
            # 玩耍模式：检测是否会撞进前台窗口矩形 → 撞到就改方向（用缓存的rect）
            if self._stand_mode:
                rect = getattr(self, "_stand_rect", None)
                if rect:
                    wl, wt, wr, wb = rect
                    pet_l, pet_t = nx, ny
                    pet_r, pet_b = nx + self.width(), ny + self.height()
                    # 若新位置与窗口矩形重叠（且不是站在顶边上方）→ 视为撞墙
                    if (pet_r > wl and pet_l < wr and pet_b > wt and pet_t < wb):
                        # 改方向：往远离窗口中心的方向走
                        wcx = (wl + wr) / 2
                        new_dir = -1 if cx < wcx else 1
                        nx = cx + new_dir * step
                        # 重新设个远离窗口的目标
                        g = self._screen_geom()
                        self._wander_target = QPointF(
                            max(g.left(), min(g.right() - self.width(),
                                              cx + new_dir * random.randint(150, 300))),
                            cy + random.randint(-40, 40),
                        )
            # 移动前 clamp 到屏幕内，防止走出边缘看不见
            g = self._screen_geom()
            nx = max(g.left(), min(g.right() - self.width(), nx))
            ny = max(g.top(), min(g.bottom() - self.height(), ny))
            self.move(int(nx), int(ny))

    def _tick_peek(self, dt):
        """躲猫猫：躲到边缘后偶尔探头张望（支持 X/Y 双向）。"""
        target_x = getattr(self, "_peek_target_x", self.x())
        target_y = getattr(self, "_peek_target_y", self.y())
        cx, cy = self.x(), self.y()
        # 第一阶段：快速滑到躲藏位置（直接到位，避免半路可见）
        if abs(cx - target_x) > 2 or abs(cy - target_y) > 2:
            k = min(1.0, dt * 6.0)   # 较快收敛
            nx = cx + (target_x - cx) * k
            ny = cy + (target_y - cy) * k
            self.move(int(nx), int(ny))

    def _tick_sleep(self, dt):
        """睡觉：缓慢呼吸由 paint 合成，这里只在闲置结束时唤醒。"""

    # -------------------------------------------------------------------
    # 动画播放
    # -------------------------------------------------------------------
    def _play(self, anim):
        self._anim = anim
        self._anim_start = time.perf_counter()

    def _mark_pet_interact(self):
        """记录一次与宠物的互动（点击/拖动/双击/喂食），重置躲猫猫计时。"""
        self._last_pet_interact = time.perf_counter()

    def _show_click_line(self):
        """点击交互台词：普通/江湖语料库随机二选一。

        - 50% 普通短句（INTERACT_TEXTS，固定 2s）
        - 50% 江湖长文案（JIANGHU_TEXTS，按字数智能调时长 2.5s-16s）
        安静模式下点击照常显示（视为主动交互）。
        """
        if random.random() < 0.5:
            self.bubble.show_random(INTERACT_TEXTS, 2000)
        else:
            self.bubble.show_random_smart(JIANGHU_TEXTS)

    def interact(self):
        """点击互动：轮流播放瞬时动画 + 气泡。"""
        self._mark_pet_interact()
        self._hover_timer.stop()
        if self.status_tip.isVisible():
            self._hide_hover_status()
        # 健康提醒中：点击即解除
        if self._alert_active:
            self._stop_health_alert()
            return
        if self._state == S_SLEEP:
            self._wake_up()
            return
        # 站窗口状态下点击 → 先下来互动（保持开关，稍后自动再站上去）
        if self._state == S_STAND:
            self._set_state(S_IDLE, random.uniform(3, 6))
        if self._interact_cd.isActive():
            return
        self._play(INTERACT_ANIMS[self._interact_idx % len(INTERACT_ANIMS)])
        self._interact_idx += 1
        self._interact_cd.start(400)
        # 躲猫猫中被点 → 被抓到
        if self._state == S_PEEK:
            self._caught()
            return
        # —— 交互增强情感：点击让宠物开心/偶尔兴奋 ——
        self._boost_emotion("happy", 8)
        if random.random() < 0.3:
            self._boost_emotion("excited", 12)
        self._mood = min(100.0, self._mood + 3)
        # 点击台词：普通/江湖语料库随机二选一（绕过情感分支）
        self._show_click_line()

    def _wake_up(self):
        self._was_sleeping = False
        self._set_state(S_IDLE, random.uniform(4, 8))
        self._play(ANIM_POP)
        if not self._quiet_mode:
            self.bubble.show_random(WAKE_TEXTS, 2200)

    def _caught(self):
        """躲猫猫被抓：滑回屏幕可见区 + 被抓反应。"""
        self._set_state(S_IDLE, random.uniform(3, 6))
        self._play(ANIM_POP)
        self._boost_emotion("excited", 20)   # 被抓到很兴奋
        self._mood = min(100.0, self._mood + 5)
        # 从屏幕边缘滑回可见区中心附近
        g = self._screen_geom()
        target_x = max(g.left(), min(g.right() - self.width(),
                                      g.center().x() - self.width() // 2))
        target_y = max(g.top(), min(g.bottom() - self.height(),
                                     g.bottom() - self.height() - 60))
        self.move(target_x, target_y)
        if not self._quiet_mode:
            self.bubble.show_random(CAUGHT_TEXTS, 2200)

    # -------------------------------------------------------------------
    # 绘制
    # -------------------------------------------------------------------
    def paintEvent(self, _):
        # 跳舞时直接播放逐帧（不走形变变换合成）
        if self._state == S_DANCE:
            now = time.perf_counter()
            frame = self._current_frame_pixmap(now)
            if frame is not None and not frame.isNull():
                p = QPainter(self)
                p.setRenderHint(QPainter.SmoothPixmapTransform, True)
                p.setRenderHint(QPainter.Antialiasing, True)
                # 按当前显示高度等比缩放绘制，脚部贴底
                ar = frame.width() / max(1, frame.height())
                dh = self._disp_h
                dw = int(dh * ar)
                # 若尺寸变了（舞蹈帧与基础图宽高比不同），重新调整窗口
                if abs(dw - self._disp_w) > 2:
                    self._disp_w = dw
                x = (self.width() - dw) / 2
                y = self.height() - self._pad - dh
                p.drawPixmap(QRectF(x, y, dw, dh), frame, QRectF(frame.rect()))
            return

        if self._pixmap.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.Antialiasing, True)

        now = time.perf_counter()
        # —— 合成变换：每个来源独立计算，最终叠加 ——
        sx, sy, ox, oy, rot = 1.0, 1.0, 0.0, 0.0, 0.0

        # 1) 瞬时动作动画
        if self._anim:
            t = min(1.0, (now - self._anim_start) * 1000.0 / self._anim.duration_ms)
            te = ease_inout(t)
            a_sx, a_sy, a_ox, a_oy, a_rot = self._anim.sample(te)
            sx *= a_sx; sy *= a_sy
            ox += a_ox; oy += a_oy
            rot += a_rot
            if t >= 1.0:
                self._anim = None

        # 2) 呼吸（持续）
        breath = math.sin(now * 1.8) * 0.015
        sy *= (1.0 + breath)
        sx *= (1.0 - breath * 0.5)

        # 3) 睡觉状态：缓慢深呼吸 + 轻微下沉
        if self._state == S_SLEEP:
            sb = math.sin(now * 0.7) * 0.04
            sy *= (1.0 + sb)
            sx *= (1.0 - sb * 0.4)
            oy += 6

        # 4) 闲逛步态：上下颠簸
        if self._state == S_WANDER:
            bob = abs(math.sin(now * 9.0)) * 6
            oy -= bob

        # 5) 朝向（行走/偷看时翻转）
        facing = self._facing

        w, h, pad = self._disp_w, self._disp_h, self._pad
        # 基准矩形：脚部贴窗口底部偏上
        base_x = (self.width() - w) / 2 + ox
        base_y = self.height() - pad - h + oy
        anchor_x = base_x + w / 2
        anchor_y = base_y + h

        # 应用变换：先位移到锚点，旋转/翻转/缩放，再画
        p.translate(anchor_x, anchor_y)
        if rot:
            p.rotate(rot)
        p.scale(facing * sx, sy)

        draw_w = w
        draw_h = h
        target = QRectF(-draw_w / 2, -draw_h, draw_w, draw_h)
        # 昏迷时半透明（趴着不动，视觉提示状态异常）
        if self._state == S_FAINT:
            p.setOpacity(0.45)
        p.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
        p.setOpacity(1.0)   # 恢复，避免影响后续绘制

        # —— 睡觉时画 Zzz ——
        if self._state == S_SLEEP:
            self._draw_zzz(p, draw_w, draw_h, now)

    def _draw_zzz(self, p, w, h, now):
        p.save()
        f = QFont("Microsoft YaHei", 9, QFont.Bold)
        p.setFont(f)
        for i in range(3):
            ph = now * 1.2 + i * 0.8
            alpha = int(180 + 75 * math.sin(ph))
            alpha = max(60, min(255, alpha))
            p.setPen(QColor(120, 140, 200, alpha))
            size = 8 + i * 4
            off = (ph % 2.0) / 2.0
            p.drawText(QPointF(w * 0.20 + i * 14, -h * 0.95 - off * 14),
                       "Z")
        p.restore()

    # -------------------------------------------------------------------
    # 鼠标事件
    # -------------------------------------------------------------------
    def enterEvent(self, e):
        """鼠标移入：启动 2 秒悬停计时，到点显示状态浮窗。"""
        self._hover_timer.start(2000)

    def leaveEvent(self, e):
        """鼠标移出：取消悬停计时，隐藏状态浮窗。"""
        self._hover_timer.stop()
        if self.status_tip.isVisible():
            self._hide_hover_status()

    def _show_hover_status(self):
        """悬停满 2 秒：显示状态浮窗，4 秒后自动隐藏。"""
        self.status_tip.show_html(self._pet_status_html())
        self.status_tip.reposition(self.geometry())
        self._status_hide_timer.start(4000)

    def _hide_hover_status(self):
        """隐藏状态浮窗（淡出）。"""
        self.status_tip.fade_out()
        QTimer.singleShot(250, self.status_tip.hide)

    def mousePressEvent(self, e):
        try:
            self._mouse_press_inner(e)
        except Exception:
            pass    # 点击异常忽略，避免闪退

    def _mouse_press_inner(self, e):
        # 躲猫猫状态下：任意左键操作（点击/拖拽）都视为"被抓到"，立即结束
        if self._state == S_PEEK and e.button() == Qt.LeftButton:
            self._caught()
            return
        if e.button() == Qt.LeftButton:
            self._mark_pet_interact()   # 任何点击都算互动，重置躲猫猫计时
            self._dragging = True
            self._moved = False
            self._press_pos = e.globalPos()
            self._drag_offset = e.globalPos() - self.frameGeometry().topLeft()
            self._drag_last_pos = e.globalPos()
            self._drag_last_t = time.perf_counter()
        elif e.button() == Qt.RightButton:
            self._show_context_menu(e.globalPos())

    def mouseMoveEvent(self, e):
        if self._dragging and (e.buttons() & Qt.LeftButton):
            moved = (e.globalPos() - self._press_pos).manhattanLength()
            if moved > 4:
                if not self._moved:
                    self._moved = True
                    self._set_state(S_DRAG, 0)
                now = time.perf_counter()
                dpos = e.globalPos() - self._drag_last_pos
                ddt = max(0.001, now - self._drag_last_t)
                # 记录瞬时速度（用于松手抛掷）
                self._vx = dpos.x() / ddt * 0.6
                self._vy = dpos.y() / ddt * 0.6
                self._drag_last_pos = e.globalPos()
                self._drag_last_t = now
            self.move(e.globalPos() - self._drag_offset)

    def mouseReleaseEvent(self, e):
        try:
            self._mouse_release_inner(e)
        except Exception:
            pass    # 松开异常忽略，避免闪退

    def _mouse_release_inner(self, e):
        if e.button() == Qt.LeftButton:
            was_dragging = self._moved
            self._dragging = False
            if was_dragging:
                # 松手 → 根据速度决定飞还是停
                speed = math.hypot(self._vx, self._vy)
                if speed > 250:
                    self._grounded = False
                    self._set_state(S_FLY, 0)
                else:
                    self._vx = self._vy = 0
                    self._set_state(S_IDLE, 0)
            else:
                # 点击 → 互动（含双击检测）
                now_ms = e.timestamp()
                if not now_ms or now_ms <= 0:
                    now_ms = int(time.time() * 1000)
                if (now_ms - self._last_click_t < 400 and
                        (e.globalPos() - self._last_click_pos).manhattanLength() < 20):
                    self._on_double_click()
                    self._last_click_t = 0
                else:
                    self._last_click_t = now_ms
                    self._last_click_pos = e.globalPos()
                    self.interact()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._on_double_click()

    def _on_double_click(self):
        self._mark_pet_interact()
        if self._state == S_SLEEP:
            self._wake_up()
            return
        # 拥有舞蹈动画的皮肤：双击直接跳舞
        if self.skin_has_anim("dance") and self._enter_dance(loops=2):
            return
        self._play(ANIM_SPIN)
        # 双击玩耍：加心情值（每次 +8，封顶 100）
        self._mood = min(100.0, self._mood + 8)
        self._boost_emotion("happy", 6)
        self._show_click_line()

    def wheelEvent(self, e):
        delta = e.angleDelta().y()
        step = 0.07
        self._scale *= (1 + step) if delta > 0 else (1 - step)
        self._scale = max(0.2, min(2.4, self._scale))
        self._resize_window()
        self._save_config()

    # -------------------------------------------------------------------
    # 拖放：吃文件
    # -------------------------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if not urls:
            e.ignore()
            return
        e.acceptProposedAction()
        path = urls[0].toLocalFile()
        fname = os.path.basename(path)
        ext = os.path.splitext(path)[1].lower()
        # 触发吃文件动画 + 台词
        self._play(ANIM_CHOMP)
        texts = EAT_TEXTS.get(ext, EAT_TEXTS["default"])
        line = random.choice(texts).replace("你的东西", f"你的 {fname}")
        if "你的" in line:
            line = line.replace(f"你的 {fname}", f"{fname}")
        self.bubble.show_text(f"啊呜！{line}", 2600)
        self._set_state(S_IDLE, 3)

    # -------------------------------------------------------------------
    # 养成系统
    # -------------------------------------------------------------------
    def _tick_pet_state(self):
        """方案一 + 三：三角联动 + 昼夜节律。

        - 心情高 → hp/mp 衰减减慢；心情低 → 加快（三角联动）
        - hp/mp → 心情反馈（三角联动）
        - 按真实时段调整衰减倍率：清晨恢复快、深夜加速掉（昼夜节律）
        - hp 归零 → 进入昏迷（冻结衰减，喂神授丹解除）
        """
        now = time.perf_counter()
        dt_min = (now - self._last_decay_t) / 60.0
        if dt_min < 0.5:       # 至少 30 秒才算一次，避免频繁
            return
        self._last_decay_t = now

        # —— 昏迷中：冻结一切衰减（保护玩家，不连环崩盘）——
        if self._state == S_FAINT:
            return

        hour = datetime.datetime.now().hour
        time_mult = _hour_decay_mult(hour)          # 昼夜倍率
        time_mood = _hour_mood_bonus(hour)          # 心情时辰加成

        # —— 三角联动① + 昼夜：心情 → hp/mp 衰减系数 × 时辰倍率 ——
        if self._mood >= MOOD_HIGH:
            buff = DECAY_BUFF
        elif self._mood <= MOOD_LOW:
            buff = DECAY_DEBUFF
        else:
            buff = 1.0
        decay_mult = buff * time_mult   # 三角联动系数 × 昼夜倍率
        self._hp = max(0.0, self._hp - DECAY_PER_MIN["hp"] * decay_mult * dt_min)
        self._mp = max(0.0, self._mp - DECAY_PER_MIN["mp"] * decay_mult * dt_min)

        # —— hp 归零 → 进入昏迷 ——
        if self._hp <= 0:
            self._enter_faint()
            return

        # —— 三角联动② + 时辰心情：hp/mp → 心情反馈 + 时辰加成 ——
        if self._hp <= 0 or self._mp <= 0:
            self._mood = max(0.0, self._mood - 2.0 * dt_min + time_mood * dt_min)
        elif self._hp >= FULL_HP_MP and self._mp >= FULL_HP_MP:
            self._mood = min(100.0, self._mood + 0.5 * dt_min + time_mood * dt_min)
        else:
            self._mood = max(0.0, self._mood - DECAY_PER_MIN["mood"] * dt_min + time_mood * dt_min)

        # —— 深夜提醒（22 点后，冷却 30 分钟）——
        if hour >= NIGHT_WARN_HOUR or hour < 2:
            if not self._quiet_mode and now - getattr(self, "_night_warn_t", 0) > 1800:
                self.bubble.show_text(random.choice(NIGHT_TIPS), 3000)
                self._night_warn_t = now

        # —— 早晨奖励（6-9 点首次进入游戏，当天只发一次）——
        today = datetime.date.today().isoformat()
        if 6 <= hour < 9 and getattr(self, "_morning_bonus_date", "") != today:
            self._hp = min(100.0, self._hp + MORNING_BONUS)
            self._mp = min(100.0, self._mp + MORNING_BONUS)
            self._mood = min(100.0, self._mood + MORNING_BONUS)
            self._morning_bonus_date = today
            if not self._quiet_mode:
                self.bubble.show_text(random.choice(MORNING_TIPS), 3200)

        # —— 低状态主动警告（冷却 5 分钟）——
        if self._state not in (S_SLEEP, S_DRAG, S_FLY, S_PEEK, S_FAINT) and \
           not self._quiet_mode and now - self._last_low_warn > 300:
            if self._hp < 25:
                self.bubble.show_random(LOW_HP_TEXTS, 2400)
                self._last_low_warn = now
            elif self._mp < 25:
                self.bubble.show_random(LOW_MP_TEXTS, 2400)
                self._last_low_warn = now

        # —— 情感衰减/增长 ——
        for emo in EMOTIONS:
            self._emotions[emo] = max(0.0, self._emotions[emo] - EMOTION_DECAY[emo] * dt_min)
        # 没人理时无聊增长
        since_interact = now - self._last_pet_interact
        if since_interact > 60:   # 超过 1 分钟没互动
            self._emotions["bored"] = min(100.0, self._emotions["bored"] + BORED_GROWTH_PER_MIN * dt_min)
        # 低心情 → 悲伤增长；高心情 → 开心增长
        if self._mood < 40:
            self._emotions["sad"] = min(100.0, self._emotions["sad"] + 1.0 * dt_min)
        elif self._mood > 70:
            self._emotions["happy"] = min(100.0, self._emotions["happy"] + 0.5 * dt_min)

    def _enter_faint(self):
        """进入昏迷状态：趴下不动，冻结衰减，等待神授丹救活。"""
        self._hp = 0.0
        self._set_state(S_FAINT, 0)   # duration=0 表示持续到被救活
        if not self._quiet_mode:
            self.bubble.show_random(FAINT_TEXTS, 4000)
        self._save_config()

    def _revive_from_faint(self):
        """从昏迷中苏醒（喂神授丹触发）。"""
        self._hp = min(100.0, self._hp + ITEMS["shenshou_dan"]["hp"])
        self._set_state(S_IDLE, 0)
        if not self._quiet_mode:
            self.bubble.show_text("苏醒过来！谢谢你救我～", 2400)
        self._save_config()

    def dominant_emotion(self):
        """返回当前主导情感类型（值最高的那个）。"""
        return max(EMOTIONS, key=lambda e: self._emotions[e])

    def _boost_emotion(self, emo, amount):
        """提升某种情感，其他对立情感略降。"""
        self._emotions[emo] = min(100.0, self._emotions[emo] + amount)
        # 开心↔悲伤 互斥
        if emo == "happy":
            self._emotions["sad"] = max(0.0, self._emotions["sad"] - amount * 0.6)
        elif emo == "sad":
            self._emotions["happy"] = max(0.0, self._emotions["happy"] - amount * 0.6)
        elif emo == "excited":
            self._emotions["bored"] = max(0.0, self._emotions["bored"] - amount * 0.8)
            self._emotions["happy"] = min(100.0, self._emotions["happy"] + amount * 0.3)

    # -------------------------------------------------------------------
    # 健康提醒
    # -------------------------------------------------------------------
    def _tick_health_reminder(self):
        """每秒检查：久坐/喝水是否到点，到点触发强提醒。"""
        if self._alert_active:
            return   # 已在提醒中，等用户解除
        now = time.perf_counter()
        idle = idle_seconds()
        triggered = None    # "sit" / "drink" / None

        # —— 久坐提醒（按系统闲置时间）——
        if self._sit_interval > 0:
            remain = self._sit_interval - idle
            # 预提醒：距到点剩 pre_alert 秒时发一次轻气泡
            if self._pre_alert > 0 and 0 < remain <= self._pre_alert:
                if not self._pre_alert_sent["sit"]:
                    self._pre_alert_sent["sit"] = True
                    self.bubble.show_random(HEALTH_PRE_TEXTS, 2200)
            if idle >= self._sit_interval:
                triggered = "sit"

        # —— 喝水提醒（按固定间隔）——
        if not triggered and self._drink_interval > 0:
            since_drink = now - self._last_drink_t
            remain = self._drink_interval - since_drink
            if self._pre_alert > 0 and 0 < remain <= self._pre_alert:
                if not self._pre_alert_sent["drink"]:
                    self._pre_alert_sent["drink"] = True
                    self.bubble.show_random(HEALTH_PRE_TEXTS, 2200)
            if since_drink >= self._drink_interval:
                triggered = "drink"

        if triggered:
            self._start_health_alert(triggered)

    def _start_health_alert(self, reason):
        """启动强提醒：放大 + 居中 + 持续动画。

        用 _scale 驱动放大（而非 paintEvent 里单独缩放），这样窗口、padding、
        角色一起按比例变大，天然不会裁切。
        """
        self._alert_active = True
        self._alert_reason = reason
        # 保存当前 scale/状态/位置，结束时恢复
        self._alert_prev_scale = self._scale
        self._alert_prev_state = self._state
        self._alert_prev_pos = (self.x(), self.y())
        # 放大：scale × 1.8（_resize_window 自动算出匹配的窗口+padding）
        self._scale = min(2.4, self._scale * 1.8)
        self._resize_window()
        # 移到屏幕居中（用新尺寸算中心）
        g = self._screen_geom()
        cx = g.center().x() - self.width() // 2
        cy = g.center().y() - self.height() // 2
        self.move(cx, cy)
        # 进入提醒状态，播选定动画
        self._set_state(S_ALERT, 0)
        anim = HEALTH_ANIM_MAP.get(self._health_anim, ANIM_JUMP)
        self._alert_anim = anim
        self._play(anim)
        # 强提醒气泡
        texts = SIT_ALERT_TEXTS if reason == "sit" else DRINK_ALERT_TEXTS
        self.bubble.show_text(random.choice(texts), 4000)

    def _stop_health_alert(self):
        """解除强提醒：恢复大小/位置，重置计时。"""
        if not self._alert_active:
            return
        self._alert_active = False
        self._alert_reason = ""
        # 恢复原始 scale（_resize_window 自动恢复窗口+padding）
        self._scale = getattr(self, "_alert_prev_scale", self._scale)
        self._resize_window()
        # 重置喝水计时（用户响应了，下一轮重新计时）
        self._last_drink_t = time.perf_counter()
        # 重置预提醒标记
        self._pre_alert_sent = {"sit": False, "drink": False}
        # 恢复状态和位置
        self._set_state(S_IDLE, random.uniform(3, 6))
        px, py = getattr(self, "_alert_prev_pos", (self.x(), self.y()))
        self.move(px, py)
        self.bubble.show_text("这就好啦～记得多活动！", 2000)

    def _tick_alert(self, dt):
        """强提醒中：循环播放动画。"""
        if self._anim is None:
            self._play(getattr(self, "_alert_anim", ANIM_JUMP))

    def _feed(self, item_id):
        """喂食某道具：扣库存、加状态、播放动画台词。"""
        self._mark_pet_interact()
        if self._inventory.get(item_id, 0) <= 0:
            item = ITEMS.get(item_id, {})
            name = item.get("name", item_id)
            self.bubble.show_text(
                random.choice(NO_ITEM_TEXTS).replace("{item}", name), 2200
            )
            return
        item = ITEMS[item_id]
        self._inventory[item_id] -= 1
        # —— 昏迷中喂神授丹 → 直接苏醒（优先于常规加血逻辑）——
        if self._state == S_FAINT and item["hp"] > 0:
            self._revive_from_faint()
            return
        # 计算实际增量（封顶 100 后的真实增加量）
        gain_hp = min(item["hp"], 100.0 - self._hp)
        gain_mp = min(item["mp"], 100.0 - self._mp)
        self._hp = min(100.0, self._hp + item["hp"])
        self._mp = min(100.0, self._mp + item["mp"])
        self._mood = min(100.0, self._mood + item["mood"])
        self._boost_emotion("happy", 10)   # 喂食让宠物开心
        self._play(ANIM_CHOMP)
        # 气泡显示实际恢复的属性
        if gain_hp > 0:
            self.bubble.show_text(f"嗷呜~血量+{int(gain_hp)}", 2400)
        elif gain_mp > 0:
            self.bubble.show_text(f"哈~内力+{int(gain_mp)}", 2400)
        else:
            self.bubble.show_text("吃饱啦～", 2000)
        self._save_config()

    def _add_pills(self, count, reason_texts=None, **fmt):
        """奖励 count 颗道具（神授丹/一滴醉随机）+ 气泡。

        fmt 用于台词模板替换（如 n=, min=, item=）。
        台词里的 {item} 会被替换成实际奖励的物品名。
        """
        # 随机选一种可奖励的道具（神授丹 或 一滴醉）
        reward_id = random.choice(["shenshou_dan", "yidizui"])
        self._inventory[reward_id] = self._inventory.get(reward_id, 0) + count
        self._mood = min(100.0, self._mood + 5)
        if reason_texts and not self._quiet_mode:
            # 默认把数量、物品名也填进模板
            fmt.setdefault("n", count)
            fmt.setdefault("item", ITEMS[reward_id]["name"])
            txt = random.choice(reason_texts)
            for k, v in fmt.items():
                txt = txt.replace("{" + k + "}", str(v))
            self.bubble.show_text(txt, 2600)
        self._play(ANIM_POP)
        self._save_config()

    def _add_item(self, item_id, count, reason_texts=None, **fmt):
        """奖励指定物品（非随机）+ 气泡。用于番茄钟按时长绑定奖励类型。"""
        if item_id not in ITEMS:
            item_id = "shenshou_dan"
        self._inventory[item_id] = self._inventory.get(item_id, 0) + count
        self._mood = min(100.0, self._mood + 5)
        if reason_texts and not self._quiet_mode:
            fmt.setdefault("n", count)
            fmt.setdefault("item", ITEMS[item_id]["name"])
            txt = random.choice(reason_texts)
            for k, v in fmt.items():
                txt = txt.replace("{" + k + "}", str(v))
            self.bubble.show_text(txt, 2600)
        self._play(ANIM_POP)
        self._save_config()

    def _tick_focus_reward(self, silent=False):
        """专注奖励：同一窗口保持前台每满 10 分钟 → +1 神授丹。

        切换窗口或闲置超 30 秒 → 重置计时（不鼓励挂机）。
        """
        FOCUS_INTERVAL = 600   # 每 10 分钟奖励一次
        idle = idle_seconds()
        title = foreground_window_title()
        now = time.perf_counter()
        # 闲置超 30 秒 或 切换了窗口 → 重置专注计时
        if idle > 30 or title != self._focus_window:
            # P1 分心主动提醒：仅当之前确有专注（窗口非空）且非安静模式时冒泡
            if self._focus_window and not silent and not self._quiet_mode:
                if self._cool_ok("distract", 300):   # 5 分钟冷却，避免频繁打扰
                    texts = DISTRACT_IDLE_TEXTS if idle > 30 else DISTRACT_SWITCH_TEXTS
                    self.bubble.show_random(texts, 2600)
            self._focus_window = title
            self._focus_start = now
            self._focus_rewarded_min = 0
            return
        if not title:
            return
        # 计算已专注的完整 10 分钟段数
        focus_dur = now - self._focus_start
        earned_intervals = int(focus_dur / FOCUS_INTERVAL)
        if earned_intervals > self._focus_rewarded_min:
            new_earned = earned_intervals - self._focus_rewarded_min
            self._focus_rewarded_min = earned_intervals
            self._add_pills(
                new_earned,
                None if silent else FOCUS_REWARD_TEXTS,
                min=earned_intervals * 10,
            )

    # —— 番茄钟 ——
    POMODORO_MIN = 25       # 默认番茄时长（分钟）
    POMODORO_REWARD = 3     # 完成奖励神授丹数
    POMODORO_MIN_RANGE = (1, 120)   # 自定义时长范围（分钟）

    def _pomo_minutes(self):
        """当前番茄时长（分钟）：自定义优先，否则用默认 POMODORO_MIN。"""
        if 1 <= self._pomodoro_custom_min <= 120:
            return self._pomodoro_custom_min
        return self.POMODORO_MIN

    def _start_pomodoro(self):
        """开始一个番茄钟（时长由 _pomo_minutes 决定）。"""
        if self._pomodoro_active:
            self.bubble.show_text("番茄钟已经在计时啦～", 1800)
            return
        mins = self._pomo_minutes()
        self._pomodoro_active = True
        self._pomodoro_end = time.perf_counter() + mins * 60
        self._pomodoro_last_title = foreground_window_title()
        self._pomodoro_last_idle = idle_seconds()
        self._pomodoro_last_show = 0   # 上次显示倒计时的分钟
        self.bubble.show_text(f"🍅 番茄钟开始！专注 {mins} 分钟吧～", 2400)

    def _cancel_pomodoro(self):
        """取消进行中的番茄钟（不奖励，区别于正常完成）。"""
        if not self._pomodoro_active:
            return
        self._pomodoro_active = False
        self._pomodoro_end = 0.0
        self._update_task_banner()   # 立即刷新横幅（移除番茄计时段）
        if not self._quiet_mode:
            self.bubble.show_random(POMODORO_CANCEL_TEXTS, 2200)

    def _set_pomodoro_duration(self):
        """设置自定义番茄时长（1-120 分钟）。

        用 QInputDialog.getInt：自带数字输入框 + 上下步进箭头，
        既能直接打字，也能点箭头/按键盘上下键调节。
        """
        lo, hi = self.POMODORO_MIN_RANGE
        # 默认值：当前自定义值，否则默认时长
        default = self._pomo_minutes()
        mins, ok = QInputDialog.getInt(
            None, "番茄钟时长", "专注几分钟？（1-120）",
            value=default, min=lo, max=hi, step=1,
        )
        if not ok:
            return
        self._set_pomo_minutes(int(mins))

    def _set_pomo_minutes(self, mins):
        """直接设定番茄时长（供菜单快捷选项调用，不弹窗）。"""
        self._pomodoro_custom_min = max(1, min(120, int(mins)))
        self._save_config()
        reward = "神授丹" if mins <= 25 else "一滴醉"
        self.bubble.show_text(f"番茄钟设为 {mins} 分钟，完成得 1 {reward}～", 2000)

    def _tick_pomodoro(self):
        """每秒检查番茄钟状态（在 _sense 里调用）。"""
        if not self._pomodoro_active:
            return
        now = time.perf_counter()
        remaining = self._pomodoro_end - now
        # 中断条件：闲置超 2 分钟（不专注）→ 中断不奖励
        if idle_seconds() > 120:
            self._pomodoro_active = False
            if not self._quiet_mode:
                self.bubble.show_random(POMODORO_BREAK_TEXTS, 2400)
            return
        # 完成：按时长决定奖励类型（≤25分钟→神授丹，>25分钟→一滴醉）
        if remaining <= 0:
            self._pomodoro_active = False
            mins = self._pomo_minutes()
            if mins <= 25:
                reward_id = "shenshou_dan"    # 短番茄 → 补血（体力）
            else:
                reward_id = "yidizui"          # 长番茄 → 补内力（脑力）
            self._add_item(
                reward_id, 1,
                None if self._quiet_mode else POMODORO_DONE_TEXTS,
                n=1,
            )
            return
        # 每隔 5 分钟显示一次倒计时
        rem_min = int(remaining // 60) + 1
        if rem_min != self._pomodoro_last_show and rem_min % 5 == 0:
            self._pomodoro_last_show = rem_min
            if not self._quiet_mode:
                self.bubble.show_text(f"🍅 还剩 {rem_min} 分钟，加油！", 1800)

    def pomodoro_remaining_min(self):
        """番茄钟剩余分钟（未进行返回0）。"""
        if not self._pomodoro_active:
            return 0
        return max(0, int((self._pomodoro_end - time.perf_counter()) // 60) + 1)

    # -------------------------------------------------------------------
    # 常驻任务横幅（专注监督 P0）
    # -------------------------------------------------------------------
    def _update_task_banner(self):
        """依据「有任务 / 番茄进行中」智能显隐横幅，每秒由 _sense 调用。"""
        has_task = bool(self._current_task)
        has_pomo = self._pomodoro_active
        should_show = self._show_task_banner and (has_task or has_pomo)
        if not should_show:
            if self.task_banner.isVisible():
                self.task_banner.hide()
            return
        rem = (max(0, int(self._pomodoro_end - time.perf_counter()))
               if has_pomo else 0)
        self.task_banner.update_content(self._current_task, rem, has_pomo)
        if not self.task_banner.isVisible():
            self.task_banner.show()
            self.task_banner.raise_()
        self.task_banner.reposition(self.geometry())

    # -------------------------------------------------------------------
    # 环境感知（1Hz）
    # -------------------------------------------------------------------
    def _sense(self):
        if self._state in (S_DRAG, S_FLY):
            return
        # —— 番茄钟检查（不受安静模式影响）——
        self._tick_pomodoro()
        # —— 常驻任务/计时横幅（专注监督 P0）——
        self._update_task_banner()
        # —— 健康提醒检查（强提醒，不受安静模式影响）——
        self._tick_health_reminder()
        # —— 养成状态衰减 ——
        self._tick_pet_state()
        idle = idle_seconds()

        # —— 检测"从闲置中醒来" ——
        if self._was_sleeping and idle < 5:
            self._wake_up()
            return
        # —— 闲置触发躲猫猫（基于"与宠物的互动"，而非系统级闲置）——
        # 含义：N 分钟没点/拖/喂宠物 → 自动躲起来（即使你一直在用电脑）
        # 若设了闲置躲猫猫，则不再走 3 分钟睡觉逻辑
        if self._idle_peek > 0:
            since_interact = time.perf_counter() - self._last_pet_interact
            if since_interact >= self._idle_peek:
                if self._state not in (S_PEEK, S_DRAG, S_FLY):
                    # 若在睡觉，先唤醒再去躲
                    if self._state == S_SLEEP:
                        self._was_sleeping = False
                    self._enter_peek()
                self._last_active_idle = idle
                return
        # —— 闲置超 3 分钟 → 睡觉（仅当未设闲置躲猫猫时）——
        elif idle > 180:
            if self._state != S_SLEEP:
                self._enter_sleep()
            self._last_active_idle = idle
            return

        if self._quiet_mode:
            # 安静模式：仍执行专注奖励检测，但不显示台词气泡
            self._tick_focus_reward(silent=True)
            return

        # —— 专注奖励检测（同一窗口持续 ≥10 分钟 → 奖励神授丹）——
        self._tick_focus_reward(silent=False)

        # —— 前台窗口识别 ——
        title = foreground_window_title()
        if title and title != self._last_fg_title:
            self._last_fg_title = title
            self._react_to_title(title)

        # —— 启动器进程检测（D:\wyclx\Launcher.exe 及其子进程）——
        # 用"从无到有"的边沿触发：检测到运行且上次未运行 → 说一句
        running = any_process_under(LAUNCHER_DIR)
        if running and not self._launcher_was_running:
            if self._cool_ok("launcher", 300):     # 5 分钟内不重复
                self.bubble.show_random(LAUNCHER_TEXTS, 2800)
        self._launcher_was_running = running

        # —— 时间感知 ——
        self._react_to_time()

        # —— 偶发闲聊（待机时随机冒泡，约每 90s 一次）——
        # 安静模式下完全不冒泡；否则从萌宠/江湖两个语料池随机抽一个池。
        if self._state == S_IDLE and not self._quiet_mode and random.random() < 0.02:
            if self._cool_ok("chatter", 60):
                if random.random() < 0.5:
                    self.bubble.show_random(IDLE_CHATTER, 2400)
                else:
                    # 江湖文案较长，用 show_random_smart 按字数给足时长
                    self.bubble.show_random_smart(JIANGHU_TEXTS)

        self._last_active_idle = idle

    def _react_to_title(self, title):
        t = title.lower()
        rules = [
            (["vscode", "visual studio code", "pycharm", "python", ".py",
              "intellij", "idea", "sublime", "vim", "neovim"], CODE_TEXTS, "code"),
            (["bilibili", "b站", "哔哩", "youtube", "netflix", "优酷", "爱奇艺",
              "腾讯视频"], VIDEO_TEXTS, "video"),
            (["chrome", "edge", "firefox", "浏览器", "browser", "safari"], BROWSE_TEXTS, "browse"),
            (["微信", "wechat", "qq", "telegram", "钉钉", "dingtalk", "discord"], CHAT_TEXTS, "chat"),
            (["steam", "epic", "原神", "league", "minecraft", "csgo", "游戏",
              "game"], GAME_TEXTS, "game"),
            (["word", "excel", "powerpoint", "ppt", "wps", "文档", "表格"], WORK_TEXTS, "work"),
        ]
        for keys, texts, topic in rules:
            if any(k in t for k in keys):
                if self._cool_ok(topic, 600):
                    self.bubble.show_random(texts, 2400)
                return

    def _react_to_time(self):
        now = datetime.datetime.now()
        hour = now.hour
        # 饭点
        if hour in (12, 18) and now.minute < 5:
            if self._cool_ok("meal", 3600):
                self.bubble.show_random(MEAL_TEXTS, 2600)
        # 深夜催睡
        elif hour >= 23 or hour < 2:
            if self._cool_ok("night", 1800):
                self.bubble.show_random(NIGHT_TEXTS, 2600)
        # 整点久坐提醒
        elif now.minute == 0 and self._cool_ok("break", 3600):
            self.bubble.show_random(BREAK_TEXTS, 2400)

    def _cool_ok(self, topic, cooldown_s):
        """该主题是否冷却完毕可触发。"""
        last = self._last_react_topic.get(topic, 0)
        if time.time() - last >= cooldown_s:
            self._last_react_topic[topic] = time.time()
            return True
        return False

    # -------------------------------------------------------------------
    # 右键菜单
    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    # 右键菜单 · 模块级样式（菜单与所有子菜单共用，QSS 自动继承）
    # -------------------------------------------------------------------
    _MENU_QSS = """
        QMenu {
            background: #ffffff;
            border: 1px solid #f0d4dd;
            border-radius: 14px;
            padding: 8px;
            font-size: 13px;
            color: #2b2b2e;
        }
        QMenu::item {
            padding: 8px 14px 8px 12px;
            border-radius: 8px;
            margin: 1px 4px;
        }
        QMenu::item:selected {
            background: #fce4ec;
            color: #c2185b;
        }
        QMenu::separator {
            height: 1px;
            background: #f4e8ec;
            margin: 5px 10px;
        }
        /* 分组小标题（disabled 的 QLabel 占位项） */
        QMenu::item:disabled {
            color: #b08aa0;
            font-size: 10.5px;
            padding: 2px 14px 2px 14px;
        }
    """

    @staticmethod
    def _menu_section(menu, text):
        """在菜单里插入一个分组小标题（不可点击的浅色标签）。

        用 QWidgetAction 包一个 QLabel，比 QAction(disabled) 排版更可控、
        不会被 hover 高亮，视觉上就是纯文字章节标题。
        """
        label = QLabel(text)
        label.setStyleSheet(
            "color:#b08aa0; font-size:10.5px; font-weight:600;"
            "padding:10px 14px 4px; letter-spacing:0.4px;"
        )
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(label)
        menu.addAction(wa)

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(self._MENU_QSS)
        # 柔和阴影：让菜单在桌面上浮起来（offset 0, blur 22, 玫红色调）
        shadow = QGraphicsDropShadowEffect(menu)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(194, 24, 91, 46))   # #c2185b @ ~18% 透明
        menu.setGraphicsEffect(shadow)

        # —— 实时同步 skins 文件夹：重新扫描，保持当前皮肤选中（按文件名匹配）——
        try:
            cur_skin_base = (self._skins[self._skin_index]["base"]
                             if self._skins and self._skin_index < len(self._skins) else None)
            self._load_skins()
            self._skin_index = 0
            if cur_skin_base:
                for i, s in enumerate(self._skins):
                    if s["base"] == cur_skin_base:
                        self._skin_index = i
        except Exception:
            self._skin_index = max(0, min(self._skin_index, len(self._skins) - 1)) if self._skins else 0

        # ============ 分组 1 · 宠物 ============
        self._menu_section(menu, "宠物")
        # —— 改名 ——
        name_act = QAction(f"✏️  改名（{self._pet_name}）", self)
        name_act.triggered.connect(self._rename_pet)
        menu.addAction(name_act)

        # —— 我的称呼（语料里"主人"的替换词）——
        master_menu = menu.addMenu(f"👤  我的称呼（{self._master}）")
        for label, val in [("   主人", "主人"), ("   少侠", "少侠")]:
            ma = QAction(("✓ " if self._master == val else "   ") + label, self)
            ma.triggered.connect(lambda _=False, v=val: self._set_master(v))
            master_menu.addAction(ma)
        master_menu.addSeparator()
        custom_ma = QAction("✏️  自定义…", self)
        custom_ma.triggered.connect(self._set_master_custom)
        master_menu.addAction(custom_ma)

        # —— 换肤 ——
        skin_menu = menu.addMenu("🎨  换肤")
        rand_act = QAction("🎲  随机皮肤", self)
        rand_act.triggered.connect(self._random_skin)
        skin_menu.addAction(rand_act)
        skin_menu.addSeparator()
        for i, _ in enumerate(self._skins):
            name = self.skin_name(i)
            # 有专属动画的皮肤标注一下
            if self.skin_has_anim("dance", i):
                name += " 💃"
            act = QAction(("✓ " if i == self._skin_index else "   ") + name, self)
            act.triggered.connect(lambda _=False, idx=i: self._switch_skin(idx))
            skin_menu.addAction(act)
        skin_menu.addSeparator()
        add_act = QAction("➕  从文件添加…", self)
        add_act.triggered.connect(self._add_skin_dialog)
        skin_menu.addAction(add_act)

        # —— 调整大小 ——
        size_menu = menu.addMenu("🖼  调整大小")
        for label, factor in [("  小 ", 0.7), ("  中 ", 1.0), ("  大 ", 1.4), ("  特大 ", 1.8)]:
            act = QAction(label, self)
            act.triggered.connect(lambda _=False, f=factor: self._set_size(f))
            size_menu.addAction(act)
        size_menu.addSeparator()
        rst = QAction("  还原默认", self)
        rst.triggered.connect(lambda: self._set_size(1.0))
        size_menu.addAction(rst)

        # ============ 分组 2 · 陪伴 ============
        self._menu_section(menu, "陪伴")
        # —— 互动 ——
        act_menu = menu.addMenu("🎮  互动")
        for label, anim in [("👋  打个招呼", ANIM_POP),
                            ("🦘  跳一下", ANIM_JUMP),
                            ("💫  转个圈", ANIM_SPIN),
                            ("🫣  躲猫猫", None)]:
            a = QAction(label, self)
            if anim:
                a.triggered.connect(lambda _=False, an=anim: self._play(an))
            else:
                a.triggered.connect(self._enter_peek)
            act_menu.addAction(a)
        # 仅当当前皮肤有舞蹈动画时显示
        if self.skin_has_anim("dance"):
            dance_a = QAction("💃  跳舞", self)
            dance_a.triggered.connect(lambda: self._enter_dance(loops=2))
            act_menu.addAction(dance_a)

        # —— 喂食 ——
        feed_menu = menu.addMenu("🍖  喂食")
        for item_id, item in ITEMS.items():
            cnt = self._inventory.get(item_id, 0)
            label = f"{item['emoji']}  {item['name']}  ×{cnt}"
            fa = QAction(label, self)
            fa.triggered.connect(lambda _=False, iid=item_id: self._feed(iid))
            feed_menu.addAction(fa)

        # —— 番茄钟（子菜单：扁平结构，每个时长直接标注奖励）——
        cur_mins = self._pomo_minutes()
        if self._pomodoro_active:
            pomo_menu = menu.addMenu(
                f"🍅  番茄进行中（剩 {self.pomodoro_remaining_min()} 分钟）")
            stop_a = QAction(
                f"⏹  取消番茄钟（剩 {self.pomodoro_remaining_min()} 分钟）", self)
            stop_a.triggered.connect(self._cancel_pomodoro)
            pomo_menu.addAction(stop_a)
        else:
            cur_reward = "神授丹" if cur_mins <= 25 else "一滴醉"
            pomo_menu = menu.addMenu(
                f"🍅  番茄钟（{cur_mins}分钟 · 得{cur_reward}）")
            start_a = QAction(
                f"▶  开始专注 {cur_mins} 分钟（得 1 {cur_reward}）", self)
            start_a.triggered.connect(self._start_pomodoro)
            pomo_menu.addAction(start_a)
        pomo_menu.addSeparator()
        # 快捷时长（每个标注奖励，当前选中打 ✓）
        for mins in [15, 25, 30, 45]:
            reward = "神授丹" if mins <= 25 else "一滴醉"
            mark = "✓ " if cur_mins == mins else "   "
            ma = QAction(f"{mark}{mins} 分钟 · 得 {reward}", self)
            ma.triggered.connect(lambda _=False, m=mins: self._set_pomo_minutes(m))
            pomo_menu.addAction(ma)
        pomo_menu.addSeparator()
        # 自定义时长
        dur_a = QAction(f"⚙  自定义时长…（当前 {cur_mins} 分钟）", self)
        dur_a.triggered.connect(self._set_pomodoro_duration)
        pomo_menu.addAction(dur_a)

        # —— 设置当前任务（专注监督 P0）——
        task_label = ("🎯  清除当前任务…" if self._current_task
                      else "🎯  设置当前任务…")
        task_a = QAction(task_label, self)
        task_a.triggered.connect(self._set_task)
        menu.addAction(task_a)

        # —— 任务横幅显示开关 ——
        banner_act = QAction("🚩  显示任务横幅", self)
        banner_act.setCheckable(True)
        banner_act.setChecked(self._show_task_banner)
        banner_act.triggered.connect(self._toggle_task_banner)
        menu.addAction(banner_act)

        # ============ 分组 3 · 行为 ============
        self._menu_section(menu, "行为")
        # —— 置顶 ——
        top_act = QAction("📌  始终置顶", self)
        top_act.setCheckable(True)
        top_act.setChecked(self._always_on_top)
        top_act.triggered.connect(self._toggle_topmost)
        menu.addAction(top_act)

        # —— 安静模式（动态文字：开启时显示效果说明）——
        if self._quiet_mode:
            quiet_label = "🔕  安静模式 ✓（少走动 · 不说话）"
        else:
            quiet_label = "🔔  安静模式（开启后少走动 · 不说话）"
        quiet_act = QAction(quiet_label, self)
        quiet_act.setCheckable(True)
        quiet_act.setChecked(self._quiet_mode)
        quiet_act.triggered.connect(self._toggle_quiet)
        menu.addAction(quiet_act)

        # —— 开机自启 ——
        auto_act = QAction("⚡  开机自启", self)
        auto_act.setCheckable(True)
        auto_act.setChecked(is_autostart_enabled())
        auto_act.triggered.connect(self._toggle_autostart)
        menu.addAction(auto_act)

        # —— 窗口玩耍模式开关 ——
        stand_act = QAction("🪟  窗口玩耍模式", self)
        stand_act.setCheckable(True)
        stand_act.setChecked(self._stand_mode)
        stand_act.triggered.connect(self._toggle_stand)
        menu.addAction(stand_act)

        # —— 闲置躲猫猫（多久不理它就自动躲起来）——
        peek_menu = menu.addMenu("🫣  闲置躲猫猫")
        cur = self._idle_peek
        for label, secs in [("  关闭", 0),
                            ("  5 分钟", 300),
                            ("  10 分钟", 600),
                            ("  15 分钟", 900)]:
            a = QAction(("✓ " if cur == secs else "   ") + label, self)
            a.triggered.connect(lambda _=False, s=secs: self._set_idle_peek(s))
            peek_menu.addAction(a)

        # —— 健康提醒设置 ——
        health_menu = menu.addMenu("🩺  健康提醒")
        # 久坐间隔（快捷档位 + 自定义）
        sit_menu = health_menu.addMenu("🪑  久坐间隔")
        for label, secs in [("  关闭", 0), ("  30 分钟", 1800),
                            ("  45 分钟", 2700), ("  60 分钟", 3600)]:
            a = QAction(("✓ " if self._sit_interval == secs else "   ") + label, self)
            a.triggered.connect(lambda _=False, s=secs: self._set_health("sit_interval", s))
            sit_menu.addAction(a)
        sit_menu.addSeparator()
        sit_cur = self._sit_interval // 60 if self._sit_interval else 0
        sit_c = QAction(f"✏️  自定义…（当前 {sit_cur} 分钟）", self)
        sit_c.triggered.connect(lambda: self._set_health_custom("sit_interval", 5, 240))
        sit_menu.addAction(sit_c)
        # 喝水间隔
        drink_menu = health_menu.addMenu("💧  喝水间隔")
        for label, secs in [("  关闭", 0), ("  30 分钟", 1800),
                            ("  60 分钟", 3600), ("  90 分钟", 5400)]:
            a = QAction(("✓ " if self._drink_interval == secs else "   ") + label, self)
            a.triggered.connect(lambda _=False, s=secs: self._set_health("drink_interval", s))
            drink_menu.addAction(a)
        drink_menu.addSeparator()
        drk_cur = self._drink_interval // 60 if self._drink_interval else 0
        drk_c = QAction(f"✏️  自定义…（当前 {drk_cur} 分钟）", self)
        drk_c.triggered.connect(lambda: self._set_health_custom("drink_interval", 5, 480))
        drink_menu.addAction(drk_c)
        # 预提醒时间
        pre_menu = health_menu.addMenu("⏰  预提醒提前")
        for label, secs in [("  关闭", 0), ("  1 分钟", 60),
                            ("  2 分钟", 120), ("  5 分钟", 300)]:
            a = QAction(("✓ " if self._pre_alert == secs else "   ") + label, self)
            a.triggered.connect(lambda _=False, s=secs: self._set_health("pre_alert", s))
            pre_menu.addAction(a)
        pre_menu.addSeparator()
        pre_cur = self._pre_alert // 60 if self._pre_alert else 0
        pre_c = QAction(f"✏️  自定义…（当前 {pre_cur} 分钟）", self)
        pre_c.triggered.connect(lambda: self._set_health_custom("pre_alert", 1, 30))
        pre_menu.addAction(pre_c)
        # 提醒动画
        anim_menu = health_menu.addMenu("🎬  提醒动画")
        for key, label in [("jump", "跳动"), ("shake", "抖动"), ("pop", "挥手")]:
            a = QAction(("✓ " if self._health_anim == key else "   ") + "  " + label, self)
            a.triggered.connect(lambda _=False, k=key: self._set_health("health_anim", k))
            anim_menu.addAction(a)

        menu.addSeparator()

        # ============ 分组 4 · 关于 ============
        self._menu_section(menu, "关于")
        # —— 检查更新 ——
        update_act = QAction("🔄  检查更新", self)
        update_act.triggered.connect(lambda: self.updater.check(silent=False))
        menu.addAction(update_act)
        about_act = QAction(f"ℹ️  关于（v{APP_VERSION}）", self)
        about_act.triggered.connect(self._show_about)
        menu.addAction(about_act)

        menu.addSeparator()

        quit_act = QAction("❌  退出程序", self)
        quit_act.triggered.connect(self._quit)
        menu.addAction(quit_act)

        # —— 署名（菜单最底部小字，不可点击）——
        sign = QAction("✦  糊宠 · 滚轮缩放·拖动·双击给我吃", self)
        sign.setEnabled(False)
        menu.addAction(sign)

        menu.exec_(pos)

    def _switch_skin(self, idx):
        if idx == self._skin_index:
            return
        self._apply_skin(idx)
        self._save_config()

    def _random_skin(self):
        if len(self._skins) <= 1:
            self._apply_skin(random.randrange(len(self._skins)))
            return
        idx = random.randrange(len(self._skins))
        while idx == self._skin_index:
            idx = random.randrange(len(self._skins))
        self._apply_skin(idx)
        self._save_config()

    def _add_skin_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择皮肤图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.gif)"
        )
        if not path:
            return
        sd = skins_dir()
        os.makedirs(sd, exist_ok=True)
        dst = os.path.join(sd, os.path.basename(path))
        try:
            import shutil
            shutil.copy(path, dst)
        except Exception as e:
            QMessageBox.warning(self, "添加失败", str(e))
            return
        self._load_skins()
        # 切到新加的（按文件名匹配皮肤 name）
        base = os.path.splitext(os.path.basename(dst))[0]
        for i, s in enumerate(self._skins):
            if s["name"] == base:
                self._apply_skin(i)
                break
        self._save_config()
        self.bubble.show_text("换上新衣服啦！", 2000)

    def _set_size(self, factor):
        self._scale = max(0.4, min(2.4, factor))
        self._resize_window()
        self._save_config()

    def _toggle_topmost(self):
        self._always_on_top = not self._always_on_top
        flags = self.windowFlags()
        if self._always_on_top:
            self.setWindowFlags(flags | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowStaysOnTopHint)
        self.show()
        self._save_config()

    def _toggle_quiet(self):
        self._quiet_mode = not self._quiet_mode
        if self._quiet_mode:
            self.bubble.hide()
            # 正在站窗口玩耍时切安静 → 立即停下回桌面，避免 _tick_stand 继续驱动走动
            if self._state == S_STAND:
                self._stand_hwnd = None
                self._set_state(S_IDLE, random.uniform(8, 16))
        self._save_config()

    def _toggle_autostart(self):
        r"""切换开机自启（写/删注册表 HKCU\...\Run）。"""
        target = not is_autostart_enabled()
        ok, err = set_autostart(target)
        if ok:
            if target:
                self.bubble.show_text("已开启开机自启～下次开机会自动见面！", 2600)
            else:
                self.bubble.show_text("已关闭开机自启～", 2000)
        else:
            self.bubble.show_text(f"设置失败：{err}", 3000)

    # -------------------------------------------------------------------
    # 当前任务 & 任务横幅（专注监督 P0）
    # -------------------------------------------------------------------
    def _set_task(self):
        """弹出输入框设置或清除当前专注任务。"""
        # 用 None 作 parent（与 _rename_pet 同款，避免 Tool 窗口焦点异常）
        task, ok = QInputDialog.getText(
            None, "设置当前任务", "在做什么？（留空则清除）：",
            text=self._current_task,
        )
        if not ok:
            return
        self._current_task = task.strip()[:30]
        self._task_start = time.perf_counter() if self._current_task else 0.0
        self._save_config()
        self._update_task_banner()
        if self._current_task:
            self.bubble.show_text(f"收到！专心做「{self._current_task}」吧～", 2200)
        else:
            self.bubble.show_text("任务已清空～", 1600)

    def _toggle_task_banner(self):
        """开关：常驻任务横幅的显示。"""
        self._show_task_banner = not self._show_task_banner
        self._save_config()
        self._update_task_banner()

    def _toggle_stand(self):
        """开关：站窗口顶边。"""
        self._stand_mode = not self._stand_mode
        self._save_config()
        if self._stand_mode:
            # 立即尝试站上去
            if self._enter_stand():
                self.bubble.show_text("跳上来啦！我在窗口上玩耍～", 2000)
            else:
                # 找不到窗口不关模式，回 IDLE 等待主循环自动重站
                self.bubble.show_text("先把鼠标放到一个窗口上哦～", 2000)
        else:
            # 关闭：回到桌面
            if self._state == S_STAND:
                self._set_state(S_IDLE, 0)
            self.bubble.show_text("回到桌面啦～", 1800)

    def _set_idle_peek(self, secs):
        """设置闲置多久后自动躲猫猫（0=关闭）。"""
        self._idle_peek = secs
        self._save_config()
        if secs == 0:
            self.bubble.show_text("好的，不再自动躲猫猫啦", 1800)
        else:
            mins = secs // 60
            self.bubble.show_text(f"好的，{mins} 分钟不理我就躲起来～", 1800)

    def _set_health(self, key, value):
        """设置健康提醒参数（sit_interval/drink_interval/pre_alert/health_anim）。"""
        setattr(self, "_" + key, value)
        # 改间隔后重置预提醒标记和喝水计时
        if key in ("sit_interval", "pre_alert"):
            self._pre_alert_sent = {"sit": False, "drink": False}
        if key == "drink_interval":
            self._last_drink_t = time.perf_counter()
            self._pre_alert_sent["drink"] = False
        self._save_config()
        names = {"sit_interval": "久坐", "drink_interval": "喝水",
                 "pre_alert": "预提醒", "health_anim": "提醒动画"}
        self.bubble.show_text(f"{names.get(key,'')}设置已更新～", 1500)

    def _set_health_custom(self, key, min_min, max_min):
        """弹输入框让用户自定义健康提醒间隔（分钟，0=关闭）。"""
        names = {"sit_interval": "久坐", "drink_interval": "喝水",
                 "pre_alert": "预提醒"}
        cur_min = getattr(self, "_" + key) // 60
        val, ok = QInputDialog.getInt(
            None, f"自定义{names.get(key,'')}间隔",
            f"输入分钟数（0=关闭，{min_min}-{max_min}）：",
            value=cur_min, min=0, max=max_min, step=5,
        )
        if not ok:
            return
        self._set_health(key, val * 60)

    def _rename_pet(self):
        """弹出输入框给宠物改名。"""
        # 用 None 作 parent（宠物窗口是 Tool 类型，做 dialog 父窗口可能焦点异常）
        name, ok = QInputDialog.getText(
            None, "给宠物起个名字", "输入昵称（最多 12 个字）：",
            text=self._pet_name,
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            self.bubble.show_text("名字不能为空哦～", 1800)
            return
        self._pet_name = name[:12]
        self._save_config()
        self._play(ANIM_POP)
        self.bubble.show_text(f"以后叫我 {self._pet_name} 吧！", 2200)
        # 二次确认保存成功
        self._save_config()

    def _set_master(self, val):
        """设置玩家称呼（主人/少侠）。"""
        self._master = val
        self.bubble._master = val
        self._save_config()
        self.bubble.show_text(f"好的，以后叫你{val}～", 2000)

    def _set_master_custom(self):
        """自定义玩家称呼。"""
        val, ok = QInputDialog.getText(
            None, "我的称呼", "输入你想让我怎么称呼你（最多 8 字）：",
            text=self._master,
        )
        if not ok:
            return
        val = val.strip()
        if not val:
            self.bubble.show_text("称呼不能为空哦～", 1800)
            return
        self._master = val[:8]
        self.bubble._master = self._master
        self._save_config()
        self.bubble.show_text(f"记住啦，{self._master}！", 2000)

    def show_welcome_if_first_run(self):
        """首次运行弹出欢迎/使用说明对话框。"""
        if not self._first_run:
            return
        msg = QMessageBox(self)
        msg.setWindowTitle(f"{self._pet_name} · 欢迎使用")
        msg.setIcon(QMessageBox.Information)
        msg.setText(
            "<div style='font-size:14px;line-height:1.7'>"
            "<b style='font-size:16px;color:#c2185b'>🐾 你的桌面小可爱来啦！</b><br><br>"
            "<b>🎮 操作方式：</b><br>"
            "&nbsp;&nbsp;• <b>左键点击</b> —— 跟它互动（跳/压/抖/转）<br>"
            "&nbsp;&nbsp;• <b>左键拖动</b> —— 移动位置（快速甩会飞出去弹跳）<br>"
            "&nbsp;&nbsp;• <b>双击</b> —— 转圈 / 跳舞（小狼皮肤）<br>"
            "&nbsp;&nbsp;• <b>右键</b> —— 菜单（换肤/调整大小/互动/置顶/退出）<br>"
            "&nbsp;&nbsp;• <b>滚轮</b> —— 缩放大小<br>"
            "&nbsp;&nbsp;• <b>拖文件到它身上</b> —— \"啊呜\"吃掉（仅动画）<br><br>"
            "<b>✨ 它会自己做的事：</b><br>"
            "&nbsp;&nbsp;• 闲置时会呼吸、眨眼、闲逛、偷看你<br>"
            "&nbsp;&nbsp;• 你离开久了它会打瞌睡（Zzz），回来会惊醒<br>"
            "&nbsp;&nbsp;• 能感知你在用的软件，偶尔搭话（可右键开\"安静模式\"）<br><br>"
            "<b>👗 换肤：</b>右键 → 换肤，或把透明 PNG 放进程序旁的 "
            "<code>skins</code> 文件夹<br>"
            "</div>"
        )
        msg.setStandardButtons(QMessageBox.Ok)
        msg.button(QMessageBox.Ok).setText("知道啦，开玩！")
        msg.exec_()
        # 标记已看过，下次不再弹
        self._first_run = False
        self._save_config()

    def _quit(self):
        self._save_config()
        QApplication.quit()

    def _mood_emoji(self, v):
        """根据数值(0-100)返回表情：高→开心，低→难受。"""
        if v >= 80:
            return "😄"
        if v >= 60:
            return "🙂"
        if v >= 40:
            return "😐"
        if v >= 20:
            return "😟"
        return "🥵"

    def _bar(self, label, value, color):
        """生成表情风状态条（用表格模拟，兼容 Qt rich text）。

        表情 + 标签 + 进度条(10格 █░) + 数值
        """
        v = max(0, min(100, int(value)))
        emoji = self._mood_emoji(v)
        # 进度条用 10 格字符表示，每格 10%
        filled = v // 10
        bar = "█" * filled + "░" * (10 - filled)
        return (
            f"<tr>"
            f"<td style='width:24px'>{emoji}</td>"
            f"<td style='color:#666;width:60px'>{label}</td>"
            f"<td><font color='{color}'><b>{bar}</b></font></td>"
            f"<td style='color:{color};font-weight:bold'>{v}</td>"
            f"</tr>"
        )

    def _pet_status_html(self):
        """养成状态 HTML（表情心情风，用表格兼容 Qt rich text）。"""
        pills = self._inventory.get("shenshou_dan", 0)
        yidizui = self._inventory.get("yidizui", 0)
        html = (
            f"<table border='0' cellpadding='2' cellspacing='0' "
            f"bgcolor='#fff5f8' width='100%'><tr><td>"
            f"<b style='color:#c2185b'>📊 {self._pet_name}的状态</b><br>"
            f"<table border='0' cellpadding='1' cellspacing='0'>"
            + self._bar("❤️血量", self._hp, "#e53935")
            + self._bar("🔷内力", self._mp, "#1e88e5")
            + self._bar("😊心情", self._mood, "#d81b60")
            + f"</table>"
            f"<font color='#888'>🎒 </font>"
            f"<b>背包</b>："
            f"<img src='{ITEMS['shenshou_dan']['icon']}' width='20' height='20'> 神授丹 ×{pills}　"
            f"<img src='{ITEMS['yidizui']['icon']}' width='20' height='20'> 一滴醉 ×{yidizui}<br>"
        )
        # 主导情感
        dom = self.dominant_emotion()
        html += (
            f"<font color='#888'>💗 </font>"
            f"<b>当前心情</b>：{EMOTION_EMOJI[dom]} {EMOTION_NAMES[dom]}"
            f"（{int(self._emotions[dom])}）<br>"
        )
        if self._pomodoro_active:
            html += f"<font color='#e53935'>🍅 番茄进行中（剩 {self.pomodoro_remaining_min()} 分钟）</font><br>"
        html += "</td></tr></table>"
        return html

    def _show_about(self):
        QMessageBox.about(
            self, "关于",
            f"<div style='font-size:13px;line-height:1.7'>"
            f"<b style='color:#c2185b;font-size:15px'>🐾 糊宠</b>　"
            f"<b>v{APP_VERSION}</b>　"
            f"<span style='color:#888'>我的宠物：<b style='color:#c2185b'>{self._pet_name}</b></span><br><br>"
            + self._pet_status_html()
            + f"陪伴你的桌面小可爱。<br>"
            f"右键换肤、喂食、番茄钟，滚轮缩放。<br><br>"
            f"如需检查新版本，请右键 → 检查更新。"
            f"<hr style='border:none;border-top:1px solid #eee;margin-top:10px'>"
            f"<span style='color:#bbb;font-size:11px'>✦ by 红烧茄子</span>"
            f"</div>"
        )

    # -------------------------------------------------------------------
    def closeEvent(self, e):
        self._save_config()
        super().closeEvent(e)
        # 主窗口被关闭（如系统关机/任务管理器）→ 退出整个程序
        QApplication.quit()


# ===========================================================================
# 自动更新模块
# ===========================================================================
def _parse_version(v):
    """把 '1.2.3' 解析成 (1,2,3) 用于比较；非法返回 (0,0,0)。"""
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except Exception:
        return (0, 0, 0)


def _fetch_json(url, timeout=12):
    """下载并解析 JSON。失败返回 None。"""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE          # 部分用户系统证书过期，放宽校验
        req = urllib.request.Request(url, headers={
            "User-Agent": "HuChong-Updater/" + APP_VERSION,
            "Cache-Control": "no-cache",
        })
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            data = r.read().decode("utf-8")
        return json.loads(data)
    except Exception:
        return None


class CheckUpdateThread(QThread):
    """后台检查更新，避免阻塞 UI。"""
    finished_signal = pyqtSignal(dict)   # result dict 或空 dict(失败)

    def run(self):
        # 依次尝试 CDN / raw，任一成功即可
        for url in (VERSION_CHECK_URL_CDN, VERSION_CHECK_URL):
            info = _fetch_json(url)
            if info and "version" in info:
                self.finished_signal.emit(info)
                return
        self.finished_signal.emit({})


class Updater:
    """封装版本检查 + 下载 + 替换重启。"""

    def __init__(self, parent_window):
        self.parent = parent_window
        self._thread = None
        self._silent = False      # 静默检查（启动时）不弹"已是最新"

    def check(self, silent=False):
        """触发一次检查。silent=True 时无新版不提示。"""
        if self._thread and self._thread.isRunning():
            return
        self._silent = silent
        self._thread = CheckUpdateThread()
        self._thread.finished_signal.connect(self._on_checked)
        self._thread.start()

    def _on_checked(self, info):
        if not info:
            if not self._silent:
                QMessageBox.warning(
                    self.parent, "检查更新",
                    "❌ 无法获取版本信息。\n请检查网络，或稍后再试。\n\n"
                    "（你也可到发布页手动查看新版本）"
                )
            return
        remote_ver = info.get("version", "0")
        if _parse_version(remote_ver) > _parse_version(APP_VERSION):
            self._prompt_update(info)
        else:
            if not self._silent:
                QMessageBox.information(
                    self.parent, "检查更新",
                    f"✨ 已是最新版本！\n当前版本：v{APP_VERSION}"
                )

    def _prompt_update(self, info):
        remote_ver = info.get("version", "?")
        date = info.get("update_date", "")
        changelog = info.get("changelog", "")
        url = info.get("download_url", "")
        msg = QMessageBox(self.parent)
        msg.setWindowTitle("发现新版本")
        msg.setIcon(QMessageBox.Information)
        msg.setText(
            f"<div style='font-size:13px;line-height:1.6'>"
            f"<b style='color:#c2185b;font-size:15px'>🎉 发现新版本 v{remote_ver}</b>"
            f"{('　更新日期：'+date) if date else ''}<br><br>"
            f"<b>更新内容：</b><br>{changelog or '（作者没有写更新说明）'}<br><br>"
            f"<b>当前版本：</b>v{APP_VERSION}<br>"
            f"</div>"
        )
        yes = msg.addButton("🔄 立即更新", QMessageBox.AcceptRole)
        no = msg.addButton("稍后再说", QMessageBox.RejectRole)
        if url:
            open_page = msg.addButton("🌐 打开下载页", QMessageBox.ActionRole)
        msg.setDefaultButton(yes)
        msg.exec_()
        clicked = msg.clickedButton()
        if clicked is yes:
            self._download_and_replace(url, remote_ver)
        elif url and clicked is open_page:
            self._open_url(url)

    @staticmethod
    def _open_url(url):
        try:
            os.startfile(url)
        except Exception:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", url])
            except Exception:
                pass

    def _download_and_replace(self, url, new_ver):
        if not url:
            QMessageBox.warning(self.parent, "更新", "❌ 该版本未提供下载地址。")
            return
        # 进度对话框
        prog = QProgressDialog(
            f"正在下载新版本 v{new_ver} ...\n（约 85MB，请耐心等待）",
            "取消", 0, 100, self.parent
        )
        prog.setWindowTitle("更新中")
        prog.setWindowModality(Qt.ApplicationModal)
        prog.setMinimumDuration(0)
        prog.setAutoClose(False)
        prog.setValue(0)

        # 在独立线程下载，避免冻结 UI
        dl = _DownloadThread(url, prog)
        dl.progress_signal.connect(prog.setValue)
        prog.canceled.connect(dl.terminate_download)
        dl.done_signal.connect(
            lambda ok, path: self._on_download_done(ok, path, prog, new_ver)
        )
        dl.start()
        self._dl_thread = dl     # 防止被回收

    def _on_download_done(self, ok, path, prog, new_ver):
        prog.close()
        if not ok or not path:
            QMessageBox.warning(
                self.parent, "更新失败",
                "❌ 下载失败，可能是网络问题或被拦截。\n"
                "建议：\n1. 检查网络/关闭代理后重试\n"
                "2. 到发布页用浏览器手动下载"
            )
            return
        # 下载成功 → 替换 exe 并重启
        ret = QMessageBox.question(
            self.parent, "更新就绪",
            f"✅ 新版本 v{new_ver} 已下载完成！\n\n点击\"是\"立即安装并重启。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if ret != QMessageBox.Yes:
            return
        self._install_and_restart(path)

    def _install_and_restart(self, new_exe_path):
        """用批处理替换正在运行的 exe 并重启。"""
        if getattr(sys, "frozen", False):
            cur_exe = sys.executable
        else:
            QMessageBox.information(
                self.parent, "更新",
                "当前以脚本模式运行，已下载新文件到：\n" + new_exe_path
            )
            return
        # 生成临时批处理：等待本进程退出 → 覆盖 exe → 重启
        bat = os.path.join(tempfile.gettempdir(), "huchong_update.bat")
        pid = os.getpid()
        with open(bat, "w", encoding="gbk") as f:
            f.write(
                "@echo off\r\n"
                "chcp 65001 >nul\r\n"
                ":: 等待旧进程退出\r\n"
                f":wait\r\n"
                f"tasklist /fi \"PID eq {pid}\" | find \"{pid}\" >nul\r\n"
                "if not errorlevel 1 (\r\n"
                "    timeout /t 1 /nobreak >nul\r\n"
                "    goto wait\r\n"
                ")\r\n"
                ":: 备份并替换\r\n"
                f"copy /y \"{cur_exe}\" \"{cur_exe}.bak\"\r\n"
                f"copy /y \"{new_exe_path}\" \"{cur_exe}\"\r\n"
                "if errorlevel 1 (\r\n"
                f"    copy /y \"{cur_exe}.bak\" \"{cur_exe}\"\r\n"
                "    echo 更新失败，已还原旧版本\r\n"
                "    pause\r\n"
                "    exit /b 1\r\n"
                ")\r\n"
                ":: 启动新版本\r\n"
                f"start \"\" \"{cur_exe}\"\r\n"
                "del \"%~f0\"\r\n"
            )
        # 通知用户并退出，让批处理接管
        QMessageBox.information(
            self.parent, "更新",
            "程序将退出并完成更新，请稍候几秒会自动重启。"
        )
        # 保存配置后退出
        if hasattr(self.parent, "_save_config"):
            self.parent._save_config()
        subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000)
        QApplication.quit()


class _DownloadThread(QThread):
    """带进度的下载线程。"""
    progress_signal = pyqtSignal(int)
    done_signal = pyqtSignal(bool, str)

    def __init__(self, url, prog_dialog):
        super().__init__()
        self.url = url
        self.prog = prog_dialog
        self._cancel = False

    def terminate_download(self):
        self._cancel = True

    def run(self):
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(self.url, headers={
                "User-Agent": "HuChong-Updater",
            })
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                total = int(r.headers.get("Content-Length", 0))
                tmp = os.path.join(tempfile.gettempdir(), "huchong_new.exe")
                downloaded = 0
                chunk = 64 * 1024
                with open(tmp, "wb") as f:
                    while True:
                        if self._cancel:
                            self.done_signal.emit(False, "")
                            return
                        data = r.read(chunk)
                        if not data:
                            break
                        f.write(data)
                        downloaded += len(data)
                        if total > 0:
                            pct = min(99, int(downloaded * 100 / total))
                            self.progress_signal.emit(pct)
                self.progress_signal.emit(100)
                self.done_signal.emit(True, tmp)
        except Exception as e:
            self.done_signal.emit(False, "")


# ===========================================================================
# 入口
# ===========================================================================
def _install_crash_guard():
    """全局崩溃防护：捕获段错误(faulthandler) + 未处理异常(excepthook)，
    写入日志文件而非静默闪退。日志在 EXE 同级目录 crash.log。"""
    import traceback
    log_path = os.path.join(app_dir(), "crash.log")
    # faulthandler：捕获 C 层段错误（如 ctypes 调用崩溃），打印到日志
    try:
        import faulthandler
        _fh = open(log_path, "a", encoding="utf-8")
        faulthandler.enable(_fh)
    except Exception:
        _fh = None
    # excepthook：捕获 Python 未处理异常
    _orig_hook = sys.excepthook
    def _hook(exc_type, exc_val, exc_tb):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.datetime.now()}] 未捕获异常:\n")
                traceback.print_exception(exc_type, exc_val, exc_tb, file=f)
        except Exception:
            pass
        _orig_hook(exc_type, exc_val, exc_tb)
    sys.excepthook = _hook


def main():
    _install_crash_guard()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    # 应用图标：资源管理器/任务栏/Alt+Tab 统一显示 assets/app.ico。
    # resource_path 兼容直接运行（脚本目录）与 PyInstaller 打包（_MEIPASS）。
    _icon_path = resource_path("app.ico")
    if os.path.exists(_icon_path):
        app.setWindowIcon(QIcon(_icon_path))
    # 桌宠有多个子窗口（气泡/状态浮窗），躲猫猫时主窗口移到屏外。
    # 设为 False：仅右键"退出"才关程序，避免子窗口隐藏/移动时误退出。
    app.setQuitOnLastWindowClosed(False)
    pet = PetWindow()
    pet.show()
    # 首次运行弹欢迎说明（看完标记，下次不再弹）
    QTimer.singleShot(400, pet.show_welcome_if_first_run)
    # 非首次运行才打招呼（避免和欢迎框同时弹气泡）
    if not pet._first_run:
        QTimer.singleShot(600, lambda: pet.bubble.show_random(GREETINGS, 2600))
    # 启动后静默检查更新（延迟，不打扰首次欢迎框）
    QTimer.singleShot(3000, lambda: pet.updater.check(silent=True))
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
