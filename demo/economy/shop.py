"""钱币商城（M4）。

商城目录来自 balance.json 的 ``economy.shop``：
- ``common_skill_point``：购买通用技能点（价格沿 ``common_skill_cost`` 曲线增长）。
- ``equipment``：基础装备（引用 content.json 的装备 id）。

所有价格乘温和通胀系数，与后期产出同步放大。
"""
from __future__ import annotations

from ._config import as_number
from .resources import Ledger, ensure_ledger, ensure_economy_state


class Shop:
    def __init__(self, eco_cfg: dict, catalog, inflation_fn=None):
        self.eco = eco_cfg or {}
        self.catalog = catalog
        self.inflation_fn = inflation_fn or (lambda state: 1.0)

    def items(self) -> list:
        return self.eco.get("shop", {}).get("items", []) or []

    def get_item(self, item_id) -> dict:
        for item in self.items():
            if item.get("id") == item_id:
                return item
        return None

    def price(self, item_def: dict, state) -> dict:
        """Cost dict ``{resource: amount}`` for one unit of ``item_def``."""
        price = dict(item_def.get("price") or {})
        curve = item_def.get("price_curve")
        if curve == "common_skill_cost":
            ensure_economy_state(state)
            points = int(state["economy"]["common_skill"].get("points", 0) or 0)
            base = as_number(self.eco.get("common_skill_cost", {}).get("base"), 0.0)
            rate = as_number(self.eco.get("common_skill_cost", {}).get("rate"), 1.0)
            price["qian"] = base * (rate ** points)
        infl = self.inflation_fn(state)
        return {key: value * infl for key, value in price.items() if value}

    def buy(self, state, item_id):
        """Purchase one unit.  Returns ``(ok, detail)``."""
        item_def = self.get_item(item_id)
        if item_def is None:
            return False, "unknown_item"
        ensure_economy_state(state)
        ledger = ensure_ledger(state)
        cost = self.price(item_def, state)
        if not ledger.spend(cost):
            return False, "insufficient_funds"

        kind = item_def.get("kind")
        if kind == "common_skill_point":
            cs = state["economy"]["common_skill"]
            cs["points"] = int(cs.get("points", 0) or 0) + int(item_def.get("effect", {}).get("points", 1))
        elif kind == "equipment":
            inv = state["economy"]["inventory"]
            inv[item_id] = int(inv.get(item_id, 0) or 0) + 1
        else:
            # Unknown kind: refund and fail.
            for key, value in cost.items():
                ledger.add(key, value)
            return False, "unknown_kind"

        stats = state["economy"]["stats"]
        stats["items_bought"] = int(stats.get("items_bought", 0) or 0) + 1
        return True, item_def.get("name", item_id)
