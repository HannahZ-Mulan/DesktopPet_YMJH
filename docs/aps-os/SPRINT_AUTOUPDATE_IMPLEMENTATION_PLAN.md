# SPRINT_AUTOUPDATE_IMPLEMENTATION_PLAN.md

> Sprint: AUTOUPDATE（自动更新链路加固）
> 阶段: Gate 3 — Implementation Plan
> Architect: 基于 Gate 2 ADR（E1 调研）+ Gate 2 PRD（E2 PM 决策）产出
> 日期: 2026-08-11
> 约束: 本文只定义"在哪改、改什么契约、验收什么"，**不含实现代码**。Developer 在 Gate 4 动手。

---

## 0. 计划基准（PO 决策锁定项 + 代码事实回顾）

### PO 拍板项（来自 Gate 2 出口）
- **分发路径 = 候选 1**：GitHub Release 权威源 + 公共加速镜像 fallback。**不引入实名组件**（EdgeOne/Gitee/对象存储全部排除）。
- **范围**：E1-E6 全做（含 E6 发布脚本化）。
- **节奏**：一次性 Gate 3 计划 + Gate 4 实现。

### PM 4 条架构硬约束
1. 镜像列表**数据驱动**（URL 写在 version.json，不在代码里硬编码）。
2. **检测链路不动**（`CheckUpdateThread` 的 CDN→raw 双源 fallback 保持原样）。
3. **GitHub Release 是权威源**（version.json 文件本身仍托管在 GitHub raw + jsDelivr CDN）。
4. **不引入实名组件**。

### 代码事实（带行号，Developer 改动时以此为准）
| 事实 | 行号 | 对改造的影响 |
|---|---|---|
| version.json 双源 + fallback | `desktop_pet.py:64,68,3538-3543` | E1：检测链路不动 |
| `download_url` 单源取值 | `desktop_pet.py:3586` | E4：此处要改成"规范化成列表" |
| `_DownloadThread.run` 单 URL 单次请求 | `desktop_pet.py:3728-3755` | E4：循环主体 |
| 下载完直接用，无校验 | `desktop_pet.py:3754-3755` | E2：校验插桩点 |
| `except Exception as e: emit(False,"")`，`e` 丢弃 | `desktop_pet.py:3756-3757` | E3：异常改造点 |
| 下载线程 SSL 全关 | `desktop_pet.py:3730-3732` | E5：改造点 ① |
| **`_fetch_json` 也关了 SSL**（容易被遗漏） | `desktop_pet.py:3518-3520` | E5：改造点 ②，必须同改 |
| 版本号三处不一致实证 | `desktop_pet.py:60`(1.1.0) / `version.json:3`(1.0.0) / `version.json:4`(v1.0.1) | E1：现状 bug 现场 |
| build.py 只读不校验不回写 | `build.py:42-50,151-156` | E1+E6：插桩点 |
| crash.log 机制（faulthandler + excepthook） | `desktop_pet.py:3763-3785` | E3：日志模式参考，但建议独立 update.log |

---

## 1. Module Design（改动点定位）

### E1 — 版本号一致性校验（落点：build.py）

**问题**：当前 `build.py:_read_app_version()` 只读 `APP_VERSION` 写进 EXE 属性，对 version.json 和 git tag 零感知。

**落点**：`build.py:main()`（`:151-156`）打包前加一个**预检函数** `_assert_version_consistent()`。

**校验内容（3 项一致性断言）**：
1. `APP_VERSION`（来自 `desktop_pet.py:60`）== `version.json["version"]`
2. `download_url` / `download_urls` 中的 GitHub Release URL **tag 段** == `APP_VERSION`（如 `v1.1.0`）。
   - 实现提示：用正则提取 `/releases/download/(v?[\d.]+)/` 的捕获组。
3. （可选，Should）`git describe --tags` 的最近 tag == `APP_VERSION`。git 不可用时跳过并 warn，不 fail。

**失败行为**：任一断言不过 → 打印具体差异 → `sys.exit(1)`，阻断打包。
**成功行为**：打印 `[OK] 版本号一致：1.1.0`，继续打包。

**改动文件**：仅 `build.py`。**不涉及核心业务逻辑**（打包期校验，运行期无影响）。

---

### E2 — SHA256 完整性校验（落点：desktop_pet.py + version.json schema）

**问题**：`_DownloadThread.run`（`:3728-3755`）下载完直接 `emit(True, tmp)`，无校验。走第三方加速镜像时有被篡改风险。

**落点**：
- version.json schema 新增 `sha256` 字段（见第 2 节）。
- `desktop_pet.py` `_DownloadThread` 在 **`:3754` 之前（下载循环结束后、emit 之前）** 插入校验逻辑。

**校验契约**：
- 下载完成后计算临时文件的 SHA256。
- 与 `info["sha256"]` 比对（`info` 由 `Updater` 通过构造函数或参数传入 `_DownloadThread`）。
- 三种分支：
  - `sha256` 字段**缺失**（旧 version.json / 老网友）→ **跳过校验，照常安装**（向后兼容，写一条 warn 日志）。
  - `sha256` 存在且**匹配** → emit(True, tmp)。
  - `sha256` 存在且**不匹配** → 删除临时文件 → emit(False, "<校验失败专用错误码>")，**不安装**。UI 层提示"文件可能被篡改，已中止"。

**改动文件**：`desktop_pet.py`（`_DownloadThread` + `Updater._on_download_done` 的失败分支文案）。**涉及核心业务逻辑（更新安装路径）**。

---

### E3 — 下载失败写日志（落点：desktop_pet.py）

**问题**：`desktop_pet.py:3756-3757` 的 `except Exception as e: self.done_signal.emit(False, "")` 把 `e` 完全丢弃，失败时用户和作者都无从排查。

**落点**：
- 新增一个**独立日志文件** `update.log`，路径 = `app_dir()/update.log`（与 `crash.log` 同目录，沿用 `app_dir()` 模式）。
- 日志格式：`[时间] [阶段] [结果] 详情`，例如：
  ```
  [2026-08-11 14:30:01] [CHECK] OK remote=1.1.0 local=1.0.0
  [2026-08-11 14:30:15] [DOWNLOAD] url=https://gh-proxy.com/... FAIL timeout
  [2026-08-11 14:30:45] [DOWNLOAD] url=https://github.com/.../v1.1.0/糊宠.exe OK size=89247123 sha256=ab12...
  [2026-08-11 14:31:02] [VERIFY] OK
  [2026-08-11 14:31:05] [INSTALL] START pid=12345
  ```
- 改造点：
  - `_DownloadThread.run` 的 `except`（`:3756`）：记录 `[DOWNLOAD] FAIL` + 异常类型和消息 + 当前尝试的 URL。
  - `Updater._on_checked`（`:3563`）：记录 `[CHECK]`。
  - `Updater._on_download_done`（`:3645`）：记录 `[VERIFY]` / `[INSTALL]`。
- 失败信号 `done_signal` 的第二个参数（str）从空字符串改成**有意义的错误码**（如 `"network"` / `"sha256_mismatch"` / `"canceled"`），UI 层据此显示不同提示。

**改动文件**：`desktop_pet.py`（多个点）。**不涉及核心逻辑变更，只是可观测性增强**。**日志无敏感信息**（不含用户身份，只有 URL/版本号/异常类型）。

---

### E4 — download_url 多源 fallback（落点：desktop_pet.py + version.json schema）

**问题**：`desktop_pet.py:3586` `url = info.get("download_url", "")` 取单值；`:3733` 单次请求。任何单点失败直接挂。

**落点**：
- version.json schema 升级（见第 2 节）：新增 `download_urls`（列表），保留 `download_url`（单值，向后兼容）。
- `Updater._prompt_update`（`:3586` 附近）：把"取单 URL"改成"规范化成有序列表"：
  - 若有 `download_urls` → 用它（顺序即优先级）。
  - 若只有 `download_url` → 包装成单元素列表。
  - 列表第一项**建议保持 GitHub 权威源**（PM 约束 3），加速镜像放后面。
- `_DownloadThread` 改造为**接收 URL 列表**（构造函数参数从 `url` 变 `urls: list[str]`）：
  - `run()` 内层循环：逐个尝试，任一成功即停止；全失败才 emit(False, ...)。
  - 每次尝试的失败进 update.log（E3）。
  - 每次尝试用独立 try/except，不让单个 URL 的异常中断整个 fallback 链。
- "打开下载页"按钮（`:3600-3601`）保留指向**列表第一项**（GitHub Release 页），作为最终人肉兜底。

**多源 fallback 策略选择**：**串行尝试**（详见第 6 节 Trade-off）。

**改动文件**：`desktop_pet.py`（`_DownloadThread` + `Updater._prompt_update` / `_download_and_replace`）。**涉及核心业务逻辑**（下载主链路重写）。

---

### E5 — SSL 校验白名单（落点：desktop_pet.py）

**问题**：`_DownloadThread`（`:3730-3732`）和 `_fetch_json`（`:3518-3520`）**两处**都全关了 SSL 校验。走第三方加速域名时中间人风险。

**落点**：
- 新增一个工具函数 `_make_ssl_context(host: str) -> ssl.SSLContext`：
  - 维护一份**可信域名白名单**（如 `github.com`、`objects.githubusercontent.com`、`*.githubusercontent.com`、`cdn.jsdelivr.net`、`raw.githubusercontent.com`）。
  - 白名单内 host → 返回**严格 SSLContext**（`check_hostname=True`、`CERT_REQUIRED`）。
  - 非白名单（如 `gh-proxy.com` 等加速镜像）→ 维持现状（宽松），但**写一条 warn 到 update.log**：`[SSL] WARN host=gh-proxy.com cert_check=relaxed`。
- `_DownloadThread.run`（`:3730`）和 `_fetch_json`（`:3518`）都改用 `_make_ssl_context(parsed_url.hostname)`。

**策略说明**：
- 权威源（GitHub 直系域名）开严格校验——E2 SHA256 是最终兜底，SSL 是第一道。
- 加速镜像维持宽松——因为镜像域名经常变、证书可能不规范，强开会让 fallback 失效，反而违背 E4 的初衷。
- 这个"权威严、镜像宽"的不对称策略是 E2+E5 的组合拳：SSL 拦截一部分，SHA256 兜底剩下的。

**改动文件**：`desktop_pet.py`。**涉及安全策略，但行为变化是"权威源更严"，不降低现有安全水位**。

---

### E6 — 发布流程脚本化（落点：独立 publish.py）

**问题**：作者现在打包后要人肉：传 GitHub Release → 改 version.json → 改 tag → 算校验值。任一步漏了就出 E1 的 bug（现状已实证）。

**落点**：**新建独立文件 `publish.py`**（不扩展 build.py，理由见第 6 节 Trade-off）。

**publish.py 的职责（输入/输出契约）**：
- **输入**：`dist/糊宠.exe`（由 build.py 产出，前置依赖）。
- **产出**：
  1. 计算 EXE 的 SHA256。
  2. 读取 `desktop_pet.py:APP_VERSION`。
  3. **生成 `version.json` 草稿**（打印到终端 + 写到 `version.json.draft`，**不直接覆盖 version.json**——让作者最后过目）。
  4. 打印"发布清单"提示作者下一步手动操作：
     ```
     [1] 上传 dist/糊宠.exe 到 GitHub Release (tag: v1.1.0)
     [2] 确认 version.json.draft 无误后覆盖 version.json
     [3] git add version.json && git commit && git push
     [4] 在群内通知网友
     ```
- **不做**的事：不自动调 GitHub API 上传（避免引入 token 管理）、不自动 git push（避免误操作）。

**version.json.draft 的 download_urls 默认模板**（作者可改）：
```python
DEFAULT_MIRRORS = [
    "https://github.com/{owner}/{repo}/releases/download/v{ver}/{exe}",  # 权威源（第 1）
    "https://gh-proxy.com/https://github.com/{owner}/{repo}/releases/download/v{ver}/{exe}",
    "https://ghfast.top/https://github.com/{owner}/{repo}/releases/download/v{ver}/{exe}",
]
```
（`owner/repo/exe` 从现有 version.json 推断，不硬编码。）

**与 build.py 的关系**：build.py 完成后打印提示 `下一步：python publish.py`，但不强制串联（作者可能要先本地测试 EXE 再发布）。

**改动文件**：新增 `publish.py`。**不涉及运行期业务逻辑**（纯开发期工具）。

---

## 2. Data Flow — version.json 新 Schema

### 设计原则
- **向后兼容**：老网友的旧 EXE（只认 `download_url`）读新 schema 不能崩。
- **数据驱动**：新客户端的镜像列表完全来自 version.json，加镜像不改代码。
- **未来扩展**：以后加 EdgeOne URL 只需在数组里加一项。

### Schema（v2，向后兼容 v1）

```json
{
  "version": "1.1.0",
  "update_date": "2026-08-11",
  "changelog": "更新链路加固：多源下载、完整性校验、失败日志。",

  "download_url": "https://github.com/HannahZ-Mulan/DesktopPet_YMJH/releases/download/v1.1.0/糊宠.exe",
  "download_urls": [
    "https://github.com/HannahZ-Mulan/DesktopPet_YMJH/releases/download/v1.1.0/糊宠.exe",
    "https://gh-proxy.com/https://github.com/HannahZ-Mulan/DesktopPet_YMJH/releases/download/v1.1.0/糊宠.exe",
    "https://ghfast.top/https://github.com/HannahZ-Mulan/DesktopPet_YMJH/releases/download/v1.1.0/糊宠.exe"
  ],
  "sha256": "a1b2c3d4e5f6...(64 hex)"
}
```

### 字段语义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | string | ✅ | 远端最新版本号（与 APP_VERSION 同格式） |
| `update_date` | string | ✅ | 发版日期（展示用） |
| `changelog` | string | ✅ | 更新说明（展示用） |
| `download_url` | string | ✅(v1) | **保留**，向后兼容老客户端。值 = `download_urls[0]` |
| `download_urls` | string[] | 新增 | **新客户端优先读这个**。顺序即优先级，第 0 项是 GitHub 权威源 |
| `sha256` | string(64hex) | 新增 | EXE 的 SHA256。缺失时新客户端跳过校验（兼容旧 version.json） |

### 客户端读取逻辑（E4 实现依据）

```
urls = info.get("download_urls") or []
if not urls and info.get("download_url"):
    urls = [info["download_url"]]      # v1 兼容
sha = info.get("sha256")               # 可能为 None（旧 schema）
```

### 兼容性矩阵（必看）

| 客户端版本 \ version.json | v1（只有 download_url） | v2（有 download_urls + sha256） |
|---|---|---|
| **旧 EXE**（现状，只认 download_url） | ✅ 正常 | ✅ 正常（读 download_url，忽略新字段） |
| **新 EXE**（本期产出） | ✅ 正常（fallback 到单 URL，跳过 SHA256） | ✅ 正常（多源 + 校验） |

**关键保证**：新 schema 是**纯增量**，不删除任何旧字段。这是"渐进扩展"取舍的结果（见第 6 节）。

---

## 3. Task Breakdown

> 工作量图例：S = <1h，M = 半天，L = 1-2 天
> 核心业务逻辑标记：⚠️ = 涉及（影响 Gate 4 分级）

| Task | 目标 | 输入 | 输出 | 依赖 | 文件 | 核心? | 量级 |
|---|---|---|---|---|---|---|---|
| **T1** | version.json schema 升级 + 现状修复 | E1/E2/E4 的字段需求 | 新 schema 文档 + 修正当前不一致的 version.json（1.0.0→1.1.0，download_url tag 修正） | 无 | `version.json` | 否 | S |
| **T2** | build.py 版本号一致性校验（E1） | T1 的 schema 定义 | `_assert_version_consistent()` + 3 项断言 | T1 | `build.py` | 否 | S |
| **T3** | update.log 日志基础设施（E3 基座） | 现有 crash.log 模式 | 新增 `_log_update(stage, status, detail)` 工具函数 + update.log 文件 | 无 | `desktop_pet.py` | 否 | S |
| **T4** | SSL 白名单工具（E5） | PM 约束（GitHub 权威源） | `_make_ssl_context(host)` 函数 + 白名单常量 | 无 | `desktop_pet.py` | 否 | S |
| **T5** | `_fetch_json` 接入 E5 SSL + E3 日志 | T3, T4 | 改造 `desktop_pet.py:3515-3529` | T3,T4 | `desktop_pet.py` | 否 | S |
| **T6** | `_DownloadThread` 多源 fallback（E4 核心） | T1 schema | 构造函数接 URL 列表；run() 串行尝试 | T1 | `desktop_pet.py` | ⚠️是 | M |
| **T7** | `_DownloadThread` SHA256 校验（E2） | T1 sha256 字段, T6 | 下载后校验；缺失跳过；不匹配拒绝 | T1,T6 | `desktop_pet.py` | ⚠️是 | M |
| **T8** | `_DownloadThread` 异常 + 日志（E3 收尾） | T3, T6 | except 记录 URL+异常；emit 带错误码 | T3,T6 | `desktop_pet.py` | ⚠️是 | S |
| **T9** | `_DownloadThread` 接入 E5 SSL | T4, T6 | 用 `_make_ssl_context(host)` 替换硬编码 | T4,T6 | `desktop_pet.py` | 否 | S |
| **T10** | `Updater` 适配新 schema + 多源（E4 上层） | T6 | `_prompt_update`/`_download_and_replace` 传 URL 列表；"打开下载页"指列表[0] | T6 | `desktop_pet.py` | ⚠️是 | S |
| **T11** | UI 失败文案优化（按错误码） | T8 错误码 | 区分 network/sha256_mismatch/canceled 的用户提示 | T8 | `desktop_pet.py` | 否 | S |
| **T12** | publish.py 发布脚本（E6） | T1 schema | 算 SHA256 + 生成 version.json.draft + 打印清单 | T1 | 新增 `publish.py` | 否 | M |
| **T13** | 文档更新 | 全部 | 更新 README/发版说明，记录新 schema 和发版流程 | T1-T12 | `README.md` 或新增 `docs/` | 否 | S |

**Task 数：13。涉及核心业务逻辑（⚠️）的：T6/T7/T8/T10 共 4 个**——这是 Gate 4 风险分级的依据。

---

## 4. Dependencies（执行顺序）

```
T1 (schema 定义) ──┬─→ T2 (build.py 校验)
                   ├─→ T6 (多源 fallback 核心) ──┬─→ T7 (SHA256) ──→ T8 (异常日志) ──┬─→ T11 (UI 文案)
                   ├─→ T10 (Updater 上层适配) ──┤                                ├─→ T9 (SSL 接入)
                   └─→ T12 (publish.py)          └────────────────────────────────┘
T3 (update.log 基座) ──┬─→ T5 (_fetch_json 接入)
                       ├─→ T8
                       └─→ T9（间接）
T4 (SSL 工具) ──┬─→ T5
                └─→ T9
T13 (文档) ← 全部完成后
```

**推荐执行批次**（Developer 按 batch 推进，每 batch 完成后可做局部自测）：

| Batch | Tasks | 说明 |
|---|---|---|
| **B1（基础设施）** | T1, T3, T4 | 互不依赖，可并行。产出 schema + 日志工具 + SSL 工具 |
| **B2（检测链路轻改造）** | T5, T2 | 把 E5/E3 先接到检测链路（不动核心下载） |
| **B3（下载链路核心）** | T6 → T7 → T8 → T9 → T10 | **核心 batch**，串行，每步独立验证。这是 Gate 4 重点审查区 |
| **B4（收尾）** | T11, T12, T13 | UI 文案 + 发布工具 + 文档 |

**关键路径**：T1 → T6 → T7 → T8 → T10（决定整体工期）。

---

## 5. Technical Risks（本计划特有，区别于 ADR 的 R0-R3）

| ID | 风险 | 影响 | 缓解 |
|---|---|---|---|
| **TR1** | **向后兼容风险**：老网友的旧 EXE 读新版 version.json 崩溃 | 高（影响存量用户） | schema 纯增量设计（第 2 节兼容矩阵已证明）；**T1 验收必须含"用旧逻辑读新 schema"测试** |
| **TR2** | **数据迁移风险**：作者手动维护的 version.json 旧数据怎么过渡 | 中 | publish.py 生成 draft 不直接覆盖；T1 顺带修正当前不一致的 version.json 作为模板 |
| **TR3** | **网友侧验证风险**：怎么在"没有真新版本"时测更新流程 | 高（Gate 4 验收 blocker） | 本地 mock version.json（见第 8 节 Testing）；用本地 HTTP server 模拟镜像源 |
| **TR4** | **多源 fallback 串行卡顿**：第一个镜像挂了，用户等超时才轮到下一个 | 中 | 单 URL timeout 保持 60s（现状）但**考虑降到 20-30s**（PM 确认）；或首 URL 失败后后续用更短 timeout |
| **TR5** | **SHA256 误判**：作者上传 EXE 后改了文件（重打包）但没更新 sha256 | 中 | E1 校验断言含"sha256 与 download_url 指向的 Release 一致"（需 publish.py 提示）；T2 build 期不校验远端 sha（无法访问） |
| **TR6** | **加速镜像 URL 模板写死**：gh-proxy 跑路后 version.json 里的 URL 全失效 | 中 | 数据驱动设计——作者改 version.json 即可，不需发新版客户端；publish.py 提供镜像列表模板可快速更新 |
| **TR7** | **update.log 膨胀**：每次启动都写 CHECK 日志，长期堆积 | 低 | 限制日志大小（如 >1MB 时滚动）；或只记失败不记成功 |
| **TR8** | **SSL 白名单遗漏**：未来加新权威源时白名单没更新 | 低 | 白名单缺失时走宽松模式 + warn（不阻断），SHA256 兜底 |

---

## 6. Trade-off Analysis（实现策略取舍）

### 取舍 1：version.json schema — 激进重构 vs 渐进扩展
- **选择：渐进扩展（双字段并存）**。
- 理由：存量网友已跑着旧 EXE，激进重构（删 download_url）会让旧客户端直接读不到下载地址。`download_urls` 与 `download_url` 共存，前者优先，成本只是多一个字段。
- 代价：长期有冗余字段。可在 N 个版本后（存量都升级了）再考虑废弃 `download_url`。

### 取舍 2：多源 fallback — 串行 vs 并发竞速
- **选择：串行尝试**。
- 理由：
  - 并发竞速（同时请求多个镜像，谁先返回用谁）速度最快，但**实现复杂度高**（要管理多线程、取消、内存）——违背"最小变更"原则。
  - 串行 + 合理 timeout（首源失败后后续缩短 timeout）已足够覆盖"镜像挂了"场景。
  - 几十人规模、低频更新，速度不是硬瓶颈。
- 代价：首个镜像卡顿时用户多等一个 timeout。缓解见 TR4。

### 取舍 3：日志 — 复用 crash.log vs 独立 update.log
- **选择：独立 update.log**。
- 理由：
  - crash.log 是"崩溃现场"，update.log 是"更新流程"，**关注点不同**，混在一起作者排查时难筛。
  - crash.log 用 faulthandler（C 层段错误）+ excepthook，机制重；update 流程只需普通 append，没必要复用那套。
  - 同目录（`app_dir()`）便于用户一键打包发作者。
- 代价：多一个文件。可接受。

### 取舍 4：publish 脚本 — 独立文件 vs 扩展 build.py
- **选择：独立 publish.py**。
- 理由：
  - **职责分离**：build.py = "产出 EXE"，publish.py = "发布到远端"。两者失败处理、用户心智不同。
  - 作者常需"打包后先本地测 EXE 再发布"——分开能让作者在两步之间插入测试。
  - build.py 已 160 行，再塞发布逻辑会过载。
- 代价：作者多记一条命令。用 build.py 末尾打印提示缓解。

### 取舍 5：SHA256 缺失时的策略 — 强制要求 vs 跳过
- **选择：缺失时跳过（向后兼容）**。
- 理由：存量 version.json 没有 sha256，强制要求会让旧 schema 的客户端无法更新。SHA256 是"有则更安全"，不是"无则不能用"。
- 代价：作者忘了填 sha256 时该次更新无校验。用 publish.py 自动计算 + E1 断言提示缓解。

---

## 7. Gate 4 风险分级建议

**建议：Level B（核心功能）**

### 判据对照（逐条命中分析）

| 信号 | 是否命中 | 证据 |
|---|---|---|
| 涉及 AI/LLM 输出 | 否 | 本期不涉及 AI |
| 涉及数据流/Schema 变更 | **是** | version.json schema 升级（新增 download_urls + sha256） |
| 涉及核心业务逻辑 | **是** | 更新安装链路（下载→校验→替换→重启）是核心；T6/T7/T8/T10 改的就是这条主链 |
| 来源 | 核心模块修改 | 改的是 `Updater`/`_DownloadThread`，非新功能 |
| 影响面 | 多模块 | 改动横跨 `desktop_pet.py`（多个函数）+ `build.py` + 新增 `publish.py` + `version.json` |

**结论**：多个信号命中 Level B → 整体按 Level B。

### Level B 对应的 Evidence 要求
- **Investigator ✅**（独立验证：多源 fallback 实际行为、SHA256 拦截、向后兼容）
- **Reviewer ✅**（代码质量 + 架构一致性 + 安全检查 + 可维护性）
- **Test Engineer ✅**（Unit + Integration + 向后兼容回归）

### 不建议 Level C 的理由
虽然 schema 变更，但：
- 不是破坏性变更（纯增量，老客户端兼容）。
- 不是生产事故修复（是预防性加固）。
- 不涉及全系统/用户数据破坏（最坏情况是"更新失败"，回退到现状）。
- 有完整回滚路径（见第 9 节）。

按"任一命中 B 即按 B，不命中 C 不升级"的原则，**Level B 是恰当的**。

---

## 8. Testing Strategy（给 Test Engineer 的输入）

### 8.1 Unit Test（必须）

| 测试项 | 目标函数 | 覆盖点 |
|---|---|---|
| **UT1** 版本号解析 | `_parse_version` | 正常格式 / 非法 / 空 / 含非数字 |
| **UT2** version.json schema 兼容 | schema 读取逻辑 | v1（只有 download_url）/ v2（全字段）/ 损坏 JSON |
| **UT3** URL 列表规范化 | `Updater` 的取值逻辑 | 有 download_urls / 只有 download_url / 两者都无 |
| **UT4** SSL 白名单匹配 | `_make_ssl_context` | github.com（严）/ gh-proxy.com（宽+warn）/ 未知域名 |
| **UT5** SHA256 计算与比对 | 校验逻辑 | 匹配 / 不匹配 / 字段缺失（跳过） |
| **UT6** 错误码映射 | emit 的错误码 | network / sha256_mismatch / canceled |

### 8.2 Integration Test（必须）

| 测试项 | 场景 | Mock 方式 |
|---|---|---|
| **IT1** 多源 fallback 串行 | 第 1 URL 超时 → 第 2 成功 | 用 `httpd` 或 `python -m http.server` 起本地 server；或 monkeypatch `urllib.request.urlopen` 模拟异常 |
| **IT2** 全部 URL 失败 | 3 个 URL 都挂 | 全部 mock 抛异常；验证最终 emit(False) + update.log 有 3 条 FAIL |
| **IT3** SHA256 拦截 | 下载内容被篡改 | mock 返回错误内容；验证临时文件被删 + emit(False, "sha256_mismatch") |
| **IT4** 向后兼容（旧 schema） | version.json 只有 download_url | 用现状 version.json；验证新客户端能单源下载 + 跳过校验 |
| **IT5** 向后兼容（旧客户端读新 schema） | 模拟旧 EXE 逻辑读 v2 version.json | 静态检查：旧逻辑 `info.get("download_url")` 能取到值（schema 设计保证） |
| **IT6** build.py 一致性断言 | 三处版本不一致 | 构造不一致的 version.json；验证 build.py sys.exit(1) |
| **IT7** publish.py 生成 draft | 正常流程 | 跑 publish.py；验证 version.json.draft 的 sha256 与实际文件一致 |

### 8.3 网友侧验证（TR3 的关键缓解）

**问题**：怎么在"没有真新版本"时端到端测更新流程？

**方案：本地 mock 链路**（Test Engineer 主导）：
1. 起本地 HTTP server：`python -m http.server 8000`，把一个测试 EXE 放进去。
2. 临时改 `VERSION_CHECK_URL` 指向 `http://localhost:8000/version.json`（仅测试构建）。
3. version.json 里：
   - `version` 设成比 APP_VERSION 高（触发更新提示）。
   - `download_urls` 含 `http://localhost:8000/糊宠.exe` + 一个故意失效的 URL（测 fallback）。
   - `sha256` 设成测试 EXE 的真实 hash（测通过）/ 错误 hash（测拦截）。
4. 跑客户端 → 触发更新 → 验证全链路。

**注意**：E5 SSL 白名单对 `localhost` 会走宽松模式（非白名单），测试时不影响。但要在 UT 里单独测白名单严格性。

### 8.4 回归测试（必须）
- **现有功能不破**：宠物交互、皮肤、番茄钟等与更新无关的功能零回归。
- **更新流程现状不破**：在不接入 mock 的正常环境下，"检查更新"按钮的行为与现状一致（除了多了日志）。

---

## 9. Rollback Plan

### 代码回滚
- 所有改动在 `desktop_pet.py` / `build.py` / 新增 `publish.py` / `version.json`。
- git revert 单 commit（建议 Developer 把 T1-T13 合并成少量 commit，便于回滚）。
- 回滚后回到现状（单源 + 无校验 + 无日志）。

### 数据回滚（version.json）
- version.json 是文本文件，git 历史里随时可恢复旧版。
- publish.py 生成的是 `.draft`，不直接覆盖，无数据破坏风险。

### 网友侧回滚
- **最坏情况**：新客户端有 bug，网友更新后挂了。
- 缓解：
  1. EXE 替换前有备份机制（`desktop_pet.py:3690` 已有 `copy /y cur_exe cur_exe.bak`）——**现状已有，本期不动**。
  2. 批处理有失败还原分支（`:3692-3696`）——现状已有。
  3. **建议本期补一个"回滚到上一版本"的右键菜单项**（Should，非 MVP）——让网友能一键还原 .bak。如不补，网友可手动把 `糊宠.exe.bak` 改名回去。

### 回滚决策权
- Gate 4 验收若发现 P0（如 SHA256 误判导致全员无法更新）→ Main Agent 触发 Gate 回退 → 代码 revert + version.json 恢复 → 通知网友手动从 GitHub Release 下载。

---

## 10. Pre-flight Checklist（Gate 3 自检）
- [x] 已理解现有架构（ADR + 本次补读确认所有行号）
- [x] 已读取相关代码（desktop_pet.py 更新模块全段 / build.py / 糊宠.spec / version.json）
- [x] 已确认影响范围（改动集中在更新模块 + build.py + 新增 publish.py，零跨模块）
- [x] 已分析风险（TR1-TR8，含向后兼容/迁移/验证三大类）
- [x] 已考虑替代方案（第 6 节 5 个 Trade-off 均有选型理由）

## 11. Constraints Honored
- **PM 约束 1（镜像列表数据驱动）**：download_urls 在 version.json，代码不硬编码镜像。
- **PM 约束 2（检测链路不动）**：`CheckUpdateThread` 的 CDN→raw fallback 保持原样（T5 只接入 SSL+日志，不改 fallback 逻辑）。
- **PM 约束 3（GitHub 权威源）**：download_urls[0] = GitHub Release；"打开下载页"指 GitHub。
- **PM 约束 4（不引入实名组件）**：零 EdgeOne/Gitee/对象存储，纯 GitHub + 公共加速镜像。
- **最小变更原则**：沿用 `_DownloadThread`/`Updater`/`app_dir()`/crash.log 模式扩展，不引入新框架/新依赖。

## 12. Definition of Done（Gate 3 出口）
- Developer 知道做什么：13 个 Task，每个有输入/输出/依赖/文件/量级。
- Reviewer 知道检查什么：核心逻辑在 T6/T7/T8/T10；安全在 T4/T9；架构一致性在 schema 设计。
- Test Engineer 知道验证什么：UT1-UT6 + IT1-IT7 + 回归 + 本地 mock 端到端。
- Gate 4 分级：**Level B**（Investigator + Reviewer + Test Engineer 三方）。
