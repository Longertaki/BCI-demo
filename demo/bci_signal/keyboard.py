"""真实键盘活跃度采集（M2）。

优先使用 ``pynput`` 监听全局键盘事件，把键盘事件频率映射为 0.0~1.0 活跃度。
若 pynput 未安装、或运行环境无显示/输入设备（如 Linux 无 X server 的 headless
环境），则自动降级为「占位模式」：``get_activity()`` 恒返回 0.0 并打印一次提示。

接入方式
--------
    pip install pynput        # 或： uv pip install pynput
    # Linux 需要 X11（后端 Xlib）；Windows / macOS 开箱即用。

映射规则
--------
    活跃度 = min(1.0, 近期按键速率 / max_cps)
    近期速率用指数滑动平均估算，随时间自然衰减（停手后活跃度缓慢回落）。
    默认 ``max_cps=10``：每秒 10 次按键达到满活跃度（约等于极速打字/狂按键）。

用法示例
--------
from bci_signal.keyboard import KeyboardActivity
kb = KeyboardActivity(max_cps=10).start()
a = kb.get_activity()   # 0.0 ~ 1.0
kb.stop()
"""

from __future__ import annotations

import math
import sys
import threading
import time
from typing import Optional

try:  # pynput 是可选依赖；缺失时走占位模式，不阻断 demo 主流程。
    from pynput import keyboard as _pynput_keyboard
    _PYNPUT_AVAILABLE = True
except Exception:  # noqa: BLE001 - 包括 ImportError 及任何导入期异常
    _PYNPUT_AVAILABLE = False
    _pynput_keyboard = None


def _clamp01(value: float) -> float:
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return value


def _cps_to_activity(cps: float, max_cps: float) -> float:
    """把每秒按键频率线性映射到 0.0~1.0。"""
    if max_cps <= 0:
        return 0.0
    return _clamp01(cps / max_cps)


class KeyboardActivity:
    """监听全局键盘，把按键频率映射为 0.0~1.0 活跃度。

    参数
    ----
    max_cps:
        达到满活跃度（1.0）所需的每秒按键次数，默认 10。
    half_life:
        活跃度的半衰期（秒）。停手后活跃度按指数衰减，
        ``half_life`` 秒后衰减到一半，默认 1.5s。
    poll_interval:
        采样兜底间隔（秒），仅用于防止极短时间内的除零，默认 0.2。
    """

    def __init__(
        self,
        max_cps: float = 10.0,
        half_life: float = 1.5,
        poll_interval: float = 0.2,
    ) -> None:
        self.max_cps = float(max_cps)
        self.half_life = float(half_life)
        self.poll_interval = float(poll_interval)

        self._lock = threading.Lock()
        self._count = 0.0          # 自上次采样以来的按键事件数
        self._ema: Optional[float] = None   # 近期按键速率（次/秒）指数滑动平均
        self._last_poll = time.monotonic()
        self._listener = None
        self._running = False
        self._fallback_reason: Optional[str] = None
        self._warned = False

    # ---- 生命周期 ---------------------------------------------------

    def start(self) -> "KeyboardActivity":
        """启动后台键盘监听。缺依赖/无显示时进入占位模式（返回 0.0）。"""
        if self._running:
            return self
        if not _PYNPUT_AVAILABLE:
            self._fallback_reason = (
                "未安装 pynput，请 `pip install pynput`（Linux 还需 X11 显示环境）"
            )
            self._warn_once()
            self._running = True
            return self
        try:
            listener = _pynput_keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            listener.daemon = True
            listener.start()  # 可能因无显示/输入设备抛异常
            self._listener = listener
        except Exception as exc:  # noqa: BLE001 - 运行环境差异
            self._fallback_reason = f"键盘监听启动失败：{exc}"
            self._warn_once()
        self._running = True
        return self

    def stop(self) -> "KeyboardActivity":
        """停止监听（幂等）。"""
        self._running = False
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # noqa: BLE001
                pass
            self._listener = None
        return self

    # ---- 主接口 -----------------------------------------------------

    def get_activity(self) -> float:
        """契约接口 M2：返回 0.0~1.0 的键盘活跃度（占位模式恒为 0.0）。"""
        if not self._running or self._fallback_reason is not None:
            return 0.0
        with self._lock:
            now = time.monotonic()
            dt = now - self._last_poll
            if dt <= 0:
                dt = self.poll_interval
            instant = self._count / dt
            self._count = 0.0
            self._last_poll = now

            if self._ema is None:
                self._ema = instant
            else:
                tau = self.half_life / math.log(2.0)
                alpha = 1.0 - math.exp(-dt / tau)
                self._ema += alpha * (instant - self._ema)
            ema = self._ema
        return _cps_to_activity(ema, self.max_cps)

    def is_available(self) -> bool:
        """是否处于真实键盘采集模式（False 表示占位模式）。"""
        return self._running and self._fallback_reason is None and self._listener is not None

    # ---- 内部 -------------------------------------------------------

    def _on_press(self, key) -> None:
        with self._lock:
            self._count += 1.0

    def _on_release(self, key) -> None:
        # 只统计按下事件，避免一次敲击被 press+release 双重计数。
        # 若希望把 release 也算作“事件频率”，可在此处同样累加并调大 max_cps。
        return None

    def _warn_once(self) -> None:
        if self._warned:
            return
        self._warned = True
        print(
            f"[bci_signal.keyboard] 占位模式：{self._fallback_reason}；"
            "get_activity() 恒返回 0.0。",
            file=sys.stderr,
        )

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return (
            f"KeyboardActivity(max_cps={self.max_cps!r}, "
            f"half_life={self.half_life!r}, available={self.is_available()})"
        )


# 模块级单例：首次调用 get_activity() 时惰性启动监听。
_DEFAULT: Optional[KeyboardActivity] = None


def get_activity() -> float:
    """契约接口 M2：返回 0.0~1.0 的键盘活跃度。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = KeyboardActivity().start()
    return _DEFAULT.get_activity()


def get_keyboard() -> KeyboardActivity:
    """返回模块级单例（首次访问时启动监听）。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = KeyboardActivity().start()
    return _DEFAULT


__all__ = ["KeyboardActivity", "get_activity", "get_keyboard"]
