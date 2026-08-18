"""M4 经济系统：累计收益、盲盒开箱、钱币商城、秘籍/装备/丹药。

用法（demo/ 在 sys.path 上时）::

    from economy import Economy
    economy = Economy(balance)            # balance 为契约 M1 的 Balance 实例
    economy.apply(world, events)          # 结算 M3 产出的事件流
    economy.open_blindbox(world)          # 手动开箱（无保底）
    economy.buy(world, "cs_point")        # 钱币商城购买

跨模块协作只依赖契约：接收 ``Balance`` 对象 + 读写 ``world.state``（普通 dict），
从不 import M1/M3/M5 的内部实现。
"""
from __future__ import annotations

import logging
import random
from pathlib import Path

from ._config import RESOURCE_KEYS, as_number, load_json, resolve_config_dir
from .resources import Ledger, ResourceEngine, ensure_ledger, ensure_economy_state
from .gear import GearCatalog, Manual, Equipment, Elixir, parse_affix, sum_affixes, AFFIX_STAT_MAP
from .blindbox import BlindBox
from .shop import Shop

log = logging.getLogger("economy")

__all__ = [
    "Economy",
    "Ledger",
    "GearCatalog",
    "Manual",
    "Equipment",
    "Elixir",
    "parse_affix",
    "sum_affixes",
    "AFFIX_STAT_MAP",
]


class Economy:
    """契约 M4 入口。

    M4 拥有的状态统一放在 ``state["economy"]``（契约级 ``ledger`` 与
    ``pending_blindboxes`` 除外）::

        state["economy"] = {
            "inventory":     {item_id: count},        # 可交易物品库存
            "buildings":     {building_id: level},    # 基础设施等级
            "common_skill":  {"points": int, "level": int},
            "stats":         {"earned": {...}, "boxes_opened": ...},
            "last_income_time": float,
        }

    M4 对侠客 dict 的扩展（供 M3/M5 读取）::

        hero["affix_bonus"] -> {stat_key: total_pct}  已穿戴装备词条结算（M4 负责）
        hero["elixirs"]     -> [elixir_id]            已服用丹药（沿用 M3 结构）
    """

    def __init__(self, balance, config_dir="config"):
        self.balance = balance
        package_dir = Path(__file__).resolve().parent.parent  # demo/
        self.config_dir = resolve_config_dir(config_dir, package_dir)
        self.balance_cfg = load_json(self.config_dir / "balance.json")
        self.content_cfg = load_json(self.config_dir / "content.json")
        self.eco_cfg = self.balance_cfg.get("economy", {}) or {}

        self.catalog = GearCatalog(self.content_cfg)
        self.resources = ResourceEngine(self.eco_cfg)
        self.blindbox = BlindBox(self.balance_cfg, self.catalog, balance=self.balance)
        self.shop = Shop(self.eco_cfg, self.catalog, inflation_fn=self.resources.inflation_factor)

    # ------------------------------------------------------------------ #
    # 内部辅助
    # ------------------------------------------------------------------ #
    @staticmethod
    def _state(world):
        """接受 World 对象（带 ``.state``）或普通 state dict。"""
        if hasattr(world, "state"):
            return world.state
        if isinstance(world, dict):
            return world
        raise TypeError("world must expose .state or be a plain dict")

    def _ensure(self, state):
        ensure_ledger(state)
        ensure_economy_state(state)

    def _ledger(self, state) -> Ledger:
        return ensure_ledger(state)

    def _find_hero(self, state, hero_id):
        for coll in (state.get("roster"), state.get("all_heroes")):
            if not coll:
                continue
            for hero in coll:
                if hero.get("id") == hero_id:
                    return hero
        return None

    def _has_item(self, state, item_id, count=1) -> bool:
        inv = state["economy"]["inventory"]
        return int(inv.get(item_id, 0) or 0) >= count

    def _consume_item(self, state, item_id, count=1) -> bool:
        inv = state["economy"]["inventory"]
        if int(inv.get(item_id, 0) or 0) < count:
            return False
        inv[item_id] = int(inv.get(item_id, 0) or 0) - count
        if inv[item_id] <= 0:
            inv.pop(item_id, None)
        return True

    def _grant(self, state, rewards):
        """把 ``{resource: amount}`` 入账并计入终身统计。"""
        ledger = self._ledger(state)
        earned = state["economy"]["stats"]["earned"]
        for key, amount in (rewards or {}).items():
            if key in RESOURCE_KEYS:
                ledger.add(key, float(amount))
                earned[key] = earned.get(key, 0.0) + float(amount)

    # ------------------------------------------------------------------ #
    # 契约 API
    # ------------------------------------------------------------------ #
    def apply(self, world, events) -> None:
        """结算 M3 产出的事件流。

        income_tick / ticket_drop / blindbox_drop / qi_yu / recruit / boss_defeated。
        boss_defeated 结算时扣减门票（world.state["tickets"]）并按其来源 tier 发放盲盒。
        """
        state = self._state(world)
        self._ensure(state)
        for event in events or []:
            if isinstance(event, dict):
                etype, data = event.get("type"), event.get("data", {})
            else:
                etype, data = getattr(event, "type", None), getattr(event, "data", {})
            self._apply_event(world, state, etype, data or {})

    def _apply_event(self, world, state, etype, data):
        if etype == "income_tick":
            dt_s = data.get("dt_s")
            if dt_s is None:
                last = float(state["economy"].get("last_income_time", state.get("time_s", 0.0)))
                dt_s = max(0.0, float(state.get("time_s", 0.0)) - last)
            self.resources.tick_income(state, dt_s)

        elif etype == "ticket_drop":
            count = max(0, int(data.get("count", 0) or 0))
            if count:
                state["tickets"] = int(state.get("tickets", 0) or 0) + count
                stats = state["economy"]["stats"]
                stats["tickets_earned"] = int(stats.get("tickets_earned", 0) or 0) + count

        elif etype == "blindbox_drop":
            count = max(0, int(data.get("count", data.get("blindboxes", 1)) or 0))
            tier = data.get("blindbox_tier") or data.get("tier") or "common"
            difficulty = data.get("difficulty") or data.get("region_difficulty")
            region_id = data.get("region_id")
            if difficulty is None:
                difficulty = self.catalog.region_difficulty(region_id or state.get("region_id"))
            self._add_pending_boxes(state, count, tier, int(difficulty or 1), region_id)

        elif etype == "qi_yu":
            rewards = data.get("rewards") or data.get("resources") or {}
            self._grant(state, rewards)
            boxes = int(data.get("blindboxes", data.get("pending_blindboxes", 0)) or 0)
            if boxes:
                tier = data.get("blindbox_tier") or "common"
                difficulty = data.get("difficulty") or data.get("region_difficulty")
                region_id = data.get("region_id")
                if difficulty is None:
                    difficulty = self.catalog.region_difficulty(region_id or state.get("region_id"))
                self._add_pending_boxes(state, boxes, tier, int(difficulty or 1), region_id)
            if not rewards and not boxes:
                self._grant_qi_yu_default(state)

        elif etype == "boss_defeated":
            self._apply_boss_event(state, data)

        elif etype == "recruit":
            hero = data.get("hero")
            if hero and hasattr(world, "add_hero"):
                world.add_hero(hero)  # M3 契约：招募侠客由 World.add_hero 入派
            elif hero:
                state.setdefault("all_heroes", []).append(hero)

        # 未知事件类型忽略，保证向前兼容

    def _add_pending_boxes(self, state, count, tier, difficulty, region_id=None):
        """累计待开盲盒计数，并按来源 tier/地区难度登记明细（FIFO）。"""
        count = max(0, int(count or 0))
        if count <= 0:
            return
        state["pending_blindboxes"] = int(state.get("pending_blindboxes", 0) or 0) + count
        boxes = state["economy"].setdefault("pending_boxes", [])
        difficulty = max(1, int(difficulty or 1))
        for _ in range(count):
            boxes.append({
                "tier": tier or "common",
                "difficulty": difficulty,
                "region_id": region_id,
            })

    def _apply_boss_event(self, state, data):
        # 1) 门票结算：挑战即消耗（无论胜负）；core 已在挑战前检查门票数量。
        ticket_cost = max(0, int(data.get("ticket_cost", 0) or 0))
        if ticket_cost:
            state["tickets"] = max(0, int(state.get("tickets", 0) or 0) - ticket_cost)
            stats = state["economy"]["stats"]
            stats["tickets_spent"] = int(stats.get("tickets_spent", 0) or 0) + ticket_cost

        # 2) 显式资源奖励照发（兼容旧事件）；胜利且未显式给奖励时发默认 boss_reward。
        rewards = data.get("rewards") or data.get("resources") or {}
        self._grant(state, rewards)

        boxes = int(data.get("blindboxes") or data.get("pending_blindboxes") or 0)
        tier = data.get("blindbox_tier") or (
            "epic" if data.get("boss_kind") == "region" else "rare"
        )
        difficulty = max(1, int(data.get("difficulty") or data.get("region_difficulty") or 1))
        won = bool(data.get("won", True))

        if won and not rewards and not boxes:
            rewards = self._boss_rewards(difficulty)
            self._grant(state, rewards)
            boxes = self._boss_blindbox_count(difficulty)

        if won and boxes:
            self._add_pending_boxes(state, boxes, tier, difficulty, data.get("region_id"))

    def _boss_rewards(self, difficulty):
        """Boss 胜利默认资源奖励（qian/lingshi/shengwang），数值来自 balance.json。"""
        cfg = self.eco_cfg.get("boss_reward", {}) or {}
        difficulty = max(1, int(difficulty or 1))
        return {
            "qian": as_number(cfg.get("qian_base"), 0.0)
                    + as_number(cfg.get("qian_per_difficulty"), 0.0) * (difficulty - 1),
            "lingshi": as_number(cfg.get("lingshi_per_difficulty"), 0.0) * difficulty,
            "shengwang": as_number(cfg.get("shengwang_per_difficulty"), 0.0) * difficulty,
        }

    def _boss_blindbox_count(self, difficulty):
        """Boss 胜利默认盲盒数量（blindboxes_per_difficulty × difficulty）。"""
        cfg = self.eco_cfg.get("boss_reward", {}) or {}
        difficulty = max(1, int(difficulty or 1))
        return max(0, int(as_number(cfg.get("blindboxes_per_difficulty"), 0.0) * difficulty))

    def _grant_qi_yu_default(self, state):
        cfg = self.eco_cfg.get("qi_yu", {}) or {}

        def rnd(key):
            return random.uniform(as_number(cfg.get(f"{key}_min"), 0.0),
                                  as_number(cfg.get(f"{key}_max"), 0.0))

        rewards = {}
        if cfg.get("qian_min") is not None:
            rewards["qian"] = rnd("qian")
        if cfg.get("lingshi_min") is not None:
            rewards["lingshi"] = rnd("lingshi")
        if rewards:
            self._grant(state, rewards)

    def open_blindbox(self, world) -> dict:
        """手动开一个盲盒（无保底）。返回 ``{"ok": bool, ...}``。"""
        state = self._state(world)
        self._ensure(state)
        result = self.blindbox.open(state)
        if result.get("ok"):
            item = result.get("item") or {}
            log.info("盲盒开出 %s(%s) [%s]", item.get("name"), result.get("kind"), result.get("rarity"))
        return result

    def buy(self, world, item_id) -> bool:
        """钱币商城购买（通用技能点 / 基础装备）。"""
        state = self._state(world)
        self._ensure(state)
        ok, detail = self.shop.buy(state, item_id)
        if ok:
            log.info("购买成功: %s", detail)
        else:
            log.info("购买失败(%s): %s", item_id, detail)
        return ok

    def _recompute(self, hero) -> None:
        """装备/学习/服丹后刷新派生战力（M3 的 recompute_power）。"""
        from core import recompute_power
        recompute_power(hero, self.balance_cfg, self.content_cfg)

    def learn_skill(self, world, hero_id, manual_id) -> bool:
        """秘籍学习：一次性、学习即消耗（从库存移除，加入侠客 skills）。"""
        state = self._state(world)
        self._ensure(state)
        hero = self._find_hero(state, hero_id)
        if hero is None:
            return False
        item = self.catalog.get(manual_id)
        if item is None or item.kind != "manual":
            return False
        if manual_id in (hero.get("skills") or []):
            return False  # 已学
        # 正魔限制：正道只学正派/通用，魔道只学魔道/通用
        hero_faction = hero.get("faction", "universal")
        item_faction = getattr(item, "faction", "universal") or "universal"
        if item_faction != "universal" and item_faction != hero_faction:
            return False
        if not self._consume_item(state, manual_id):
            return False
        hero.setdefault("skills", []).append(manual_id)
        self._recompute(hero)
        state["economy"]["stats"]["skills_learned"] = int(state["economy"]["stats"].get("skills_learned", 0) or 0) + 1
        return True

    def equip(self, world, hero_id, gear_id) -> bool:
        """装备穿戴：从库存移除，加入侠客 equipment，并结算百分比词条。"""
        state = self._state(world)
        self._ensure(state)
        hero = self._find_hero(state, hero_id)
        if hero is None:
            return False
        item = self.catalog.get(gear_id)
        if item is None or item.kind != "equipment":
            return False
        if gear_id in (hero.get("equipment") or []):
            return False  # 已穿戴
        # 部位唯一：同一 slot 只能穿戴一件
        slot = getattr(item, "slot", "") or ""
        if slot:
            worn = [getattr(self.catalog.get(gid), "slot", "") or "" for gid in (hero.get("equipment") or [])]
            if slot in worn:
                return False
        if not self._consume_item(state, gear_id):
            return False
        hero.setdefault("equipment", []).append(gear_id)
        # M4 结算装备百分比词条（content 文档指定由 M4 结算）
        equipped = [self.catalog.get(gid) for gid in hero["equipment"]]
        equipped = [e for e in equipped if e is not None]
        hero["affix_bonus"] = sum_affixes(equipped)
        self._recompute(hero)
        return True

    def use_elixir(self, world, hero_id, elixir_id) -> bool:
        """服用丹药（M4 扩展）：一次性消耗，永久提升强度。

        写入 ``hero["elixirs"]``（沿用 M3 的角色结构，M3 计算 power 时读取）。
        """
        state = self._state(world)
        self._ensure(state)
        hero = self._find_hero(state, hero_id)
        if hero is None:
            return False
        item = self.catalog.get(elixir_id)
        if item is None or item.kind != "elixir":
            return False
        if not self._consume_item(state, elixir_id):
            return False
        hero.setdefault("elixirs", []).append(elixir_id)
        self._recompute(hero)
        state["economy"]["stats"]["elixirs_used"] = int(state["economy"]["stats"].get("elixirs_used", 0) or 0) + 1
        return True

    def sell_item(self, world, item_id, count=1) -> bool:
        """出售可交易物品（M4 扩展）：按稀有度价值 × sell_ratio 换钱币。"""
        state = self._state(world)
        self._ensure(state)
        count = max(1, int(count))
        item = self.catalog.get(item_id)
        if item is None:
            return False
        if not getattr(item, "tradable", True):
            return False
        if not self._has_item(state, item_id, count):
            return False
        rarity_value = self.eco_cfg.get("rarity_value", {}) or {}
        value = as_number(rarity_value.get(item.rarity), 0.0)
        sell_ratio = as_number(self.eco_cfg.get("sell_ratio"), 0.5)
        if value <= 0:
            return False
        if not self._consume_item(state, item_id, count):
            return False
        self._ledger(state).add("qian", value * sell_ratio * count)
        return True

    def upgrade_passive(self, world, building_id) -> bool:
        """升级基础设施被动收益：成本沿 passive_cost 曲线，乘温和通胀。"""
        state = self._state(world)
        self._ensure(state)
        building = next(
            (b for b in (self.eco_cfg.get("buildings", []) or []) if b.get("id") == building_id), None
        )
        if building is None:
            return False
        level = int(state["economy"]["buildings"].get(building_id, 0) or 0)
        base = as_number(self.eco_cfg.get("passive_cost", {}).get("base"), 0.0)
        rate = as_number(self.eco_cfg.get("passive_cost", {}).get("rate"), 1.0)
        cost = base * (rate ** level) * self.resources.inflation_factor(state)
        if not self._ledger(state).sub("qian", cost):
            return False
        state["economy"]["buildings"][building_id] = level + 1
        return True

    def exchange(self, world, amount, direction) -> bool:
        """灵石↔钱币兑换，比例来自 balance.json。

        - ``"lingshi_to_qian"``：花 ``amount`` 灵石 → 得 ``amount × lingshi_to_qian`` 钱币
        - ``"qian_to_lingshi"``：花 ``amount`` 钱币 → 得 ``floor(amount / qian_to_lingshi)`` 灵石
        """
        state = self._state(world)
        self._ensure(state)
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            return False
        if amount <= 0:
            return False
        rates = self.eco_cfg.get("exchange", {}) or {}
        ledger = self._ledger(state)

        if direction in ("lingshi_to_qian", "lingshi->qian", "l2q"):
            rate = as_number(rates.get("lingshi_to_qian"), 0.0)
            if not ledger.sub("lingshi", amount):
                return False
            ledger.add("qian", amount * rate)
            state["economy"]["stats"]["exchanges"] = int(state["economy"]["stats"].get("exchanges", 0) or 0) + 1
            return True

        if direction in ("qian_to_lingshi", "qian->lingshi", "q2l"):
            rate = as_number(rates.get("qian_to_lingshi"), 0.0)
            if rate <= 0:
                return False
            if not ledger.sub("qian", amount):
                return False
            ledger.add("lingshi", int(amount // rate))
            state["economy"]["stats"]["exchanges"] = int(state["economy"]["stats"].get("exchanges", 0) or 0) + 1
            return True

        return False

    def upgrade_common_skill(self, world) -> bool:
        """投入 1 点通用技能点，提升钱币/经验获取倍率（倍率参数来自 balance.json）。"""
        state = self._state(world)
        self._ensure(state)
        cs = state["economy"]["common_skill"]
        if int(cs.get("points", 0) or 0) < 1:
            return False
        cs["points"] = int(cs.get("points", 0) or 0) - 1
        cs["level"] = int(cs.get("level", 0) or 0) + 1
        return True

    # ------------------------------------------------------------------ #
    # 只读查询（供 M3/M5 读取，不改动契约 API）
    # ------------------------------------------------------------------ #
    def buildings(self) -> list:
        """基础设施目录（供 M5 菜单展示），直接来自 balance.json。"""
        return list(self.eco_cfg.get("buildings", []) or [])

    def shop_items(self) -> list:
        """钱币商城目录（供 M5 菜单展示），直接来自 balance.json。"""
        return list(self.eco_cfg.get("shop", {}).get("items", []) or [])

    def get_income_rates(self, world) -> dict:
        state = self._state(world)
        self._ensure(state)
        return self.resources.income_rates(state)

    def get_multipliers(self, world) -> dict:
        state = self._state(world)
        self._ensure(state)
        qian_mult, exp_mult = self.resources.common_skill_multipliers(state)
        return {
            "qian": qian_mult,
            "exp": exp_mult,
            "buildings": self.resources.building_multipliers(state),
            "inflation": self.resources.inflation_factor(state),
        }

    def get_exp_multiplier(self, world) -> float:
        state = self._state(world)
        self._ensure(state)
        _, exp_mult = self.resources.common_skill_multipliers(state)
        return exp_mult
