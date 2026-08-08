# Bug: 长语料显示时长不足，"一闪而过"读不完

## Bug Description

气泡（Bubble）的显示时长没有根据文字长度精准控制。项目里**已有**按字数算时长的能力（`show_smart` / `show_random_smart`），但绝大多数 `bubble.show_*` 调用点没用它，而是传**固定的毫秒数**（如 2200、2400、2600）。结果是：当某条语料较长（≥12 字）时，固定时长不够读完，气泡就淡出消失了——用户看到的就是"长文案一闪而过"。

- **期望行为**：任何气泡文案，显示时长 = 按字数计算（每字约 200ms，下限 2.5s，上限 16s），确保用户能从容读完。
- **实际行为**：长语料（如奖励台词、低血量提醒、分心提醒、番茄钟、休息提醒等）用固定短时长，读不完就消失。

## Problem Statement

需要让所有**较长（≥12 字）语料**的气泡显示走"按字数算时长"的逻辑，并把阅读速度基准从当前的 160ms/字调慢到 200ms/字（约 5 字/秒），让长文案（尤其江湖段子、奖励台词）更易读完。短文案（≤10 字）保持现状不动，避免无谓延长。

## Solution Statement

采用**复用已有能力 + 精准替换**的最小改动策略：

1. **调慢基准**：把 `show_smart` 的每字时长从 `160ms` 改为 `200ms`（一处改动，惠及所有已用 smart 的点：江湖语料待机/点击）。
2. **精准替换调用点**：把审计出的"长语料(≥12字) + 固定短时长"调用点，从 `show_random(texts, 固定ms)` / `show_text(txt, 固定ms)` 改为已有的 `show_random_smart(texts)` / `show_smart(txt)`。这些方法内部就是 `random.choice + 按字数算时长`，零新代码。
3. **不动**短文案调用点（≤10 字，如 INTERACT/WANDER/SLEEP 等），它们固定时长本就足够，改成 smart 反而会让"换好衣服啦"这类本该快速消失的提示无谓延长。

## Steps to Reproduce

1. 启动 `python desktop_pet.py`。
2. 触发以下任一长语料场景（较难主动触发，可用临时手段）：
   - 让血量/内力降到低位 → 触发 `LOW_HP_TEXTS`/`LOW_MP_TEXTS`（最长 14 字，当前 2400ms）。
   - 完成番茄钟 → `POMODORO_DONE_TEXTS`（最长 23 字，走 `_add_pills` 当前 2600ms）。
   - 切换窗口打断专注 → `DISTRACT_SWITCH_TEXTS`（最长 20 字，当前 2600ms）。
   - 道具不足时喂食 → `NO_ITEM_TEXTS`（最长 18 字，当前 2200ms）。
3. 观察：长文案显示约 2-2.6 秒就淡出，来不及读完。

## Root Cause Analysis

**调用链**：`bubble.show_random(texts, duration)` / `show_text(text, duration)` → `show_text` 内部用传入的固定 `duration` 启动 `_timer`（`desktop_pet.py:742`）。只有 `show_smart(text)` 会按 `len(text) * 系数` 算 duration（`:753-754`）。

**缺陷本质**：能力存在但未被一致使用。对 50+ 个调用点逐一审计后，**长语料(≥12字) + 固定短时长**的问题点共 **9 处**（见下）。这些点传的固定时长（2200-2600ms）小于按 200ms/字计算所需时长，导致长文案读不完。

**时长审计表**（200ms/字基准，下限 2500ms、上限 16000ms）：

| 行号 | 调用 | 语料最长 | 当前ms | 需ms | 判定 |
|------|------|---------|--------|------|------|
| 2386 | `show_random(LOW_HP_TEXTS, 2400)` | 14字 | 2400 | 2800 | ✗ |
| 2389 | `show_random(LOW_MP_TEXTS, 2400)` | 14字 | 2400 | 2800 | ✗ |
| 2533 | `show_text(NO_ITEM..., 2200)` | 18字 | 2200 | 3600 | ✗ |
| 2576 | `show_text(txt, 2600)` 奖励台词 | 25字 | 2600 | 5000 | ✗ |
| 2592 | `show_text(txt, 2600)` 奖励台词 | 25字 | 2600 | 5000 | ✗ |
| 2611 | `show_random(DISTRACT_*, 2600)` | 20字 | 2600 | 4000 | ✗ |
| 2662 | `show_random(POMODORO_CANCEL, 2200)` | 13字 | 2200 | 2600 | ✗ |
| 2698 | `show_random(POMODORO_BREAK, 2400)` | 13字 | 2400 | 2600 | ✗ |
| 2840 | `show_random(texts, 2400)` 环境感知 | 14字 | 2400 | 2800 | ✗ |
| 2856 | `show_random(BREAK_TEXTS, 2400)` | 12字 | 2400 | 2500 | ✗ |

注：`2576`/`2592` 是 `_add_pills`/`_add_item` 的奖励台词，承接 `FOCUS_REWARD_TEXTS`(25字)、`POMODORO_DONE_TEXTS`(23字) 等长语料。

**已正确的点（不动）**：`show_random_smart(JIANGHU_TEXTS)`（1998、2820）、`show_text(NIGHT/MORNING_TIPS, 3000/3200)`（13-14字，新基准下够）、以及所有 ≤10 字的短文案调用点。

## Relevant Files

Use these files to fix the bug:

- `desktop_pet.py`
  - **`show_smart`（约 748-755 行）**：时长算法所在，把 `160` 改为 `200`。
  - **9 处调用点（见上表）**：从固定时长改为 `show_random_smart` / `show_smart`。
  - **`show_random_smart` / `show_smart`（约 748-759 行）**：已有的按字数算时长方法，本次复用，**不改动其签名**。
  - **`_add_pills`（约 2559-2578 行）**、**`_add_item`（约 2580-2594 行）**：奖励台词显示处（2576/2592），改用 `show_smart`。

### New Files
无需新建文件。

## Step by Step Tasks

IMPORTANT: Execute every step in order, top to bottom.

### Task 1: 调慢 `show_smart` 阅读速度基准 160ms → 200ms/字

**User Story**: 作为用户，我希望长文案显示得更从容，这样我能读完江湖段子和长奖励台词。

- 定位 `Bubble.show_smart`（约 748-755 行）：
  ```python
  n = len(text)
  duration = min(16000, max(2500, int(n * 160)))
  ```
- 把 `160` 改为 `200`，并同步更新 docstring 里的"每字约 160ms（≈6字/秒）"为"每字约 200ms（≈5字/秒）"：
  ```python
  n = len(text)
  duration = min(16000, max(2500, int(n * 200)))
  ```
- 不改下限 2500、上限 16000。

**Acceptance Criteria**:
- [ ] `show_smart` 系数改为 200。
- [ ] docstring 同步更新。
- [ ] 下限/上限不变（2500/16000）。

### Task 2: 把 9 处"长语料 + 固定短时长"调用点改为按字数算时长

**User Story**: 作为用户，我希望所有较长的提醒台词都显示足够久，不再一闪而过。

逐一修改（用 `show_random_smart` 替换 `show_random(list, 固定ms)`，用 `show_smart` 替换 `show_text(txt, 固定ms)`）：

- **2386** `self.bubble.show_random(LOW_HP_TEXTS, 2400)` → `self.bubble.show_random_smart(LOW_HP_TEXTS)`
- **2389** `self.bubble.show_random(LOW_MP_TEXTS, 2400)` → `self.bubble.show_random_smart(LOW_MP_TEXTS)`
- **2533** `..., 2200)` 这行是 `show_text(random.choice(NO_ITEM_TEXTS).replace("{item}", name), 2200)` → 改为 `self.bubble.show_smart(random.choice(NO_ITEM_TEXTS).replace("{item}", name))`
- **2576** `self.bubble.show_text(txt, 2600)`（`_add_pills` 内）→ `self.bubble.show_smart(txt)`
- **2592** `self.bubble.show_text(txt, 2600)`（`_add_item` 内）→ `self.bubble.show_smart(txt)`
- **2611** `self.bubble.show_random(texts, 2600)`（DISTRACT）→ `self.bubble.show_random_smart(texts)`
- **2662** `self.bubble.show_random(POMODORO_CANCEL_TEXTS, 2200)` → `self.bubble.show_random_smart(POMODORO_CANCEL_TEXTS)`
- **2698** `self.bubble.show_random(POMODORO_BREAK_TEXTS, 2400)` → `self.bubble.show_random_smart(POMODORO_BREAK_TEXTS)`
- **2840** `self.bubble.show_random(texts, 2400)`（环境感知 CODE/VIDEO 等）→ `self.bubble.show_random_smart(texts)`
- **2856** `self.bubble.show_random(BREAK_TEXTS, 2400)` → `self.bubble.show_random_smart(BREAK_TEXTS)`

- 不改动 ≤10 字的短文案调用点、不改动已正确的 NIGHT/MORNING_TIPS（3000/3200ms 够用）、不改动 JIANGHU 的 smart 调用。

**Acceptance Criteria**:
- [ ] 上述 10 处（含 2576/2592 共 10 行）全部改为 smart 版本。
- [ ] 改动仅限这些行，不波及其他调用点。
- [ ] `show_random_smart` / `show_smart` 方法本身签名不变。

### Task 3: 运行 Validation Commands 验证无回归

**User Story**: 作为维护者，我要确认改动无语法错误，且长语料显示时长变长、短文案不受影响。

- 运行下面的 Validation Commands。

**Acceptance Criteria**:
- [ ] `python -m py_compile desktop_pet.py` 通过。
- [ ] 长语料（如低血量提醒、江湖段子）显示时长明显变长，能读完。
- [ ] 短文案（如 INTERACT/WANDER/SLEEP）显示时长不变（仍 1800-2500ms）。
- [ ] 无任何调用点报错（`show_random_smart`/`show_smart` 均为已存在方法）。

## Validation Commands

Execute every command to validate the bug is fixed with zero regressions.

1. **语法编译检查**：
   ```bash
   python -m py_compile desktop_pet.py
   ```
   预期：无输出，退出码 0。

2. **算法验证**（确认新系数生效）：
   ```bash
   python -c "n=106; print('106字->', min(16000, max(2500, int(n*200))), 'ms'); n=14; print('14字->', min(16000, max(2500, int(n*200))), 'ms'); n=5; print('5字->', min(16000, max(2500, int(n*200))), 'ms')"
   ```
   预期：106字→16000ms（上限）、14字→2800ms、5字→2500ms（下限）。

3. **启动 + 行为验证**：
   ```bash
   python desktop_pet.py
   ```
   - 触发江湖语料（点击宠物多次 / 待机闲聊）：长段子应显示约 16 秒，能读完。
   - 若方便触发低血量/番茄完成/分心提醒：确认显示时长比修复前明显变长。
   - 短文案场景（点击交互的普通短句、闲逛、睡觉）：确认仍约 2 秒，未被无谓延长。

4. **回归确认**：
   - 短文案调用点（INTERACT/WANDER/SLEEP/WAKE 等 ≤10字）未被改动，时长不变。
   - `show_random_smart` / `show_smart` 方法签名未变，原有 JIANGHU 调用点（1998/2820）仍正常工作。

## Notes

- **不引入新依赖**：仅复用标准库和已有的 `show_smart` / `show_random_smart`。
- **改动哲学**：能力（按字数算时长）早已存在，本 bug 的本质是"能力未被一致使用"。修复 = 把漏用的长语料调用点补上 + 微调系数，而非新造轮子。
- **为何不动短文案**：≤10 字的文案（如"哇哦～""走走走～"）固定 1800-2500ms 已足够，改成 smart 会被下限 2500ms 抬高，反而让本该轻快的提示变得拖沓。这是"只改长语料"原则的依据。
- **系数选择 200ms/字**：约 5 字/秒，是中文舒适阅读速度的下限。160ms/字（6字/秒）偏快，长文案易读不完；200ms 更从容，尤其适合江湖段子这种需要细看的文案。上限 16000ms（16秒）足够读完 106 字的最长江湖段子（实际 106×200=21200ms 被 cap 到 16000）。
- **未来扩展**：若要全应用统一智能时长，可考虑让 `show_random`/`show_text` 在 `duration` 省略时默认走 smart 逻辑——但那会波及所有调用点，本次按用户要求不做。
