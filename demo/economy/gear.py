"""秘籍 / 装备 / 丹药 与词条数据模型（M4）。

Item ids, names, rarities and bonuses come from ``demo/config/content.json``
(schema filled by E1).  This module only models and indexes static content; it
does not touch the world state.  Equipment percentage affixes are also parsed
here because the content schema assigns their settlement to M4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

# Normalise Chinese affix stat names to English keys for M3/M5 consumption.
AFFIX_STAT_MAP = {
    "攻击": "attack",
    "防御": "defense",
    "身法": "agility",
    "内功": "internal",
    "会心": "crit",
    "气血": "hp",
    "闪避": "dodge",
}


@dataclass
class Manual:
    """秘籍：一次性、可交易，学习即消耗（消耗逻辑在 Economy.learn_skill）。"""
    id: str
    name: str
    rarity: str
    power_bonus: int = 0
    faction: str = "universal"
    desc: str = ""
    tradable: bool = True
    kind: str = "manual"

    def to_dict(self):
        return asdict(self)


@dataclass
class Equipment:
    """装备：品质（quality）+ 特殊词条（affixes）。"""
    id: str
    name: str
    rarity: str
    quality: str = ""
    slot: str = ""
    affixes: list = field(default_factory=list)
    power_bonus: int = 0
    desc: str = ""
    tradable: bool = True
    kind: str = "equipment"

    def to_dict(self):
        return asdict(self)


@dataclass
class Elixir:
    """丹药：一次性消耗品，永久提升强度。"""
    id: str
    name: str
    rarity: str
    power_bonus: int = 0
    effect: str = ""
    desc: str = ""
    tradable: bool = True
    kind: str = "elixir"

    def to_dict(self):
        return asdict(self)


_KIND_CLASSES = {"manual": Manual, "equipment": Equipment, "elixir": Elixir}


def parse_affix(affix: str):
    """Parse an affix string such as ``攻击+30%`` into ``(stat_key, pct)``.

    Returns ``None`` when the string cannot be parsed.
    """
    m = re.match(r"^([\u4e00-\u9fa5A-Za-z]+)\+([0-9.]+)\s*%?$", str(affix).strip())
    if not m:
        return None
    raw_stat, value = m.group(1), float(m.group(2))
    return AFFIX_STAT_MAP.get(raw_stat, raw_stat), value


def sum_affixes(equipment_list) -> dict:
    """Sum percentage affixes of a list of :class:`Equipment` into a stat dict."""
    totals = {}
    for equip in equipment_list or []:
        for affix in equip.affixes or []:
            parsed = parse_affix(affix)
            if parsed is None:
                continue
            stat, pct = parsed
            totals[stat] = totals.get(stat, 0.0) + pct
    return totals


class GearCatalog:
    """Index ``content.json`` items by id and by kind."""

    def __init__(self, content: dict):
        self.manuals = {}
        self.equipment = {}
        self.elixirs = {}
        content = content or {}
        self.content = content
        for raw in content.get("manuals", []) or []:
            item = Manual(
                id=raw["id"],
                name=raw.get("name", raw["id"]),
                rarity=raw.get("rarity", "common"),
                power_bonus=int(raw.get("power_bonus", 0) or 0),
                faction=raw.get("faction", "universal"),
                desc=raw.get("desc", ""),
                tradable=bool(raw.get("tradable", True)),
            )
            self.manuals[item.id] = item
        for raw in content.get("equipment", []) or []:
            item = Equipment(
                id=raw["id"],
                name=raw.get("name", raw["id"]),
                rarity=raw.get("rarity", "common"),
                quality=raw.get("quality", ""),
                slot=raw.get("slot", ""),
                affixes=list(raw.get("affixes", []) or []),
                power_bonus=int(raw.get("power_bonus", 0) or 0),
                desc=raw.get("desc", ""),
                tradable=bool(raw.get("tradable", True)),
            )
            self.equipment[item.id] = item
        for raw in content.get("elixirs", []) or []:
            item = Elixir(
                id=raw["id"],
                name=raw.get("name", raw["id"]),
                rarity=raw.get("rarity", "common"),
                power_bonus=int(raw.get("power_bonus", 0) or 0),
                effect=raw.get("effect", ""),
                desc=raw.get("desc", ""),
                tradable=bool(raw.get("tradable", True)),
            )
            self.elixirs[item.id] = item

    def get(self, item_id):
        for coll in (self.manuals, self.equipment, self.elixirs):
            if item_id in coll:
                return coll[item_id]
        return None

    def kind(self, item_id) -> str:
        item = self.get(item_id)
        return item.kind if item is not None else None

    def items_of_kind(self, kind) -> list:
        if kind == "manual":
            return list(self.manuals.values())
        if kind == "equipment":
            return list(self.equipment.values())
        if kind == "elixir":
            return list(self.elixirs.values())
        return []

    def by_kind_and_rarity(self, kind) -> dict:
        grouped = {}
        for item in self.items_of_kind(kind):
            grouped.setdefault(item.rarity, []).append(item)
        return grouped

    def region_difficulty(self, region_id) -> int:
        """返回地区难度档；未知地区回退 1（掉落强度缩放 n 的下界）。"""
        for region in (self.content.get("regions") or []):
            if isinstance(region, dict) and region.get("id") == region_id:
                try:
                    return int(region.get("difficulty", 1))
                except (TypeError, ValueError):
                    return 1
        return 1
