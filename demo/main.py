#!/usr/bin/env python3
"""袖里乾坤（Qiankun）Demo v1 入口：串起 信号采集 → 核心模拟 → 经济系统 → 桌宠 UI。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from balance import Balance          # noqa: E402
from core import World               # noqa: E402
from economy import Economy          # noqa: E402
from bci_signal import get_activity  # noqa: E402
from ui import PetWindow             # noqa: E402


def build_args(argv=None):
    ap = argparse.ArgumentParser(description="袖里乾坤（Qiankun）Demo v1")
    ap.add_argument("--headless", action="store_true", help="无 GUI，打印快照日志")
    ap.add_argument("--keyboard", action="store_true", help="用真实键盘活跃度（默认模拟源）")
    ap.add_argument("--speed", type=float, default=1.0, help="世界时间加速倍率（>0）")
    ap.add_argument("--tick-dt", type=float, default=None,
                    help="每步真实秒数（headless 默认 0.01，GUI 默认 1.0）")
    ap.add_argument("--steps", type=int, default=None, help="headless 最大 tick 步数")
    ap.add_argument("--auto-demo", action="store_true", help="自动演示闭环（开箱/购点/升级）")
    ap.add_argument("--config", default="config", help="配置目录（含 balance.json / content.json）")
    return ap.parse_args(argv)


def main(argv=None):
    args = build_args(argv)

    balance = Balance.load(args.config)
    config_dir = Path(args.config).resolve()
    content = json.loads((config_dir / "content.json").read_text(encoding="utf-8"))

    world = World(balance, content)
    economy = Economy(balance, str(config_dir))

    activity_fn = (lambda: get_activity("keyboard")) if args.keyboard else get_activity

    tick_dt = args.tick_dt if args.tick_dt is not None else (0.01 if args.headless else 1.0)

    pet = PetWindow(
        world,
        economy,
        headless=args.headless,
        activity_fn=activity_fn,
        tick_dt=tick_dt,
        speed=args.speed,
        max_steps=args.steps,
        auto_demo=args.auto_demo,
        config_dir=str(config_dir),
    )
    pet.run()


if __name__ == "__main__":
    main()
