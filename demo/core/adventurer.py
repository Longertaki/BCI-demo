"""主角/侠客数据结构与强度计算（M3）。

角色 dict 结构遵循契约 3.3，并扩展两个字段：
* ``exp``     当前累计经验（用于练级）
* ``elixirs`` 已服用丹药 id 列表（丹药加成计入 power）

战力公式（所有数值来自 balance.json / content.json）：
    investment(level) = Σ_{k=1}^{level-1} level_cost(k)
    base_power        = (investment(level) + level_cost(0)) × aptitude
    item_bonus        = Σ skills.power_bonus + Σ equipment.power_bonus + Σ elixirs.power_bonus
    power             = round(base_power + item_bonus)

其中 level_cost(n) = balance.growth("hero_level_cost", n)。
"""

from __future__ import annotations

from ._util import curve_value


def new_hero(id, name, aptitude, faction="zheng", is_protagonist=False, level=1):
    """构造一个角色 dict（初始 skills/equipment/elixirs 为空）。"""
    return {
        "id": id,
        "name": name,
        "is_protagonist": bool(is_protagonist),
        "level": int(level),
        "aptitude": int(aptitude),
        "skills": [],
        "equipment": [],
        "elixirs": [],
        "faction": faction,
        "exp": 0,
        "power": 0,
    }


def hero_from_content(entry, is_protagonist=None):
    """从 content.json 的 heroes 条目构造角色（保留初始等级/技能/装备/丹药）。

    is_protagonist 为 None 时取 entry 的 is_protagonist 字段。
    """
    if is_protagonist is None:
        is_protagonist = bool(entry.get("is_protagonist", False))
    hero = new_hero(
        id=entry["id"],
        name=entry["name"],
        aptitude=int(entry["aptitude"]),
        faction=entry.get("faction", "zheng"),
        is_protagonist=is_protagonist,
        level=int(entry.get("level", 1)),
    )
    hero["skills"] = [str(s) for s in entry.get("skills", [])]
    hero["equipment"] = [str(s) for s in entry.get("equipment", [])]
    hero["elixirs"] = [str(s) for s in entry.get("elixirs", [])]
    return hero


def level_cost(balance, level):
    """从 level 升到 level+1 所需经验（balance.growth("hero_level_cost", level)）。"""
    return curve_value(balance, "hero_level_cost", int(level), method_name="growth")


def total_investment(balance, level):
    """达到 level 所需的累计修炼投入。"""
    total = 0.0
    for k in range(1, int(level)):
        total += level_cost(balance, k)
    return total


def _content_index(content, key):
    if not isinstance(content, dict):
        return {}
    entries = content.get(key, [])
    if not isinstance(entries, list):
        return {}
    return {str(e.get("id")): e for e in entries if isinstance(e, dict) and "id" in e}


def item_bonus(hero, content):
    """技能/装备/丹药带来的直接战力加成（来自 content.json 的 power_bonus）。"""
    manuals = _content_index(content, "manuals")
    equipment = _content_index(content, "equipment")
    elixirs = _content_index(content, "elixirs")
    bonus = 0.0
    for mid in hero.get("skills", []):
        bonus += float(manuals.get(str(mid), {}).get("power_bonus") or 0.0)
    for eid in hero.get("equipment", []):
        bonus += float(equipment.get(str(eid), {}).get("power_bonus") or 0.0)
    for did in hero.get("elixirs", []):
        bonus += float(elixirs.get(str(did), {}).get("power_bonus") or 0.0)
    return bonus


def compute_power(hero, balance, content):
    """派生角色强度（不修改角色）。"""
    level = int(hero.get("level", 1))
    aptitude = int(hero.get("aptitude", 1))
    base_cost = level_cost(balance, 0)
    invest = total_investment(balance, level)
    base_power = (invest + base_cost) * aptitude
    power = base_power + item_bonus(hero, content)
    # 装备词条百分比加成（M4 结算到 hero["affix_bonus"] = {stat: pct}）
    affix = hero.get("affix_bonus") or {}
    total_pct = sum(float(v) for v in affix.values() if isinstance(v, (int, float)))
    if total_pct:
        power *= (1.0 + total_pct / 100.0)
    return int(round(power))


def recompute_power(hero, balance, content):
    """重算并写回 hero["power"]，返回新战力。"""
    hero["power"] = compute_power(hero, balance, content)
    return hero["power"]


def gain_exp(hero, amount, balance, content):
    """给角色加经验并自动升级（练级），返回升了几级。"""
    amount = max(0.0, float(amount))
    if amount <= 0:
        return 0
    hero["exp"] = hero.get("exp", 0) + amount
    levels = 0
    while True:
        cost = level_cost(balance, int(hero["level"]))
        if hero["exp"] < cost:
            break
        hero["exp"] -= cost
        hero["level"] = int(hero["level"]) + 1
        levels += 1
    recompute_power(hero, balance, content)
    return levels
