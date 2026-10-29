"""页面注册表。

新增页面时只需写一个 ``BasePage`` 子类，再把它加进 ``NAV_PAGES``。
"""

from __future__ import annotations

from .base import BasePage
from .home import HomePage
from .tif_map import TifMapPage

__all__ = ["BasePage", "HomePage", "TifMapPage", "NAV_PAGES"]

# 顺序即左侧导航顺序
NAV_PAGES: list[type[BasePage]] = [TifMapPage, HomePage]
