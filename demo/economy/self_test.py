"""M4 经济系统最小自测（可独立运行，不依赖 M1/M3 实现）。

用法（从项目根目录）::

    python3 demo/economy/self_test.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

from economy import Economy  # noqa: E402


# --------------------------------------------------------------------- #
# 契约 M1 的极简桩（自测专用；正式环境由 E1 提供真实 Balance）
# --------------------------------------------------------------------- #
class StubBalance:
    def __init__(self, cfg):
        self.cfg = cfg

    @classmethod
    def load(cls, config_dir="config"):
        return cls(json.loads((Path(config_dir) / "balance.json").read_text(encoding="utf-8")))

    def _find(self, key):
        node = self.cfg
        for part in key.split("."):
            if isinstance(node, dict):
                node = node.get(part)
            else:
                return None
        return node

    def curve(self, key, n):
        node = self._find(key)
        if isinstance(node, dict) and "base" in node:
            return node["base"] * node["rate"] ** n
        raise KeyError(key)

    def growth(self, key, n):
        return self.curve(key, n)

    def drop_rate(self, key):
        node = self._find(key)
        return float(node) if node is not None else 0.0

    def drop_table(self, key):
        node = self._find(key)
        return dict(node) if isinstance(node, dict) else {}


# --------------------------------------------------------------------- #
# 契约 M3 World 的极简桩（自测专用；正式环境由 E3 提供真实 World）
# --------------------------------------------------------------------- #
class StubWorld:
    def __init__(self, state):
        self._state = state

    @property
    def state(self):
        return self._state

    def add_hero(self, hero):
        if any(h.get("id") == hero.get("id") for h in self._state["all_heroes"]):
            return False
        hero.setdefault("skills", [])
        hero.setdefault("equipment", [])
        hero.setdefault("elixirs", [])
        self._state["all_heroes"].append(hero)
        return True


def fresh_state():
    return {
        "time_s": 0.0,
        "qiyun_speed": 1.0,
        "roster": [
            {"id": "hero", "name": "无名少侠", "level": 1, "aptitude": 6,
             "skills": [], "equipment": [], "elixirs": [], "faction": "zheng", "power": 10},
        ],
        "all_heroes": [],
        "ledger": {"qian": 0, "lingshi": 0, "neili": 0, "shengwang": 0},
        "region_id": "r1",
        "progress": 0.0,
        "pending_blindboxes": 0,
    }


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    return cond


def main():
    balance = StubBalance.load(str(DEMO_DIR / "config"))
    econ = Economy(balance, config_dir=str(DEMO_DIR / "config"))
    world = StubWorld(fresh_state())
    st = world.state
    results = []

    # 1) 累计收益自动入账
    st["time_s"] = 100.0
    econ.apply(world, [{"type": "income_tick", "data": {"dt_s": 100.0}}])
    results.append(check("累计收益: 钱币自动入账", st["ledger"]["qian"] > 99.0))
    results.append(check("累计收益: 灵石自动入账", st["ledger"]["lingshi"] > 4.0))
    results.append(check("累计收益: 终身统计存在", st["economy"]["stats"]["earned"]["qian"] > 0))

    # 2) 盲盒掉落 → 手动开箱（无保底）
    econ.apply(world, [{"type": "blindbox_drop", "data": {"count": 5}}])
    results.append(check("盲盒掉落: 待开箱=5", st["pending_blindboxes"] == 5))
    opened = [econ.open_blindbox(world) for _ in range(5)]
    results.append(check("盲盒开箱: 5 次全部成功", all(r.get("ok") for r in opened)))
    results.append(check("盲盒开箱: 待开箱归零", st["pending_blindboxes"] == 0))
    results.append(check("盲盒开箱: 无保底(只开一次消耗一箱)", econ.open_blindbox(world)["ok"] is False))

    # 3) 注入一件装备/秘籍/丹药模拟掉落结果
    st["economy"]["inventory"]["m1"] = 1
    st["economy"]["inventory"]["e1"] = 1
    st["economy"]["inventory"]["d1"] = 1

    # 4) 学习秘籍（一次性、消耗）
    before = int(st["economy"]["inventory"].get("m1", 0))
    results.append(check("学习秘籍: 成功", econ.learn_skill(world, "hero", "m1") is True))
    results.append(check("学习秘籍: 库存消耗", st["economy"]["inventory"].get("m1", 0) == before - 1))
    results.append(check("学习秘籍: 写入 skills", "m1" in st["roster"][0]["skills"]))
    results.append(check("学习秘籍: 重复学习被拒", econ.learn_skill(world, "hero", "m1") is False))

    # 5) 装备（词条结算）
    results.append(check("装备: 成功", econ.equip(world, "hero", "e1") is True))
    results.append(check("装备: 写入 equipment", "e1" in st["roster"][0]["equipment"]))
    results.append(check("装备: 词条字段存在", "affix_bonus" in st["roster"][0]))

    # 6) 丹药（写入 M3 的 elixirs 列表）
    results.append(check("丹药: 服用成功", econ.use_elixir(world, "hero", "d1") is True))
    results.append(check("丹药: 写入 elixirs", "d1" in st["roster"][0]["elixirs"]))

    # 7) 钱币商城（通用技能点 + 基础装备）
    st["ledger"]["qian"] = 10000.0
    results.append(check("商城: 买通用技能点", econ.buy(world, "cs_point") is True))
    results.append(check("商城: 技能点+1", st["economy"]["common_skill"]["points"] == 1))
    results.append(check("商城: 买基础装备", econ.buy(world, "e1") is True))
    results.append(check("商城: 装备入库存", st["economy"]["inventory"].get("e1", 0) >= 1))

    # 8) 通用技能升级（钱币/经验倍率）
    results.append(check("通用技能: 升级成功", econ.upgrade_common_skill(world) is True))
    qian_mult, exp_mult = econ.get_multipliers(world)["qian"], econ.get_exp_multiplier(world)
    results.append(check("通用技能: 钱币倍率>1", qian_mult > 1.0))
    results.append(check("通用技能: 经验倍率>1", exp_mult > 1.0))

    # 9) 被动收益（基础设施）
    results.append(check("被动: 升级聚财堂", econ.upgrade_passive(world, "b_qian") is True))
    rates_before = econ.get_income_rates(world)["qian"]
    st["ledger"]["qian"] = 10000.0
    econ.upgrade_passive(world, "b_qian")
    rates_after = econ.get_income_rates(world)["qian"]
    results.append(check("被动: 产出随等级上升", rates_after > rates_before))

    # 10) 兑换（灵石↔钱币，比例来自 balance.json）
    st["ledger"]["lingshi"] = 10.0
    qian_before = st["ledger"]["qian"]
    results.append(check("兑换: 灵石→钱币", econ.exchange(world, 10, "lingshi_to_qian") is True))
    results.append(check("兑换: 钱币增加", st["ledger"]["qian"] > qian_before))
    results.append(check("兑换: 灵石减少", st["ledger"]["lingshi"] < 10.0))
    results.append(check("兑换: 非法方向被拒", econ.exchange(world, 10, "bogus") is False))

    # 11) 事件结算：奇遇 / Boss / 招募
    qian0 = st["ledger"]["qian"]
    econ.apply(world, [{"type": "qi_yu", "data": {"region_id": "r1"}}])
    results.append(check("事件: 奇遇发放资源", st["ledger"]["qian"] > qian0 or st["ledger"]["lingshi"] > 0))

    boxes0 = st["pending_blindboxes"]
    econ.apply(world, [{"type": "boss_defeated", "data": {"won": True, "difficulty": 3}}])
    results.append(check("事件: Boss 胜利发奖", st["pending_blindboxes"] > boxes0 and st["ledger"]["qian"] > qian0))

    econ.apply(world, [{"type": "recruit", "data": {"hero": {"id": "hero_9", "name": "测试侠客", "aptitude": 5, "faction": "zheng"}}}])
    results.append(check("事件: 招募入派", any(h.get("id") == "hero_9" for h in st["all_heroes"])))

    # 12) 温和通胀：时间推进到后期，产出与成本同步放大
    st["time_s"] = 36000.0 * 2  # 两个通胀档位
    infl = econ.get_multipliers(world)["inflation"]
    results.append(check("通胀: 系数>1", infl > 1.0))
    econ.apply(world, [{"type": "income_tick", "data": {"dt_s": 1.0}}])

    print()
    print("自测结果: %d/%d 通过" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
