"""统一样式的下拉框。

两件事：

1. **从下沿往下展开**。Qt 默认让当前项对准输入框、于是列表会向上顶出去、
   把输入框盖住；这里改成固定贴输入框下沿往下弹。
2. **统一弹层样式**。列表挂在独立的顶层容器里，QSS 的后代选择器匹配不到，
   所以用 objectName ``ComboPopup`` 定位；当前项的高亮由委托自己画。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from .. import theme

POPUP_OBJECT_NAME = "ComboPopup"

_ROW_HEIGHT = 28
_TEXT_MARGIN = 10
_SEPARATOR_HEIGHT = 9


class ComboItemDelegate(QStyledItemDelegate):
    """弹层列表的项：统一行高，并自己画当前项（否则只剩一个细框）。"""

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt 命名
        if index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator":
            return QSize(option.rect.width(), _SEPARATOR_HEIGHT)
        hint = super().sizeHint(option, index)
        return QSize(hint.width() + _TEXT_MARGIN * 2, _ROW_HEIGHT)

    def paint(self, painter, option, index) -> None:
        view_option = QStyleOptionViewItem(option)
        self.initStyleOption(view_option, index)

        selected = bool(view_option.state & QStyle.StateFlag.State_Selected) or bool(
            view_option.state & QStyle.StateFlag.State_HasFocus
        )
        if selected:
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.COLORS["accent"]))
            painter.drawRect(view_option.rect.adjusted(1, 1, -1, -1))
            painter.restore()
            view_option.state &= ~(
                QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus
            )
            view_option.palette.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))

        # 文本左右留白，避免贴着边框
        padded = QStyleOptionViewItem(view_option)
        padded.rect = view_option.rect.adjusted(_TEXT_MARGIN, 0, -_TEXT_MARGIN, 0)
        super().paint(painter, padded, index)


class DownwardComboBox(QComboBox):
    """弹出列表固定从输入框下沿往下展开。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        view = self.view()
        view.setObjectName(POPUP_OBJECT_NAME)
        view.setItemDelegate(ComboItemDelegate(view))

    def showPopup(self) -> None:  # noqa: N802 - Qt 命名
        super().showPopup()

        popup = self.view().window()
        if popup is None:
            return

        geometry = popup.frameGeometry()
        # 宽度至少与输入框一致，且不窄于最长条目（否则文字会被截断）
        content_width = self.view().sizeHintForColumn(0) + 10
        width = max(geometry.width(), self.width(), content_width)
        height = geometry.height()

        anchor = self.mapToGlobal(QPoint(0, self.height()))
        x, y = anchor.x(), anchor.y()

        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            # 底部放不下就整体上移，但始终保持「从上往下」的顺序
            y = min(y, available.bottom() - height)
            y = max(y, available.top())
            x = max(x, available.left())
            x = min(x, available.right() - width)

        popup.setGeometry(x, y, width, height)
