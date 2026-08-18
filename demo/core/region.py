"""地区 / 关卡 / Boss（M3）。

* 地区难度强度     = balance.curve("region_difficulty", difficulty)
* 关卡 Boss 强度   = balance.curve("boss_power", level_boss.tier)
* 地区 Boss 强度   = balance.curve("boss_power", region_boss.tier)
两者均为 base*rate^n 形式。

结构（content.json regions 段）::

    {
      "id": "r1", "name": "新手村外", "difficulty": 1,
      "levels": [
        {"id": "r1_l1", "name": "村口野径",
         "boss": {"id": "r1_b1", "name": "野狗", "tier": 1}}
      ],
      "region_boss": {"id": "r1_rb", "name": "野猪王", "tier": 2}
    }

兼容旧结构：若 region 只有 ``boss`` 而没有 ``levels``/``region_boss``，
会自动生成一个关卡，并把旧 ``boss`` 作为地区 Boss 回退。
"""

from __future__ import annotations

from ._util import curve_value


def region_power(balance, difficulty):
    """地区怪物强度（base*rate^difficulty）。"""
    return curve_value(balance, "region_difficulty", int(difficulty))


def boss_power(balance, tier):
    """Boss 强度（base*rate^tier）。"""
    return curve_value(balance, "boss_power", int(tier))


class Level:
    """地区内的单个关卡（每关一个关卡 Boss）。"""

    def __init__(self, entry, region_difficulty=1):
        if not isinstance(entry, dict) or "id" not in entry:
            raise ValueError("关卡条目必须包含 id")
        self.id = entry["id"]
        self.name = entry.get("name", self.id)
        # 关卡怪物强度默认继承地区难度；若单独给 difficulty 则覆盖。
        self.difficulty = int(entry.get("difficulty", region_difficulty))

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


class Region:
    """地区封装：id / 名称 / 难度档 / 关卡列表 + 地区 Boss。"""

    def __init__(self, entry):
        if not isinstance(entry, dict) or "id" not in entry:
            raise ValueError("地区条目必须包含 id")
        self.id = entry["id"]
        self.name = entry.get("name", self.id)
        self.difficulty = int(entry.get("difficulty", 1))

        # 地区 Boss：优先 region_boss，其次旧版 boss。
        rb = entry.get("region_boss")
        if not isinstance(rb, dict):
            rb = entry.get("boss")
        if isinstance(rb, dict):
            self.region_boss_id = rb.get("id", f"{self.id}_boss")
            self.region_boss_name = rb.get("name", f"{self.name}·Boss")
            self.region_boss_tier = int(rb.get("tier", self.difficulty))
        else:
            self.region_boss_id = f"{self.id}_boss"
            self.region_boss_name = f"{self.name}·Boss"
            self.region_boss_tier = self.difficulty

        # 关卡列表
        raw_levels = entry.get("levels")
        if isinstance(raw_levels, list) and raw_levels:
            self.levels = [Level(l, self.difficulty) for l in raw_levels]
        else:
            # 旧结构回退：单关卡 + 地区 Boss。
            legacy_boss = entry.get("boss")
            self.levels = [Level(
                {"id": f"{self.id}_l1", "name": f"{self.name}·关卡一",
                 "boss": legacy_boss if isinstance(legacy_boss, dict) else None},
                self.difficulty,
            )]

    def level_by_id(self, level_id):
        for level in self.levels:
            if level.id == level_id:
                return level
        return None

    def mob_power(self, balance):
        return curve_value(balance, "region_difficulty", self.difficulty)

    def region_boss_power(self, balance):
        return curve_value(balance, "boss_power", self.region_boss_tier)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "difficulty": self.difficulty,
            "levels": [level.to_dict() for level in self.levels],
            "region_boss": {
                "id": self.region_boss_id,
                "name": self.region_boss_name,
                "tier": self.region_boss_tier,
            },
        }
