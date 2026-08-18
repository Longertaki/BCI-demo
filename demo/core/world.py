"""世界状态 + 时间推进（M3 核心）。

World 维护 WorldState（契约 3.4），tick 推进时间并产出 Event 列表（契约 3.5），
由 M4 结算。鼠标只负责「选关」（choose_region / challenge_boss 传入关卡/Boss id），
战斗自动结算（阵容总战力 >= Boss 强度即获胜）。

Boss 结构（见 game-design 4.13 / 7）：
    * 每个地区含多个关卡，每关一个关卡 Boss（挑战需先探索完当前关卡，胜利即通关该关）；
    * 通关地区（清完所有关卡）后才能挑战地区 Boss；
    * 击败地区 Boss 进入下一地区（闯关无等级门槛，按通关上一地区解锁）；
    * 挑战任何 Boss 消耗 1 张门票（world.state["tickets"]，由 tick 随机掉落事件提供，
      core 在挑战前检查、economy 结算时扣减）。
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
        first_region = self._regions[0]
        first_level = first_region.levels[0] if first_region.levels else None

        self._state = {
            "time_s": 0.0,
            "qiyun_speed": 0.0,
            "roster": [protagonist],
            "all_heroes": [protagonist],
            "ledger": {"qian": 0, "lingshi": 0, "neili": 0, "shengwang": 0},
            "region_id": first_region.id,
            "level_id": first_level.id if first_level is not None else None,
            "progress": 0.0,
            "level_progress": 0.0,
            "pending_blindboxes": 0,
            "tickets": 0,
            "cleared_levels": [],
            "cleared_regions": [],
        }
        # 内部源：已通关关卡 / 已通关地区；各地区的当前关卡探索进度（0~1）。
        self._cleared_levels = set()
        self._cleared_regions = set()
        self._level_progress = {}
        self._sync_clears()

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

    def _next_region(self, region):
        try:
            idx = self._regions.index(region)
        except ValueError:
            return None
        return self._regions[idx + 1] if idx + 1 < len(self._regions) else None

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

    # ---------- 关卡/进度辅助 ----------

    def _next_level(self, region):
        """返回 (首个未通关关卡的下标, 关卡)；全部通关时返回 (None, None)。"""
        for idx, level in enumerate(region.levels):
            if level.id not in self._cleared_levels:
                return idx, level
        return None, None

    def _current_level(self, region):
        return self._next_level(region)[1]

    def _level_progress_of(self, region_id):
        return float(self._level_progress.get(region_id, 0.0))

    def _sync_clears(self):
        self._state["cleared_levels"] = sorted(self._cleared_levels)
        self._state["cleared_regions"] = sorted(self._cleared_regions)

    def _sync_progress(self, region):
        cur = self._current_level(region)
        if cur is None:
            self._state["level_id"] = None
            self._state["level_progress"] = 1.0
            self._state["progress"] = 1.0
        else:
            frac = min(1.0, self._level_progress_of(region.id))
            self._state["level_id"] = cur.id
            self._state["level_progress"] = frac
            cleared = sum(1 for l in region.levels if l.id in self._cleared_levels)
            total = len(region.levels)
            self._state["progress"] = min(1.0, (cleared + frac) / total) if total else 0.0

    # ---------- 查询 ----------

    @property
    def state(self):
        """返回 WorldState dict（活引用，M4 可直接结算 ledger / pending_blindboxes / tickets）。"""
        return self._state

    def protagonist(self):
        for hero in self._state["all_heroes"]:
            if hero.get("is_protagonist"):
                return hero
        return self._state["all_heroes"][0]

    def roster_power(self):
        return sum(int(h.get("power", 0)) for h in self._state["roster"])

    def is_region_unlocked(self, region_id):
        """地区按「通关上一地区」解锁，无等级门槛。"""
        region = self._region_by_id(region_id)
        if region is None:
            return False
        try:
            idx = self._regions.index(region)
        except ValueError:
            return False
        if idx == 0:
            return True
        previous = self._regions[idx - 1]
        return previous.id in self._cleared_regions

    def region_boss_available(self, region_id):
        """该地区 Boss 是否可挑战（清完所有关卡）。"""
        region = self._region_by_id(region_id)
        if region is None:
            return False
        return self._next_level(region)[0] is None

    def list_regions(self):
        return [
            {
                **r.to_dict(),
                "unlocked": self.is_region_unlocked(r.id),
                "cleared": r.id in self._cleared_regions,
                "levels_cleared": sum(1 for l in r.levels if l.id in self._cleared_levels),
                "region_boss_available": self.region_boss_available(r.id),
            }
            for r in self._regions
        ]

    def list_levels(self, region_id=None):
        """列出某地区（默认当前地区）的关卡及其通关状态，供 UI/测试使用。"""
        region = self._region_by_id(region_id) if region_id is not None             else self._region_by_id(self._state["region_id"])
        if region is None:
            return []
        cur = self._current_level(region)
        out = []
        for level in region.levels:
            is_current = cur is not None and level.id == cur.id
            out.append({
                **level.to_dict(),
                "cleared": level.id in self._cleared_levels,
                "is_current": is_current,
                "progress": self._level_progress_of(region.id) if is_current else 0.0,
            })
        return out

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

        # 闯荡进度：速度 ÷ 当前地区强度，推进当前关卡
        region = self._region_by_id(state["region_id"])
        if region is not None:
            cur = self._current_level(region)
            if cur is not None:
                mob_power = region.mob_power(self.balance)
                step = speed * dt_s if mob_power <= 0 else speed * dt_s / mob_power
                self._level_progress[region.id] = min(
                    1.0, self._level_progress_of(region.id) + step
                )
            self._sync_progress(region)

        # 盲盒掉落（日常闯荡 → common 盲盒；泊松，无保底）
        blind_rate = drop_rate_value(self.balance, "blindbox")
        blind_count = self._poisson(blind_rate * act * dt_s, self.rng)
        if blind_count > 0:
            events.append({
                "type": "blindbox_drop",
                "data": {
                    "count": blind_count,
                    "source": "idle",
                    "blindbox_tier": "common",
                    "region_id": state["region_id"],
                    "difficulty": region.difficulty if region is not None else 1,
                },
            })

        # Boss 门票掉落（日常闯荡随机掉落；泊松，无保底）
        ticket_rate = drop_rate_value(self.balance, "ticket")
        ticket_count = self._poisson(ticket_rate * act * dt_s, self.rng)
        if ticket_count > 0:
            events.append({"type": "ticket_drop", "data": {"count": ticket_count}})

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
        """选择闯关地区（鼠标选关 = 传入 region_id；无等级门槛，需已解锁）。"""
        region = self._region_by_id(region_id)
        if region is None:
            raise ValueError(f"未知地区: {region_id}")
        if not self.is_region_unlocked(region_id):
            raise ValueError(f"地区未解锁: {region_id}")
        self._state["region_id"] = region_id
        self._level_progress.setdefault(region_id, 0.0)
        self._sync_progress(region)

    def _resolve_boss_target(self, region, boss_id):
        """把挑战请求解析为 ("level"|"region", 关卡或 None)。

        校验前置条件；不满足时抛 ValueError。
        """
        if boss_id is None:
            # 自动选择：先清未通关关卡，全部通关后挑战地区 Boss。
            _, level = self._next_level(region)
            if level is not None:
                frac = self._level_progress_of(region.id)
                if frac < 1.0:
                    raise ValueError(
                        f"关卡 {level.id} 尚未探索完成"
                        f"（progress={frac:.2f}），无法挑战 Boss"
                    )
                return "level", level
            return "region", None

        if boss_id in (region.region_boss_id, "region_boss"):
            if self._next_level(region)[0] is not None:
                raise ValueError(
                    f"地区 {region.id} 尚未通关（还有关卡未清），无法挑战地区 Boss"
                )
            return "region", None

        for idx, level in enumerate(region.levels):
            if boss_id not in (level.id, level.boss_id):
                continue
            if level.id in self._cleared_levels:
                return "level", level  # 已通关关卡 Boss 可重复刷（仍消耗门票）
            next_idx, _ = self._next_level(region)
            if next_idx is not None and idx != next_idx:
                raise ValueError(f"关卡 {level.id} 的前置关卡尚未通关")
            frac = self._level_progress_of(region.id)
            if frac < 1.0:
                raise ValueError(
                    f"关卡 {level.id} 尚未探索完成"
                    f"（progress={frac:.2f}），无法挑战 Boss"
                )
            return "level", level

        raise ValueError(f"未知 Boss: {boss_id}")

    def challenge_boss(self, region_id, boss_id=None):
        """挑战 Boss（自动战斗结算），返回 Event 列表。

        boss_id 为 None 时自动挑战「下一可挑战 Boss」；也可显式传关卡 id / 关卡 Boss id /
        地区 Boss id。挑战前检查门票（无门票抛 ValueError），门票由 economy.apply 结算扣减。
        """
        region = self._region_by_id(region_id)
        if region is None:
            raise ValueError(f"未知地区: {region_id}")
        if not self.is_region_unlocked(region_id):
            raise ValueError(f"地区未解锁: {region_id}")

        kind, level = self._resolve_boss_target(region, boss_id)

        tickets = int(self._state.get("tickets", 0) or 0)
        if tickets < 1:
            raise ValueError("门票不足，无法挑战 Boss（门票由日常闯荡随机掉落）")

        roster_power = self.roster_power()
        if kind == "level":
            boss_power = level.boss_power(self.balance)
            boss_id_out = level.boss_id
            boss_name = level.boss_name
            boss_tier = level.boss_tier
            boss_kind = "level"
            blindbox_tier = "rare"
            level_id = level.id
        else:
            boss_power = region.region_boss_power(self.balance)
            boss_id_out = region.region_boss_id
            boss_name = region.region_boss_name
            boss_tier = region.region_boss_tier
            boss_kind = "region"
            blindbox_tier = "epic"
            level_id = None

        won = roster_power >= boss_power
        events = [{
            "type": "boss_defeated",
            "data": {
                "region_id": region_id,
                "region_name": region.name,
                "difficulty": region.difficulty,
                "region_difficulty": region.difficulty,
                "level_id": level_id,
                "boss_id": boss_id_out,
                "boss_name": boss_name,
                "boss_kind": boss_kind,
                "boss_tier": boss_tier,
                "won": won,
                "roster_power": roster_power,
                "boss_power": boss_power,
                "ticket_cost": 1,
                "blindbox_tier": blindbox_tier,
                "blindbox_source": "level_boss" if boss_kind == "level" else "region_boss",
            },
        }]

        if won:
            if kind == "level":
                if level.id not in self._cleared_levels:
                    self._cleared_levels.add(level.id)
                    self._level_progress[region.id] = 0.0  # 下一关卡从 0 开始探索
            else:
                if region.id not in self._cleared_regions:
                    self._cleared_regions.add(region.id)
                    nxt = self._next_region(region)
                    if nxt is not None:
                        self._state["region_id"] = nxt.id
                        self._level_progress.setdefault(nxt.id, 0.0)
                    else:
                        self._level_progress[region.id] = 1.0
            self._sync_clears()

        cur_region = self._region_by_id(self._state["region_id"])
        if cur_region is not None:
            self._sync_progress(cur_region)
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
