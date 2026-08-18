"""盲盒开箱（M4）。

盲盒稀有度按来源决定（非统一概率池，见 game-design §7）：
    * 日常闯荡掉落 → common 盲盒
    * 关卡 Boss     → rare   盲盒
    * 地区 Boss     → epic   盲盒

每个待开盲盒记录其 ``tier``（来源稀有度）与 ``difficulty``（来源地区难度）。
开箱时：物品类型来自 ``drops.item_types``；物品稀有度在 tier 对应的
``drops.blindbox_tiers`` 权重内掷骰（可向下小幅波动，但不跨到更高稀有度）；
物品强度按 ``growth.item_power_scale`` 缩放（item_power = base × base*rate**difficulty）。

掉落表来自 balance.json，**无保底**：每次开箱独立掷骰，不做软/硬保底累计。
"""
from __future__ import annotations

import random

from ._config import dget
from .gear import GearCatalog
from .resources import ensure_economy_state

_RARITY_ORDER = ["common", "rare", "epic"]

_DEFAULT_TIER_WEIGHTS = {
    "common": {"common": 1.0},
    "rare": {"common": 0.15, "rare": 0.85},
    "epic": {"common": 0.05, "rare": 0.20, "epic": 0.75},
}


class BlindBox:
    def __init__(self, balance_cfg: dict, catalog: GearCatalog, balance=None):
        self.balance_cfg = balance_cfg or {}
        self.catalog = catalog
        self.balance = balance  # optional contract Balance object

    # ------------------------------------------------------------------ #
    def _item_type_weights(self) -> dict:
        if self.balance is not None:
            try:
                table = self.balance.drop_table("item_types")
                if table:
                    return table
            except Exception:
                pass
        return (
            dget(self.balance_cfg, "drops.item_types",
                 {"manual": 0.4, "equipment": 0.4, "elixir": 0.2})
            or {"manual": 0.4, "equipment": 0.4, "elixir": 0.2}
        )

    def _tier_weights(self, tier) -> dict:
        """某个盲盒来源 tier 的物品稀有度权重。"""
        table = None
        if self.balance is not None:
            try:
                table = self.balance.drop_table("blindbox_tiers")
            except Exception:
                table = None
        if not table:
            table = dget(self.balance_cfg, "drops.blindbox_tiers", {}) or {}
        weights = (table or {}).get(tier)
        if not weights:
            weights = _DEFAULT_TIER_WEIGHTS.get(tier) or {tier: 1.0}
        return dict(weights)

    def _item_power_scale(self, difficulty) -> float:
        """掉落物强度缩放系数 = growth.item_power_scale(base*rate**difficulty)。"""
        if self.balance is not None:
            try:
                return float(self.balance.growth("item_power_scale", int(difficulty)))
            except Exception:
                pass
        cfg = dget(self.balance_cfg, "growth.item_power_scale", {}) or {}
        base = float(cfg.get("base", 1.0) or 1.0)
        rate = float(cfg.get("rate", 1.0) or 1.0)
        return base * (rate ** int(difficulty))

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

    def _roll_rarity(self, tier):
        return self._weighted_choice(self._tier_weights(tier)) or "common"

    def _pick_item(self, kind, rarity, tier):
        """按稀有度选物品；缺该稀有度时只向下回退，不跨到比 tier 更高稀有度。"""
        grouped = self.catalog.by_kind_and_rarity(kind)
        candidates = grouped.get(rarity)
        if not candidates:
            idx = _RARITY_ORDER.index(rarity) if rarity in _RARITY_ORDER else 0
            for step in range(idx - 1, -1, -1):
                if grouped.get(_RARITY_ORDER[step]):
                    candidates = grouped[_RARITY_ORDER[step]]
                    rarity = _RARITY_ORDER[step]
                    break

        if not candidates:
            # 最后兜底：该 kind 内稀有度 <= tier 的物品（common 盲盒只开 common）。
            cap_idx = _RARITY_ORDER.index(tier) if tier in _RARITY_ORDER else len(_RARITY_ORDER) - 1
            allowed = [
                it for it in self.catalog.items_of_kind(kind)
                if it.rarity in _RARITY_ORDER and _RARITY_ORDER.index(it.rarity) <= cap_idx
            ]
            candidates = allowed or self.catalog.items_of_kind(kind)
            if candidates:
                item = random.choice(candidates)
                return item, item.rarity
        if not candidates:
            return None, rarity
        return random.choice(candidates), rarity

    def _scale_item(self, item, difficulty):
        """返回 (item_dict, scale)：写入按来源地区难度缩放后的实际强度。"""
        base = int(getattr(item, "power_bonus", 0) or 0)
        scale = self._item_power_scale(difficulty)
        scaled = int(round(base * scale))
        data = item.to_dict()
        data["power_bonus"] = scaled
        data["base_power_bonus"] = base
        data["power_scale"] = scale
        data["source_difficulty"] = int(difficulty)
        return data, scale

    # ------------------------------------------------------------------ #
    def open(self, state) -> dict:
        """Open exactly one box.  Returns a result dict; never auto-opens."""
        if int(state.get("pending_blindboxes", 0) or 0) < 1:
            return {"ok": False, "reason": "no_blindboxes"}

        ensure_economy_state(state)
        boxes = state["economy"].setdefault("pending_boxes", [])
        if boxes:
            desc = boxes.pop(0)
        else:
            # 兼容只有 int 计数、没有明细的旧状态：默认 common + 当前地区难度。
            desc = {
                "tier": "common",
                "difficulty": self.catalog.region_difficulty(state.get("region_id")),
                "region_id": state.get("region_id"),
            }
        state["pending_blindboxes"] = int(state.get("pending_blindboxes", 0) or 0) - 1

        tier = desc.get("tier", "common") or "common"
        difficulty = int(desc.get("difficulty", 1) or 1)
        kind = self._roll_kind()
        rarity = self._roll_rarity(tier)
        item, final_rarity = self._pick_item(kind, rarity, tier)
        if item is None:
            return {"ok": False, "reason": "empty_drop_table"}

        inv = state["economy"]["inventory"]
        inv[item.id] = int(inv.get(item.id, 0) or 0) + 1
        stats = state["economy"]["stats"]
        stats["boxes_opened"] = int(stats.get("boxes_opened", 0) or 0) + 1

        item_data, scale = self._scale_item(item, difficulty)
        return {
            "ok": True,
            "item_id": item.id,
            "kind": item.kind,
            "rarity": final_rarity,
            "blindbox_tier": tier,
            "source_difficulty": difficulty,
            "source_region_id": desc.get("region_id"),
            "item": item_data,
        }
