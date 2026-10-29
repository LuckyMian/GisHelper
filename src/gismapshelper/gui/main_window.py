"""主窗口：左侧导航 + 右侧页面容器。"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from .pages import NAV_PAGES, BasePage


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("GisMapsHelper — GIS 地图工具")
        # 三栏最小宽度合计约 994 px（350 + 320 + 300 + 分隔条），1080×680 会把右栏挤出窗口；
        # 按屏幕可用区域取大尺寸，上限对齐 docs/layout.png 的 1440×900。
        screen = self.screen()
        available = screen.availableGeometry() if screen is not None else None
        if available is None:
            self.resize(1440, 900)
        else:
            self.resize(min(1440, available.width() - 80), min(900, available.height() - 80))
        self.setMinimumSize(920, 560)

        self._pages: list[BasePage] = []
        self._build()
        self._populate_pages()

        self._nav.setCurrentRow(0)
        self.statusBar().showMessage("就绪")

    # ------------------------------------------------------------------ 布局
    def _build(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_content(), 1)
        self.setCentralWidget(central)

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(190)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel("GisMapsHelper")
        title.setObjectName("SidebarTitle")

        self._nav = QListWidget()
        self._nav.setObjectName("Nav")
        self._nav.setIconSize(QSize(18, 18))

        version = QLabel(f"v{__version__} · PySide6")
        version.setObjectName("SidebarVersion")

        layout.addWidget(title)
        layout.addWidget(self._nav, 1)
        layout.addWidget(version)
        return sidebar

    def _build_content(self) -> QWidget:
        content = QWidget()
        content.setObjectName("Content")

        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 12)
        layout.setSpacing(2)

        self._page_title = QLabel()
        self._page_title.setObjectName("PageTitle")
        self._page_subtitle = QLabel()
        self._page_subtitle.setObjectName("PageSubtitle")

        self._stack = QStackedWidget()

        layout.addWidget(self._page_title)
        layout.addWidget(self._page_subtitle)
        layout.addWidget(self._stack, 1)
        return content

    # ------------------------------------------------------------------ 页面
    def _populate_pages(self) -> None:
        for page_cls in NAV_PAGES:
            page = page_cls()
            item = QListWidgetItem(page.title)
            item.setSizeHint(QSize(0, 40))
            self._nav.addItem(item)
            self._stack.addWidget(page)
            self._pages.append(page)

        self._nav.currentRowChanged.connect(self._on_nav_changed)

    def _on_nav_changed(self, row: int) -> None:
        if not 0 <= row < len(self._pages):
            return
        page = self._pages[row]
        self._stack.setCurrentIndex(row)
        self._page_title.setText(page.title)
        self._page_subtitle.setText(page.subtitle)
        page.on_show()
