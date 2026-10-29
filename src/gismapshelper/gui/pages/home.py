"""首页：显示运行环境，确认窗口、主题、页面容器都工作正常。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QVBoxLayout, QWidget

from ...app import environment_rows
from .base import BasePage


class HomePage(BasePage):
    title = "首页"
    subtitle = "运行环境与项目概况"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(14)

        layout.addWidget(self._build_env_card())

        tip = QLabel("界面骨架已就绪：左侧导航 + 页面容器。后续每个功能做成一个独立页面即可。")
        tip.setObjectName("Tip")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        layout.addStretch(1)

    def _build_env_card(self) -> QGroupBox:
        card = QGroupBox("运行环境")
        form = QFormLayout(card)
        form.setContentsMargins(16, 18, 16, 16)
        form.setHorizontalSpacing(28)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        for key, value in environment_rows():
            key_label = QLabel(key)
            key_label.setObjectName("EnvKey")
            value_label = QLabel(value)
            value_label.setObjectName("EnvValue")
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value_label.setWordWrap(True)
            form.addRow(key_label, value_label)
        return card
