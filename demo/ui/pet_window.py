# -*- coding: utf-8 -*-
"""
M5 · 桌宠 UI（袖里乾坤 demo）

契约见 docs/demo-contract.md §4.5（M5 ui）：

    class PetWindow:
        def __init__(self, world, economy)
        def run(self) -> None   # 主循环：显示状态 + 鼠标菜单

设计定位（demo-agent-plan / game-design 原则 7）：
    「桌宠陪伴、而非仪表盘」——小窗、含蓄状态文案，不铺满数字条。

后端选择（按契约：优先 pygame 可 always-on-top，tkinter 兜底）：
    1. pygame  （若已安装且能初始化显示）
    2. tkinter （标准库兜底，-topmost 置顶）
    3. headless 日志（无显示环境时自动降级，永不崩溃）

本模块只通过「契约接口 + 文件」协作：
    - 调用 world/economy 的契约方法（set_roster / open_blindbox / buy / exchange ...）
    - 商城/地区/建筑目录优先取自 world/economy 暴露的数据，
      否则回退读取 config/content.json 与 config/balance.json。
    - 不 import 其他执行者模块的内部实现。

用法：
    from ui.pet_window import PetWindow

    # GUI（桌宠窗口，右键弹出菜单）
    PetWindow(world, economy, activity_fn=signal.get_activity).run()

    # headless（main.py --headless --sim 调用，只打印日志）
    PetWindow(world, economy, headless=True,
              activity_fn=signal.get_activity).run()

    # headless 冒烟：挂机 → 掉落盲盒 → 自动开箱 → 升级 全循环演示
    PetWindow(world, economy, headless=True, activity_fn=signal.get_activity,
              auto_demo=True, max_steps=60).run()
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# 颜色（桌宠暗色主题，含蓄不刺眼）
# ---------------------------------------------------------------------------
_COLOR_BG = "#17171f"
_COLOR_CARD = "#212130"
_COLOR_BORDER = "#34344a"
_COLOR_TITLE = "#e8e6f0"
_COLOR_TEXT = "#c9c7d6"
_COLOR_SUBTLE = "#8f8da0"
_COLOR_ACCENT = "#e6b95c"
_COLOR_OK = "#7fe0a0"
_COLOR_ERR = "#e08a8a"

_LEDGER_LABELS = {"qian": "钱", "lingshi": "灵石", "neili": "内力", "shengwang": "声望"}
_LEDGER_ORDER = ("qian", "lingshi", "neili", "shengwang")

# 中文字体候选（UI 展示用，非数值）
_CJK_FONTS = [
    "Noto Sans CJK SC", "Noto Sans SC", "WenQuanYi Micro Hei",
    "WenQuanYi Zen Hei", "Microsoft YaHei", "PingFang SC",
    "Source Han Sans SC", "SimHei", "AR PL UMing CN", "DejaVu Sans",
]


def _rgb(hexstr: str):
    """hex 颜色 -> (r, g, b)，供 pygame 使用。"""
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _safe_call(obj, name: str, *args, default=None):
    """防御式调用契约方法，返回 (ok, result|错误信息)。"""
    fn = getattr(obj, name, None) if obj is not None else None
    if fn is None or not callable(fn):
        return False, f"接口缺失：{name}"
    try:
        return True, fn(*args)
    except Exception as exc:  # noqa: BLE001 - 契约方法签名/内部实现未知，需兜底
        return False, f"{name} 调用失败：{exc}"


class _BackendUnavailable(RuntimeError):
    """图形后端初始化失败（无显示环境等）时抛出，用于降级。"""


class PetWindow:
    """桌宠窗口：世界状态含蓄展示 + 鼠标菜单操作。"""

    def __init__(
        self,
        world,
        economy,
        *,
        headless: bool = False,
        activity_fn: Optional[Callable[[], float]] = None,
        tick_dt: float = 1.0,
        speed: float = 1.0,
        config_dir: Optional[str] = None,
        bracelet_connected: bool = False,
        log_interval: float = 5.0,
        max_steps: Optional[int] = None,
        auto_demo: bool = False,
        title: str = "袖里乾坤",
    ):
        self.world = world
        self.economy = economy
        self.headless = headless
        self.activity_fn = activity_fn
        self.tick_dt = max(float(tick_dt), 1e-3)
        self.speed = max(float(speed), 0.0)
        self.bracelet_connected = bracelet_connected
        self.log_interval = max(float(log_interval), self.tick_dt)
        self.max_steps = max_steps
        self.auto_demo = auto_demo
        self.title = title

        if config_dir is None:
            config_dir = Path(__file__).resolve().parent.parent / "config"
        self.config_dir = Path(config_dir)

        self._log_steps = max(1, int(round(self.log_interval / self.tick_dt)))

        # 懒加载缓存
        self._content_cache = None
        self._balance_cache = None

        # 图形后端运行时状态
        self._tk_root = None
        self._tk_cv = None
        self._tk_font_family = None
        self._tk_message = None
        self._tk_message_ts = 0.0
        self._tk_menu = None

        self._pg_running = False
        self._pg_menu = None
        self._pg_menu_pos = (0, 0)
        self._pg_message = None
        self._pg_message_ts = 0.0

    # ------------------------------------------------------------------
    # 配置（文件协作）
    # ------------------------------------------------------------------
    def _content(self) -> dict:
        if self._content_cache is None:
            self._content_cache = _read_json(self.config_dir / "content.json")
        return self._content_cache

    def _balance(self) -> dict:
        if self._balance_cache is None:
            self._balance_cache = _read_json(self.config_dir / "balance.json")
        return self._balance_cache

    # ------------------------------------------------------------------
    # 契约状态读取（WorldState dict，兼容 property / get_state()）
    # ------------------------------------------------------------------
    def _state(self) -> dict:
        st = getattr(self.world, "state", None)
        if callable(st):
            try:
                st = st()
            except Exception:
                st = None
        if not isinstance(st, dict):
            fn = getattr(self.world, "get_state", None)
            if callable(fn):
                try:
                    st = fn()
                except Exception:
                    st = None
        return st if isinstance(st, dict) else {}

    def _ledger(self) -> dict:
        st = self._state()
        led = st.get("ledger")
        if not isinstance(led, dict):
            led = getattr(self.world, "ledger", None)
        if not isinstance(led, dict):
            led = {}
        return {k: led.get(k, 0) for k in _LEDGER_ORDER}

    def _roster(self) -> list:
        st = self._state()
        roster = st.get("roster")
        if not isinstance(roster, list):
            roster = getattr(self.world, "roster", None)
        return [r for r in roster if isinstance(r, dict)] if isinstance(roster, list) else []

    def _all_heroes(self) -> list:
        st = self._state()
        heroes = st.get("all_heroes")
        if not isinstance(heroes, list):
            heroes = getattr(self.world, "heroes", None) or getattr(self.world, "all_heroes", None)
        return [h for h in heroes if isinstance(h, dict)] if isinstance(heroes, list) else []

    def _pending_boxes(self) -> int:
        st = self._state()
        val = st.get("pending_blindboxes", 0)
        if val is None:
            val = getattr(self.world, "pending_blindboxes", 0)
        try:
            return int(val or 0)
        except (TypeError, ValueError):
            return 0

    def _qiyun_speed(self) -> float:
        st = self._state()
        try:
            return float(st.get("qiyun_speed", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _progress(self) -> float:
        st = self._state()
        try:
            return float(st.get("progress", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _region_id(self) -> str:
        st = self._state()
        return str(st.get("region_id") or getattr(self.world, "region_id", "") or "")

    def is_bracelet_connected(self) -> bool:
        """手环连接状态：占位。优先读 world，其次用构造参数。"""
        st = self._state()
        val = st.get("bracelet_connected")
        if val is None:
            val = st.get("band_connected")
        if val is None:
            val = getattr(self.world, "bracelet_connected", None)
        if val is None and callable(getattr(self.world, "is_bracelet_connected", None)):
            try:
                val = self.world.is_bracelet_connected()
            except Exception:
                val = None
        return bool(val) if val is not None else bool(self.bracelet_connected)

    # ------------------------------------------------------------------
    # 目录（world/economy 优先，config 文件兜底）
    # ------------------------------------------------------------------
    @staticmethod
    def _as_id_name(obj) -> dict:
        if isinstance(obj, str):
            return {"id": obj, "name": obj}
        if isinstance(obj, dict):
            return {
                "id": obj.get("id", obj.get("name", "?")),
                "name": obj.get("name", obj.get("id", "?")),
            }
        return {"id": "?", "name": "?"}

    @staticmethod
    def _normalize_items(val) -> list:
        if isinstance(val, dict):
            val = [val]
        if not isinstance(val, (list, tuple)):
            return []
        out = []
        for it in val:
            if isinstance(it, str):
                out.append({"id": it, "name": it})
            elif isinstance(it, dict):
                out.append({
                    "id": it.get("id", it.get("name", "?")),
                    "name": it.get("name", it.get("id", "?")),
                    "kind": it.get("kind", it.get("type", "")),
                    "rarity": it.get("rarity", ""),
                })
        return out

    def _enrich_items(self, items) -> list:
        """用 content.json 的名称/稀有度补齐商城条目的展示信息。"""
        content = self._content()
        lookup = {}
        for key in ("equipment", "manuals", "elixirs"):
            for it in content.get(key) or []:
                if isinstance(it, dict) and it.get("id"):
                    lookup[it["id"]] = it
        for it in items:
            src = lookup.get(it.get("id"))
            if src:
                if not it.get("name") or it.get("name") == it.get("id"):
                    it["name"] = src.get("name", it.get("id"))
                if not it.get("rarity"):
                    it["rarity"] = src.get("rarity", "")
        return items

    def regions(self) -> list:
        """地区目录：[{id, name}]。world 暴露优先，否则读 content.json。"""
        for attr in ("regions", "region_list"):
            val = getattr(self.world, attr, None)
            if isinstance(val, (list, tuple)) and val:
                return [self._as_id_name(r) for r in val]
        fn = getattr(self.world, "list_regions", None)
        if callable(fn):
            try:
                val = fn()
                if isinstance(val, (list, tuple)) and val:
                    return [self._as_id_name(r) for r in val]
            except Exception:
                pass
        regs = self._content().get("regions") or []
        return [self._as_id_name(r) for r in regs if isinstance(r, dict)]

    def shop_items(self) -> list:
        """钱币商城目录：[{id,name,kind,rarity}]。economy.shop 优先，否则 content.json。"""
        shop = getattr(self.economy, "shop", None)
        if shop is not None and callable(getattr(shop, "items", None)):
            try:
                val = shop.items()
                if val:
                    return self._enrich_items(self._normalize_items(val))
            except Exception:
                pass
        for name in ("shop_items", "list_shop", "items", "catalog"):
            fn = getattr(self.economy, name, None)
            if callable(fn):
                try:
                    val = fn()
                    if val:
                        return self._enrich_items(self._normalize_items(val))
                except Exception:
                    pass
            val = getattr(self.economy, name, None)
            if isinstance(val, (list, dict)) and val:
                return self._enrich_items(self._normalize_items(val))
        content = self._content()
        items = []
        for kind, key in (("装备", "equipment"), ("秘籍", "manuals"), ("丹药", "elixirs")):
            for it in content.get(key) or []:
                if isinstance(it, dict):
                    items.append({
                        "id": it.get("id", "?"),
                        "name": it.get("name", it.get("id", "?")),
                        "kind": kind,
                        "rarity": it.get("rarity", ""),
                    })
        if not items:
            items.append({"id": "common_skill", "name": "通用技能", "kind": "技能", "rarity": ""})
        return items

    def buildings(self) -> list:
        """被动收益（基础设施）目录。economy 暴露优先，balance.json 次之，最后占位。"""
        for name in ("buildings", "passives", "list_buildings"):
            fn = getattr(self.economy, name, None)
            if callable(fn):
                try:
                    val = fn()
                    if val:
                        return self._normalize_items(val)
                except Exception:
                    pass
            val = getattr(self.economy, name, None)
            if isinstance(val, (list, dict)) and val:
                return self._normalize_items(val)
        # 真实 M4 数据源：balance.json → economy.buildings
        bal = self._balance()
        val = (bal.get("economy") or {}).get("buildings")
        if isinstance(val, (list, tuple)) and val:
            return self._normalize_items(val)
        content = self._content()
        for key in ("buildings", "passives"):
            val = content.get(key)
            if isinstance(val, (list, tuple)) and val:
                return self._normalize_items(val)
        # 契约未定义建筑目录且 config 无此字段时的占位（E1 补充后自动生效）
        return [{"id": "base", "name": "门派基业", "kind": "被动收益"}]

    def _region_name(self, region_id: str) -> str:
        if not region_id:
            return ""
        for r in self.regions():
            if r.get("id") == region_id:
                return r.get("name", region_id)
        return region_id

    # ------------------------------------------------------------------
    # 含蓄文案（UI 展示用，非数值曲线）
    # ------------------------------------------------------------------
    @staticmethod
    def _fmt_num(v) -> str:
        try:
            v = round(float(v), 4)
        except (TypeError, ValueError):
            return "0"
        if v >= 1e8:
            return f"{v / 1e8:.2f}亿"
        if v >= 1e4:
            return f"{v / 1e4:.1f}万"
        if v == int(v):
            return str(int(v))
        return f"{v:.2f}"

    @staticmethod
    def _qiyun_phrase(speed: float) -> str:
        if speed <= 0:
            return "气运凝滞"
        if speed < 0.6:
            return "气运徐来"
        if speed < 1.6:
            return "气运流转"
        if speed < 3.5:
            return "气运奔涌"
        return "气运如虹"

    @staticmethod
    def _cheng(progress: float) -> str:
        """进度 0~1 -> 汉语成数（含蓄表达，如 0.37 -> 四成）。"""
        try:
            p = float(progress)
        except (TypeError, ValueError):
            p = 0.0
        p = max(0.0, min(1.0, p))
        tenths = int(round(p * 10))
        if tenths <= 0:
            return "初入"
        if tenths >= 10:
            return "圆满"
        return f"{'零一二三四五六七八九'[tenths]}成"

    @staticmethod
    def _time_line(t) -> str:
        try:
            t = int(t)
        except (TypeError, ValueError):
            t = 0
        if t < 60:
            return f"入世 {t} 秒"
        days, rem = divmod(t, 86400)
        hours, rem = divmod(rem, 3600)
        mins = rem // 60
        if days:
            return f"入世 {days} 日 {hours} 时"
        if hours:
            return f"入世 {hours} 时 {mins} 分"
        return f"入世 {mins} 分"

    def _ledger_line(self) -> str:
        led = self._ledger()
        return " · ".join(f"{_LEDGER_LABELS[k]}{self._fmt_num(led.get(k, 0))}" for k in _LEDGER_ORDER)

    def _roster_line(self) -> str:
        roster = self._roster()
        if not roster:
            return "侠客：暂无"
        names = [h.get("name", h.get("id", "?")) for h in roster]
        return "侠客：" + "、".join(names)

    # ------------------------------------------------------------------
    # 快照（GUI 绘制与 headless 日志共用）
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        region_id = self._region_id()
        return {
            "time_s": self._state().get("time_s", 0.0),
            "qiyun_speed": self._qiyun_speed(),
            "region_id": region_id,
            "region_name": self._region_name(region_id),
            "progress": self._progress(),
            "ledger": self._ledger(),
            "pending_blindboxes": self._pending_boxes(),
            "roster": [h.get("name", h.get("id", "?")) for h in self._roster()],
            "roster_count": len(self._roster()),
            "bracelet_connected": self.is_bracelet_connected(),
        }

    def render_snapshot(self) -> str:
        s = self.snapshot()
        led = " ".join(
            f"{_LEDGER_LABELS[k]}{self._fmt_num(s['ledger'].get(k, 0))}" for k in _LEDGER_ORDER
        )
        region = s["region_name"] or s["region_id"] or "未选"
        return (
            f"[袖里乾坤] {self._time_line(s['time_s'])} | 地区:{region} "
            f"进度:{self._cheng(s['progress'])} | 气运:{self._qiyun_phrase(s['qiyun_speed'])} | "
            f"{led} | 盲盒×{s['pending_blindboxes']} | "
            f"阵容({s['roster_count']}):{'、'.join(s['roster']) or '空'} | "
            f"手环:{'已连接' if s['bracelet_connected'] else '未连接'}"
        )

    # ------------------------------------------------------------------
    # 模拟推进（供 run() 驱动 signal → world.tick → economy.apply）
    # ------------------------------------------------------------------
    def step_sim(self) -> list:
        """单步推进模拟。activity_fn 为 None 时不驱动（由 main.py 自行驱动）。"""
        if self.activity_fn is None or self.world is None:
            return []
        try:
            activity = float(self.activity_fn())
        except Exception:
            activity = 1.0
        activity = max(0.0, min(1.0, activity))
        try:
            events = self.world.tick(self.tick_dt * self.speed, activity) or []
        except Exception as exc:  # noqa: BLE001
            print(f"[PetWindow] world.tick 失败：{exc}")
            return []
        if self.economy is not None:
            _safe_call(self.economy, "apply", self.world, events)
        return events

    # ------------------------------------------------------------------
    # 菜单动作（返回 (ok, message)）
    # ------------------------------------------------------------------
    def _describe_item(self, res) -> str:
        if isinstance(res, dict):
            item = res.get("item") or res.get("result") or res.get("reward")
            if item is None:
                item = res
            if isinstance(item, dict):
                name = item.get("name", item.get("id", "?"))
                rarity = item.get("rarity", "")
                kind = item.get("kind", item.get("type", ""))
                txt = str(name)
                if rarity:
                    txt += f"（{rarity}）"
                if kind:
                    txt += f" · {kind}"
                return txt
            return str(item)
        return str(res)

    def _item_label(self, item_id) -> str:
        for it in self.shop_items():
            if it.get("id") == item_id:
                return it.get("name", item_id)
        return str(item_id)

    def _building_label(self, building_id) -> str:
        for it in self.buildings():
            if it.get("id") == building_id:
                return it.get("name", building_id)
        return str(building_id)

    def action_open_blindbox(self):
        if self._pending_boxes() <= 0:
            return False, "暂无待开的盲盒"
        ok, res = _safe_call(self.economy, "open_blindbox", self.world)
        if not ok:
            return False, res
        if isinstance(res, dict) and res.get("ok") is False:
            return False, "开箱失败（无可开盲盒）"
        return True, "开箱成功：" + self._describe_item(res)

    def action_buy(self, item_id):
        ok, res = _safe_call(self.economy, "buy", self.world, item_id)
        if not ok:
            return False, res
        if res is False or res is None:
            return False, "购买失败（钱币不足或商品不存在）"
        return True, f"购买成功：{self._item_label(item_id)}"

    def action_set_roster(self, ids):
        ids = [str(i) for i in (ids or [])]
        if len(ids) > 3:
            return False, "阵容最多 3 人"
        ok, res = _safe_call(self.world, "set_roster", ids)
        if not ok:
            return False, res
        return True, f"阵容已更新（{len(ids)} 人）"

    def _toggle_roster(self, hero_id):
        roster = self._roster()
        current = [h.get("id") for h in roster]
        hero = next((h for h in self._all_heroes() if h.get("id") == hero_id), None)
        if hero is None:
            return False, "侠客不存在"
        if hero_id in current:
            if hero.get("is_protagonist"):
                return False, "主角不可下场"
            new = [i for i in current if i != hero_id]
        else:
            if len(current) >= 3:
                return False, "阵容最多 3 人"
            new = current + [hero_id]
        return self.action_set_roster(new)

    def action_choose_region(self, region_id):
        ok, res = _safe_call(self.world, "choose_region", region_id)
        if not ok:
            return False, res
        return True, f"已前往：{self._region_name(region_id)}"

    def action_challenge_boss(self, region_id):
        ok, res = _safe_call(self.world, "challenge_boss", region_id)
        if not ok:
            return False, res
        # M3 challenge_boss 返回 Event 列表（自动战斗结算），交给 M4 结算奖励
        won = None
        if isinstance(res, (list, tuple)):
            events = list(res)
            for ev in events:
                data = ev.get("data", {}) if isinstance(ev, dict) else {}
                if ev.get("type") == "boss_defeated" and isinstance(data, dict):
                    won = data.get("won")
            if self.economy is not None and events:
                _safe_call(self.economy, "apply", self.world, events)
        name = self._region_name(region_id)
        if won is True:
            return True, f"Boss 挑战胜利：{name}"
        if won is False:
            return True, f"Boss 挑战失败：{name}（阵容强度不足）"
        return True, f"已挑战 Boss：{name}"

    def action_upgrade_common_skill(self):
        if not hasattr(self.economy, "upgrade_common_skill"):
            return False, "经济模块未提供通用技能点接口"
        for args in ((self.world,), (self.world, 1), (1,), ()):
            ok, res = _safe_call(self.economy, "upgrade_common_skill", *args)
            if ok:
                if res is False or res is None:
                    return False, "通用技能点升级失败（资源不足）"
                return True, "通用技能点升级成功"
        return False, "通用技能点升级失败（接口签名不符）"

    def action_upgrade_passive(self, building_id=None):
        if not hasattr(self.economy, "upgrade_passive"):
            return False, "经济模块未提供被动收益接口"
        attempts = []
        if building_id:
            attempts.append((self.world, building_id))
        attempts += [(self.world, "base"), (self.world,), (self.world, "passive"), ("base",)]
        for args in attempts:
            ok, res = _safe_call(self.economy, "upgrade_passive", *args)
            if ok:
                if res is False or res is None:
                    return False, "被动收益升级失败（资源不足）"
                label = self._building_label(building_id) if building_id else "门派基业"
                return True, f"被动收益升级成功：{label}"
        return False, "被动收益升级失败（接口签名不符）"

    def action_exchange(self, amount, direction):
        ok, res = _safe_call(self.economy, "exchange", self.world, amount, direction)
        if not ok:
            ok, res = _safe_call(self.economy, "exchange", self.world, direction, amount)
        if not ok:
            return False, res
        if res is False or res is None:
            return False, "兑换失败（资源不足）"
        d = str(direction)
        label = "钱币→灵石" if d in ("qian_to_lingshi", "qian->lingshi", "q2l") else "灵石→钱币"
        return True, f"兑换成功：{label} ×{amount}"

    def _exchange_entries(self) -> list:
        """兑换菜单项：(direction, amount, label)。数量参考 balance.json 汇率。"""
        bal = self._balance()
        ex = (bal.get("economy") or {}).get("exchange") or {}
        try:
            q2l_rate = max(1, int(float(ex.get("qian_to_lingshi", 100))))
        except (TypeError, ValueError):
            q2l_rate = 100
        return [
            ("lingshi_to_qian", 1, "灵石→钱币（卖 1 灵石）"),
            ("lingshi_to_qian", 5, "灵石→钱币（卖 5 灵石）"),
            ("lingshi_to_qian", 10, "灵石→钱币（卖 10 灵石）"),
            ("qian_to_lingshi", q2l_rate, "钱币→灵石（买 1 灵石）"),
            ("qian_to_lingshi", q2l_rate * 5, "钱币→灵石（买 5 灵石）"),
            ("qian_to_lingshi", q2l_rate * 10, "钱币→灵石（买 10 灵石）"),
        ]

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def run(self) -> None:
        if self.headless:
            self._headless_run()
            return
        # pygame 优先，tkinter 兜底，最后 headless
        try:
            import pygame  # noqa: F401
            self._run_pygame()
            return
        except ImportError:
            pass
        except _BackendUnavailable as exc:
            print(f"[PetWindow] pygame 不可用：{exc}，尝试 tkinter。")
        try:
            import tkinter  # noqa: F401
            self._run_tkinter()
            return
        except ImportError:
            pass
        except _BackendUnavailable as exc:
            print(f"[PetWindow] tkinter 不可用：{exc}。")
        print("[PetWindow] 无可用图形后端，降级为 headless 日志模式。")
        self._headless_run()

    # ------------------------------------------------------------------
    # headless 模式
    # ------------------------------------------------------------------
    def _headless_run(self) -> None:
        print("[PetWindow] headless 模式启动（无 UI，仅日志）。")
        print(self.render_snapshot())
        steps = 0
        try:
            while True:
                if self.max_steps is not None and steps >= self.max_steps:
                    print("[PetWindow] 达到 max_steps，headless 循环结束。")
                    break
                if self.activity_fn is not None:
                    self.step_sim()
                steps += 1
                if steps % self._log_steps == 0:
                    if self.auto_demo:
                        self._demo_actions()
                    print(self.render_snapshot())
                time.sleep(self.tick_dt)
        except KeyboardInterrupt:
            print("[PetWindow] headless 循环被中断，退出。")

    def _demo_actions(self) -> None:
        """headless 冒烟演示：开箱 → 购买技能点 → 升级通用技能。"""
        for _ in range(3):
            if self._pending_boxes() <= 0:
                break
            ok, msg = self.action_open_blindbox()
            print(f"  [演示·开箱] {msg}")
        ok, msg = self.action_buy("cs_point")
        print(f"  [演示·购点] {msg}")
        ok, msg = self.action_upgrade_common_skill()
        print(f"  [演示·升级] {msg}")

    # ------------------------------------------------------------------
    # pygame 后端
    # ------------------------------------------------------------------
    def _pg_font(self, size: int, bold: bool = False):
        import pygame
        try:
            avail = set(pygame.font.get_fonts())
            family = next((f.lower() for f in _CJK_FONTS if f.lower() in avail), None)
            if family:
                return pygame.font.SysFont(family, size, bold=bold)
        except Exception:
            pass
        return pygame.font.SysFont(None, size, bold=bold)

    def _run_pygame(self) -> None:
        import pygame
        try:
            pygame.init()
            pygame.display.init()
        except Exception as exc:  # noqa: BLE001
            raise _BackendUnavailable(f"pygame.init 失败：{exc}") from exc

        W, H = 260, 200
        try:
            dw, dh = pygame.display.get_desktop_sizes()[0]
        except Exception:
            dw, dh = 1920, 1080
        # 右下角（桌宠常驻角落）
        import os
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{max(0, dw - W - 24)},{max(0, dh - H - 64)}"
        try:
            screen = pygame.display.set_mode((W, H), pygame.NOFRAME)
        except Exception as exc:  # noqa: BLE001
            pygame.quit()
            raise _BackendUnavailable(f"pygame 显示初始化失败：{exc}") from exc
        pygame.display.set_caption(self.title)

        self._pg_running = True
        clock = pygame.time.Clock()
        font = self._pg_font(12)
        try:
            while self._pg_running:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self._pg_running = False
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
                        self._pg_open_menu(event.pos)
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        if self._pg_menu is not None:
                            self._pg_menu_click(event.pos)
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        self._pg_menu = None
                if self.activity_fn is not None:
                    self.step_sim()
                self._pg_draw(screen, font)
                pygame.display.flip()
                clock.tick(max(1.0, 1.0 / self.tick_dt))
        finally:
            pygame.quit()

    # ---- pygame 菜单 ----
    def _pg_entry(self, label, action=None, submenu=None, disabled=False):
        return {"label": label, "action": action, "submenu": submenu, "disabled": disabled}

    def _pg_root_menu(self):
        return _PgMenu(
            "袖里乾坤",
            [
                self._pg_entry("开箱", action=self.action_open_blindbox),
                self._pg_entry("商城", submenu=self._pg_shop_menu()),
                self._pg_entry("阵容", submenu=self._pg_roster_menu()),
                self._pg_entry("选择地区", submenu=self._pg_region_menu(choose=True)),
                self._pg_entry("挑战 Boss", submenu=self._pg_region_menu(choose=False)),
                self._pg_entry("升级", submenu=self._pg_upgrade_menu()),
                self._pg_entry("兑换", submenu=self._pg_exchange_menu()),
                self._pg_entry("退出", action=self._pg_quit),
            ],
            parent=None,
        )

    def _pg_shop_menu(self):
        items = self.shop_items()
        entries = [self._pg_entry(f"{it.get('name', it['id'])}",
                                  action=lambda i=it["id"]: self.action_buy(i)) for it in items]
        return _PgMenu("商城", entries or [self._pg_entry("（无商品）", disabled=True)])

    def _pg_roster_menu(self):
        roster_ids = {h.get("id") for h in self._roster()}
        entries = []
        for h in self._all_heroes():
            hid = h.get("id")
            mark = "✓" if hid in roster_ids else "○"
            entries.append(self._pg_entry(f"{mark} {h.get('name', hid)}",
                                          action=lambda i=hid: self._toggle_roster(i)))
        return _PgMenu("阵容（≤3，主角必上）",
                       entries or [self._pg_entry("（无侠客）", disabled=True)])

    def _pg_region_menu(self, choose: bool):
        entries = []
        for r in self.regions():
            rid = r["id"]
            action = (lambda i=rid: self.action_choose_region(i)) if choose else \
                     (lambda i=rid: self.action_challenge_boss(i))
            entries.append(self._pg_entry(r.get("name", rid), action=action))
        return _PgMenu("选择地区" if choose else "挑战 Boss",
                       entries or [self._pg_entry("（无地区）", disabled=True)])

    def _pg_upgrade_menu(self):
        entries = [self._pg_entry("通用技能点", action=self.action_upgrade_common_skill)]
        for b in self.buildings():
            entries.append(self._pg_entry(b.get("name", b["id"]),
                                          action=lambda i=b["id"]: self.action_upgrade_passive(i)))
        return _PgMenu("升级", entries)

    def _pg_exchange_menu(self):
        entries = [
            self._pg_entry(label, action=lambda a=amount, d=direction: self.action_exchange(a, d))
            for direction, amount, label in self._exchange_entries()
        ]
        return _PgMenu("灵石 ↔ 钱币", entries)

    def _pg_open_menu(self, pos):
        self._pg_menu = self._pg_root_menu()
        self._pg_menu_pos = pos

    def _pg_quit(self):
        self._pg_running = False
        return True, "退出"

    def _pg_menu_click(self, pos):
        m = self._pg_menu
        if m is None:
            return
        x0, y0 = self._pg_menu_pos
        line_h = 20
        idx = (pos[1] - y0 - 26) // line_h
        if idx < 0 or idx >= len(m.entries):
            return
        entry = m.entries[idx]
        if entry.get("disabled"):
            return
        if entry.get("submenu") is not None:
            sub = entry["submenu"]
            sub.parent = m
            self._pg_menu = sub
        elif entry.get("action") is not None:
            self._pg_menu = None
            ok, msg = entry["action"]()
            self._pg_message = (msg, ok, time.time())

    # ---- pygame 绘制 ----
    def _pg_draw(self, screen, font):
        import pygame
        screen.fill(_rgb(_COLOR_BG))
        W, H = screen.get_size()
        pygame.draw.rect(screen, _rgb(_COLOR_CARD), (2, 2, W - 4, H - 4), border_radius=12)
        pygame.draw.rect(screen, _rgb(_COLOR_BORDER), (2, 2, W - 4, H - 4), 2, border_radius=12)

        self._pg_text(screen, font, "袖里乾坤", 12, 8, _rgb(_COLOR_TITLE))

        conn = self.is_bracelet_connected()
        dot = _rgb(_COLOR_OK) if conn else _rgb(_COLOR_SUBTLE)
        pygame.draw.circle(screen, dot, (W - 24, 18), 8)
        self._pg_text(screen, font, "手环", W - 38, 12, _rgb(_COLOR_SUBTLE), anchor_e=True)

        s = self.snapshot()
        self._pg_text(screen, font, self._qiyun_phrase(s["qiyun_speed"]), 12, 36, _rgb(_COLOR_TEXT))
        region = s["region_name"] or s["region_id"] or "未选"
        self._pg_text(screen, font, f"{region} · {self._cheng(s['progress'])}", 12, 58, _rgb(_COLOR_SUBTLE))
        self._pg_text(screen, font, self._ledger_line(), 12, 80, _rgb(_COLOR_TEXT))
        box = s["pending_blindboxes"]
        self._pg_text(screen, font, f"盲盒 ×{box}" if box else "盲盒 ·", 12, 102,
                      _rgb(_COLOR_ACCENT if box else _COLOR_SUBTLE))
        self._pg_text(screen, font, self._fit(self._roster_line(), 26), 12, 124, _rgb(_COLOR_TEXT))
        self._pg_text(screen, font, self._time_line(s["time_s"]), 12, 146, _rgb(_COLOR_SUBTLE))

        if self._pg_message and time.time() - self._pg_message_ts < 3.5:
            msg, ok, _ts = self._pg_message
            color = _rgb(_COLOR_OK) if ok else _rgb(_COLOR_ERR)
            self._pg_text(screen, font, self._fit(msg, 30), 12, H - 26, color)

        if self._pg_menu is not None:
            self._pg_draw_menu(screen, font)

    @staticmethod
    def _pg_text(screen, font, text, x, y, color, anchor_e=False):
        surf = font.render(str(text), True, color)
        if anchor_e:
            x -= surf.get_width()
        screen.blit(surf, (x, y))

    def _pg_draw_menu(self, screen, font):
        import pygame
        m = self._pg_menu
        if m is None:
            return
        line_h = 20
        w, h = 176, 26 + len(m.entries) * line_h + 6
        x, y = self._pg_menu_pos
        W, H = screen.get_size()
        x = max(0, min(x, W - w))
        y = max(0, min(y, H - h))
        self._pg_menu_pos = (x, y)
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        surf.fill((24, 24, 34, 236))
        title = font.render(m.title, True, _rgb(_COLOR_ACCENT))
        surf.blit(title, (6, 4))
        for i, e in enumerate(m.entries):
            color = _rgb(_COLOR_TEXT) if not e.get("disabled") else _rgb(_COLOR_SUBTLE)
            txt = font.render(self._fit(e["label"], 20), True, color)
            surf.blit(txt, (8, 26 + i * line_h))
        screen.blit(surf, (x, y))

    # ------------------------------------------------------------------
    # tkinter 后端
    # ------------------------------------------------------------------
    def _tk_font(self, size: int, bold: bool = False):
        if not self._tk_font_family:
            family = "TkDefaultFont"
            try:
                import tkinter.font as tkfont
                avail = set(tkfont.families())
                family = next((f for f in _CJK_FONTS if f in avail), "TkDefaultFont")
            except Exception:
                pass
            self._tk_font_family = family
        return (self._tk_font_family, size, "bold" if bold else "normal")

    def _run_tkinter(self) -> None:
        import tkinter as tk

        try:
            root = tk.Tk()
        except Exception as exc:  # noqa: BLE001
            raise _BackendUnavailable(f"tk.Tk() 失败（无显示环境）：{exc}") from exc

        self._tk_root = root
        root.overrideredirect(True)  # 无边框桌宠小窗
        root.attributes("-topmost", True)
        root.title(self.title)

        W, H = 256, 184
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = max(0, sw - W - 24), max(0, sh - H - 64)
        root.geometry(f"{W}x{H}+{x}+{y}")
        root.configure(bg=_COLOR_BG)

        cv = tk.Canvas(root, width=W, height=H, bg=_COLOR_BG, highlightthickness=0)
        cv.pack(fill="both", expand=True)
        self._tk_cv = cv

        cv.bind("<Button-1>", self._tk_start_drag)
        cv.bind("<B1-Motion>", self._tk_drag_move)
        cv.bind("<Button-3>", self._tk_menu_popup)

        self._tk_tick()

        try:
            root.mainloop()
        finally:
            try:
                root.destroy()
            except Exception:
                pass

    def _tk_start_drag(self, event):
        self._tk_drag_offset = (event.x_root - self._tk_root.winfo_x(),
                                event.y_root - self._tk_root.winfo_y())

    def _tk_drag_move(self, event):
        off = getattr(self, "_tk_drag_offset", None)
        if off is None:
            return
        self._tk_root.geometry(f"+{event.x_root - off[0]}+{event.y_root - off[1]}")

    def _tk_menu_popup(self, event):
        self._build_tk_menu()
        try:
            self._tk_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._tk_menu.grab_release()
            except Exception:
                pass

    def _build_tk_menu(self):
        import tkinter as tk

        root = self._tk_root
        menu = tk.Menu(root, tearoff=0)
        self._tk_menu = menu

        menu.add_command(label="开箱", command=lambda: self._tk_action(self.action_open_blindbox))

        shop = tk.Menu(menu, tearoff=0)
        for it in self.shop_items():
            shop.add_command(label=f"{it.get('name', it['id'])}",
                             command=lambda i=it["id"]: self._tk_action(lambda: self.action_buy(i)))
        if not self.shop_items():
            shop.add_command(label="（无商品）", state="disabled")
        menu.add_cascade(label="商城", menu=shop)

        roster = tk.Menu(menu, tearoff=0)
        roster_ids = {h.get("id") for h in self._roster()}
        for h in self._all_heroes():
            hid = h.get("id")
            var = tk.BooleanVar(value=(hid in roster_ids))
            roster.add_checkbutton(
                label=h.get("name", hid),
                variable=var,
                command=lambda i=hid: self._tk_action(lambda: self._toggle_roster(i)),
            )
        if not self._all_heroes():
            roster.add_command(label="（无侠客）", state="disabled")
        menu.add_cascade(label="阵容", menu=roster)

        region_menu = tk.Menu(menu, tearoff=0)
        for r in self.regions():
            region_menu.add_command(label=r.get("name", r["id"]),
                                    command=lambda i=r["id"]: self._tk_action(lambda: self.action_choose_region(i)))
        if not self.regions():
            region_menu.add_command(label="（无地区）", state="disabled")
        menu.add_cascade(label="选择地区", menu=region_menu)

        boss_menu = tk.Menu(menu, tearoff=0)
        for r in self.regions():
            boss_menu.add_command(label=r.get("name", r["id"]),
                                  command=lambda i=r["id"]: self._tk_action(lambda: self.action_challenge_boss(i)))
        if not self.regions():
            boss_menu.add_command(label="（无地区）", state="disabled")
        menu.add_cascade(label="挑战 Boss", menu=boss_menu)

        up = tk.Menu(menu, tearoff=0)
        up.add_command(label="通用技能点", command=lambda: self._tk_action(self.action_upgrade_common_skill))
        for b in self.buildings():
            up.add_command(label=b.get("name", b["id"]),
                           command=lambda i=b["id"]: self._tk_action(lambda: self.action_upgrade_passive(i)))
        menu.add_cascade(label="升级", menu=up)

        ex = tk.Menu(menu, tearoff=0)
        for direction, amount, label in self._exchange_entries():
            ex.add_command(label=label,
                           command=lambda a=amount, d=direction: self._tk_action(lambda: self.action_exchange(a, d)))
        menu.add_cascade(label="兑换", menu=ex)

        menu.add_separator()
        conn = self.is_bracelet_connected()
        menu.add_command(label=f"手环指示灯：{'已连接' if conn else '未连接'}（占位）", state="disabled")
        menu.add_command(label="退出", command=self._tk_quit)

    def _tk_action(self, fn):
        try:
            ok, msg = fn()
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, f"操作失败：{exc}"
        self._tk_message = (msg, ok)
        self._tk_message_ts = time.time()

    def _tk_quit(self):
        if self._tk_root is not None:
            self._tk_root.quit()

    def _tk_tick(self):
        if self.activity_fn is not None:
            self.step_sim()
        self._tk_draw()
        if self._tk_root is not None:
            self._tk_root.after(int(self.tick_dt * 1000), self._tk_tick)

    @staticmethod
    def _fit(text, max_chars: int) -> str:
        text = str(text)
        return text if len(text) <= max_chars else text[: max_chars - 1] + "…"

    def _tk_draw(self):
        cv = self._tk_cv
        if cv is None:
            return
        cv.delete("all")
        W = int(cv.winfo_width() or 0)
        H = int(cv.winfo_height() or 0)
        if W <= 1:
            W = 256
        if H <= 1:
            H = 184
        cv.create_rectangle(2, 2, W - 2, H - 2, fill=_COLOR_CARD, outline=_COLOR_BORDER, width=2)

        cv.create_text(12, 10, anchor="nw", text="袖里乾坤", font=self._tk_font(12, True), fill=_COLOR_TITLE)

        conn = self.is_bracelet_connected()
        dot = _COLOR_OK if conn else _COLOR_SUBTLE
        cv.create_oval(W - 34, 9, W - 16, 27, fill=dot, outline="")
        cv.create_text(W - 40, 18, anchor="e", text="手环", font=self._tk_font(9), fill=_COLOR_SUBTLE)

        s = self.snapshot()
        cv.create_text(12, 38, anchor="nw", text=self._qiyun_phrase(s["qiyun_speed"]),
                       font=self._tk_font(10), fill=_COLOR_TEXT)
        region = s["region_name"] or s["region_id"] or "未选"
        cv.create_text(12, 60, anchor="nw", text=f"{region} · {self._cheng(s['progress'])}",
                       font=self._tk_font(9), fill=_COLOR_SUBTLE)
        cv.create_text(12, 80, anchor="nw", text=self._fit(self._ledger_line(), 30),
                       font=self._tk_font(9), fill=_COLOR_TEXT)
        box = s["pending_blindboxes"]
        cv.create_text(12, 100, anchor="nw", text=f"盲盒 ×{box}" if box else "盲盒 ·",
                       font=self._tk_font(9), fill=_COLOR_ACCENT if box else _COLOR_SUBTLE)
        cv.create_text(12, 120, anchor="nw", text=self._fit(self._roster_line(), 30),
                       font=self._tk_font(9), fill=_COLOR_TEXT)
        cv.create_text(12, 140, anchor="nw", text=self._time_line(s["time_s"]),
                       font=self._tk_font(8), fill=_COLOR_SUBTLE)

        if self._tk_message and time.time() - self._tk_message_ts < 3.5:
            msg, ok = self._tk_message
            color = _COLOR_OK if ok else _COLOR_ERR
            cv.create_rectangle(6, H - 32, W - 6, H - 6, fill=_COLOR_CARD, outline=color)
            cv.create_text(12, H - 19, anchor="nw", text=self._fit(msg, 32),
                           font=self._tk_font(8), fill=color)


class _PgMenu:
    """pygame 右键菜单节点（简单列表式导航）。"""

    def __init__(self, title, entries, parent=None):
        self.title = title
        self.entries = entries
        self.parent = parent


def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 直接运行本模块时的自测桩（仅 __main__，不参与正常 import）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="M5 桌宠 UI 自测（headless）")
    parser.add_argument("--headless", action="store_true", help="无 UI 模式")
    parser.add_argument("--auto-demo", action="store_true", help="演示开箱/升级动作")
    parser.add_argument("--max-steps", type=int, default=30, help="headless 循环步数")
    args = parser.parse_args()

    # 桩：不依赖其他执行者模块
    class _FakeWorld:
        def __init__(self, content):
            self._content = content
            self._t = 0.0
            self._boxes = 0
            self._progress = 0.0
            self._ledger = {"qian": 1000, "lingshi": 50, "neili": 20, "shengwang": 5}
            self._region = (content.get("regions") or [{}])[0].get("id", "r1")
            self._all = []
            for i, h in enumerate(content.get("heroes") or []):
                d = dict(h)
                d.setdefault("is_protagonist", i == 0)
                d.setdefault("level", 1)
                d.setdefault("power", 100)
                self._all.append(d)
            self._roster = [self._all[0]["id"]] if self._all else []

        @property
        def state(self):
            return {
                "time_s": self._t,
                "qiyun_speed": 1.0 + 0.5 * (self._t / 60.0),
                "roster": [dict(h) for h in self._all if h["id"] in self._roster],
                "all_heroes": [dict(h) for h in self._all],
                "ledger": dict(self._ledger),
                "region_id": self._region,
                "progress": self._progress,
                "pending_blindboxes": self._boxes,
            }

        def tick(self, dt, activity):
            self._t += dt
            self._progress = min(1.0, self._progress + dt * (0.05 + 0.1 * activity))
            self._ledger["qian"] += dt * 1.0
            self._ledger["lingshi"] += dt * 0.05
            if self._progress >= 1.0:
                self._progress = 0.0
                self._boxes += 1
            return []

        def choose_region(self, rid):
            self._region = rid
            self._progress = 0.0

        def challenge_boss(self, rid):
            self._region = rid
            self._boxes += 1
            return {"boss": rid}

        def set_roster(self, ids):
            self._roster = list(ids)[:3]

        def list_regions(self):
            return self._content.get("regions") or []

    class _FakeEconomy:
        def __init__(self):
            self._common_level = 0

        def apply(self, world, events):
            return None

        def open_blindbox(self, world):
            if world._boxes <= 0:
                return {"ok": False}
            world._boxes -= 1
            world._ledger["qian"] += 5
            return {"item": {"id": "m1", "name": "吐纳心法", "rarity": "common", "kind": "秘籍"}}

        def buy(self, world, item_id):
            if world._ledger.get("qian", 0) < 50:
                return False
            world._ledger["qian"] -= 50
            return True

        def upgrade_common_skill(self, world):
            if world._ledger.get("qian", 0) < 200:
                return False
            world._ledger["qian"] -= 200
            self._common_level += 1
            return True

        def upgrade_passive(self, world, building_id):
            if world._ledger.get("lingshi", 0) < 500:
                return False
            world._ledger["lingshi"] -= 500
            return True

        def exchange(self, world, amount, direction):
            return True

    content = _read_json(Path(__file__).resolve().parent.parent / "config" / "content.json")
    world = _FakeWorld(content)
    economy = _FakeEconomy()

    win = PetWindow(
        world,
        economy,
        headless=args.headless or True,
        activity_fn=lambda: 0.8,
        auto_demo=args.auto_demo,
        max_steps=args.max_steps,
        tick_dt=0.2,
        log_interval=1.0,
    )

    # 直接自测各菜单动作路径
    print("== 动作自测 ==")
    for ok, msg in (
        win.action_set_roster([world._all[0]["id"], world._all[1]["id"]]) if len(world._all) > 1 else
        win.action_set_roster([world._all[0]["id"]]),
        win.action_choose_region("r2"),
        win.action_challenge_boss("r2"),
        win.action_buy("e1"),
        win.action_upgrade_common_skill(),
        win.action_upgrade_passive("base"),
        win.action_exchange(10, "lingshi_to_qian"),
        win.action_open_blindbox(),
    ):
        print(f"  [{'OK' if ok else 'FAIL'}] {msg}")

    win.run()
