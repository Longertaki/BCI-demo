# Demo v1 接口契约

> 版本：v0.1 ｜ 状态：阶段 0 契约（执行者以此为准）
> 范围：不接 LLM、不接真实手环；键鼠活跃度（可模拟）+ 数值 JSON + 桌宠 UI。

---

## 1. 技术栈与约束

- **语言**：Python 3（项目根 `/home/alex/prime/Qiankun`）。
- **数值**：全部走 `demo/config/*.json`，代码里不写死数值。
- **UI**：桌面角落小窗口（桌宠式）。建议 pygame（可 always-on-top）；如不装 pygame，用 tkinter 兜底。
- **依赖**：尽量只用标准库；非标准库（pygame）由 UI 模块自行声明并 `uv pip install`。
- **模块间**：只通过「接口契约 + 文件」协作，不直接 import 别的执行者的内部实现细节。

---

## 2. 目录结构（阶段 0 先搭好）

```
demo/
├── config/
│   ├── balance.json      # 数值参数（曲线、掉落率、经济）
│   └── content.json      # 静态内容（地区、侠客、秘籍、装备、丹药）
├── balance.py            # M1：读取 JSON + 提供曲线函数
├── signal/               # M2：信号采集
│   ├── __init__.py
│   ├── keyboard.py       #   真实键盘活跃度
│   └── simulator.py      #   模拟信号源（demo 主力）
├── core/                 # M3：核心世界模拟
│   ├── __init__.py
│   ├── world.py          #   世界状态 + tick 推进
│   ├── qiyun.py          #   气运流转速度计算
│   ├── adventurer.py     #   主角/侠客
│   └── region.py         #   地区/难度/Boss
├── economy/              # M4：经济系统
│   ├── __init__.py
│   ├── resources.py      #   累计收益 + 门派资源账本
│   ├── blindbox.py       #   盲盒开箱
│   ├── shop.py           #   钱币商城
│   └── gear.py           #   秘籍/装备/丹药/词条
├── ui/                   # M5：桌宠 UI
│   ├── __init__.py
│   └── pet_window.py     #   桌面角落窗口 + 菜单
├── main.py               # 入口：串起 M2→M3→M4→M5
└── tests/                # T1：测试
    ├── test_loop.py      #   玩法循环 smoke test
    └── test_economy.py   #   经济/数值 sanity
```

---

## 3. 数据结构（全局约定）

### 3.1 活跃度因子
- `activity: float`，范围 `0.0 ~ 1.0`（封顶），由 M2 输出，喂给 M3。

### 3.2 资源账本 Ledger（dict）
```python
{"qian": 0,       # 钱币
 "lingshi": 0,    # 灵石
 "neili": 0,      # 内力
 "shengwang": 0}  # 声望
```

### 3.3 角色（主角/侠客）
```python
{
  "id": "hero" / "hero_1",
  "name": str,
  "is_protagonist": bool,
  "level": int,          # 修炼等级
  "aptitude": int,       # 资质 1~10
  "skills": [str],       # 已学秘籍 id
  "equipment": [str],    # 装备 id
  "faction": "zheng"/"mo",  # 正魔
  "power": int           # 强度（派生，由 M3 计算）
}
```

### 3.4 世界状态 WorldState
```python
{
  "time_s": float,           # 世界内累计时间（秒）
  "qiyun_speed": float,      # 气运流转速度（M3 计算）
  "roster": [角色],           # 阵容（≤3，含主角）
  "all_heroes": [角色],       # 全部门派侠客
  "ledger": Ledger,
  "region_id": str,          # 当前地区
  "progress": float,         # 当前地区进度 0~1
  "pending_blindboxes": int, # 待开盲盒数
}
```

### 3.5 事件 Event
```python
{"type": "blindbox_drop" | "qi_yu" | "recruit" | "boss_defeated" | "income_tick",
 "data": {...}}
```
- M3 产出 Event 列表 → M4 结算。

---

## 4. 各模块接口

### M1 balance.py
```python
class Balance:
    @classmethod
    def load(cls, config_dir="config") -> "Balance"
    def curve(self, key: str, n: int) -> float   # base * rate**n
    def growth(self, key: str, n: int) -> float
    def drop_rate(self, key: str) -> float
    def drop_table(self, key: str) -> dict
```
- 只读 JSON，不做业务逻辑。`config/balance.json` 里的字段由 M1 定义并示例。

### M2 signal
```python
def get_activity() -> float   # 真实键盘或模拟，输出 0~1
```
- `simulator.py` 提供可调频率的模拟（demo 主力）。
- `keyboard.py` 监听全局键盘（跨平台可用 `pynput`，如不便则先占位 + 文档说明）。

### M3 core
```python
class World:
    def __init__(self, balance: Balance, content: dict)
    def tick(self, dt_s: float, activity: float) -> list[Event]
    def choose_region(self, region_id: str)      # 选择闯关地区
    def challenge_boss(self, region_id: str)     # 选择 Boss 关卡（自动战斗结算）
    def set_roster(self, ids: list[str])          # 调配阵容（≤3）
    @property
    def state(self) -> WorldState
```
- `qiyun.py`：`speed = base(level) * multiplier(skills) * activity`，activity 封顶。
- `region.py`：地区有难度档，Boss 强度按 `base*rate**n`。

### M4 economy
```python
class Economy:
    def __init__(self, balance: Balance)
    def apply(self, world: World, events: list[Event]) -> None  # 结算事件
    def open_blindbox(self, world: World) -> dict               # 开箱
    def buy(self, world: World, item_id: str) -> bool           # 钱币商城
    def learn_skill(self, world: World, hero_id: str, manual_id: str) -> bool
    def equip(self, world: World, hero_id: str, gear_id: str) -> bool
    def upgrade_passive(self, world: World, building_id: str) -> bool
    def exchange(self, world: World, amount: int, direction: str) -> bool  # 灵石↔钱币
```
- 盲盒掉落表用 `balance.drop_table(...)`，**无保底**。
- 通用技能点：`upgrade_common_skill` 提升钱币/经验获取倍率。

### M5 ui
```python
class PetWindow:
    def __init__(self, world, economy)
    def run(self) -> None   # 主循环：显示状态 + 鼠标菜单（开箱/商城/阵容/选关）
```
- 显示：世界状态、侠客列表、资源账本、手环指示灯（占位）、待开盲盒数。
- 菜单：开箱、商城购买、调配阵容、选择地区/Boss、升级通用技能/被动收益。
- 提供一个 **`--headless`** 模式（无 UI，跑 `main.py --headless` 能输出日志），供测试者用。

### main.py
- 解析 `--headless`、`--sim`（模拟信号）、`--speed N`（时间加速）。
- 串联：signal → world.tick → economy.apply → ui.run（或 headless 循环打印）。

---

## 5. 验收标准（Demo v1 完成定义）

1. `main.py --headless --sim` 能跑通「挂机 → 闯荡 → 掉落盲盒 → 开箱 → 升级」循环并输出日志。
2. 阵容 ≤3 人可调配；地区有难度差异；Boss 可选关挑战。
3. 盲盒开出秘籍/装备/丹药，有稀有度（无保底）。
4. 累计收益自动积累；盲盒需手动开。
5. 门派资源可投入技能（倍率）与被动收益（基础设施）。
6. 钱币商城可买通用技能/基础装备；通用技能点提升钱币/经验获取。
7. 灵石 ↔ 钱币可兑换（比例来自 balance.json）。
8. 数值全部来自 `config/*.json`。
9. UI（非 headless）能以桌宠式窗口运行，鼠标菜单可操作，含手环指示灯占位。
