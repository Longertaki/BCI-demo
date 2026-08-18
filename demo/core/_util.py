"""M3 内部共享工具（仅限 core 包内部使用，不属于公开接口契约）。"""

from __future__ import annotations


def clamp(value, lo=0.0, hi=1.0):
    """把 value 限制在 [lo, hi] 区间。"""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def as_config(balance):
    """从 Balance 实例或 dict 中取出原始配置 dict。

    兼容：
    * 直接传入 dict（配置原文）
    * 带 ``.data`` / ``.config`` / ``.raw`` 属性的 Balance 对象
    """
    if isinstance(balance, dict):
        return balance
    for attr in ("data", "config", "raw"):
        value = getattr(balance, attr, None)
        if isinstance(value, dict):
            return value
    return None


def lookup(cfg, *path, default=None):
    """嵌套 dict 取值，缺失时返回 default。"""
    cur = cfg
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _find_curve_section(cfg, key):
    """在配置里定位 base/rate 曲线节（顶层或 growth.*）。"""
    if isinstance(cfg, dict):
        if key in cfg:
            section = cfg[key]
            if isinstance(section, dict) and "base" in section and "rate" in section:
                return section
        growth = cfg.get("growth")
        if isinstance(growth, dict):
            section = growth.get(key)
            if isinstance(section, dict) and "base" in section and "rate" in section:
                return section
    raise KeyError(f"balance 配置缺少曲线节: {key}")


def curve_value(balance, key, n, method_name="curve"):
    """读取曲线值 base*rate**n。

    优先调用 Balance 的 curve/growth 方法（接口契约），
    若 balance 是 dict 或方法不可用，则直接从原始配置计算。
    """
    method = getattr(balance, method_name, None)
    if callable(method):
        try:
            return float(method(key, n))
        except Exception:
            pass
    cfg = as_config(balance)
    if cfg is None:
        raise TypeError("balance 必须是 Balance 实例或配置 dict")
    section = _find_curve_section(cfg, key)
    return float(section["base"]) * float(section["rate"]) ** n


def _first_number(mapping, *keys):
    if not isinstance(mapping, dict):
        return None
    for k in keys:
        v = mapping.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def drop_rate_value(balance, key, default=0.0):
    """读取事件/掉落率（每秒概率）。

    优先 Balance.drop_rate(key)；否则在配置里依次找：
    drops[key] / drops[key_rate] / drops[key_base_rate]、
    drops.events[key]、顶层 events[key]。仍缺失返回 default。
    """
    method = getattr(balance, "drop_rate", None)
    if callable(method):
        try:
            return float(method(key))
        except Exception:
            pass
    cfg = as_config(balance)
    if not isinstance(cfg, dict):
        return default

    drops = cfg.get("drops")
    if isinstance(drops, dict):
        value = _first_number(drops, key, key + "_rate", key + "_base_rate")
        if value is not None:
            return value
        events = drops.get("events")
        if isinstance(events, dict):
            value = _first_number(events, key, key + "_rate")
            if value is not None:
                return value

    events = cfg.get("events")
    if isinstance(events, dict):
        value = _first_number(events, key, key + "_rate")
        if value is not None:
            return value
    return default
