"""地区/难度/Boss（M3）。

* 地区难度强度 = balance.curve("region_difficulty", difficulty)
* Boss 强度    = balance.curve("boss_power", boss.tier)     （tier 缺失时回退 difficulty）
两者均为 base*rate^n 形式。
"""

from __future__ import annotations

from ._util import curve_value


def region_power(balance, difficulty):
    """地区怪物强度（base*rate^difficulty）。"""
    return curve_value(balance, "region_difficulty", int(difficulty))


def boss_power(balance, tier):
    """Boss 强度（base*rate^tier）。"""
    return curve_value(balance, "boss_power", int(tier))


class Region:
    """地区封装：id / 名称 / 难度档 / Boss 档位 + 强度计算。"""

    def __init__(self, entry):
        if not isinstance(entry, dict) or "id" not in entry:
            raise ValueError("地区条目必须包含 id")
        self.id = entry["id"]
        self.name = entry.get("name", self.id)
        self.difficulty = int(entry.get("difficulty", 1))

        boss = entry.get("boss")
        if isinstance(boss, dict):
            self.boss_id = boss.get("id", f"{self.id}_boss")
            self.boss_name = boss.get("name", f"{self.name}·Boss")
            self.boss_tier = int(boss.get("tier", self.difficulty))
        else:
            self.boss_id = f"{self.id}_boss"
            self.boss_name = f"{self.name}·Boss"
            self.boss_tier = self.difficulty

    def mob_power(self, balance):
        return curve_value(balance, "region_difficulty", self.difficulty)

    def boss_power(self, balance):
        return curve_value(balance, "boss_power", self.boss_tier)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "difficulty": self.difficulty,
            "boss": {"id": self.boss_id, "name": self.boss_name, "tier": self.boss_tier},
        }
