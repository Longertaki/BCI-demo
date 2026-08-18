"""气运流转速度计算（M3）。

公式：speed = base(主角等级) × multiplier(已学技能) × activity(封顶 0~1)

* base(level)        = balance.curve("qiyun", level)          （base*rate^level）
* multiplier(skills) = 1 + qiyun.multiplier_per_skill × 技能数
* activity           = clamp(activity, 0, qiyun.activity_cap)

注意：multiplier_per_skill / activity_cap 需要 balance 暴露配置 dict
（属性 .data/.config/.raw，或直接传 dict）。
"""

from __future__ import annotations

from ._util import as_config, clamp, curve_value, lookup


def _qiyun_cfg(balance):
    cfg = as_config(balance)
    if cfg is None:
        raise TypeError(
            "气运参数需要 balance 提供配置 dict（.data/.config/.raw 属性，或直接传 dict）"
        )
    return cfg


def activity_factor(activity, balance=None):
    """把活跃度封顶到 [0, activity_cap]。balance 为 None 时按 1.0 封顶。"""
    cap = 1.0
    if balance is not None:
        cap = float(lookup(_qiyun_cfg(balance), "qiyun", "activity_cap", default=1.0))
    return clamp(float(activity), 0.0, cap)


def skill_multiplier(skills, balance=None):
    """技能倍率 = 1 + multiplier_per_skill × 技能数。"""
    cfg = _qiyun_cfg(balance) if balance is not None else None
    per_skill = float(lookup(cfg, "qiyun", "multiplier_per_skill", default=0.0))
    return 1.0 + per_skill * len(list(skills or []))


def qiyun_speed(balance, hero, activity):
    """计算主角当前的气运流转速度。hero 为主角角色 dict。"""
    level = int(hero.get("level", 1))
    base = curve_value(balance, "qiyun", level)
    multiplier = skill_multiplier(hero.get("skills", []), balance)
    act = activity_factor(activity, balance)
    return base * multiplier * act
