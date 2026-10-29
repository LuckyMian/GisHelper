"""地图控件的基础行为（offscreen，不联网）。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None  # type: ignore[assignment]
    QTest = None  # type: ignore[assignment]
    QPoint = None  # type: ignore[assignment]
    Qt = None  # type: ignore[assignment]


@unittest.skipIf(QApplication is None, "未安装 PySide6")
class TileMapWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from gismapshelper.gui.app import create_application

        cls.app = create_application([])

    def setUp(self) -> None:
        from gismapshelper.core.basemaps import get_basemap
        from gismapshelper.gui.widgets.tilemap import TileMapWidget

        self.widget = TileMapWidget()
        self.widget.set_basemap(get_basemap("blank"))
        self.widget.resize(800, 600)
        self.widget.show()

    def tearDown(self) -> None:
        self.widget.close()
        self.widget.deleteLater()

    def test_set_view(self) -> None:
        self.widget.set_view(116.4, 39.9, 10)
        self.assertEqual(self.widget.zoom(), 10)
        self.assertAlmostEqual(self.widget.center()[0], 116.4, places=9)
        self.assertAlmostEqual(self.widget.center()[1], 39.9, places=9)

    def test_zoom_is_clamped_to_basemap(self) -> None:
        self.widget.set_view(0.0, 0.0, 99)
        self.assertEqual(self.widget.zoom(), self.widget.basemap.max_zoom)

    def test_latitude_is_clamped(self) -> None:
        self.widget.set_view(0.0, 89.9, 4)
        self.assertLess(self.widget.center()[1], 86.0)

    def test_fit_bounds_centers_and_picks_zoom(self) -> None:
        bounds = (116.0, 39.0, 117.0, 40.0)
        self.widget.fit_bounds(bounds)
        lon, lat = self.widget.center()
        self.assertAlmostEqual(lon, 116.5, places=6)
        self.assertAlmostEqual(lat, 39.5, places=6)
        self.assertGreaterEqual(self.widget.zoom(), 3)
        self.assertLessEqual(self.widget.zoom(), 14)

    def test_rect_roundtrip(self) -> None:
        bounds = (116.0, 39.0, 117.0, 40.0)
        self.widget.set_rect(bounds)
        self.assertEqual(self.widget.bounds(), bounds)
        self.widget.clear_rect()
        self.assertIsNone(self.widget.bounds())

    def test_hint(self) -> None:
        self.widget.set_hint("测试提示")
        self.assertEqual(self.widget.current_hint, "测试提示")

    def test_blank_basemap_makes_no_requests(self) -> None:
        self.assertEqual(len(self.widget._active), 0)  # noqa: SLF001 - 测试内部状态

    def test_overlays_store_and_visibility(self) -> None:
        from gismapshelper.gui.widgets.tilemap import MapOverlay

        overlays = [
            MapOverlay("a", "rect", label="A", bounds=(116.0, 39.0, 116.1, 39.1)),
            MapOverlay("b", "rect", label="B", bounds=(116.2, 39.2, 116.3, 39.3), visible=False),
            MapOverlay("o", "origin", label="原点", point=(116.15, 39.15)),
        ]
        self.widget.set_overlays(overlays)
        self.assertEqual(len(self.widget.overlays()), 3)
        self.assertFalse(self.widget.overlays()[1].visible)
        self.assertEqual(self.widget.bounds(), (116.0, 39.0, 116.1, 39.1))

    def test_origin_pick_emits_on_click(self) -> None:
        captured: list[tuple[float, float]] = []
        self.widget.origin_picked.connect(lambda lon, lat: captured.append((lon, lat)))
        self.widget.set_view(116.4, 39.9, 10)

        self.widget.start_origin_pick()
        self.assertTrue(self.widget.is_picking_origin())
        QTest.mouseClick(
            self.widget, Qt.MouseButton.LeftButton, pos=QPoint(400, 300)
        )

        self.assertFalse(self.widget.is_picking_origin())
        self.assertEqual(len(captured), 1)
        lon, lat = captured[0]
        self.assertAlmostEqual(lon, 116.4, places=3)
        self.assertAlmostEqual(lat, 39.9, places=3)

    def test_origin_pick_cancel_with_escape(self) -> None:
        cancelled: list[bool] = []
        self.widget.pick_cancelled.connect(lambda: cancelled.append(True))
        self.widget.start_origin_pick()
        QTest.keyClick(self.widget, Qt.Key.Key_Escape)

        self.assertFalse(self.widget.is_picking_origin())
        self.assertEqual(cancelled, [True])


if __name__ == "__main__":
    unittest.main()
