"""模拟活跃度信号源（M2 · demo 主力）。

提供一个可调频率 / 波形的活跃度生成器，输出 0.0~1.0（封顶）。
demo 主循环反复调用 :func:`get_activity` 即可得到随时间变化的活跃度因子，
喂给 M3 计算气运流转速度（speed = base * multiplier * activity）。

用法示例
--------
from bci_signal.simulator import ActivitySimulator
sim = ActivitySimulator(waveform="sine", frequency=0.05)   # 20s 一个周期
a = sim.get_activity()   # 0.0 ~ 1.0，随时间变化
sim.set_waveform("random").set_frequency(0.5)              # 每秒 0.5 个随机脉冲
"""

from __future__ import annotations

import math
import random
import time
from typing import Optional

# 支持的波形名（对外可读、可传）。
WAVEFORMS: tuple[str, ...] = ("sine", "square", "triangle", "random", "constant")


def _clamp01(value: float) -> float:
    """封顶到 [0.0, 1.0]。"""
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return value


class ActivitySimulator:
    """可调频率/波形的模拟活跃度源。

    参数
    ----
    waveform:
        "sine"     正弦波（平滑起伏，demo 默认）
        "square"   方波脉冲（高低两档）
        "triangle" 三角波（线性升降）
        "random"   随机脉冲（每个脉冲随机取一个值）
        "constant" 恒定值（调试用，取 ``constant`` 参数）
    frequency:
        频率，单位 Hz。周期波为「每秒循环次数」；random 为「每秒脉冲数」。
        frequency=0 表示周期无限长（random 退化为恒定首值）。
    phase:
        初始相位，0.0~1.0 表示一个完整周期的偏移（只影响周期波）。
    lo / hi:
        输出范围，默认 0.0~1.0；最终结果仍封顶到 [0, 1]。
    seed:
        随机脉冲的随机种子（``None`` 用系统熵；给定整数可复现测试）。
    smooth:
        random 波形专用：True 时在相邻脉冲间线性插值（曲线更平滑），
        False 时脉冲直接跳变（更接近“脉冲”）。
    constant:
        constant 波形下的固定输出值，默认 0.5。
    """

    def __init__(
        self,
        waveform: str = "sine",
        frequency: float = 0.05,
        phase: float = 0.0,
        lo: float = 0.0,
        hi: float = 1.0,
        seed: Optional[int] = None,
        smooth: bool = False,
        constant: float = 0.5,
    ) -> None:
        if waveform not in WAVEFORMS:
            raise ValueError(f"不支持的波形 {waveform!r}，可选：{WAVEFORMS}")
        self._waveform = waveform
        self.set_frequency(frequency)
        self.set_phase(phase)
        self.set_range(lo, hi)
        self.smooth = bool(smooth)
        self.constant = float(constant)

        self._t0 = time.monotonic()
        self._rng = random.Random(seed)
        # random 波形的内部状态：首脉冲值 + 脉冲序号。
        self._prev_value = self._rng.random()
        self._last_value = self._prev_value
        self._last_index = 0

    # ---- 属性与配置 -------------------------------------------------

    @property
    def waveform(self) -> str:
        return self._waveform

    @property
    def frequency(self) -> float:
        return self._frequency

    @property
    def phase(self) -> float:
        return self._phase

    @property
    def lo(self) -> float:
        return self._lo

    @property
    def hi(self) -> float:
        return self._hi

    def set_waveform(self, waveform: str) -> "ActivitySimulator":
        if waveform not in WAVEFORMS:
            raise ValueError(f"不支持的波形 {waveform!r}，可选：{WAVEFORMS}")
        self._waveform = waveform
        return self

    def set_frequency(self, frequency: float) -> "ActivitySimulator":
        frequency = float(frequency)
        if frequency < 0:
            raise ValueError(f"frequency 不能为负：{frequency!r}")
        self._frequency = frequency
        return self

    def set_phase(self, phase: float) -> "ActivitySimulator":
        self._phase = float(phase)
        return self

    def set_range(self, lo: float, hi: float) -> "ActivitySimulator":
        lo, hi = float(lo), float(hi)
        if not (0.0 <= lo <= hi <= 1.0):
            raise ValueError(f"需要 0.0 <= lo <= hi <= 1.0，实际 lo={lo}, hi={hi}")
        self._lo = lo
        self._hi = hi
        return self

    def reset(self, seed: Optional[int] = None) -> "ActivitySimulator":
        """重置时间起点与随机状态（可换随机种子）。"""
        self._t0 = time.monotonic()
        self._rng = random.Random(seed)
        self._prev_value = self._rng.random()
        self._last_value = self._prev_value
        self._last_index = 0
        return self

    # ---- 主接口 -----------------------------------------------------

    def get_activity(self, t: Optional[float] = None) -> float:
        """返回当前活跃度，0.0~1.0 封顶。

        参数 ``t`` 为「自创建/重置以来的秒数」，默认 ``None`` 表示用真实时钟。
        测试可传入确定值以便验证波形；random 波形要求 ``t`` 单调不减。
        """
        if t is None:
            t = time.monotonic() - self._t0
        if self._waveform == "constant":
            return _clamp01(self.constant)
        raw = self._raw_value(t)
        return _clamp01(self._lo + (self._hi - self._lo) * raw)

    def _raw_value(self, t: float) -> float:
        """计算 0.0~1.0 的未缩放波形值。"""
        wf = self._waveform
        if wf == "constant":
            return self.constant
        if wf == "random":
            return self._random_raw(t)
        # 周期波：按 phase 偏移后的周期位置。
        cycle = (self._frequency * t + self._phase) % 1.0
        if wf == "sine":
            return 0.5 + 0.5 * math.sin(2.0 * math.pi * cycle)
        if wf == "square":
            return 1.0 if cycle < 0.5 else 0.0
        # triangle：0 -> 1 -> 0
        return 1.0 - 2.0 * abs(cycle - 0.5)

    def _hold_seconds(self) -> float:
        if self._frequency > 0:
            return 1.0 / self._frequency
        return float("inf")

    def _random_raw(self, t: float) -> float:
        """随机脉冲：每 1/frequency 秒换一个随机值。"""
        hold = self._hold_seconds()
        index = int(t // hold) if hold != float("inf") else 0
        if index != self._last_index:
            # 进入新脉冲：旧的当前值变前值，再抽新值。
            self._last_index = index
            self._prev_value = self._last_value
            self._last_value = self._rng.random()
        if self.smooth:
            frac = (t / hold) - index if hold != float("inf") else 0.0
            return self._prev_value + (self._last_value - self._prev_value) * frac
        return self._last_value

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return (
            f"ActivitySimulator(waveform={self._waveform!r}, "
            f"frequency={self._frequency!r}, phase={self._phase!r}, "
            f"lo={self._lo!r}, hi={self._hi!r})"
        )


# 模块级默认模拟源（demo 主力）。主循环直接调用 get_activity() 即可。
_DEFAULT: ActivitySimulator = ActivitySimulator(waveform="sine", frequency=0.05)


def get_activity() -> float:
    """契约接口 M2：返回 0.0~1.0 的活跃度（默认正弦波模拟源）。"""
    return _DEFAULT.get_activity()


def get_simulator() -> ActivitySimulator:
    """返回模块级默认模拟源，便于 main.py 调整频率/波形。"""
    return _DEFAULT


__all__ = ["ActivitySimulator", "get_activity", "get_simulator", "WAVEFORMS"]
