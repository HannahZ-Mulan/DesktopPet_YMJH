# Gate 4 — Acceptance Gate

## Sprint Context
- Sprint: SPRINT_AUTOUPDATE
- Created: 2026-08-11
- Last Updated: 2026-08-11

## Gate Status
PASSED

## Gate Decision
- Decision: **GO**
- Decision Date: 2026-08-11
- Decision Reason: Level B 三方验证全部通过。Investigator✅(V1-V7+P1-1复验) + Reviewer✅(整体B无P0，P1已修) + Test Engineer✅(65/65用例，P1中文URL修复复验通过)。无遗留 P0/P1。P2 系列 5 项留后续 Sprint。PO 已拍板 P1-2=方案A。可进入发布。

## 风险分级：**Level B（核心功能）**
判据：涉及数据流/Schema 变更 + 涉及核心业务逻辑（更新安装链路）+ 多模块影响。
Evidence 要求：Investigator ✅ + Reviewer ✅ + Test Engineer ✅（三方缺一不可）。

## Evidence Registry

| ID | Source Agent | Type | Status | Code Version | Collected | Summary |
|----|-------------|------|--------|-------------|-----------|---------|
| E0 | Main Agent | Context | ✅ Valid | — | 2026-08-11 | Gate 3 PASSED，Implementation Plan 就绪。Gate 4 Level B。Developer 按 4 Batch 推进 13 Tasks。 |
| E1 | Developer | Implementation | 📦 Done | working tree | 2026-08-11 | 13 Tasks 全部实现。P1-1 已由 Fixer 修复并经 Investigator 复验通过。 |
| E2 | Investigator | Validation | ✅ Valid | working tree | 2026-08-11 | V1-V7 全 PASS + P1-1 修复复验 PASS（场景 X/X2/X3 等 9 端到端 + 15 决策矩阵全过）。无 regression。状态：READY_FOR_TEST_ENGINEER。 |
| E3 | Reviewer | Code Review | ✅ Valid | working tree | 2026-08-11 | 整体 B，无 P0。原 P1-1（错误码覆盖）已修复；P1-2（SHA256 fallback）PO 已拍板方案 A。修复后的错误码逻辑经 Investigator 动态验证等价覆盖。P2 系列 5 项留后续。有条件通过 → **通过**。 |
| E4 | Fixer | Patch | 📦 Done | working tree (+P1-1 fix) | 2026-08-11 | 修复 P1-1 错误码覆盖。新增 `_ERROR_SEVERITY` dict + `_more_severe()` 函数。最小修复。已复验通过。 |
| E5 | Test Engineer | Test Result | ✅ Valid | working tree (+中文URL编码 fix) | 2026-08-11 | 65/65 用例全 PASS。P1 中文 URL 编码修复复测通过（update.log 出现 [DOWNLOAD] OK url=.../%E7%B3%8A%E5%AE%A0.exe）。无新 regression。无遗留 P0/P1。Gate 4 可 GO。 |
| E6 | Fixer | Patch | 📦 Done | working tree (+中文URL编码 fix) | 2026-08-11 | 修复 P1：新增 `_encode_url()` 工具函数（percent-encode path 段，保留 scheme/host），在 `_fetch_json` 和 `_DownloadThread.run` 两处调用。翻转测试用例 test_e2e_chinese_url_encoded_ok。65 用例全 PASS。 |

## 输入（来自 Gate 3）
- Implementation Plan: `docs/aps-os/SPRINT_AUTOUPDATE_IMPLEMENTATION_PLAN.md`
- 核心逻辑 Task: T6/T7/T8/T10
- 改动文件: `desktop_pet.py` / `build.py` / 新增 `publish.py` / `version.json`

## Conflict Log
（暂无）

## PO 设计决策记录（2026-08-11）

### P1-2：SHA256 不匹配后的 fallback 行为
- **PO 决策**：方案 A —— 单个镜像 SHA256 不匹配时，删掉损坏文件，继续试下一个源。
- **理由**：不降低安全性（未校验文件绝不安装）；更鲁棒（镜像被投毒不影响整体）；与 E4 多源 fallback 设计意图一致。
- **Developer 现状代码即此方案，无需改动**。仅作决策记录。
- **Investigator + Reviewer 均推荐此方案**。

### P1-1：错误码覆盖 bug（需修复）
- **PO 决策**：修复。
- **问题**：`desktop_pet.py:3960, 4031-4042`，`last_error="network"` 会无条件覆盖前一轮的 `sha256_mismatch`，导致安全事件被网络错误掩盖。
- **修复方案**：给错误码定严重性优先级（sha256_mismatch > verify_error:* > network > canceled），只在"新错误码严重性 ≥ 已有"时才覆盖。
- **路由**：Fixer 修复 → Investigator 复验。

## Change History
- 2026-08-11: Gate 4 created. 风险分级 = Level B。任务路由给 Developer 按 B1-B4 批次实现。
- 2026-08-11: Developer 完成 13 Tasks (E1)。Investigator 验证 V1-V7 全 PASS (E2)。Reviewer 有条件通过 (E3)，无 P0，2 个 P1。
- 2026-08-11: PO 拍板：P1-2=方案 A（现状不改）；P1-1=修。路由 Fixer 修 P1-1。
- 2026-08-11: Fixer 完成 P1-1 修复 (E4)。E1/E2/E3 标记 🔄 Stale（因 Fixer 改动了 _DownloadThread 错误码逻辑）。路由回 Investigator 针对性复验 P1-1 修复。
- 2026-08-11: Investigator 复验 P1-1 修复 PASS（E2 更新为 Valid）。无 regression。E1/E3 恢复 Valid（P1-1 修复后错误码逻辑经动态验证等价覆盖）。三方中 Investigator✅ Reviewer✅，进入 Test Engineer（E5）动态测试。
- 2026-08-11: Test Engineer 完成 65 用例全 PASS（E5），但发现新 P1：含中文 URL 触发 UnicodeEncodeError，所有客户端下载全失败。Fix Loop 第 1 次。路由 Fixer 修复。E5 标记 ❌ Found P1。
- 2026-08-11: Fixer 完成 P1 修复 (E6)：新增 `_encode_url()` + 翻转测试用例。65 用例全 PASS。E5 标记 🔄 Stale。路由回 Test Engineer 复测（动态 bug 修复）。
- 2026-08-11: Test Engineer 复测 P1 修复 PASS（E5 更新为 Valid）。65/65 用例全过，无 regression，无遗留 P0/P1。Gate 4 Decision = **GO**。Fix Loop 1/3（远未到极限）。可进入发布。
