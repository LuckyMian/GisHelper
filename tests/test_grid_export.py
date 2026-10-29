"""网格导出：N×N 子影像 + GeoJSON + DXF，原点位置可换。"""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from gismapshelper.core import frames, grid_export
from gismapshelper.core.gdal_env import configure as configure_gdal

# 本机 OSGeo4W 的旧 PROJ 会顶掉 rasterio 自带的库，必须先隔离
configure_gdal()

try:
    import rasterio
    from rasterio.transform import from_origin

    HAVE_RASTERIO = True
except ImportError:  # pragma: no cover
    HAVE_RASTERIO = False

try:
    import ezdxf

    HAVE_EZDXF = True
except ImportError:  # pragma: no cover
    HAVE_EZDXF = False


def make_raster(path: Path, width: int = 300, height: int = 300, res: float = 10.0) -> Path:
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
        dst.write(np.zeros((1, height, width), dtype="uint8"))
    return path


@unittest.skipUnless(HAVE_RASTERIO and HAVE_EZDXF, "需要 rasterio 与 ezdxf")
class ExportGridTest(unittest.TestCase):
    def test_3x3_writes_tiles_geojson_and_dxf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = make_raster(Path(tmp) / "area.tif")
            result = grid_export.export_grid(source, Path(tmp) / "out", 3)

            self.assertEqual(len(result.tiles), 9)
            for tile in result.tiles:
                self.assertTrue(tile.exists())
                with rasterio.open(tile) as ds:
                    self.assertEqual((ds.width, ds.height), (100, 100))
                    self.assertEqual(str(ds.crs), "EPSG:3857")

            data = json.loads(result.geojson_path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["features"]), 9)
            self.assertEqual(data["features"][0]["geometry"]["type"], "Polygon")
            self.assertEqual(data["features"][0]["properties"]["grid"], "3x3")

            doc = ezdxf.readfile(result.dxf_path)
            polylines = list(doc.modelspace().query("LWPOLYLINE"))
            self.assertEqual(len(polylines), 9)
            self.assertIsNone(result.dwg_path)   # 本机没装 ODA，只出 DXF

    def test_center_origin_is_symmetric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = make_raster(Path(tmp) / "area.tif")
            result = grid_export.export_grid(source, Path(tmp) / "out", 3, origin_anchor="center")

            doc = ezdxf.readfile(result.dxf_path)
            points = [p for polyline in doc.modelspace().query("LWPOLYLINE")
                      for p in polyline.get_points("xy")]
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            self.assertAlmostEqual(min(xs) + max(xs), 0.0, places=3)
            self.assertAlmostEqual(min(ys) + max(ys), 0.0, places=3)

    def test_nw_origin_puts_image_in_fourth_quadrant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = make_raster(Path(tmp) / "area.tif")
            result = grid_export.export_grid(source, Path(tmp) / "out", 2, origin_anchor="nw")

            doc = ezdxf.readfile(result.dxf_path)
            points = [p for polyline in doc.modelspace().query("LWPOLYLINE")
                      for p in polyline.get_points("xy")]
            self.assertGreaterEqual(min(p[0] for p in points), -1e-6)
            self.assertLessEqual(max(p[1] for p in points), 1e-6)
            self.assertAlmostEqual(min(p[0] for p in points), 0.0, places=3)
            self.assertAlmostEqual(max(p[1] for p in points), 0.0, places=3)

    def test_progress_reaches_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = make_raster(Path(tmp) / "area.tif", 120, 120)
            seen: list[float] = []
            grid_export.export_grid(
                source, Path(tmp) / "out", 2, progress=lambda f, _m: seen.append(f)
            )
            self.assertTrue(seen)
            self.assertEqual(seen[-1], 1.0)

    def test_rejects_unknown_grid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = make_raster(Path(tmp) / "area.tif")
            with self.assertRaises(grid_export.GridExportError):
                grid_export.export_grid(source, Path(tmp) / "out", 5)

    def test_rejects_unknown_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = make_raster(Path(tmp) / "area.tif")
            with self.assertRaises(grid_export.GridExportError):
                grid_export.export_grid(
                    source, Path(tmp) / "out", 2, origin_anchor="middle"
                )

    def test_4326_source_is_reprojected_before_slicing(self) -> None:
        """经纬度影像先投影到 3857 再切，块才是米制方形（影像本身要接近正方形）。"""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "geo.tif"
            # 391 px × 0.0001° ≈ 3.34 km，300 px ≈ 3.33 km —— 地面上接近正方形
            with rasterio.open(
                source, "w", driver="GTiff", width=391, height=300, count=1,
                dtype="uint8", crs="EPSG:4326",
                transform=from_origin(116.15, 40.20, 0.0001, 0.0001),
            ) as dst:
                dst.write(np.zeros((1, 300, 391), dtype="uint8"))

            result = grid_export.export_grid(source, Path(tmp) / "out", 3)
            self.assertTrue(result.reprojected)
            self.assertEqual(result.source_crs, "EPSG:4326")

            with rasterio.open(result.tiles[0]) as ds:
                self.assertEqual(str(ds.crs), "EPSG:3857")

            # 每块在 3857 下近似正方形
            for cell in result.cells:
                west, south, east, north = cell.bounds_wgs84
                from gismapshelper.core import geo

                x0, y0 = geo.lonlat_to_merc(west, south)
                x1, y1 = geo.lonlat_to_merc(east, north)
                ratio = abs(x1 - x0) / abs(y1 - y0)
                self.assertAlmostEqual(ratio, 1.0, delta=0.03)

    def test_4326_without_reproject_keeps_degrees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "geo.tif"
            with rasterio.open(
                source, "w", driver="GTiff", width=300, height=300, count=1,
                dtype="uint8", crs="EPSG:4326",
                transform=from_origin(116.15, 40.20, 0.0001, 0.0001),
            ) as dst:
                dst.write(np.zeros((1, 300, 300), dtype="uint8"))

            result = grid_export.export_grid(
                source, Path(tmp) / "out", 3, auto_reproject=False
            )
            self.assertFalse(result.reprojected)
            with rasterio.open(result.tiles[0]) as ds:
                self.assertEqual(str(ds.crs), "EPSG:4326")


class AnchorTest(unittest.TestCase):
    def test_square_bounds_anchor(self) -> None:
        lon, lat, edge = 116.3, 40.0, 1000.0
        center = frames.square_bounds(lon, lat, edge)
        self.assertAlmostEqual((center[0] + center[2]) / 2, lon, places=6)
        self.assertAlmostEqual((center[1] + center[3]) / 2, lat, places=6)

        nw = frames.square_bounds(lon, lat, edge, "nw")
        self.assertAlmostEqual(nw[0], lon, places=6)
        self.assertAlmostEqual(nw[3], lat, places=6)

        se = frames.square_bounds(lon, lat, edge, "se")
        self.assertAlmostEqual(se[2], lon, places=6)
        self.assertAlmostEqual(se[1], lat, places=6)

    def test_anchor_point(self) -> None:
        bounds = (0.0, 10.0, 20.0, 30.0)
        self.assertEqual(frames.anchor_point(bounds), (10.0, 20.0))
        self.assertEqual(frames.anchor_point(bounds, "nw"), (0.0, 30.0))
        self.assertEqual(frames.anchor_point(bounds, "ne"), (20.0, 30.0))
        self.assertEqual(frames.anchor_point(bounds, "sw"), (0.0, 10.0))
        self.assertEqual(frames.anchor_point(bounds, "se"), (20.0, 10.0))


if __name__ == "__main__":
    unittest.main()
