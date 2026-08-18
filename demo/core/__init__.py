"""M3 核心世界模拟模块。"""

from .adventurer import (
    compute_power,
    gain_exp,
    hero_from_content,
    item_bonus,
    level_cost,
    new_hero,
    recompute_power,
)
from .qiyun import activity_factor, qiyun_speed, skill_multiplier
from .region import Level, Region, boss_power, region_power
from .world import World

__all__ = [
    "World",
    "Region",
    "Level",
    "region_power",
    "boss_power",
    "qiyun_speed",
    "activity_factor",
    "skill_multiplier",
    "new_hero",
    "hero_from_content",
    "level_cost",
    "compute_power",
    "recompute_power",
    "item_bonus",
    "gain_exp",
]
