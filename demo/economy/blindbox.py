"""盲盒开箱（M4）。

掉落表来自 balance.json（``drops.rarity`` 稀有度权重 + ``drops.item_types``
物品类型权重），**无保底**：每次开箱独立掷骰，不做软/硬保底累计。

优先走契约接口 ``Balance.drop_table(...)``；若 Balance 对象不可用则直接回退到
balance.json 的 ``drops`` 段。
"""
from __future__ import annotations

import random

from ._config import dget
from .gear import GearCatalog
from .resources import ensure_economy_state

_RARITY_ORDER = ["common", "rare", "epic"]


class BlindBox:
    def __init__(self, balance_cfg: dict, catalog: GearCatalog, balance=None):
        self.balance_cfg = balance_cfg or {}
        self.catalog = catalog
        self.balance = balance  # optional contract Balance object

    # ------------------------------------------------------------------ #
    def _rarity_weights(self) -> dict:
        if self.balance is not None:
            try:
                table = self.balance.drop_table("rarity")
                if table:
                    return table
            except Exception:
                pass
        return dget(self.balance_cfg, "drops.rarity", {"common": 1.0}) or {"common": 1.0}

    def _item_type_weights(self) -> dict:
        if self.balance is not None:
            try:
                table = self.balance.drop_table("item_types")
                if table:
                    return table
            except Exception:
                pass
        return (
            dget(self.balance_cfg, "drops.item_types", {"manual": 0.4, "equipment": 0.4, "elixir": 0.2})
            or {"manual": 0.4, "equipment": 0.4, "elixir": 0.2}
        )

    @staticmethod
    def _weighted_choice(weights: dict):
        items = [k for k, w in (weights or {}).items() if w and float(w) > 0]
        if not items:
            return None
        total = sum(float(weights[k]) for k in items)
        r = random.random() * total
        acc = 0.0
        for k in items:
            acc += float(weights[k])
            if r <= acc:
                return k
        return items[-1]

    # ------------------------------------------------------------------ #
    def _roll_kind(self):
        return self._weighted_choice(self._item_type_weights()) or "equipment"

    def _roll_rarity(self):
        return self._weighted_choice(self._rarity_weights()) or "common"

    def _pick_item(self, kind, rarity):
        grouped = self.catalog.by_kind_and_rarity(kind)
        candidates = grouped.get(rarity)
        if not candidates:
            # Content table missing this rarity: fall back to nearest rarity.
            idx = _RARITY_ORDER.index(rarity) if rarity in _RARITY_ORDER else 0
            for offset in range(1, len(_RARITY_ORDER)):
                for step in (idx - offset, idx + offset):
                    if 0 <= step < len(_RARITY_ORDER) and grouped.get(_RARITY_ORDER[step]):
                        candidates = grouped[_RARITY_ORDER[step]]
                        rarity = _RARITY_ORDER[step]
                        break
                if candidates:
                    break
        if not candidates:
            candidates = self.catalog.items_of_kind(kind)
        if not candidates:
            return None, rarity
        return random.choice(candidates), rarity

    # ------------------------------------------------------------------ #
    def open(self, state) -> dict:
        """Open exactly one box.  Returns a result dict; never auto-opens."""
        if int(state.get("pending_blindboxes", 0) or 0) < 1:
            return {"ok": False, "reason": "no_blindboxes"}
        state["pending_blindboxes"] = int(state.get("pending_blindboxes", 0) or 0) - 1

        kind = self._roll_kind()
        rarity = self._roll_rarity()
        item, final_rarity = self._pick_item(kind, rarity)
        if item is None:
            return {"ok": False, "reason": "empty_drop_table"}

        ensure_economy_state(state)
        inv = state["economy"]["inventory"]
        inv[item.id] = int(inv.get(item.id, 0) or 0) + 1
        stats = state["economy"]["stats"]
        stats["boxes_opened"] = int(stats.get("boxes_opened", 0) or 0) + 1

        return {
            "ok": True,
            "item_id": item.id,
            "kind": item.kind,
            "rarity": final_rarity,
            "item": item.to_dict(),
        }
