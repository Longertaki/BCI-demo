"""M2 信号采集模块：键盘活跃度 + 模拟信号源。

对外契约接口
------------
    get_activity() -> float        # 默认模拟源（demo 主力），输出 0.0~1.0
    ActivitySimulator              # 可调频率/波形的模拟活跃度源（simulator.py）
    KeyboardActivity               # 真实键盘活跃度（keyboard.py，缺 pynput 时占位）
    keyboard.get_activity()        # 真实键盘单例
    simulator.get_activity()       # 模拟源单例

包名为 ``bci_signal``：预留未来接入脑机 / 肌电（EMG）等生物电输入。
"""

from .keyboard import KeyboardActivity
from .keyboard import get_activity as _key_get_activity
from .simulator import ActivitySimulator
from .simulator import get_activity as _sim_get_activity

__all__ = ["ActivitySimulator", "KeyboardActivity", "get_activity"]


def get_activity(source: str = "sim") -> float:
    """契约接口 M2：返回 0.0~1.0 的活跃度因子。

    source:
        "sim"（默认，模拟源，demo 主力）
        "keyboard" / "key" / "real"（真实键盘）
    """
    src = source.lower()
    if src in ("sim", "simulator", "simulate"):
        return _sim_get_activity()
    if src in ("keyboard", "key", "real"):
        return _key_get_activity()
    raise ValueError(f"未知信号源 {source!r}，可选 'sim' 或 'keyboard'")
