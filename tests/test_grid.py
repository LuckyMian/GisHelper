"""网格切分：像素窗口连续性、WGS-84 范围、正方形判定。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from gismapshelper.core import grid
from gismapshelper.core.gdal_env import configure as configure_gdal
from gismapshelper.core.raster import read_raster_info

# 本机 OSGeo4W 的旧 PROJ 会顶掉 rasterio 自带的库，必须先隔离
configure_gdal()

try:
    import rasterio
    from rasterio.transform import from_origin

    HAVE_RASTERIO = True
except ImportError:  # pragma: no cover
    HAVE_RASTERIO = False


def make_raster(path: Path, width: int, height: int, *, res: float = 10.0) -> Path:
    """北京附近、Web 墨卡托的小影像。"""
    data = np.zeros((1, height, width), dtype="uint8")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="uint8",
        crs="EPSG:3857",
        transform=from_origin(12930000.0, 4900000.0, res, res),
    ) as dst:
        dst.write(data)
    return path


@unittest.skipUnless(HAVE_RASTERIO, "需要 rasterio")
class GridCellsTest(unittest.TestCase):
    def test_cells_cover_image_without_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            info = read_raster_info(make_raster(Path(tmp) / "a.tif", 300, 300))

            cells = grid.cells(info, 3)
            self.assertEqual(len(cells), 9)
            self.assertEqual([(c.row, c.col) for c in cells],
                             [(r, c) for r in range(3) for c in range(3)])

            for cell in cells:
                self.assertEqual(cell.window[2:], (100, 100))

            # 相邻格子首尾相接、总覆盖整幅
            self.assertEqual(sum(c.window[2] for c in cells if c.row == 0), 300)
            self.assertEqual(sum(c.window[3] for c in cells if c.col == 0), 300)
            self.assertEqual(cells[0].window[:2], (0, 0))
            self.assertEqual(cells[-1].window[0] + cells[-1].window[2], 300)
            self.assertEqual(cells[-1].window[1] + cells[-1].window[3], 300)

    def test_cell_bounds_inside_image_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            info = read_raster_info(make_raster(Path(tmp) / "a.tif", 300, 300))
            cells = grid.cells(info, 3)
            image = info.lonlat

            for cell in cells:
                west, south, east, north = cell.bounds_wgs84
                self.assertGreaterEqual(west, image.west - 1e-9)
                self.assertLessEqual(east, image.east + 1e-9)
                self.assertGreaterEqual(south, image.south - 1e-9)
                self.assertLessEqual(north, image.north + 1e-9)
                self.assertLess(west, east)
                self.assertLess(south, north)

    def test_odd_size_splits_unevenly_but_covers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            info = read_raster_info(make_raster(Path(tmp) / "a.tif", 301, 302))
            cells = grid.cells(info, 3)
            self.assertEqual(sum(c.window[2] for c in cells if c.row == 0), 301)
            self.assertEqual(sum(c.window[3] for c in cells if c.col == 0), 302)

    def test_rejects_unsupported_grid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            info = read_raster_info(make_raster(Path(tmp) / "a.tif", 100, 100))
            with self.assertRaises(grid.GridError):
                grid.cells(info, 0)

    def test_squareness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            square = read_raster_info(make_raster(Path(tmp) / "s.tif", 300, 300))
            is_square, width, height = grid.squareness(square)
            self.assertTrue(is_square)
            self.assertAlmostEqual(width / height, 1.0, places=2)

            wide = read_raster_info(make_raster(Path(tmp) / "w.tif", 300, 200))
            is_square, width, height = grid.squareness(wide)
            self.assertFalse(is_square)
            self.assertGreater(width, height)


if __name__ == "__main__":
    unittest.main()
