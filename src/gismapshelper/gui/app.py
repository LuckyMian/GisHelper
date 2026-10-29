"""GUI 启动逻辑。"""

from __future__ import annotations

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .. import __version__
from . import theme
from .main_window import MainWindow


def create_application(argv: list[str] | None = None) -> QApplication:
    """创建（或复用）QApplication，并统一设置字体与样式。"""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("GisMapsHelper")
    app.setApplicationDisplayName("GisMapsHelper")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("GisMapsHelper")
    app.setStyle("Fusion")
    app.setPalette(theme.build_palette())
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setStyleSheet(theme.STYLESHEET)
    return app


def run_gui() -> int:
    app = create_application()
    window = MainWindow()
    window.show()
    return app.exec()
