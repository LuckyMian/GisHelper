"""界面配色与样式表。

所有颜色集中在这里，后续换主题只改这一处；
控件通过 objectName 匹配，布局代码里不写颜色。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

COLORS = {
    "sidebar_bg": "#1f2937",
    "sidebar_text": "#cbd5e1",
    "sidebar_hover": "#2b3646",
    "accent": "#2563eb",
    "content_bg": "#f7f8fa",
    "card_bg": "#ffffff",
    "border": "#e5e7eb",
    "title": "#111827",
    "text": "#374151",
    "muted": "#6b7280",
}


def build_palette() -> QPalette:
    """浅色调色板。

    Windows 深色模式下 Qt 默认调色板是深色的，凡是没被 QSS 覆盖的控件
    （下拉列表、提示框、对话框、滚动条）都会变黑，与浅色界面撕裂。
    这里显式给一套浅色调色板，让两者一致。
    """
    window = QColor(COLORS["content_bg"])
    base = QColor(COLORS["card_bg"])
    text = QColor(COLORS["title"])
    accent = QColor(COLORS["accent"])
    disabled = QColor("#9aa3af")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f8fafc"))
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, QColor("#f3f4f6"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#dc2626"))
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, base)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Link, accent)
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLORS["muted"]))
    palette.setColor(QPalette.ColorRole.Light, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Midlight, QColor(COLORS["border"]))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#d1d5db"))
    palette.setColor(QPalette.ColorRole.Dark, QColor("#9ca3af"))
    palette.setColor(QPalette.ColorRole.Shadow, QColor("#6b7280"))

    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor("#f9fafb"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor("#f3f4f6"))
    return palette

STYLESHEET = f"""
QWidget {{
    color: {COLORS["text"]};
    font-size: 13px;
}}

QWidget#Content {{
    background: {COLORS["content_bg"]};
}}

/* ---------- 侧边导航 ---------- */
QFrame#Sidebar {{
    background: {COLORS["sidebar_bg"]};
}}

QLabel#SidebarTitle {{
    color: #ffffff;
    font-size: 16px;
    font-weight: 600;
    padding: 20px 16px 10px 16px;
}}

QLabel#SidebarVersion {{
    color: #8b96a5;
    font-size: 11px;
    padding: 10px 16px 16px 16px;
}}

QListWidget#Nav {{
    background: transparent;
    border: none;
    outline: none;
    padding: 4px 10px;
}}

QListWidget#Nav::item {{
    color: {COLORS["sidebar_text"]};
    padding: 10px 12px;
    border-radius: 6px;
    margin: 2px 0;
}}

QListWidget#Nav::item:hover {{
    background: {COLORS["sidebar_hover"]};
    color: #ffffff;
}}

QListWidget#Nav::item:selected {{
    background: {COLORS["accent"]};
    color: #ffffff;
}}

/* ---------- 内容区标题 ---------- */
QLabel#PageTitle {{
    font-size: 20px;
    font-weight: 600;
    color: {COLORS["title"]};
}}

QLabel#PageSubtitle {{
    font-size: 12px;
    color: {COLORS["muted"]};
    padding-bottom: 6px;
}}

/* ---------- 卡片 ---------- */
QGroupBox {{
    background: {COLORS["card_bg"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    margin-top: 12px;
    padding: 18px 16px 16px 16px;
    font-weight: 600;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 4px;
    color: {COLORS["title"]};
}}

QLabel#EnvKey {{
    color: {COLORS["muted"]};
    font-weight: 400;
}}

QLabel#EnvValue {{
    color: {COLORS["title"]};
    font-weight: 400;
}}

QLabel#Tip {{
    color: {COLORS["muted"]};
    font-weight: 400;
    background: #eef2ff;
    border: 1px solid #dbe3ff;
    border-radius: 8px;
    padding: 12px 14px;
}}

/* ---------- 输入框与按钮 ---------- */
QLineEdit, QComboBox {{
    background: #ffffff;
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 6px 9px;
    color: {COLORS["title"]};
}}

QLineEdit:focus, QComboBox:focus {{
    border-color: {COLORS["accent"]};
}}

QLineEdit#PathEdit {{
    padding: 7px 10px;
    color: {COLORS["muted"]};
}}

QPushButton {{
    background: #ffffff;
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 7px 14px;
    color: {COLORS["text"]};
}}

QPushButton:hover {{
    border-color: {COLORS["accent"]};
    color: {COLORS["accent"]};
}}

QPushButton:disabled {{
    color: #9aa3af;
    background: #f3f4f6;
}}

QPushButton#Primary {{
    background: {COLORS["accent"]};
    border-color: {COLORS["accent"]};
    color: #ffffff;
}}

QPushButton#Primary:hover {{
    background: #1d4ed8;
    color: #ffffff;
}}

/* ---------- 小字提示 / 警告 ---------- */
QLabel#SectionHint {{
    color: {COLORS["muted"]};
    font-size: 12px;
    font-weight: 400;
}}

QLabel#WarnTip {{
    background: #fff7ed;
    border: 1px solid #fed7aa;
    border-radius: 6px;
    color: #9a3412;
    font-weight: 400;
    padding: 8px 10px;
}}

QLabel#InfoPlaceholder {{
    color: {COLORS["muted"]};
    font-weight: 400;
    padding: 6px 2px;
}}

/* ---------- 地图 ---------- */
QToolButton#MapButton {{
    background: rgba(255, 255, 255, 0.94);
    border: 1px solid #d7dce3;
    border-radius: 6px;
    color: #374151;
    font-size: 16px;
    font-weight: 600;
}}

QToolButton#MapButton:hover {{
    border-color: {COLORS["accent"]};
    color: {COLORS["accent"]};
}}

QScrollArea#InfoScroll {{
    background: transparent;
}}

QScrollArea#InfoScroll > QWidget > QWidget {{
    background: transparent;
}}

QSplitter::handle {{
    background: transparent;
}}

/* ---------- 右侧功能区 ---------- */
QLabel#FuncTitle {{
    color: {COLORS["muted"]};
    font-size: 12px;
    font-weight: 600;
    padding: 2px 2px 0 2px;
}}

QListWidget#FuncMenu {{
    background: {COLORS["card_bg"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    padding: 4px;
    outline: none;
}}

QListWidget#FuncMenu::item {{
    color: {COLORS["text"]};
    padding: 9px 12px;
    border-radius: 6px;
}}

QListWidget#FuncMenu::item:hover {{
    background: #eef2ff;
    color: {COLORS["title"]};
}}

QListWidget#FuncMenu::item:selected {{
    background: {COLORS["accent"]};
    color: #ffffff;
}}

/* ---------- 表格 ---------- */
QTableWidget {{
    background: #ffffff;
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    gridline-color: #eef1f5;
}}

QHeaderView::section {{
    background: #f8fafc;
    border: none;
    border-bottom: 1px solid {COLORS["border"]};
    padding: 6px;
    font-weight: 600;
}}

/* ---------- 状态栏 ---------- */
QStatusBar {{
    background: {COLORS["card_bg"]};
    color: {COLORS["muted"]};
    border-top: 1px solid {COLORS["border"]};
    padding-left: 12px;
}}

/* ---------- 下拉列表（弹出层）---------- */
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 22px;
    border: none;
}}

/* 弹出列表挂在独立的顶层容器里，不是 QComboBox 的子控件，
   所以只能用 objectName 匹配（见 gui/widgets/combo.py 的 ComboPopup）。
   这里只改底色与边框，不写 ::item —— 一旦写了 item 规则，
   Qt 就不再画选中高亮，当前行会变成只剩一个细框。 */
QAbstractItemView#ComboPopup {{
    background: #ffffff;
    border: 1px solid {COLORS["border"]};
    color: {COLORS["title"]};
    outline: 0;
    selection-background-color: {COLORS["accent"]};
    selection-color: #ffffff;
}}

/* ---------- 悬停提示 ---------- */
QToolTip {{
    background: #ffffff;
    color: {COLORS["title"]};
    border: 1px solid {COLORS["border"]};
    padding: 5px 8px;
}}

/* ---------- 表格选中行 ---------- */
QTableWidget::item:selected, QTableView::item:selected {{
    background: #dbeafe;
    color: {COLORS["title"]};
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: #cfd6df;
    border-radius: 5px;
    min-height: 28px;
}}

QScrollBar::handle:vertical:hover {{
    background: #b4bdc9;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background: #cfd6df;
    border-radius: 5px;
    min-width: 28px;
}}

QScrollBar::handle:horizontal:hover {{
    background: #b4bdc9;
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
    border: none;
    background: none;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

/* ---------- 左侧标签页（图层 / 影像信息）---------- */
QTabWidget#PanelTabs::pane {{
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    background: {COLORS["card_bg"]};
    top: -1px;
}}

QTabWidget#PanelTabs QTabBar::tab {{
    background: transparent;
    color: {COLORS["muted"]};
    padding: 6px 16px;
    margin-right: 4px;
    border: 1px solid transparent;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}

QTabWidget#PanelTabs QTabBar::tab:selected {{
    background: {COLORS["card_bg"]};
    color: {COLORS["accent"]};
    border-color: {COLORS["border"]};
    border-bottom-color: {COLORS["card_bg"]};
}}

QTabWidget#PanelTabs QTabBar::tab:hover {{
    color: {COLORS["accent"]};
}}

/* ---------- 原点数值 ---------- */
QLabel#OriginValue {{
    color: {COLORS["title"]};
    font-weight: 600;
}}

/* ---------- 图层列表 ---------- */
QListWidget#LayerList {{
    background: #ffffff;
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 4px;
    outline: 0;
}}

QListWidget#LayerList::item {{
    padding: 6px 4px;
    border-radius: 4px;
    color: {COLORS["text"]};
}}

QListWidget#LayerList::item:selected {{
    background: #e8efff;
    color: {COLORS["title"]};
}}

QListWidget#LayerList::item:hover {{
    background: #f5f8ff;
}}
"""
