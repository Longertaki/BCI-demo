"""M1 数值配置模块。

读取 `config/balance.json`，对外提供曲线、成长、掉落查询接口。
本模块只读 JSON、不做业务逻辑；数值一律来自配置文件，代码中不写死数值。

契约接口（见 docs/demo-contract.md）：

    Balance.load(config_dir="config") -> Balance
    balance.curve(key, n)          -> float   # base * rate ** n
    balance.growth(key, n)         -> float
    balance.drop_rate(key)         -> float
    balance.drop_table(key)        -> dict

曲线 key：
    - growth 段：hero_level_cost / region_difficulty / boss_power
    - qiyun  段：qiyun（使用其中的 base/rate）

掉落 key：
    - drop_rate("blindbox_base_rate") / drop_rate("rarity.common") / drop_rate("item_types.manual") ...
    - drop_table("rarity") / drop_table("item_types")

另外暴露只读属性 `data`（完整 JSON）以及 `qiyun` / `growth_config` /
`drops` / `economy`，便于 M3/M4 直接读取各自字段。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict


class Balance:
    """数值配置读取器（M1）。"""

    def __init__(self, data: Dict[str, Any], source: str | Path | None = None) -> None:
        self.data: Dict[str, Any] = data
        self.source: str | None = str(source) if source else None
        self.problems: list[str] = []
        self._validate()

    # ------------------------------------------------------------------ 加载
    @classmethod
    def load(cls, config_dir: str = "config") -> "Balance":
        """从 config_dir/balance.json 加载数值。

        config_dir 可传相对路径（先按当前工作目录解析，再回退到本文件所在
        目录）或绝对路径。
        """
        cfg = Path(config_dir)
        candidate = cfg / "balance.json"
        if not candidate.exists() and not cfg.is_absolute():
            alt = Path(__file__).resolve().parent / cfg / "balance.json"
            if alt.exists():
                candidate = alt
        if not candidate.exists():
            raise FileNotFoundError(
                f"找不到 balance.json：{candidate}（config_dir={config_dir!r}）"
            )
        with candidate.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls(data, source=candidate)

    # ---------------------------------------------------------------- 校验
    def _validate(self) -> None:
        problems: list[str] = []
        for key in ("qiyun", "growth", "drops", "economy"):
            if key not in self.data:
                problems.append(f"缺少顶层字段 {key!r}")

        growth = self.data.get("growth", {})
        if isinstance(growth, dict):
            for k, v in growth.items():
                if not (isinstance(v, dict) and "base" in v and "rate" in v):
                    problems.append(f"growth.{k} 必须为 {{base, rate}} 结构")

        drops = self.data.get("drops", {})
        if isinstance(drops, dict):
            for table in ("rarity", "item_types"):
                t = drops.get(table)
                if isinstance(t, dict) and t:
                    total = sum(float(x) for x in t.values())
                    if abs(total - 1.0) > 1e-6:
                        problems.append(
                            f"drops.{table} 概率之和为 {total:.4f}，应为 1.0"
                        )

        self.problems = problems
        for p in problems:
            print(f"[Balance] WARN: {p}", file=sys.stderr)

    # ---------------------------------------------------------------- 只读
    @property
    def qiyun(self) -> Dict[str, Any]:
        return self.data.get("qiyun", {})

    @property
    def growth_config(self) -> Dict[str, Any]:
        """growth 段原始配置（方法名 growth 已被契约占用，故属性加 _config 后缀）。"""
        return self.data.get("growth", {})

    @property
    def drops(self) -> Dict[str, Any]:
        return self.data.get("drops", {})

    @property
    def economy(self) -> Dict[str, Any]:
        return self.data.get("economy", {})

    # ---------------------------------------------------------------- 曲线
    def _curve_entry(self, key: str) -> Dict[str, Any]:
        growth = self.data.get("growth", {})
        if key in growth:
            entry = growth[key]
        elif key == "qiyun":
            entry = self.data.get("qiyun", {})
        else:
            raise KeyError(f"未定义的曲线 key: {key!r}（可用：growth 段各 key 或 'qiyun'）")
        if not (isinstance(entry, dict) and "base" in entry and "rate" in entry):
            raise KeyError(f"曲线 {key!r} 缺少 base/rate 字段")
        return entry

    def curve(self, key: str, n: int) -> float:
        """通用曲线：base * rate ** n。"""
        entry = self._curve_entry(key)
        return float(entry["base"]) * float(entry["rate"]) ** float(n)

    def growth(self, key: str, n: int) -> float:
        """成长曲线（growth 段）：base * rate ** n。"""
        growth = self.data.get("growth", {})
        if key not in growth:
            raise KeyError(
                f"未定义的成长曲线 key: {key!r}（可用：{sorted(growth)})"
            )
        entry = growth[key]
        return float(entry["base"]) * float(entry["rate"]) ** float(n)

    # ---------------------------------------------------------------- 掉落
    def drop_rate(self, key: str) -> float:
        """读取掉落相关概率。

        支持：
            - "blindbox_base_rate"（或别名 "blindbox_rate"）
            - 点分路径："rarity.common" / "item_types.manual" 等
            - drops 段中的直接数值 key
        """
        drops = self.data.get("drops", {})
        if key in ("blindbox_base_rate", "blindbox_rate"):
            if "blindbox_base_rate" not in drops:
                raise KeyError("drops 缺少 blindbox_base_rate")
            return float(drops["blindbox_base_rate"])
        if "." in key:
            table, sub = key.split(".", 1)
            if table in drops and isinstance(drops[table], dict):
                if sub not in drops[table]:
                    raise KeyError(f"掉落表 {table!r} 中无 {sub!r}")
                return float(drops[table][sub])
            raise KeyError(f"未知掉落表 key: {table!r}")
        if key in drops and isinstance(drops[key], (int, float)):
            return float(drops[key])
        raise KeyError(f"未定义的掉落概率 key: {key!r}")

    def drop_table(self, key: str) -> dict:
        """读取掉落分布表（dict），如 "rarity" / "item_types"。"""
        drops = self.data.get("drops", {})
        if key not in drops or not isinstance(drops[key], dict):
            raise KeyError(f"未定义的掉落表 key: {key!r}（可用：rarity / item_types）")
        return dict(drops[key])

    # ---------------------------------------------------------------- 其他
    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Balance(source={self.source!r}, problems={len(self.problems)})"


__all__ = ["Balance"]


if __name__ == "__main__":  # 最小自测：python balance.py
    b = Balance.load()
    print("加载:", b)
    print("curve(growth.hero_level_cost, 0) =", b.curve("hero_level_cost", 0))
    print("curve(growth.hero_level_cost, 10) =", b.curve("hero_level_cost", 10))
    print("growth(boss_power, 3) =", b.growth("boss_power", 3))
    print("curve(qiyun, 5) =", b.curve("qiyun", 5))
    print("drop_rate(blindbox_base_rate) =", b.drop_rate("blindbox_base_rate"))
    print("drop_rate(rarity.epic) =", b.drop_rate("rarity.epic"))
    print("drop_table(item_types) =", b.drop_table("item_types"))
