# -*- coding: utf-8 -*-
"""
Test Engineer — SPRINT_AUTOUPDATE 动态测试套件

覆盖：
  UT1-UT6   Unit Test
  IT1-IT7   Integration Test
  P1-1 回归 (场景 X / X3 / Y / Z)
  本地 mock 端到端
  回归 (import + 主窗口实例化)

执行：python -m unittest tests_autoupdate.test_autoupdate -v
（pytest 不可用，使用 stdlib unittest）

设计原则：
  · 不污染项目仓库：所有测试产物写到 tempfile.mkdtemp()
  · update.log 路径通过 monkeypatch 重定向到 tmp（覆盖 desktop_pet.UPDATE_LOG_PATH + _update_log_path）
  · mock 网络统一用 monkeypatch urllib.request.urlopen，避免真实网络抖动
  · 不修改生产代码
"""
