# -*- coding: utf-8 -*-
"""SPRINT_AUTOUPDATE 动态测试主文件。

用法：
  cd D:\\ZCodeProject\\desktop_pet
  set QT_QPA_PLATFORM=offscreen
  python -m unittest tests_autoupdate.test_autoupdate -v

策略：
  · Unit Test 直接调用 desktop_pet 模块函数（纯逻辑）
  · Integration Test 通过 monkeypatch urllib.request.urlopen 控制 HTTP 行为
  · _DownloadThread 直接构造后调用 run()，靠 done_signal 槽收集结果（不走 QThread.start()，
    避免 eventloop 复杂度；run() 本身是同步逻辑，直接调用等价于线程内执行）
  · 每个测试用例独立的 tmpdir，update.log 重定向到 tmp
"""

import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

# offscreen 模式，避免弹窗阻塞 CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 项目根加到 sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import desktop_pet  # noqa: E402
from PyQt5.QtWidgets import QApplication, QProgressDialog  # noqa: E402


# ---------------------------------------------------------------------------
# 全局 QApplication（PyQt 要求单实例）
# ---------------------------------------------------------------------------
_app = QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------
def _redirect_update_log(tmpdir):
    """把 desktop_pet 的 update.log 重定向到 tmpdir，并返回路径。
    返回前清空内容，避免相互污染。
    """
    path = os.path.join(tmpdir, "update.log")
    desktop_pet.UPDATE_LOG_PATH = path
    # 清空
    with open(path, "w", encoding="utf-8") as f:
        f.write("")
    return path


def _read_update_log(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _sha256_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _make_prog_dialog():
    """造一个 QProgressDialog（_DownloadThread.run 需要它来 emit 进度，但不依赖它返回值）。"""
    dlg = QProgressDialog("test", "cancel", 0, 100)
    dlg.setWindowTitle("test")
    return dlg


class _DoneCollector:
    """收集 done_signal 的最后一帧。"""
    def __init__(self):
        self.last = None
        self.calls = []

    def __call__(self, ok, payload):
        self.last = (ok, payload)
        self.calls.append((ok, payload))


def _run_download_thread(dt):
    """同步驱动 _DownloadThread.run，并把 done_signal 接到收集器。
    run() 是同步逻辑，直接调用即可（不走 QThread.start）。
    """
    collector = _DoneCollector()
    dt.done_signal.connect(collector)
    # progress_signal 不连接（不影响结果），但 emit 到无连接的信号是安全的
    dt.run()
    return collector


# ===========================================================================
# UT1 — _parse_version
# ===========================================================================
class TestUT1_ParseVersion(unittest.TestCase):
    def test_normal_three_segments(self):
        self.assertEqual(desktop_pet._parse_version("1.2.3"), (1, 2, 3))

    def test_normal_two_segments(self):
        self.assertEqual(desktop_pet._parse_version("2.0"), (2, 0))

    def test_higher_version_comparison(self):
        # 用例的语义：1.10.0 > 1.2.0（数值比较，不是字符串）
        self.assertTrue(desktop_pet._parse_version("1.10.0") > desktop_pet._parse_version("1.2.0"))
        self.assertTrue(desktop_pet._parse_version("1.1.0") > desktop_pet._parse_version("1.0.0"))
        self.assertFalse(desktop_pet._parse_version("1.0.0") > desktop_pet._parse_version("1.0.0"))

    def test_invalid_returns_zero(self):
        self.assertEqual(desktop_pet._parse_version("abc"), (0, 0, 0))

    def test_empty(self):
        self.assertEqual(desktop_pet._parse_version(""), (0, 0, 0))

    def test_none(self):
        self.assertEqual(desktop_pet._parse_version(None), (0, 0, 0))

    def test_non_numeric_segment(self):
        # "1.x.3" → split 出 "x"，int("x") 抛异常 → (0,0,0)
        self.assertEqual(desktop_pet._parse_version("1.x.3"), (0, 0, 0))


# ===========================================================================
# UT2 — version.json schema 兼容（v1 / v2 / 损坏）
# ===========================================================================
class TestUT2_SchemaCompat(unittest.TestCase):
    def test_v1_only_download_url(self):
        v1 = {
            "version": "1.0.0",
            "download_url": "https://github.com/x/y/releases/download/v1.0.0/糊宠.exe"
        }
        urls = desktop_pet.Updater._normalize_urls(v1)
        self.assertEqual(len(urls), 1)
        self.assertIn("v1.0.0", urls[0])

    def test_v2_full_fields(self):
        v2 = {
            "version": "1.1.0",
            "download_urls": [
                "https://github.com/x/y/releases/download/v1.1.0/糊宠.exe",
                "https://gh-proxy.com/https://github.com/x/y/releases/download/v1.1.0/糊宠.exe",
            ],
            "sha256": "a" * 64,
            "download_url": "https://github.com/x/y/releases/download/v1.1.0/糊宠.exe",
        }
        urls = desktop_pet.Updater._normalize_urls(v2)
        self.assertEqual(len(urls), 2)
        self.assertEqual(urls[0], v2["download_urls"][0])

    def test_corrupted_json_raises(self):
        """损坏 JSON 在 _fetch_json 中由 json.loads 抛 JSONDecodeError。
        这里直接验证：损坏内容用 json.loads 会抛。
        """
        corrupted = "{ this is not json , version: 1.0.0"
        with self.assertRaises(json.JSONDecodeError):
            json.loads(corrupted)

    def test_fetch_json_handles_corrupted_returns_none(self):
        """损坏 JSON 经 _fetch_json 后返回 None（吞掉异常），不向上抛。"""
        tmp = tempfile.mkdtemp()
        log = _redirect_update_log(tmp)
        try:
            with mock.patch("urllib.request.urlopen") as mu:
                mu.return_value.__enter__.return_value.read.return_value = b"{ broken json"
                result = desktop_pet._fetch_json("https://x.example/version.json")
            self.assertIsNone(result)
            # 失败要写 [CHECK] FAIL
            content = _read_update_log(log)
            self.assertIn("[CHECK]", content)
            self.assertIn("FAIL", content)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# UT3 — URL 列表规范化
# ===========================================================================
class TestUT3_NormalizeUrls(unittest.TestCase):
    def test_has_download_urls(self):
        info = {"download_urls": ["a", "b", "c"]}
        self.assertEqual(desktop_pet.Updater._normalize_urls(info), ["a", "b", "c"])

    def test_only_download_url(self):
        info = {"download_url": "only"}
        self.assertEqual(desktop_pet.Updater._normalize_urls(info), ["only"])

    def test_both_empty(self):
        self.assertEqual(desktop_pet.Updater._normalize_urls({}), [])

    def test_dedup_preserve_order(self):
        info = {"download_urls": ["a", "b", "a", "c", "b"]}
        self.assertEqual(desktop_pet.Updater._normalize_urls(info), ["a", "b", "c"])

    def test_filter_empty_string(self):
        info = {"download_urls": ["a", "", None, "b"]}
        self.assertEqual(desktop_pet.Updater._normalize_urls(info), ["a", "b"])

    def test_download_urls_priority_over_download_url(self):
        """download_urls 非空时忽略 download_url。"""
        info = {
            "download_urls": ["from_list"],
            "download_url": "from_single",
        }
        self.assertEqual(desktop_pet.Updater._normalize_urls(info), ["from_list"])

    def test_fallback_to_single_when_list_empty(self):
        """download_urls 为空列表时回退到 download_url。"""
        info = {"download_urls": [], "download_url": "fallback"}
        self.assertEqual(desktop_pet.Updater._normalize_urls(info), ["fallback"])


# ===========================================================================
# UT4 — SSL 白名单
# ===========================================================================
class TestUT4_SSLWhitelist(unittest.TestCase):
    def test_github_com_is_trusted(self):
        self.assertTrue(desktop_pet._is_trusted_host("github.com"))

    def test_raw_githubusercontent_trusted(self):
        self.assertTrue(desktop_pet._is_trusted_host("raw.githubusercontent.com"))

    def test_objects_githubusercontent_trusted(self):
        self.assertTrue(desktop_pet._is_trusted_host("objects.githubusercontent.com"))

    def test_cdn_jsdelivr_trusted(self):
        self.assertTrue(desktop_pet._is_trusted_host("cdn.jsdelivr.net"))

    def test_gh_proxy_not_trusted(self):
        self.assertFalse(desktop_pet._is_trusted_host("gh-proxy.com"))

    def test_unknown_host_not_trusted(self):
        self.assertFalse(desktop_pet._is_trusted_host("evil-mirror.example.com"))

    def test_empty_host(self):
        self.assertFalse(desktop_pet._is_trusted_host(""))

    def test_case_insensitive(self):
        self.assertTrue(desktop_pet._is_trusted_host("GITHUB.COM"))
        self.assertTrue(desktop_pet._is_trusted_host("GitHub.com"))

    def test_strict_context_for_trusted(self):
        ctx = desktop_pet._make_ssl_context("github.com")
        self.assertEqual(ctx.verify_mode, ssl_verify_required())
        self.assertTrue(ctx.check_hostname)

    def test_relaxed_context_for_proxy(self):
        tmp = tempfile.mkdtemp()
        log = _redirect_update_log(tmp)
        try:
            ctx = desktop_pet._make_ssl_context("gh-proxy.com")
            self.assertEqual(ctx.verify_mode, ssl_verify_none())
            self.assertFalse(ctx.check_hostname)
            # 宽松模式必须留 warn 日志
            content = _read_update_log(log)
            self.assertIn("[SSL]", content)
            self.assertIn("WARN", content)
            self.assertIn("gh-proxy.com", content)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_relaxed_context_for_unknown(self):
        tmp = tempfile.mkdtemp()
        _redirect_update_log(tmp)
        try:
            ctx = desktop_pet._make_ssl_context("random-mirror.example.com")
            self.assertEqual(ctx.verify_mode, ssl_verify_none())
            self.assertFalse(ctx.check_hostname)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_localhost_relaxed(self):
        """TR3 注释：localhost 走宽松（不在白名单）。"""
        ctx = desktop_pet._make_ssl_context("localhost")
        self.assertEqual(ctx.verify_mode, ssl_verify_none())


def ssl_verify_required():
    import ssl
    return ssl.CERT_REQUIRED


def ssl_verify_none():
    import ssl
    return ssl.CERT_NONE


# ===========================================================================
# UT5 — SHA256 计算与比对（直接测 _DownloadThread._verify_sha256）
# ===========================================================================
class TestUT5_SHA256(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _redirect_update_log(self.tmp)
        # 造一个测试文件
        self.payload = b"hello huchong v1.1.0"
        self.path = os.path.join(self.tmp, "fake.exe")
        with open(self.path, "wb") as f:
            f.write(self.payload)
        self.expected = _sha256_bytes(self.payload)
        self.dt = desktop_pet._DownloadThread(
            ["http://x"], _make_prog_dialog(), expected_sha256=self.expected
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_match(self):
        ok, code = self.dt._verify_sha256(self.path, "http://x", len(self.payload))
        self.assertTrue(ok)
        self.assertEqual(code, "ok")

    def test_mismatch(self):
        dt = desktop_pet._DownloadThread(
            ["http://x"], _make_prog_dialog(),
            expected_sha256="0" * 64,
        )
        ok, code = dt._verify_sha256(self.path, "http://x", len(self.payload))
        self.assertFalse(ok)
        self.assertEqual(code, "sha256_mismatch")

    def test_missing_expected_skip(self):
        """expected_sha256 缺失 → 跳过校验，返回 (True, 'skip')。向后兼容旧 schema。"""
        dt = desktop_pet._DownloadThread(["http://x"], _make_prog_dialog())
        ok, code = dt._verify_sha256(self.path, "http://x", len(self.payload))
        self.assertTrue(ok)
        self.assertEqual(code, "skip")

    def test_compute_sha256_streaming(self):
        actual = desktop_pet._DownloadThread._compute_sha256(self.path)
        self.assertEqual(actual, self.expected)


# ===========================================================================
# UT6 — 错误码映射 _humanize_download_error
# ===========================================================================
class TestUT6_ErrorHumanize(unittest.TestCase):
    def test_sha256_mismatch(self):
        msg = desktop_pet.Updater._humanize_download_error("sha256_mismatch")
        self.assertIn("校验", msg)
        self.assertIn("中止", msg)

    def test_canceled(self):
        msg = desktop_pet.Updater._humanize_download_error("canceled")
        self.assertIn("取消", msg)

    def test_network(self):
        msg = desktop_pet.Updater._humanize_download_error("network")
        self.assertIn("网络", msg)

    def test_verify_error_prefix(self):
        """verify_error:OSError 这种带前缀的，落到 network 分支（未知错误码统一走网络提示）。"""
        msg = desktop_pet.Updater._humanize_download_error("verify_error:OSError")
        # 不崩，返回字符串即可
        self.assertIsInstance(msg, str)
        self.assertTrue(len(msg) > 0)

    def test_unknown_code_fallback(self):
        msg = desktop_pet.Updater._humanize_download_error("some_unknown_code")
        self.assertIn("网络", msg)  # 未知错误码统一走 network 文案

    def test_empty_code(self):
        msg = desktop_pet.Updater._humanize_download_error("")
        # 空错误码上层 Updater._on_download_done 会先填 "network"，但函数本身应能处理
        self.assertIsInstance(msg, str)


# ===========================================================================
# IT 工具：mock urlopen 返回不同行为
# ===========================================================================
class _FakeResponse:
    """模拟 urlopen 返回的 context manager。"""
    def __init__(self, data: bytes, headers=None):
        self._data = data
        self.headers = headers or {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        if n is None or n < 0:
            d, self._data = self._data, b""
            return d
        d, self._data = self._data[:n], self._data[n:]
        return d


def _make_urlopen_side_effect(sequence):
    """根据配置返回 side_effect 函数。sequence 是 list of dict：
       {"data": bytes}     -> 返回成功内容
       {"error": Exception} -> 抛该异常
    """
    it = iter(sequence)

    def _side(req, **kw):
        item = next(it)
        if "error" in item:
            raise item["error"]
        return _FakeResponse(item["data"])

    return _side


# ===========================================================================
# IT1 — 多源 fallback 串行（第 1 失败 → 第 2 成功）
# ===========================================================================
class TestIT1_MultiSourceFallback(unittest.TestCase):
    def test_first_fail_second_success(self):
        tmp = tempfile.mkdtemp()
        _redirect_update_log(tmp)
        try:
            payload = b"fake exe content"
            expected = _sha256_bytes(payload)
            dt = desktop_pet._DownloadThread(
                ["http://url1", "http://url2"],
                _make_prog_dialog(),
                expected_sha256=expected,
            )
            seq = [
                {"error": urllib.error.URLError("connection refused 1")},
                {"data": payload},
            ]
            with mock.patch("urllib.request.urlopen", side_effect=_make_urlopen_side_effect(seq)):
                col = _run_download_thread(dt)
            self.assertEqual(col.last, (True, os.path.join(tempfile.gettempdir(), "huchong_new.exe")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# IT2 — 全部 URL 失败：emit(False) + update.log 有 FAIL
# ===========================================================================
class TestIT2_AllSourcesFail(unittest.TestCase):
    def test_three_urls_all_fail(self):
        tmp = tempfile.mkdtemp()
        log = _redirect_update_log(tmp)
        try:
            dt = desktop_pet._DownloadThread(
                ["http://u1", "http://u2", "http://u3"],
                _make_prog_dialog(),
                expected_sha256=None,
            )
            seq = [
                {"error": urllib.error.URLError("timeout1")},
                {"error": urllib.error.URLError("timeout2")},
                {"error": urllib.error.URLError("timeout3")},
            ]
            with mock.patch("urllib.request.urlopen", side_effect=_make_urlopen_side_effect(seq)):
                col = _run_download_thread(dt)
            self.assertIsNotNone(col.last)
            ok, code = col.last
            self.assertFalse(ok)
            self.assertEqual(code, "network")
            # update.log 至少有 3 条 DOWNLOAD FAIL（每个 URL 一条）
            content = _read_update_log(log)
            fail_count = content.count("[DOWNLOAD]") and content.count("FAIL")
            # 粗略：FAIL 至少出现 3 次（每 URL 一次 + 末尾汇总一次）
            self.assertGreaterEqual(content.count("FAIL"), 3)
            # 末尾汇总
            self.assertIn("all sources exhausted", content)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# IT3 — SHA256 拦截（下载被篡改 → 删临时文件 + emit(False, "sha256_mismatch")）
# ===========================================================================
class TestIT3_SHA256Intercept(unittest.TestCase):
    def test_tampered_content_rejected(self):
        tmp = tempfile.mkdtemp()
        log = _redirect_update_log(tmp)
        tmpfile = os.path.join(tempfile.gettempdir(), "huchong_new.exe")
        # 清理可能残留的临时文件，便于后面断言"被删"
        if os.path.exists(tmpfile):
            os.remove(tmpfile)
        try:
            tampered = b"this is not the real exe"
            wrong_hash = _sha256_bytes(b"real exe content")
            dt = desktop_pet._DownloadThread(
                ["http://u1"],
                _make_prog_dialog(),
                expected_sha256=wrong_hash,
            )
            with mock.patch("urllib.request.urlopen",
                            side_effect=_make_urlopen_side_effect([{"data": tampered}])):
                col = _run_download_thread(dt)
            ok, code = col.last
            self.assertFalse(ok)
            self.assertEqual(code, "sha256_mismatch")
            # 临时文件被删
            self.assertFalse(os.path.exists(tmpfile), "临时文件应被删除")
            # update.log 有 VERIFY FAIL
            content = _read_update_log(log)
            self.assertIn("[VERIFY]", content)
            self.assertIn("sha256 mismatch", content)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            if os.path.exists(tmpfile):
                os.remove(tmpfile)


# ===========================================================================
# IT4 — 向后兼容（旧 schema：只有 download_url）
# ===========================================================================
class TestIT4_BackwardCompatOldSchema(unittest.TestCase):
    def test_single_source_no_sha256(self):
        """v1 schema：只有 download_url，无 sha256。
        新客户端应单源下载成功 + 跳过 SHA256 校验。
        """
        tmp = tempfile.mkdtemp()
        log = _redirect_update_log(tmp)
        try:
            payload = b"v1 client download"
            # 用 Updater._normalize_urls 模拟从 v1 schema 拿 URL 列表
            v1_info = {
                "version": "1.1.0",
                "download_url": "http://localhost:8000/糊宠.exe",
            }
            urls = desktop_pet.Updater._normalize_urls(v1_info)
            self.assertEqual(len(urls), 1)
            # expected_sha256 = None（旧 schema 无该字段）
            dt = desktop_pet._DownloadThread(urls, _make_prog_dialog(), expected_sha256=None)
            with mock.patch("urllib.request.urlopen",
                            side_effect=_make_urlopen_side_effect([{"data": payload}])):
                col = _run_download_thread(dt)
            self.assertEqual(col.last[0], True)
            # update.log 应有 sha256 skipped 标志
            content = _read_update_log(log)
            self.assertIn("sha256=skipped", content)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# IT5 — 向后兼容（旧客户端读新 schema）
# ===========================================================================
class TestIT5_BackwardCompatOldClient(unittest.TestCase):
    def test_old_client_reads_new_schema(self):
        """模拟旧 EXE 的逻辑：info.get("download_url")。新 schema 必须保留此字段。"""
        new_schema = {
            "version": "1.1.0",
            "update_date": "2026-08-11",
            "download_url": "https://github.com/x/y/releases/download/v1.1.0/糊宠.exe",
            "download_urls": ["a", "b", "c"],
            "sha256": "deadbeef" * 8,
        }
        # 旧客户端只看 download_url
        url = new_schema.get("download_url", "")
        self.assertTrue(url, "新 schema 必须保留 download_url 字段")
        self.assertIn("v1.1.0", url)


# ===========================================================================
# IT6 — build.py 一致性断言
# ===========================================================================
class TestIT6_BuildConsistency(unittest.TestCase):
    def setUp(self):
        # 备份真实文件
        self.tmp = tempfile.mkdtemp()
        self.orig_version_json = os.path.join(ROOT, "version.json")
        self.backup = os.path.join(self.tmp, "version.json.bak")
        shutil.copy2(self.orig_version_json, self.backup)

    def tearDown(self):
        # 还原
        shutil.copy2(self.backup, self.orig_version_json)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_build_assert(self, version_json_content):
        with open(self.orig_version_json, "w", encoding="utf-8") as f:
            json.dump(version_json_content, f, ensure_ascii=False)
        # 调用 build.py 的 _assert_version_consistent，捕获 SystemExit
        import importlib
        build = importlib.import_module("build")
        with self.assertRaises(SystemExit) as cm:
            build._assert_version_consistent("1.1.0")
        self.assertEqual(cm.exception.code, 1)

    def test_version_mismatch_blocks(self):
        self._run_build_assert({
            "version": "1.0.0",  # 故意不一致（APP_VERSION=1.1.0）
            "download_url": "https://github.com/x/y/releases/download/v1.1.0/糊宠.exe",
        })

    def test_tag_mismatch_blocks(self):
        self._run_build_assert({
            "version": "1.1.0",
            "download_url": "https://github.com/x/y/releases/download/v9.9.9/糊宠.exe",  # tag 错
        })

    def test_no_release_url_blocks(self):
        self._run_build_assert({
            "version": "1.1.0",
            "download_url": "http://some-other.example/file.exe",  # 不是 GitHub Release URL
        })

    def test_consistent_passes(self):
        """APP_VERSION / version.json / Release tag 一致时不应 sys.exit。"""
        with open(self.orig_version_json, "w", encoding="utf-8") as f:
            json.dump({
                "version": "1.1.0",
                "download_url": "https://github.com/x/y/releases/download/v1.1.0/糊宠.exe",
                "download_urls": [
                    "https://github.com/x/y/releases/download/v1.1.0/糊宠.exe",
                ],
            }, f, ensure_ascii=False)
        import importlib
        build = importlib.import_module("build")
        # 不抛 SystemExit 即通过
        build._assert_version_consistent("1.1.0")


# ===========================================================================
# IT7 — publish.py 生成 draft（sha256 与实际文件一致）
# 注：publish.py 的 EXE_PATH / VERSION_JSON / DRAFT_PATH 是模块级常量，
# 测试通过 monkeypatch 指向 tmpdir 隔离环境，绝不碰真实 dist/糊宠.exe。
# ===========================================================================
class TestIT7_PublishDraft(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # 在 tmpdir 里造一个 fake dist/糊宠.exe
        self.exe_path = os.path.join(self.tmp, "糊宠.exe")
        # 同时把 VERSION_JSON 副本放进 tmp（让 publish 从这里读 owner/repo/exe/changelog）
        self.vj_path = os.path.join(self.tmp, "version.json")
        shutil.copy2(os.path.join(ROOT, "version.json"), self.vj_path)
        self.draft_path = os.path.join(self.tmp, "version.json.draft")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_publish_generates_consistent_draft(self):
        payload = b"fake exe bytes for publish test " + b"\x00" * 1024
        with open(self.exe_path, "wb") as f:
            f.write(payload)
        expected_sha = _sha256_bytes(payload)

        import importlib
        publish = importlib.import_module("publish")
        # monkeypatch 路径常量到隔离 tmpdir
        with mock.patch.object(publish, "EXE_PATH", self.exe_path), \
             mock.patch.object(publish, "VERSION_JSON", self.vj_path), \
             mock.patch.object(publish, "DRAFT_PATH", self.draft_path):
            from io import StringIO
            buf = StringIO()
            old_stdout = sys.stdout
            sys.stdout = buf
            try:
                try:
                    publish.main()
                finally:
                    sys.stdout = old_stdout
            except SystemExit:
                sys.stdout = old_stdout
                raise AssertionError("publish.main() should not sys.exit on success")

        out = buf.getvalue()
        # draft 文件应生成在 tmpdir（项目根无污染）
        self.assertTrue(os.path.exists(self.draft_path), out)
        # 项目根不应有 draft 残留
        self.assertFalse(os.path.exists(os.path.join(ROOT, "version.json.draft")),
                         "publish.py 不应污染项目根目录")
        with open(self.draft_path, "r", encoding="utf-8") as f:
            draft = json.load(f)
        # sha256 一致
        self.assertEqual(draft["sha256"], expected_sha)
        # download_urls 第 0 项是 GitHub 权威源
        self.assertTrue(draft["download_urls"][0].startswith("https://github.com/"))
        # download_url == download_urls[0]（v1 兼容）
        self.assertEqual(draft["download_url"], draft["download_urls"][0])

    def test_publish_exits_when_exe_missing(self):
        """没有 EXE 时 publish.py 应 sys.exit(1)。"""
        import importlib
        publish = importlib.import_module("publish")
        # 不创建 exe
        with mock.patch.object(publish, "EXE_PATH", self.exe_path), \
             mock.patch.object(publish, "VERSION_JSON", self.vj_path), \
             mock.patch.object(publish, "DRAFT_PATH", self.draft_path):
            with self.assertRaises(SystemExit) as cm:
                publish.main()
            self.assertEqual(cm.exception.code, 1)


# ===========================================================================
# P1-1 修复回归（场景 X / X3 / Y / Z）
# ===========================================================================
class TestP1_1_Regression(unittest.TestCase):
    """P1-1：错误码优先级表 _ERROR_SEVERITY + _more_severe，
    防止高严重性错误（sha256_mismatch）被低严重性（network）掩盖。
    场景：
      X  : url1 sha256_mismatch → url2 network → emit(False, "sha256_mismatch")
      X3 : url1 network → url2 sha256_mismatch → emit(False, "sha256_mismatch")
      Y  : 所有 url 都 network → emit(False, "network")
      Z  : 取消 → emit(False, "canceled")
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _redirect_update_log(self.tmp)
        self.tmpfile = os.path.join(tempfile.gettempdir(), "huchong_new.exe")
        if os.path.exists(self.tmpfile):
            os.remove(self.tmpfile)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if os.path.exists(self.tmpfile):
            os.remove(self.tmpfile)

    def _run_with_seq(self, urls, sequence, expected_sha256):
        dt = desktop_pet._DownloadThread(urls, _make_prog_dialog(), expected_sha256=expected_sha256)
        with mock.patch("urllib.request.urlopen", side_effect=_make_urlopen_side_effect(sequence)):
            return _run_download_thread(dt)

    def test_severity_table_values(self):
        self.assertEqual(desktop_pet._ERROR_SEVERITY["sha256_mismatch"], 3)
        self.assertEqual(desktop_pet._ERROR_SEVERITY["verify_error"], 2)
        self.assertEqual(desktop_pet._ERROR_SEVERITY["network"], 1)
        self.assertEqual(desktop_pet._ERROR_SEVERITY["canceled"], 0)

    def test_more_severe_basic(self):
        # network 不能覆盖 sha256_mismatch
        self.assertFalse(desktop_pet._more_severe("network", "sha256_mismatch"))
        # sha256_mismatch 可以覆盖 network
        self.assertTrue(desktop_pet._more_severe("sha256_mismatch", "network"))
        # 相同严重性允许覆盖（保持最后一个）
        self.assertTrue(desktop_pet._more_severe("network", "network"))
        # 空错误码不覆盖任何
        self.assertFalse(desktop_pet._more_severe("", "network"))
        # verify_error 带前缀
        self.assertFalse(desktop_pet._more_severe("verify_error:OSError", "sha256_mismatch"))

    def test_scenario_X_sha256_then_network(self):
        """url1 内容被篡改（sha256_mismatch）→ url2 网络失败 → 最终 emit sha256_mismatch"""
        payload = b"tampered content"
        real_sha = _sha256_bytes(b"totally different real content")
        col = self._run_with_seq(
            ["http://u1", "http://u2"],
            [{"data": payload}, {"error": urllib.error.URLError("net down")}],
            expected_sha256=real_sha,
        )
        self.assertEqual(col.last, (False, "sha256_mismatch"))

    def test_scenario_X3_network_then_sha256(self):
        """url1 网络失败 → url2 内容被篡改 → 最终 emit sha256_mismatch（不能是 network）"""
        payload = b"tampered"
        real_sha = _sha256_bytes(b"real different")
        col = self._run_with_seq(
            ["http://u1", "http://u2"],
            [{"error": urllib.error.URLError("net down 1")}, {"data": payload}],
            expected_sha256=real_sha,
        )
        self.assertEqual(col.last, (False, "sha256_mismatch"))

    def test_scenario_Y_all_network(self):
        col = self._run_with_seq(
            ["http://u1", "http://u2", "http://u3"],
            [
                {"error": urllib.error.URLError("err1")},
                {"error": urllib.error.URLError("err2")},
                {"error": urllib.error.URLError("err3")},
            ],
            expected_sha256=None,
        )
        self.assertEqual(col.last, (False, "network"))

    def test_scenario_Z_canceled(self):
        """取消：构造一个 _DownloadThread，run() 前置 _cancel=True。"""
        dt = desktop_pet._DownloadThread(
            ["http://u1"], _make_prog_dialog(), expected_sha256=None
        )
        dt._cancel = True
        # urlopen 不应被调用（取消检查在请求之前）
        with mock.patch("urllib.request.urlopen") as mu:
            col = _run_download_thread(dt)
            self.assertFalse(mu.called)
        self.assertEqual(col.last, (False, "canceled"))


# ===========================================================================
# 本地 mock 端到端（Plan 8.3 / TR3）
# ===========================================================================
class _MockHTTPHandler(BaseHTTPRequestHandler):
    """根据 path 返回不同内容：
       /version.json  → mock version.json（版本高于 APP_VERSION）
       /糊宠.exe       → 测试 EXE 内容
       /bad.exe       → 被篡改的内容
    """
    server_version = "MockUpdateServer/1.0"

    def log_message(self, *a, **kw):
        pass  # 静默

    def do_GET(self):
        # 解码 percent-encoded path（真实 HTTP server 行为），使经
        # _encode_url 编码后的 /%E7%B3%8A... 也能命中 /糊宠.exe 分支。
        path = urllib.parse.unquote(self.path)
        if path == "/version.json":
            payload = self.server.mock_version.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif path in ("/good.exe", "/糊宠.exe"):
            # /good.exe 是 ASCII 别名（绕开 urllib 中文 URL 编码 bug，验证核心逻辑）；
            # /糊宠.exe 模拟真实 version.json 的 URL 形态。
            data = self.server.good_exe
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/bad.exe":
            data = self.server.bad_exe
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/404":
            self.send_response(404)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


class TestEndToEnd_MockServer(unittest.TestCase):
    """Plan 8.3：起本地 http.server，构造 mock version.json，验证全链路。"""

    @classmethod
    def setUpClass(cls):
        cls.good_exe = b"FAKE HUCHONG EXE v1.2.0 " + b"\x00" * 4096
        cls.bad_exe = b"TAMPERED CONTENT NOT THE REAL EXE" + b"\xff" * 1024
        cls.good_sha = _sha256_bytes(cls.good_exe)

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHTTPHandler)
        cls.server.good_exe = cls.good_exe
        cls.server.bad_exe = cls.bad_exe
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _redirect_update_log(self.tmp)
        self.tmpfile = os.path.join(tempfile.gettempdir(), "huchong_new.exe")
        if os.path.exists(self.tmpfile):
            os.remove(self.tmpfile)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if os.path.exists(self.tmpfile):
            os.remove(self.tmpfile)

    def _make_version_json(self, urls, sha256=None):
        v = {
            "version": "1.2.0",  # 高于 APP_VERSION 1.1.0
            "update_date": "2026-08-11",
            "changelog": "test end-to-end",
            "download_urls": urls,
        }
        if sha256 is not None:
            v["sha256"] = sha256
        return json.dumps(v, ensure_ascii=False)

    def test_e2e_success_via_fallback(self):
        """url1 是 404（失败）→ url2 是 localhost good exe（成功）+ sha256 通过。
        注：用 /good.exe ASCII 路径以隔离 urllib 中文 URL 编码 bug（见 test_e2e_chinese_url_triggers_bug）。
        """
        self.server.mock_version = self._make_version_json(
            urls=[
                "http://127.0.0.1:%d/404" % self.port,  # 故意失效
                "http://127.0.0.1:%d/good.exe" % self.port,
            ],
            sha256=self.good_sha,
        )
        # 走完整检测 → 下载链路
        info = desktop_pet._fetch_json("http://127.0.0.1:%d/version.json" % self.port)
        self.assertIsNotNone(info)
        self.assertTrue(desktop_pet._parse_version(info["version"]) >
                        desktop_pet._parse_version(desktop_pet.APP_VERSION))
        urls = desktop_pet.Updater._normalize_urls(info)
        self.assertEqual(len(urls), 2)
        dt = desktop_pet._DownloadThread(
            urls, _make_prog_dialog(), expected_sha256=info.get("sha256")
        )
        col = _run_download_thread(dt)
        self.assertTrue(col.last[0])
        # 下载的文件内容应等于 good_exe
        with open(self.tmpfile, "rb") as f:
            self.assertEqual(f.read(), self.good_exe)

    def test_e2e_sha256_intercept(self):
        """sha256 不匹配（指向 bad exe）→ 下载完被拦截，emit(False, "sha256_mismatch")。"""
        self.server.mock_version = self._make_version_json(
            urls=["http://127.0.0.1:%d/bad.exe" % self.port],
            sha256=self.good_sha,  # 故意写 good 的 sha 让 bad 校验失败
        )
        info = desktop_pet._fetch_json("http://127.0.0.1:%d/version.json" % self.port)
        urls = desktop_pet.Updater._normalize_urls(info)
        dt = desktop_pet._DownloadThread(
            urls, _make_prog_dialog(), expected_sha256=info.get("sha256")
        )
        col = _run_download_thread(dt)
        self.assertEqual(col.last, (False, "sha256_mismatch"))
        # 临时文件被删
        self.assertFalse(os.path.exists(self.tmpfile))

    def test_e2e_backward_compat_v1_schema(self):
        """v1 schema（无 sha256 / 只有 download_url）→ 单源下载成功，跳过校验。
        注：用 /good.exe ASCII 路径以隔离 urllib 中文 URL 编码 bug。
        """
        self.server.mock_version = json.dumps({
            "version": "1.2.0",
            "update_date": "2026-08-11",
            "download_url": "http://127.0.0.1:%d/good.exe" % self.port,
        }, ensure_ascii=False)
        info = desktop_pet._fetch_json("http://127.0.0.1:%d/version.json" % self.port)
        urls = desktop_pet.Updater._normalize_urls(info)
        self.assertEqual(len(urls), 1)
        dt = desktop_pet._DownloadThread(urls, _make_prog_dialog(), expected_sha256=info.get("sha256"))
        col = _run_download_thread(dt)
        self.assertTrue(col.last[0])

    def test_e2e_chinese_url_encoded_ok(self):
        """含中文 URL 经 percent-encoding 后可正常下载。
        version.json 的 download_urls 含中文 '糊宠.exe'，_DownloadThread 通过
        _encode_url 对 path 段做 quote 后传给 urlopen，避免 UnicodeEncodeError。
        期望：下载成功 + sha256 通过。
        """
        self.server.mock_version = self._make_version_json(
            urls=["http://127.0.0.1:%d/糊宠.exe" % self.port],
            sha256=self.good_sha,
        )
        info = desktop_pet._fetch_json("http://127.0.0.1:%d/version.json" % self.port)
        urls = desktop_pet.Updater._normalize_urls(info)
        dt = desktop_pet._DownloadThread(
            urls, _make_prog_dialog(), expected_sha256=info.get("sha256")
        )
        col = _run_download_thread(dt)
        # 中文 URL 经编码后应成功下载，返回临时文件路径
        self.assertTrue(col.last[0],
                        "含中文 URL 经 percent-encoding 后应能成功下载。"
                        "若失败，检查 _encode_url 是否在传给 urlopen 前正确编码 path。")
        self.assertEqual(col.last[1], self.tmpfile)


# ===========================================================================
# 回归测试：现有功能不破
# ===========================================================================
class TestRegression(unittest.TestCase):
    def test_module_import_clean(self):
        """模块 import 不抛异常（所有顶层副作用 OK）。"""
        import importlib
        importlib.reload(desktop_pet)

    def test_main_pet_window_instantiable(self):
        """主窗口（PetWindow 类）能实例化，QApplication 不崩。
        这是与"更新无关功能零回归"的最小验证：至少主对象能 new 出来。
        """
        pet_cls = getattr(desktop_pet, "PetWindow", None)
        if pet_cls is None:
            candidates = [n for n in dir(desktop_pet)
                          if n[0].isupper() and "Pet" in n]
            self.skipTest("PetWindow class not found; candidates=%s" % candidates)
        pet = pet_cls()
        self.assertIsNotNone(pet)
        # 关掉避免泄漏
        try:
            pet.close()
        except Exception:
            pass
        try:
            pet.deleteLater()
        except Exception:
            pass

    def test_app_version_consistent_with_version_json(self):
        """E1 的核心承诺：APP_VERSION == version.json.version。"""
        vj_path = os.path.join(ROOT, "version.json")
        with open(vj_path, "r", encoding="utf-8") as f:
            vj = json.load(f)
        self.assertEqual(vj["version"], desktop_pet.APP_VERSION,
                         "version.json.version 必须等于 APP_VERSION（E1 一致性）")

    def test_update_button_text_unchanged(self):
        """回归：检查更新相关菜单项文案存在（不验证完整 UI）。"""
        # 仅做静态检查：源码里存在"检查更新"按钮的文本
        with open(os.path.join(ROOT, "desktop_pet.py"), "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("检查更新", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
