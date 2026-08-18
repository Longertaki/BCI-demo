"""M2 信号模块自测（stdlib unittest，可 headless 运行）。

运行方式（在 demo/ 目录下）：
    python3 -m unittest tests.test_bci_signal -v
或：
    cd demo && python3 -m unittest discover -s tests -p "test_bci_signal.py" -v
"""

import os
import sys
import unittest

# 确保 demo/ 在 sys.path 最前，使 `import bci_signal` 命中本项目的 bci_signal 包
# （而非标准库 signal）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bci_signal import get_activity  # noqa: E402
from bci_signal.keyboard import KeyboardActivity, _cps_to_activity  # noqa: E402
from bci_signal.simulator import ActivitySimulator, WAVEFORMS  # noqa: E402


class TestSimulator(unittest.TestCase):
    def test_output_always_clamped(self):
        for wf in WAVEFORMS:
            sim = ActivitySimulator(waveform=wf, frequency=0.37, seed=7)
            for t in (0.0, 0.13, 0.5, 1.0, 2.71, 10.0):
                v = sim.get_activity(t=t)
                self.assertTrue(0.0 <= v <= 1.0, f"{wf}@{t} -> {v}")
                self.assertIsInstance(v, float)

    def test_sine_covers_full_range(self):
        sim = ActivitySimulator(waveform="sine", frequency=1.0)
        # phase=0 时：t=0.25 为波峰(≈1)，t=0.75 为波谷(≈0)。
        values = [sim.get_activity(t=t) for t in (0.25, 0.75)]
        self.assertGreater(values[0], 0.95)
        self.assertLess(values[1], 0.05)

    def test_constant(self):
        sim = ActivitySimulator(waveform="constant", constant=0.42)
        self.assertAlmostEqual(sim.get_activity(t=0), 0.42)
        self.assertAlmostEqual(sim.get_activity(t=999), 0.42)
        # 越界也会被封顶。
        sim.constant = 2.0
        self.assertEqual(sim.get_activity(), 1.0)
        sim.constant = -1.0
        self.assertEqual(sim.get_activity(), 0.0)

    def test_range_scaling_and_clamp(self):
        sim = ActivitySimulator(waveform="sine", frequency=1.0, lo=0.3, hi=0.7)
        values = [sim.get_activity(t=t) for t in (0.25, 0.75)]
        # 波峰(0.25) -> hi，波谷(0.75) -> lo。
        self.assertAlmostEqual(values[0], 0.7, delta=0.05)
        self.assertAlmostEqual(values[1], 0.3, delta=0.05)

    def test_random_reproducible_with_seed(self):
        a = ActivitySimulator(waveform="random", frequency=1.0, seed=123)
        b = ActivitySimulator(waveform="random", frequency=1.0, seed=123)
        for t in (0.1, 0.6, 1.1, 1.6, 2.1):
            self.assertEqual(a.get_activity(t=t), b.get_activity(t=t))

    def test_invalid_waveform_raises(self):
        with self.assertRaises(ValueError):
            ActivitySimulator(waveform="nope")

    def test_invalid_range_raises(self):
        with self.assertRaises(ValueError):
            ActivitySimulator(waveform="sine", lo=0.8, hi=0.2)

    def test_chaining(self):
        sim = ActivitySimulator()
        sim.set_waveform("square").set_frequency(2.0).set_phase(0.5).set_range(0.0, 1.0)
        self.assertEqual(sim.waveform, "square")
        self.assertEqual(sim.frequency, 2.0)


class TestKeyboard(unittest.TestCase):
    def test_cps_mapping(self):
        self.assertEqual(_cps_to_activity(0.0, 10.0), 0.0)
        self.assertEqual(_cps_to_activity(5.0, 10.0), 0.5)
        self.assertEqual(_cps_to_activity(10.0, 10.0), 1.0)
        self.assertEqual(_cps_to_activity(99.0, 10.0), 1.0)
        self.assertEqual(_cps_to_activity(5.0, 0.0), 0.0)

    def test_placeholder_mode_returns_zero(self):
        # 未启动时 get_activity 应为 0.0；headless 下即使 start() 也应安全返回 0.0。
        kb = KeyboardActivity(max_cps=10.0)
        self.assertEqual(kb.get_activity(), 0.0)
        kb.start()
        try:
            self.assertIsInstance(kb.get_activity(), float)
            self.assertEqual(kb.get_activity(), 0.0)
        finally:
            kb.stop()

    def test_start_stop_idempotent(self):
        kb = KeyboardActivity(max_cps=10.0)
        kb.start().start().stop().stop()
        self.assertFalse(kb._running)


class TestPackage(unittest.TestCase):
    def test_default_get_activity(self):
        v = get_activity()  # 默认 sim 源
        self.assertTrue(0.0 <= v <= 1.0)
        v2 = get_activity(source="sim")
        self.assertTrue(0.0 <= v2 <= 1.0)
        v3 = get_activity(source="keyboard")
        self.assertEqual(v3, 0.0)  # headless 占位模式

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            get_activity(source="emg")


if __name__ == "__main__":
    unittest.main()
