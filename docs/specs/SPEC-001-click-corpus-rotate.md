# Feature: 点击时在「普通 / 江湖」语料库间随机轮换

## Feature Description

当前点击宠物时，台词来源固定为 `INTERACT_TEXTS`（"哇哦～/好高！/转晕啦～"）或情感分支 `EMOTION_TEXTS[dom]`，内容单一。本功能让宠物在**被点击（单击 + 双击）时**，每次从「普通语料库 `INTERACT_TEXTS`」和「江湖语料库 `JIANGHU_TEXTS`」中**随机二选一**显示台词，让交互内容更丰富、更有惊喜感。

注意：待机闲聊（`_sense` 中）**早已实现**这种"普通/江湖二选一"逻辑，本特性只是把同样的轮换机制**推广到点击交互**，保持全应用语料风格一致。

## User Story

As a 桌宠主人
I want 点击宠物时，它的台词在「普通萌宠短句」和「一梦江湖长段子」之间随机切换
So that 互动不会千篇一律，偶尔蹦出江湖梗增加趣味和惊喜感

## Problem Statement

点击交互的台词来源过于单一固定（始终是 `INTERACT_TEXTS` 或 `EMOTION_TEXTS`），缺乏变化。而项目里已经有一整套高质量的江湖主题长文案（`JIANGHU_TEXTS`）却只在待机闲聊中偶尔出现，点击这种最高频的交互场景没能利用上。

## Solution Statement

引入一个统一的**点击台词选择方法** `_pick_click_text()`：每次被调用时以 50% 概率返回 `INTERACT_TEXTS` 的一条（用 `show_random` 固定短时长），50% 概率返回 `JIANGHU_TEXTS` 的一条（用 `show_random_smart` 按字数智能调时长，因为江湖文案很长）。

然后将现有的两个点击触发点——
- 单击 `interact()`（`desktop_pet.py:2019-2023`）
- 双击 `_on_double_click()`（`desktop_pet.py:2266`）

——的台词显示**统一替换**为调用这个新方法，并**绕过原有的情感分支判断**（情感台词 `EMOTION_TEXTS` 在点击时不再出现，按用户明确要求"仅 INTERACT_TEXTS"）。

安静模式下点击**照常显示**（用户明确要求"点击时照常显示"，视为主动交互有问必答）。

## Relevant Files

Use these files to implement the feature:

- `desktop_pet.py`
  - **`interact()` 方法（约 2000-2023 行）**：单击的核心交互逻辑，当前末尾有"情感高→EMOTION_TEXTS，否则→INTERACT_TEXTS"的分支，需把台词部分替换为新方法，并去掉情感分支。
  - **`_on_double_click()` 方法（约 2254-2266 行）**：双击的交互逻辑，末尾 `bubble.show_random(INTERACT_TEXTS, 2000)` 需替换为新方法。
  - **语料常量定义区（约 515-575 行）**：`INTERACT_TEXTS`（普通池）和 `JIANGHU_TEXTS`（江湖池）的定义所在，**只读取不改动**。
  - **Bubble 类的 `show_random` / `show_random_smart`（约 745-759 行）**：两个显示方法的现成实现，新方法将组合调用它们，**不改动**。
  - **`_decide_behavior_inner` 的无聊分支（约 1463 行）**：`EMOTION_TEXTS["bored"]` 在非点击场景仍保留——本特性只影响点击，不波及行为决策里的情感台词。

### New Files
无需新建文件。纯在 `desktop_pet.py` 内新增一个方法 + 改两处调用点。

## Implementation Plan

### Phase 1: Foundation —— 新增统一的点击台词选择方法

在 `PetWindow` 类中新增 `_show_click_line()` 方法，封装"普通/江湖随机二选一 + 智能选时长"的逻辑：
- 50% 概率：`self.bubble.show_random(INTERACT_TEXTS, 2000)`（短句，固定 2 秒）
- 50% 概率：`self.bubble.show_random_smart(JIANGHU_TEXTS)`（长文案，按字数自动算时长 2.5s-16s）

这样所有点击触发点共用同一段逻辑，避免重复代码，未来调整概率只改一处。

### Phase 2: Core Implementation —— 替换两个点击触发点

- **单击 `interact()`**：删除原有的情感分支判断（`dom = self.dominant_emotion()` 及后面的 `if/else`），把 `self.bubble.show_random(...)` 替换为 `self._show_click_line()`。注意：情感数值提升（`_boost_emotion`、`_mood`）等**非台词逻辑保留不变**，只动台词显示。
- **双击 `_on_double_click()`**：把末尾 `self.bubble.show_random(INTERACT_TEXTS, 2000)` 替换为 `self._show_click_line()`。

### Phase 3: Integration —— 安静模式兼容

确认 `interact()` / `_on_double_click()` 中的 `bubble.show_*` 调用**不受安静模式拦截**——当前这两处本来就没有 `if not self._quiet_mode` 守卫，点击本就会冒泡，符合用户"点击时照常显示"的要求。无需额外改动，只需在验证阶段确认。

## Step by Step Tasks

IMPORTANT: Execute every step in order, top to bottom.

### Task 1: 新增 `_show_click_line()` 方法

**User Story**: 作为开发者，我想要一个统一的点击台词显示方法，这样单击和双击共用同一段"普通/江湖随机二选一"逻辑，避免重复代码。

- 在 `PetWindow` 类中（建议放在 `interact()` 方法之前，约 1995 行附近）新增方法：
  ```python
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
  ```
- 不改动任何现有方法，仅新增。

**Acceptance Criteria**:
- [ ] 方法定义存在且无语法错误。
- [ ] 50% 概率走 `show_random(INTERACT_TEXTS, 2000)`。
- [ ] 50% 概率走 `show_random_smart(JIANGHU_TEXTS)`。
- [ ] 方法内无安静模式判断（点击照常显示）。

### Task 2: 替换单击 `interact()` 的台词显示

**User Story**: 作为用户，我希望单击宠物时台词在普通和江湖之间随机切换，而不是固定的几句萌宠短句。

- 定位 `interact()` 方法末尾（约 2018-2023 行）：
  ```python
  # 台词受主导情感影响
  dom = self.dominant_emotion()
  if self._emotions[dom] > 50 and random.random() < 0.6:
      self.bubble.show_random(EMOTION_TEXTS[dom], 2000)
  else:
      self.bubble.show_random(INTERACT_TEXTS, 2000)
  ```
- 替换为：
  ```python
  # 点击台词：普通/江湖语料库随机二选一（绕过情感分支）
  self._show_click_line()
  ```
- **保留**该方法中前面的 `_boost_emotion`、`_mood`、`_interact_idx` 等非台词逻辑不变。
- 删除不再使用的 `dom = self.dominant_emotion()` 局部变量（避免 lint 死代码警告）。

**Acceptance Criteria**:
- [ ] 单击后 50% 显示普通短句、50% 显示江湖长文案。
- [ ] 情感数值提升逻辑（happy/excited/mood）仍正常工作。
- [ ] 江湖长文案显示时长足以读完（不会 2 秒就消失）。
- [ ] 点击时不再出现 `EMOTION_TEXTS` 情感台词。

### Task 3: 替换双击 `_on_double_click()` 的台词显示

**User Story**: 作为用户，我希望双击宠物时也能享受同样的语料轮换，保持单击/双击台词风格一致。

- 定位 `_on_double_click()` 末尾（约 2266 行）：
  ```python
  self.bubble.show_random(INTERACT_TEXTS, 2000)
  ```
- 替换为：
  ```python
  self._show_click_line()
  ```
- 保留该方法中 `_mark_pet_interact()`、跳舞判断、`_play(ANIM_SPIN)`、`_mood`/`_boost_emotion` 等逻辑不变。

**Acceptance Criteria**:
- [ ] 双击后 50% 普通短句、50% 江湖长文案。
- [ ] 双击的跳舞/转圈/心情提升逻辑不受影响。
- [ ] 睡觉状态下双击仍优先唤醒（`_wake_up` 早退），不被新台词逻辑干扰。

### Task 4: 运行 Validation Commands 验证无回归

**User Story**: 作为维护者，我要确认改动没有引入语法错误，且点击台词轮换按预期工作。

- 运行下面的 Validation Commands，确认全部通过。

**Acceptance Criteria**:
- [ ] `python -m py_compile desktop_pet.py` 通过，无报错。
- [ ] 启动程序后单击宠物 10 次，统计台词来源：约半数 INTERACT_TEXTS、半数 JIANGHU_TEXTS。
- [ ] 双击宠物多次，确认同样轮换。
- [ ] 开启安静模式后点击，台词仍照常显示（不受安静模式拦截）。
- [ ] 待机闲聊（非点击）的语料逻辑未受影响。

## Testing Strategy

### Unit Tests
项目无单元测试套件（单文件 PyQt 应用，无 `tests/` 目录、无 `pyproject.toml` test 脚本）。核心逻辑（50/50 概率分支）结构简单，通过手动运行验证。

### Integration Tests
手动运行 `python desktop_pet.py`，连续点击 10+ 次观察台词分布。

### Edge Cases
- **江湖文案很长**：必须确认用 `show_random_smart`（按字数调时长），否则用 `show_random` 固定 2 秒会读不完。已在方案中区分。
- **安静模式 + 点击**：确认点击台词不被安静模式吞掉（`interact`/`_on_double_click` 本就无 `if not self._quiet_mode` 守卫）。
- **躲猫猫状态点击**：`interact()` 中 `S_PEEK` 会先走 `_caught()` 早退（2010-2012 行），不会触发新台词逻辑——确认这个早退在台词替换之前，不受影响。
- **双击唤醒**：`_on_double_click` 中 `S_SLEEP` 会先 `_wake_up()` 早退，不触发新台词——确认不受影响。

## Acceptance Criteria

- [ ] 单击宠物：台词在 `INTERACT_TEXTS` 与 `JIANGHU_TEXTS` 间约 50/50 随机分布。
- [ ] 双击宠物：同样 50/50 随机分布。
- [ ] 江湖长文案显示时长按字数自动计算（2.5s-16s），能读完。
- [ ] 点击不再触发 `EMOTION_TEXTS` 情感台词（绕过情感分支）。
- [ ] 安静模式下点击照常显示台词。
- [ ] 情感数值、心情值、跳舞/转圈等非台词交互逻辑零回归。
- [ ] 待机闲聊语料逻辑零回归。
- [ ] `python -m py_compile desktop_pet.py` 通过。

## Validation Commands

Execute every command to validate the feature works correctly with zero regressions.

1. **语法编译检查**：
   ```bash
   python -m py_compile desktop_pet.py
   ```
   预期：无输出，退出码 0。

2. **启动 + 点击轮换验证**：
   ```bash
   python desktop_pet.py
   ```
   - 连续**单击**宠物 10 次，记录每次台词：应出现约 5 次萌宠短句（"哇哦～"等）+ 约 5 次江湖长段子。
   - 连续**双击**宠物 10 次（若无舞蹈皮肤则转圈），同样观察轮换。
   - 确认江湖长文案显示时长明显长于 2 秒（能读完）。

3. **安静模式兼容验证**：
   - 右键开启"安静模式"，然后点击宠物，确认台词**照常显示**（不被吞）。

4. **回归验证（非点击场景不受影响）**：
   - 待机约 2 分钟（或调高闲聊概率临时测试），确认待机闲聊仍是原来的普通/江湖随机逻辑、未被新方法误改。
   - 拖拽、抛掷、躲猫猫被抓等其他交互的台词（`WANDER_TEXTS`/`CAUGHT_TEXTS` 等）未受影响。

## Notes

- **本特性不引入新依赖**：仅用标准库 `random` 和已有的 bubble 方法。
- **概率可调**：未来若想调整普通/江湖比例（如 70/30），只需改 `_show_click_line()` 中的一处 `0.5`。
- **与待机闲聊的关系**：`_sense` 中的待机闲聊（`desktop_pet.py:2806-2812`）已是同样的 50/50 随机逻辑，本特性让点击与之风格统一。两处逻辑独立、互不影响。
- **绕过情感分支是有意为之**：用户明确要求点击时"仅 INTERACT_TEXTS"作为普通池，不含 `EMOTION_TEXTS`。情感数值本身仍随点击正常累积，只是不再决定点击台词。
- **江湖文案显示**：务必用 `show_random_smart` 而非 `show_random`，否则 2 秒读不完上百字的长段子——这是方案中两池采用不同显示方法的原因。
