"""界面冒烟测试：用 offscreen 平台建窗口，不需要真实显示器。"""

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QListWidget, QStackedWidget
except ImportError:  # pragma: no cover - 未装 PySide6 时整类跳过
    QApplication = None  # type: ignore[assignment]


@unittest.skipIf(QApplication is None, "未安装 PySide6")
class MainWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from gismapshelper.gui.app import create_application

        cls.app = create_application([])

    def test_nav_matches_registered_pages(self) -> None:
        from gismapshelper.gui.main_window import MainWindow
        from gismapshelper.gui.pages import NAV_PAGES

        window = MainWindow()
        try:
            nav = window.findChild(QListWidget, "Nav")
            stack = window.findChild(QStackedWidget)
            self.assertIsNotNone(nav)
            self.assertIsNotNone(stack)
            self.assertEqual(nav.count(), len(NAV_PAGES))
            self.assertEqual(stack.count(), len(NAV_PAGES))
            self.assertEqual(nav.currentRow(), 0)
            self.assertEqual(stack.currentIndex(), 0)
        finally:
            window.close()

    def test_first_page_shows_title(self) -> None:
        from PySide6.QtWidgets import QLabel

        from gismapshelper.gui.main_window import MainWindow
        from gismapshelper.gui.pages import NAV_PAGES

        window = MainWindow()
        try:
            title = window.findChild(QLabel, "PageTitle")
            self.assertIsNotNone(title)
            self.assertEqual(title.text(), NAV_PAGES[0].title)
        finally:
            window.close()

    def test_tif_page_is_first(self) -> None:
        from gismapshelper.gui.pages import NAV_PAGES, TifMapPage

        self.assertIs(NAV_PAGES[0], TifMapPage)

    def test_palette_is_light_even_in_dark_windows(self) -> None:
        """Windows 深色模式下 Qt 默认调色板是深色的，必须被显式覆盖。

        否则下拉列表、提示框、对话框这些没写 QSS 的控件会变成黑底。
        """
        from PySide6.QtGui import QPalette

        from gismapshelper.gui import theme

        palette = self.app.palette()
        self.assertEqual(palette.color(QPalette.ColorRole.Base).name(), "#ffffff")
        self.assertEqual(palette.color(QPalette.ColorRole.Window).name(), theme.COLORS["content_bg"])
        self.assertEqual(
            palette.color(QPalette.ColorRole.Highlight).name(), theme.COLORS["accent"]
        )

    def test_basemap_popup_is_styled_light(self) -> None:
        from PySide6.QtGui import QPalette
        from PySide6.QtWidgets import QComboBox

        from gismapshelper.gui.pages import TifMapPage

        page = TifMapPage()
        try:
            combo = page.findChild(QComboBox)
            self.assertIsNotNone(combo)
            view = combo.view()
            self.assertEqual(view.objectName(), "ComboPopup")
            self.assertEqual(view.palette().color(QPalette.ColorRole.Base).name(), "#ffffff")
            self.assertGreater(combo.count(), 1)
        finally:
            page.deleteLater()


@unittest.skipIf(QApplication is None, "未安装 PySide6")
class TifMapPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from gismapshelper.gui.app import create_application

        cls.app = create_application([])

    def test_load_path_fills_info_and_rect(self) -> None:
        try:
            from test_raster import build_tiff
        except ImportError:  # pragma: no cover - 直接跑单个文件时
            self.skipTest("需要 tests/test_raster.py 里的 TIFF 构造器")

        from gismapshelper.gui.pages.tif_map import TifMapPage

        with tempfile.TemporaryDirectory() as tmp:
            path = build_tiff(Path(tmp) / "sample.tif", 600, 400, epsg=3857)
            page = TifMapPage()
            try:
                self.assertTrue(page.load_path(path))
                info = page.current_info
                self.assertIsNotNone(info)
                self.assertEqual(info.crs, "EPSG:3857")
                self.assertEqual((info.width, info.height), (600, 400))
                self.assertIsNotNone(page.map.bounds())
                self.assertTrue(page._copy_button.isEnabled())  # noqa: SLF001
            finally:
                page.deleteLater()

    def test_origin_and_slice_layers(self) -> None:
        try:
            from test_raster import build_tiff
        except ImportError:  # pragma: no cover
            self.skipTest("需要 tests/test_raster.py 里的 TIFF 构造器")

        from gismapshelper.gui.pages.tif_map import TifMapPage

        with tempfile.TemporaryDirectory() as tmp:
            path = build_tiff(Path(tmp) / "sample.tif", 600, 400, epsg=3857)
            page = TifMapPage()
            try:
                page._combo.setCurrentIndex(page._combo.findData("blank"))  # noqa: SLF001
                self.assertTrue(page.load_path(path))
                page.set_origin(116.35, 39.95)
                self.assertTrue(page.add_slice("distance", 1000))
                self.assertTrue(page.add_slice("pixels", 1024))

                layers = page.layers()
                self.assertEqual([entry.kind for entry in layers],
                                 ["tif", "origin", "slice", "slice"])
                self.assertAlmostEqual(layers[1].point[1], 39.95)
                self.assertEqual(len(page.map.overlays()), 4)

                # 距离方案：正方形四边相对原点正好 ±500 m
                from gismapshelper.core import frames

                bounds = page.layers()[2].bounds
                self.assertIsNone(bounds)  # 切片几何是实时算的，不缓存
                slice_bounds = page.map.overlays()[2].bounds
                self.assertIsNotNone(slice_bounds)
                offsets = frames.local_offsets(slice_bounds, 116.35, 39.95)
                self.assertAlmostEqual(offsets[0], -500.0, places=1)
                self.assertAlmostEqual(offsets[3], 500.0, places=1)
            finally:
                page.deleteLater()

    def test_gcj_basemap_shifts_overlays(self) -> None:
        from gismapshelper.gui.pages.tif_map import TifMapPage

        page = TifMapPage()
        try:
            index = page._combo.findData("gaode_vec")  # noqa: SLF001
            page._combo.setCurrentIndex(index)  # noqa: SLF001
            page.set_origin(116.35, 39.95)

            overlays = {overlay.key: overlay for overlay in page.map.overlays()}
            self.assertIn("origin", overlays)
            lon, lat = overlays["origin"].point
            self.assertNotAlmostEqual(lon, 116.35, places=4)
            self.assertLess(abs(lon - 116.35), 0.02)
        finally:
            page.deleteLater()

    def test_export_all_layers_to_one_geojson(self) -> None:
        try:
            from test_raster import build_tiff
        except ImportError:  # pragma: no cover
            self.skipTest("需要 tests/test_raster.py 里的 TIFF 构造器")

        from gismapshelper.core import geojson
        from gismapshelper.gui.pages.tif_map import TifMapPage

        with tempfile.TemporaryDirectory() as tmp:
            path = build_tiff(Path(tmp) / "sample.tif", 600, 400, epsg=3857)
            page = TifMapPage()
            try:
                self.assertTrue(page.load_path(path))
                page.set_origin(116.35, 39.95)
                self.assertTrue(page.add_slice("distance", 1000))
                self.assertTrue(page._export_button.isEnabled())  # noqa: SLF001

                target = Path(tmp) / "out" / "all.geojson"
                written = page.export_all_geojson(target)
                self.assertTrue(written.exists())

                data = json.loads(written.read_text(encoding="utf-8"))
                self.assertEqual(data["type"], "FeatureCollection")
                self.assertEqual(len(data["features"]), 3)
                self.assertEqual(
                    [feature["geometry"]["type"] for feature in data["features"]],
                    ["Polygon", "Point", "Polygon"],
                )
                ring = data["features"][0]["geometry"]["coordinates"][0]
                self.assertEqual(len(ring), 5)
                self.assertEqual(ring[0], ring[-1])
                self.assertEqual(data["features"][0]["properties"]["datum"], "WGS84")

                # GCJ-02 基准导出的结果应当整体偏移
                shifted = json.loads(
                    page.export_all_geojson(
                        Path(tmp) / "gcj.geojson", datum=geojson.DATUM_GCJ02
                    ).read_text(encoding="utf-8")
                )
                self.assertNotEqual(
                    shifted["features"][0]["geometry"]["coordinates"][0][0], ring[0]
                )
            finally:
                page.deleteLater()

    def test_export_without_layers_raises(self) -> None:
        from gismapshelper.gui.pages.tif_map import TifMapPage

        with tempfile.TemporaryDirectory() as tmp:
            page = TifMapPage()
            try:
                self.assertFalse(page._export_button.isEnabled())  # noqa: SLF001
                with self.assertRaises(ValueError):
                    page.export_all_geojson(Path(tmp) / "empty.geojson")
            finally:
                page.deleteLater()

    def test_export_start_dir_prefers_tif_folder(self) -> None:
        """默认保存目录要落在 TIF 旁边，而不是用户主目录根（沙箱里主目录不可写）。"""
        try:
            from test_raster import build_tiff
        except ImportError:  # pragma: no cover
            self.skipTest("需要 tests/test_raster.py 里的 TIFF 构造器")

        from gismapshelper.gui.pages.tif_map import TifMapPage

        with tempfile.TemporaryDirectory() as tmp:
            path = build_tiff(Path(tmp) / "sample.tif", 600, 400, epsg=3857)
            page = TifMapPage()
            try:
                self.assertTrue(page.load_path(path))
                self.assertEqual(page._export_start_dir(), path.parent)  # noqa: SLF001
            finally:
                page.deleteLater()

    def test_right_function_menu_switches_panels(self) -> None:
        """右栏：上面是功能菜单，下面的面板跟着切换；左栏只留图层与影像信息。"""
        from gismapshelper.gui.pages.tif_map import TifMapPage

        page = TifMapPage()
        try:
            names = [page._func_menu.item(i).text() for i in range(page._func_menu.count())]  # noqa: SLF001
            self.assertEqual(names, ["原点与切片", "切片 TIF", "裁剪 TIF", "导出 GeoJSON"])
            self.assertEqual(page._func_stack.count(), 4)  # noqa: SLF001
            for row in range(4):
                page._func_menu.setCurrentRow(row)  # noqa: SLF001
                self.assertEqual(page._func_stack.currentIndex(), row)  # noqa: SLF001

            tab_names = [page._tabs.tabText(i) for i in range(page._tabs.count())]  # noqa: SLF001
            self.assertEqual(tab_names, ["图层", "影像信息"])
        finally:
            page.deleteLater()

    def test_grid_panel_warns_when_image_not_square(self) -> None:
        """在 3857 下不是正方形的 TIF，切片面板要出提示。"""
        try:
            from test_raster import build_tiff
        except ImportError:  # pragma: no cover
            self.skipTest("需要 tests/test_raster.py 里的 TIFF 构造器")

        from gismapshelper.gui.pages.tif_map import TifMapPage

        with tempfile.TemporaryDirectory() as tmp:
            path = build_tiff(Path(tmp) / "wide.tif", 600, 400, epsg=3857)
            page = TifMapPage()
            try:
                self.assertTrue(page.load_path(path))
                self.assertIn("切成 3×3", page._grid_hint.text())  # noqa: SLF001
                self.assertIn("不是正方形", page._grid_warn.text())  # noqa: SLF001
                self.assertFalse(page._grid_warn.isHidden())  # noqa: SLF001

                page._grid_combo.setCurrentIndex(page._grid_combo.findData(9))  # noqa: SLF001
                self.assertIn("9×9", page._grid_hint.text())  # noqa: SLF001
            finally:
                page.deleteLater()

    def test_slice_anchor_nw(self) -> None:
        """参考点选左上时，原点应落在矩形左上角。"""
        from gismapshelper.gui.pages.tif_map import TifMapPage

        page = TifMapPage()
        try:
            page.set_origin(116.3, 40.0)
            page._anchor_combo.setCurrentIndex(  # noqa: SLF001
                page._anchor_combo.findData("nw")  # noqa: SLF001
            )
            self.assertTrue(page.add_slice("distance", 1000))

            entry = page.layers()[-1]
            self.assertIn("左上", entry.title)
            west, south, east, north = page._slice_bounds(entry)  # noqa: SLF001
            self.assertAlmostEqual(west, 116.3, places=6)
            self.assertAlmostEqual(north, 40.0, places=6)
            self.assertGreater(east, west)
            self.assertLess(south, north)
        finally:
            page.deleteLater()

    def test_crop_button_follows_selection(self) -> None:
        """裁剪按钮只在选中矩形图层（TIF 本身 / 切片）且装了 rasterio 时可用。"""
        try:
            from test_raster import build_tiff
        except ImportError:  # pragma: no cover
            self.skipTest("需要 tests/test_raster.py 里的 TIFF 构造器")

        from gismapshelper.core import crop
        from gismapshelper.gui.pages.tif_map import TifMapPage

        if not crop.RASTERIO_AVAILABLE:
            self.skipTest("未安装 rasterio")

        with tempfile.TemporaryDirectory() as tmp:
            path = build_tiff(Path(tmp) / "sample.tif", 600, 400, epsg=3857)
            page = TifMapPage()
            try:
                self.assertFalse(page._crop_button.isEnabled())  # noqa: SLF001
                self.assertTrue(page.load_path(path))
                self.assertTrue(page._crop_button.isEnabled())  # noqa: SLF001

                page.set_origin(116.35, 39.95)
                self.assertFalse(page._crop_button.isEnabled())  # noqa: SLF001

                self.assertTrue(page.add_slice("distance", 1000))
                self.assertTrue(page._crop_button.isEnabled())  # noqa: SLF001
            finally:
                page.deleteLater()


if __name__ == "__main__":
    unittest.main()
