# -*- coding: utf-8 -*-
"""M5 桌宠 UI 包（袖里乾坤 demo）。

对外只暴露契约接口：PetWindow(world, economy).run()
"""

from .pet_window import PetWindow  # noqa: F401

__all__ = ["PetWindow"]
