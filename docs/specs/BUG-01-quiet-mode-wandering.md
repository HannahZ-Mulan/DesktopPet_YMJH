# Bug: 安静模式下宠物一直在乱动（与"10分钟沿桌边走一次"的指令违背）

## Bug Description

用户启用了"安静模式"（`config.json` 中 `quiet: true`），按设计意图，安静模式下宠物应当**几乎不动**——每 10 分钟左右才沿桌子边缘走一次。但实际表现是：**宠物一直在乱动**，不停在屏幕/窗口边缘走动、换边、跳窗，与安静模式的设计完全违背。

进一步检查 `config.json` 发现 `stand_mode: true`（站窗口玩耍模式）和 `quiet: true`（安静模式）同时为开启状态。两个模式叠加时，站窗口模式的逻辑完全绕过了安静模式的"少走动"约束，导致宠物持续运动。

- **期望行为**：安静模式开启时，宠物基本静止待机，约每 10 分钟才沿屏幕/桌子边缘走一次，绝不站窗口玩耍、不跳窗、不持续走动。
- **实际行为**：安静模式下宠物仍持续走动、站窗口、跳窗换边，几乎和正常模式无区别。

## Problem Statement

需要解决的核心问题：**当 `quiet_mode` 开启时，必须让宠物真正安静下来——压制所有非必要的自主运动**（尤其是站窗口玩耍模式 `_tick_stand` / `_enter_stand` 的持续走动），同时保留"每 10 分钟沿桌边走一次"这一最低限度的运动需求。

当前代码的缺陷是：安静模式的判断只散落在 `_decide_behavior_inner`、`_enter_wander`、若干 `bubble` 调用处，**但没有覆盖 `_tick_inner` 中 `_enter_stand()` 的自动触发分支**。这是导致"一直在乱动"的直接原因。

## Solution Statement

采用**最小改动 + 唯一真相源**的修复策略：

1. **主修复**：在 `_tick_inner` 的站窗口自动触发分支中，增加 `not self._quiet_mode` 守卫——安静模式下绝不自动进入站窗口玩耍，从源头切断持续走动。
2. **兜底修复**：若进入安静模式时宠物**正处于** `S_STAND` 状态（用户在已站着玩时切了安静模式），立即退出站窗口状态回到 IDLE，避免"切了安静还在站着走"的残留。
3. **不改动**安静模式在 `_decide_behavior_inner` 中的 wander 概率（`0.005`、3 秒决策周期，期望频率 ≈ 1 次/10 分钟，已正确符合需求），避免引入新的行为回归。

## Steps to Reproduce

1. 确保 `config.json` 中 `quiet: true` 且 `stand_mode: true`（当前文件即为此状态）。
2. 启动 `desktop_pet.py`（或运行打包后的 `糊宠.exe`）。
3. 观察宠物：在无任何鼠标交互的情况下，宠物会在数秒内跳到某个窗口边缘，开始沿边走动、到角换边、鼠标移到别的窗口又跳过去。
4. 预期：安静模式下宠物应几乎静止；实际：持续乱动。

## Root Cause Analysis

调用链与时间常数梳理：

**1. 决策定时器（次因，但本身是正确的）**
- `_behavior_timer` 每 **3000ms（3 秒）** 触发一次 `_decide_behavior` → `_decide_behavior_inner`（`desktop_pet.py:1155`）。
- 安静模式分支（`desktop_pet.py:1439-1444`）：
  ```python
  if self._quiet_mode:
      if self._state == S_IDLE and random.random() < 0.005 and not self.underMouse():
          self._enter_wander()
      else:
          self._set_state(S_IDLE, random.uniform(8, 16))
      return
  ```
- 期望频率：`0.005 / 3s ≈ 1 次 / 600s = 1 次 / 10 分钟`。**这与"每 10 分钟走一次"的需求精确吻合，本身不是 bug。**

**2. 站窗口自动触发（主因 / 真 bug）**
- `_tick_inner`（每帧调用）中（`desktop_pet.py:1855-1859`）：
  ```python
  elif self._stand_mode and self._state in (S_IDLE, S_LOOK):
      self._stand_retry_t = getattr(self, "_stand_retry_t", 0) + dt
      if self._stand_retry_t >= 1.0:
          self._stand_retry_t = 0
          self._enter_stand()
  ```
- **这里完全没有 `quiet_mode` 检查。** 当 `stand_mode=True` 时，只要宠物处于 IDLE/LOOK，每 ~1 秒就会尝试 `_enter_stand()` 跳到鼠标下方的窗口上。
- 一旦进入 `S_STAND`，`_tick_stand`（`desktop_pet.py:1637`）会**持续**：沿边走动（`_stand_walk_dir`）、到角换边（`_maybe_turn_corner`）、鼠标停在新窗口上就跳过去。这些动作**全程没有安静模式判断**，于是宠物"一直在乱动"。

**3. 状态机层面的冲突**
- 安静模式分支只在 `_state == S_IDLE` 时生效（`desktop_pet.py:1440`）。但站窗口模式会把宠物从 IDLE 拉进 S_STAND，之后 `_decide_behavior_inner` 里的安静模式分支根本进不去（因为状态不是 IDLE，且 `S_STAND` 不在 `return` 早退列表之外的处理范围）。
- 结论：**`stand_mode` 与 `quiet_mode` 同时开启时，站窗口逻辑完全劫持了行为决策，安静模式形同虚设。**

## Relevant Files

Use these files to fix the bug:

- `desktop_pet.py`
  - **`_tick_inner`（约 1830-1880 行）**：站窗口自动触发分支在此，是主修复点——需加 `not self._quiet_mode` 守卫。
  - **`_toggle_quiet`（约 3227-3231 行）**：切换安静模式的入口，是兜底修复点——开启安静模式时若正处 `S_STAND`，应立即退出。
  - **`_decide_behavior_inner`（约 1438-1444 行）**：安静模式 wander 概率逻辑，**确认无需改动**（0.005/3s ≈ 1次/10分钟，已正确）。
  - **`_enter_wander`（约 1471-1501 行）**：安静模式沿边缘走的实现，**确认无需改动**（已正确按 bottom/left/right 边缘走）。

## Step by Step Tasks

IMPORTANT: Execute every step in order, top to bottom.

### Task 1: 在 `_tick_inner` 站窗口自动触发分支增加安静模式守卫

**User Story**: 作为安静模式用户，我希望开启安静模式后宠物绝不再自动跳上窗口玩耍，这样它才不会一直沿窗口边缘乱跑。

- 定位 `desktop_pet.py` 中 `_tick_inner` 方法的站窗口自动触发分支（约 1855 行）：
  ```python
  elif self._stand_mode and self._state in (S_IDLE, S_LOOK):
      self._stand_retry_t = getattr(self, "_stand_retry_t", 0) + dt
      if self._stand_retry_t >= 1.0:
          self._stand_retry_t = 0
          self._enter_stand()
  ```
- 在条件中追加 `and not self._quiet_mode`，使安静模式下完全不触发自动站窗口：
  ```python
  elif self._stand_mode and not self._quiet_mode and self._state in (S_IDLE, S_LOOK):
      self._stand_retry_t = getattr(self, "_stand_retry_t", 0) + dt
      if self._stand_retry_t >= 1.0:
          self._stand_retry_t = 0
          self._enter_stand()
  ```
- 不改动 `_stand_retry_t` 累计逻辑、不改动 `_enter_stand` 内部实现，保持最小改动。

**Acceptance Criteria**:
- [ ] `quiet_mode=True` 且 `stand_mode=True` 时，宠物处于 IDLE/LOOK 状态下不再调用 `_enter_stand()`。
- [ ] `quiet_mode=False` 时，站窗口自动触发行为与修复前完全一致（无回归）。
- [ ] 改动仅限该 `elif` 行的条件表达式，不涉及其它行。

### Task 2: 切换进入安静模式时，若正处 `S_STAND` 立即退出

**User Story**: 作为用户，我希望在宠物正站着窗口玩耍时点开安静模式，它能立刻停下来回到桌面待机，而不是把当前这轮站窗口动作走完。

- 定位 `_toggle_quiet` 方法（约 3227-3231 行）：
  ```python
  def _toggle_quiet(self):
      self._quiet_mode = not self._quiet_mode
      if self._quiet_mode:
          self.bubble.hide()
      self._save_config()
  ```
- 在 `if self._quiet_mode:` 分支内，新增：若当前状态为 `S_STAND`，立即重置为 IDLE 并清掉站窗口残留句柄，避免 `_tick_stand` 继续驱动走动：
  ```python
  def _toggle_quiet(self):
      self._quiet_mode = not self._quiet_mode
      if self._quiet_mode:
          self.bubble.hide()
          # 正在站窗口玩耍时切安静 → 立即停下回桌面
          if self._state == S_STAND:
              self._stand_hwnd = None
              self._set_state(S_IDLE, random.uniform(8, 16))
      self._save_config()
  ```
- 说明：`_stand_hwnd` 是 `_enter_stand` 设置、`_tick_stand` 读取的窗口句柄；置 None 后即使状态机有残留也不会再读窗口矩形。`_set_state(S_IDLE, ...)` 让宠物回到安静模式能管到的 IDLE 态。

**Acceptance Criteria**:
- [ ] 宠物处于 `S_STAND` 时点击开启安静模式，宠物立即停止站窗口走动、回到 IDLE。
- [ ] 关闭安静模式（`_quiet_mode` 由 True→False）时行为不变，不会误触发任何状态切换。
- [ ] 非站窗口状态下切换安静模式，行为与修复前一致。

### Task 3: 运行 Validation Commands 验证修复无回归

**User Story**: 作为维护者，我要用项目可用的校验手段确认改动没有引入语法/导入/回归问题。

- 运行下面的 Validation Commands，确认全部通过。

**Acceptance Criteria**:
- [ ] Python 语法编译通过（`python -m py_compile desktop_pet.py` 无报错）。
- [ ] 程序可正常启动（`python desktop_pet.py` 启动不抛异常，手动观察 30 秒：安静模式+站窗口同开时宠物不再持续乱动）。
- [ ] 关闭安静模式后，站窗口玩耍功能仍正常工作（手动验证：取消安静模式，鼠标停在某个窗口上，宠物会跳上去沿边走）。

## Validation Commands

Execute every command to validate the bug is fixed with zero regressions.

1. **语法编译检查**（项目为单文件 Python 脚本，无 `package.json` / `pyproject.toml` 的 test/typecheck 脚本，故用 `py_compile`）：
   ```bash
   python -m py_compile desktop_pet.py
   ```
   预期：无任何输出，退出码 0。

2. **启动 + 行为复现（修复前 vs 修复后对比）**：
   - **修复前复现**（可选，若已应用补丁可跳过）：用 git/备份还原旧代码，`config.json` 保持 `quiet:true, stand_mode:true`，运行 `python desktop_pet.py`，观察宠物会在 ~10 秒内跳窗并持续走动。
   - **修复后验证**：
     ```bash
     python desktop_pet.py
     ```
     保持 `config.json` 为 `quiet:true, stand_mode:true`，启动后观察至少 60 秒：宠物应**几乎不动**（仅待机/呼吸/偶尔眨眼），不跳窗、不沿窗口边缘走。可适当把 `_behavior_timer` 临时调短或在 10 分钟后观察是否出现一次沿屏幕边缘的 wander（验证 wander 逻辑本身仍在工作）。

3. **回归验证（关闭安静模式后站窗口仍正常）**：
   - 右键菜单关闭"安静模式"，保持"站窗口玩耍"开启，鼠标移到某个窗口上停留，确认宠物会跳上窗口沿边走动（即 Task 1 的守卫在非安静模式下不影响功能）。

4. **回归验证（站窗口中切安静模式立即停止）**：
   - 非安静模式下让宠物进入站窗口玩耍，然后右键开启"安静模式"，确认宠物立即停下回到桌面 IDLE（验证 Task 2）。

## Notes

- **不改动 `_decide_behavior_inner` 中安静模式的 `0.005` 概率**：经计算 `0.005 概率 × 每 3 秒决策一次 ≈ 每 600 秒（10 分钟）触发一次 wander`，这恰好是用户期望的"10 分钟沿桌边走一次"，属于正确实现，改了反而违背需求。
- **不改动 `_enter_wander` 的安静模式边缘行走逻辑**：它已正确实现"沿 bottom/left/right 边缘走、不走到屏幕中间"，符合"沿桌子边缘走"的描述。
- 本 bug 的本质是 **`stand_mode` 与 `quiet_mode` 两个模式的状态机隔离不完整**：安静模式的守卫只加在了"决策层"（`_decide_behavior_inner`），没加在"渲染/.tick 层"（`_tick_inner` 的自动站窗口分支）。修复聚焦于补全这处遗漏，不做大面积重构。
- `config.json` 当前 `quiet:true, stand_mode:true` 的组合是真实复现环境；修复后该组合应表现为"安静优先"，站窗口功能在关闭安静模式后恢复。
- 项目为单文件 PyQt 桌宠应用（`desktop_pet.py` ~3800 行），无单元测试套件、无 `package.json`/`pyproject.toml` 中的 test/typecheck 脚本，因此 Validation 以 `py_compile` + 手动运行观察为主。
