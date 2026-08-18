"""累计收益（挂机自动积累）与门派资源账本（M4）。

``Ledger`` 封装世界状态里的资源账本 dict（qian/lingshi/neili/shengwang）。
``ResourceEngine`` 计算被动产出：基础产出 × 温和通胀 × (1 + 基础设施倍率)，
钱币额外乘通用技能倍率；并在入账时记录终身累计收益。
"""
from __future__ import annotations

from ._config import RESOURCE_KEYS, as_number


class Ledger:
    """Thin, in-place wrapper over the world-state ``ledger`` dict."""

    def __init__(self, data=None):
        self.data = data if data is not None else {}
        self.ensure_keys()

    def ensure_keys(self):
        for key in RESOURCE_KEYS:
            self.data.setdefault(key, 0)

    def get(self, key):
        return float(self.data.get(key, 0))

    def add(self, key, amount):
        self.data[key] = self.get(key) + float(amount)
        return self.data[key]

    def sub(self, key, amount):
        if self.get(key) + 1e-9 < float(amount):
            return False
        self.data[key] = self.get(key) - float(amount)
        return True

    def can_afford(self, costs):
        for key, amount in (costs or {}).items():
            if self.get(key) + 1e-9 < float(amount):
                return False
        return True

    def spend(self, costs):
        if not self.can_afford(costs):
            return False
        for key, amount in (costs or {}).items():
            self.data[key] = self.get(key) - float(amount)
        return True

    def as_dict(self):
        return dict(self.data)


def ensure_ledger(state) -> Ledger:
    state.setdefault("ledger", {})
    return Ledger(state["ledger"])


def ensure_economy_state(state) -> dict:
    """Create/repair the ``state["economy"]`` sub-state owned by M4."""
    # Boss 门票：挑战 Boss 消耗，日常闯荡掉落（契约 4.13 / US-67）。
    state.setdefault("tickets", 0)

    eco = state.setdefault("economy", {})
    eco.setdefault("inventory", {})
    eco.setdefault("buildings", {})
    eco.setdefault("common_skill", {"points": 0, "level": 0})
    # 待开盲盒明细（FIFO）：每个盲盒记录其来源 tier 与来源地区难度。
    # ``state["pending_blindboxes"]`` 仍保持 int 计数（契约 3.4），两者同步。
    eco.setdefault("pending_boxes", [])
    eco.setdefault("stats", {
        "earned": {key: 0.0 for key in RESOURCE_KEYS},
        "boxes_opened": 0,
        "items_bought": 0,
        "exchanges": 0,
        "skills_learned": 0,
        "elixirs_used": 0,
        "tickets_earned": 0,
        "tickets_spent": 0,
    })
    eco.setdefault("last_income_time", float(state.get("time_s", 0.0)))
    return eco


class ResourceEngine:
    """Passive income computation.  All tuning values come from balance.json."""

    def __init__(self, eco_cfg: dict):
        self.eco = eco_cfg or {}

    # ------------------------------------------------------------------ #
    # 温和通胀：世界档位每提升一档，产出与物价同乘 inflation 系数
    # ------------------------------------------------------------------ #
    def inflation_factor(self, state) -> float:
        base = as_number(self.eco.get("inflation"), 1.0)
        stage_s = as_number(self.eco.get("inflation_stage_s"), 36000.0)
        if base <= 0 or stage_s <= 0:
            return 1.0
        stage = int(float(state.get("time_s", 0.0)) // stage_s)
        return base ** stage

    # ------------------------------------------------------------------ #
    def common_skill_multipliers(self, state) -> tuple:
        """Return ``(qian_mult, exp_mult)`` from invested common-skill levels."""
        ensure_economy_state(state)
        cs = state["economy"]["common_skill"]
        cfg = self.eco.get("common_skill", {}) or {}
        qian_per = as_number(cfg.get("qian_mult_per_level"), 0.0)
        exp_per = as_number(cfg.get("exp_mult_per_level"), 0.0)
        level = int(cs.get("level", 0) or 0)
        return 1.0 + qian_per * level, 1.0 + exp_per * level

    def building_multipliers(self, state) -> dict:
        """Return per-resource passive multipliers from upgraded buildings."""
        ensure_economy_state(state)
        mults = {key: 0.0 for key in RESOURCE_KEYS}
        levels = state["economy"]["buildings"]
        for building in self.eco.get("buildings", []) or []:
            level = int(levels.get(building.get("id"), 0) or 0)
            resource = building.get("resource")
            if resource in mults:
                mults[resource] += level * as_number(building.get("mult_per_level"), 0.0)
        return mults

    def income_rates(self, state) -> dict:
        """Per-second income for each resource (neili is not passively earned)."""
        ensure_economy_state(state)
        base = self.eco.get("income_per_s", {}) or {}
        infl = self.inflation_factor(state)
        bmult = self.building_multipliers(state)
        qian_mult, _ = self.common_skill_multipliers(state)
        return {
            "qian": as_number(base.get("qian"), 0.0) * infl * (1.0 + bmult["qian"]) * qian_mult,
            "lingshi": as_number(base.get("lingshi"), 0.0) * infl * (1.0 + bmult["lingshi"]),
            "shengwang": as_number(base.get("shengwang"), 0.0) * infl * (1.0 + bmult["shengwang"]),
            "neili": 0.0,
        }

    def tick_income(self, state, dt_s) -> dict:
        """Credit ``dt_s`` seconds of passive income into the ledger."""
        dt_s = max(0.0, float(dt_s or 0.0))
        if dt_s <= 0:
            return {}
        ensure_economy_state(state)
        ledger = ensure_ledger(state)
        rates = self.income_rates(state)
        earned = state["economy"]["stats"]["earned"]
        gained = {}
        for key, rate in rates.items():
            amount = rate * dt_s
            ledger.add(key, amount)
            earned[key] = earned.get(key, 0.0) + amount
            gained[key] = amount
        state["economy"]["last_income_time"] = float(state.get("time_s", 0.0))
        return gained
