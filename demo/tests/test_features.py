"""E6 特性测试：Boss 门票 + 盲盒稀有度按来源 + 掉落物强度匹配地区 + 关卡/地区 Boss 结构。

运行方式（demo/ 目录下）：
    python3 -m unittest tests.test_features -v
"""

import json
import os
import sys
import unittest

DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DEMO_DIR)

from balance import Balance            # noqa: E402
from core import World                 # noqa: E402
from economy import Economy            # noqa: E402


class FeatureTestCase(unittest.TestCase):
    def setUp(self):
        self.balance = Balance.load(os.path.join(DEMO_DIR, "config"))
        with open(os.path.join(DEMO_DIR, "config", "content.json"), encoding="utf-8") as fh:
            self.content = json.load(fh)

    def make_world(self, seed=7):
        world = World(self.balance, self.content, seed=seed)
        # 让阵容战力足以击败任何 Boss，隔离「战力」对 Boss 结构测试的影响。
        for hero in world.state["roster"]:
            hero["power"] = 10 ** 9
        return world

    @staticmethod
    def boost(world):
        for hero in world.state["roster"]:
            hero["power"] = 10 ** 9

    @staticmethod
    def fill_progress(world, region_id):
        """把当前关卡的探索进度填满（测试桩：直接置 1.0）。"""
        world._level_progress[region_id] = 1.0
        world._sync_progress(world._region_by_id(region_id))

    # ------------------------------------------------------------------ #
    # 1) 门票掉落与消耗
    # ------------------------------------------------------------------ #
    def test_ticket_drops_from_idle_and_consumed_by_boss(self):
        world = self.make_world()
        economy = Economy(self.balance, os.path.join(DEMO_DIR, "config"))

        events = world.tick(10000.0, 1.0)  # 长挂机：必然产生门票掉落事件（泊松 λ=20）
        self.assertTrue(
            any(e.get("type") == "ticket_drop" for e in events),
            "挂机 tick 应产生 ticket_drop 事件",
        )
        economy.apply(world, events)
        self.assertGreater(world.state["tickets"], 0, "挂机结算后应获得门票")

        before = world.state["tickets"]
        self.fill_progress(world, "r1")
        self.boost(world)
        boss_events = world.challenge_boss("r1", "r1_b1")
        self.assertEqual(boss_events[0]["data"]["ticket_cost"], 1)
        economy.apply(world, boss_events)
        self.assertEqual(world.state["tickets"], before - 1, "挑战 Boss 应消耗 1 张门票")

    def test_challenge_boss_rejected_without_ticket(self):
        world = self.make_world()
        world.state["tickets"] = 0
        self.fill_progress(world, "r1")
        with self.assertRaises(ValueError) as ctx:
            world.challenge_boss("r1", "r1_b1")
        self.assertIn("门票", str(ctx.exception))

    # ------------------------------------------------------------------ #
    # 2) 盲盒稀有度按来源
    # ------------------------------------------------------------------ #
    def test_level_boss_drops_rare_blindbox(self):
        world = self.make_world()
        economy = Economy(self.balance, os.path.join(DEMO_DIR, "config"))
        world.state["tickets"] = 1
        self.fill_progress(world, "r1")

        events = world.challenge_boss("r1", "r1_b1")
        data = events[0]["data"]
        self.assertEqual(data["boss_kind"], "level")
        self.assertEqual(data["blindbox_tier"], "rare")
        self.assertTrue(data["won"])

        economy.apply(world, events)
        self.assertEqual(world.state["tickets"], 0)
        self.assertEqual(world.state["pending_blindboxes"], 1)
        self.assertEqual(world.state["economy"]["pending_boxes"][0]["tier"], "rare")

        result = economy.open_blindbox(world)
        self.assertTrue(result["ok"])
        self.assertEqual(result["blindbox_tier"], "rare")
        self.assertIn(result["rarity"], ("common", "rare"))
        self.assertNotEqual(result["rarity"], "epic", "稀有盲盒不得开出更高稀有度物品")

    def test_region_boss_drops_epic_blindbox_and_unlocks_next_region(self):
        world = self.make_world()
        economy = Economy(self.balance, os.path.join(DEMO_DIR, "config"))
        # 通关 r1 两个关卡（测试桩直接标记已清关）。
        world._cleared_levels.update({"r1_l1", "r1_l2"})
        world._sync_clears()
        world._level_progress["r1"] = 1.0
        world._sync_progress(world._region_by_id("r1"))
        world.state["tickets"] = 5

        self.assertTrue(world.region_boss_available("r1"))
        events = world.challenge_boss("r1", "r1_rb")
        data = events[0]["data"]
        self.assertEqual(data["boss_kind"], "region")
        self.assertEqual(data["blindbox_tier"], "epic")
        self.assertTrue(data["won"])

        economy.apply(world, events)
        self.assertEqual(world.state["economy"]["pending_boxes"][0]["tier"], "epic")

        result = economy.open_blindbox(world)
        self.assertTrue(result["ok"])
        self.assertEqual(result["blindbox_tier"], "epic")

        # 击败地区 Boss → 进入下一地区（无等级门槛）。
        self.assertEqual(world.state["region_id"], "r2")
        self.assertIn("r1", world.state["cleared_regions"])
        self.assertTrue(world.is_region_unlocked("r2"))

    # ------------------------------------------------------------------ #
    # 3) 地区 Boss 需要先通关地区所有关卡
    # ------------------------------------------------------------------ #
    def test_region_boss_requires_all_levels_cleared(self):
        world = self.make_world()
        economy = Economy(self.balance, os.path.join(DEMO_DIR, "config"))
        world.state["tickets"] = 5

        # 一个关卡都没清：地区 Boss 必须被拒绝。
        self.fill_progress(world, "r1")
        with self.assertRaises(ValueError) as ctx:
            world.challenge_boss("r1", "r1_rb")
        self.assertIn("尚未通关", str(ctx.exception))

        # 只清第一关：地区 Boss 仍被拒绝。
        economy.apply(world, world.challenge_boss("r1", "r1_b1"))
        with self.assertRaises(ValueError):
            world.challenge_boss("r1", "r1_rb")

        # 清完第二关：地区 Boss 可挑战。
        self.fill_progress(world, "r1")
        economy.apply(world, world.challenge_boss("r1", "r1_b2"))
        self.assertTrue(world.region_boss_available("r1"))
        events = world.challenge_boss("r1", "r1_rb")
        self.assertTrue(events[0]["data"]["won"])

    # ------------------------------------------------------------------ #
    # 4) 掉落物强度随来源地区难度缩放
    # ------------------------------------------------------------------ #
    def test_item_power_scales_with_region_difficulty(self):
        world = self.make_world()
        economy = Economy(self.balance, os.path.join(DEMO_DIR, "config"))

        # 注入两个同 tier（common）但来源地区难度不同的盲盒。
        economy.apply(world, [{
            "type": "blindbox_drop",
            "data": {"count": 1, "blindbox_tier": "common", "difficulty": 1, "region_id": "r1"},
        }])
        economy.apply(world, [{
            "type": "blindbox_drop",
            "data": {"count": 1, "blindbox_tier": "common", "difficulty": 3, "region_id": "r3"},
        }])

        low = economy.open_blindbox(world)
        high = economy.open_blindbox(world)
        self.assertTrue(low["ok"] and high["ok"])
        self.assertEqual(low["rarity"], "common", "普通盲盒只应开出 common 物品")

        scale1 = self.balance.growth("item_power_scale", 1)
        scale3 = self.balance.growth("item_power_scale", 3)
        self.assertGreater(scale3, scale1, "难度系数曲线应随难度递增")

        for result, scale in ((low, scale1), (high, scale3)):
            item = result["item"]
            self.assertEqual(
                item["power_bonus"],
                int(round(item["base_power_bonus"] * scale)),
                "掉落物强度应 = base_power × region_difficulty 系数",
            )
            self.assertEqual(result["source_difficulty"], item["source_difficulty"])


if __name__ == "__main__":
    unittest.main()
