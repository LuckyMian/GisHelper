"""页面基类。

每个功能页继承 ``BasePage``，用类属性声明标题与副标题，
主窗口切换页面时自动读取，页面本身不必关心标题栏。
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


class BasePage(QWidget):
    title: str = "未命名页面"
    subtitle: str = ""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

    def on_show(self) -> None:
        """页面被切换到前台时调用，需要刷新数据时重写此方法。"""
