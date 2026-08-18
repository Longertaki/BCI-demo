"""世界状态 + 时间推进（M3 核心）。

World 维护 WorldState（契约 3.4），tick 推进时间并产出 Event 列表（契约 3.5），
由 M4 结算。鼠标只负责「选关」（choose_region / challenge_boss 传入关卡 id），
战斗自动结算（阵容总战力 >= Boss 强度即获胜）。
"""

from __future__ import annotations

import math
import random

from ._util import clamp, drop_rate_value
from .adventurer import gain_exp, hero_from_content, recompute_power
from .qiyun import qiyun_speed
from .region import Region


class World:
    def __init__(self, balance, content, seed=None):
        self.balance = balance
        self.content = content if isinstance(content, dict) else {}
        self.rng = random.Random(seed)

        self._regions = self._load_regions(self.content)
        if not self._regions:
            raise ValueError("content.json 至少需要一个地区")

        heroes = self.content.get("heroes")
        if not isinstance(heroes, list) or not heroes:
            raise ValueError("content.json 需要 heroes 列表（第一人作为主角）")
        protagonist = self._make_protagonist(heroes)
        recompute_power(protagonist, self.balance, self.content)

        self._protagonist_id = protagonist["id"]
        self._state = {
            "time_s": 0.0,
            "qiyun_speed": 0.0,
            "roster": [protagonist],
            "all_heroes": [protagonist],
            "ledger": {"qian": 0, "lingshi": 0, "neili": 0, "shengwang": 0},
            "region_id": self._regions[0].id,
            "progress": 0.0,
            "pending_blindboxes": 0,
        }
        self._cleared_regions = set()

    # ---------- 内部工具 ----------

    @staticmethod
    def _make_protagonist(heroes):
        for entry in heroes:
            if isinstance(entry, dict) and entry.get("is_protagonist"):
                return hero_from_content(entry, is_protagonist=True)
        return hero_from_content(heroes[0], is_protagonist=True)

    def _load_regions(self, content):
        regions = []
        seen = set()
        for entry in content.get("regions", []):
            if not isinstance(entry, dict) or "id" not in entry:
                continue
            if entry["id"] in seen:
                continue
            seen.add(entry["id"])
            regions.append(Region(entry))
        regions.sort(key=lambda r: (r.difficulty, r.id))
        return regions

    def _region_by_id(self, region_id):
        for region in self._regions:
            if region.id == region_id:
                return region
        return None

    def _max_cleared_difficulty(self):
        best = 0
        for rid in self._cleared_regions:
            region = self._region_by_id(rid)
            if region is not None and region.difficulty > best:
                best = region.difficulty
        return best

    def _next_region(self, region):
        for candidate in self._regions:
            if candidate.difficulty > region.difficulty:
                return candidate
        return None

    def _recruitable_candidate(self):
        recruited = {h.get("id") for h in self._state["all_heroes"]}
        candidates = [
            e for e in self.content.get("heroes", [])
            if isinstance(e, dict) and e.get("id") not in recruited
        ]
        if not candidates:
            return None
        return self.rng.choice(candidates)

    @staticmethod
    def _poisson(lam, rng):
        if lam <= 0:
            return 0
        if lam > 50:
            return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
        limit = math.exp(-lam)
        k = 0
        p = 1.0
        while True:
            k += 1
            p *= rng.random()
            if p <= limit:
                return k - 1

    # ---------- 查询 ----------

    @property
    def state(self):
        """返回 WorldState dict（活引用，M4 可直接结算 ledger / pending_blindboxes）。"""
        return self._state

    def protagonist(self):
        for hero in self._state["all_heroes"]:
            if hero.get("is_protagonist"):
                return hero
        return self._state["all_heroes"][0]

    def roster_power(self):
        return sum(int(h.get("power", 0)) for h in self._state["roster"])

    def is_region_unlocked(self, region_id):
        region = self._region_by_id(region_id)
        if region is None:
            return False
        return region.difficulty <= self._max_cleared_difficulty() + 1

    def list_regions(self):
        return [
            {**r.to_dict(), "unlocked": self.is_region_unlocked(r.id)}
            for r in self._regions
        ]

    def add_hero(self, hero):
        """把已招募侠客加入门派（M4 结算 recruit 事件时可调用）。"""
        hid = hero.get("id")
        if any(h.get("id") == hid for h in self._state["all_heroes"]):
            return False
        hero.setdefault("is_protagonist", False)
        hero.setdefault("skills", [])
        hero.setdefault("equipment", [])
        hero.setdefault("elixirs", [])
        hero.setdefault("exp", 0)
        recompute_power(hero, self.balance, self.content)
        self._state["all_heroes"].append(hero)
        return True

    # ---------- 契约方法 ----------

    def tick(self, dt_s, activity):
        """推进 dt_s 秒，返回 Event 列表。"""
        dt_s = float(dt_s)
        if dt_s < 0:
            raise ValueError("dt_s 必须 >= 0")
        act = clamp(float(activity), 0.0, 1.0)
        state = self._state
        state["time_s"] += dt_s
        if dt_s <= 0:
            return []

        protagonist = self.protagonist()
        speed = qiyun_speed(self.balance, protagonist, act)
        state["qiyun_speed"] = speed

        events = []

        # 收益 tick（M4 按 dt 结算累计收益）
        events.append({
            "type": "income_tick",
            "data": {"dt_s": dt_s, "activity": act, "qiyun_speed": speed},
        })

        # 修炼：阵容自动练级（每秒经验 = 气运流转速度）
        for hero in state["roster"]:
            gain_exp(hero, speed * dt_s, self.balance, self.content)

        # 闯荡进度：速度 ÷ 当前地区强度
        region = self._region_by_id(state["region_id"])
        if region is not None:
            mob_power = region.mob_power(self.balance)
            step = speed * dt_s if mob_power <= 0 else speed * dt_s / mob_power
            state["progress"] = min(1.0, state["progress"] + step)

        # 盲盒掉落（泊松，无保底）
        blind_rate = drop_rate_value(self.balance, "blindbox")
        blind_count = self._poisson(blind_rate * act * dt_s, self.rng)
        if blind_count > 0:
            events.append({
                "type": "blindbox_drop",
                "data": {"count": blind_count, "source": "idle"},
            })

        # 奇遇
        qi_rate = drop_rate_value(self.balance, "qi_yu")
        if qi_rate > 0 and self.rng.random() < 1.0 - math.exp(-qi_rate * act * dt_s):
            events.append({
                "type": "qi_yu",
                "data": {"region_id": state["region_id"], "qiyun_speed": speed},
            })

        # 招募
        recruit_rate = drop_rate_value(self.balance, "recruit")
        candidate = self._recruitable_candidate()
        if recruit_rate > 0 and candidate is not None and \
                self.rng.random() < 1.0 - math.exp(-recruit_rate * act * dt_s):
            recruit = hero_from_content(candidate)
            recompute_power(recruit, self.balance, self.content)
            events.append({"type": "recruit", "data": {"hero": recruit}})

        return events

    def choose_region(self, region_id):
        """选择闯关地区（鼠标选关 = 传入 region_id）。"""
        region = self._region_by_id(region_id)
        if region is None:
            raise ValueError(f"未知地区: {region_id}")
        if not self.is_region_unlocked(region_id):
            raise ValueError(f"地区未解锁: {region_id}")
        self._state["region_id"] = region_id
        self._state["progress"] = 0.0

    def challenge_boss(self, region_id):
        """挑战地区 Boss，自动战斗结算，返回 Event 列表。"""
        region = self._region_by_id(region_id)
        if region is None:
            raise ValueError(f"未知地区: {region_id}")
        if not self.is_region_unlocked(region_id):
            raise ValueError(f"地区未解锁: {region_id}")
        if self._state["progress"] < 1.0:
            raise ValueError(
                f"地区 {region_id} 尚未探索完成（progress={self._state['progress']:.2f}），无法挑战 Boss"
            )

        roster_power = self.roster_power()
        boss = region.boss_power(self.balance)
        won = roster_power >= boss

        events = [{
            "type": "boss_defeated",
            "data": {
                "region_id": region_id,
                "region_name": region.name,
                "difficulty": region.difficulty,
                "boss_id": region.boss_id,
                "boss_name": region.boss_name,
                "boss_tier": region.boss_tier,
                "won": won,
                "roster_power": roster_power,
                "boss_power": boss,
            },
        }]

        self._state["progress"] = 0.0
        if won:
            self._cleared_regions.add(region_id)
            nxt = self._next_region(region)
            if nxt is not None:
                self._state["region_id"] = nxt.id
        return events

    def set_roster(self, ids):
        """调配阵容：主角固定入队，总人数 ≤ 3。ids 为侠客 id 列表。"""
        ids = list(ids or [])
        by_id = {h["id"]: h for h in self._state["all_heroes"]}
        protagonist = self.protagonist()

        selected = []
        seen = {protagonist["id"]}
        for hid in ids:
            hid = str(hid)
            if hid == protagonist["id"]:
                continue
            if hid not in by_id:
                raise ValueError(f"侠客不在门派中: {hid}")
            if hid in seen:
                continue
            seen.add(hid)
            selected.append(by_id[hid])

        roster = [protagonist] + selected
        if len(roster) > 3:
            raise ValueError("阵容最多 3 人（含主角）")
        self._state["roster"] = roster
