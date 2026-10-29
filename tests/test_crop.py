"""裁剪：输出保留坐标系与地理参考，像素与源影像对齐。"""

import tempfile
import unittest
from pathlib import Path

from gismapshelper.core import crop, frames
from gismapshelper.core.gdal_env import configure as configure_gdal

# 本机 OSGeo4W 的旧 PROJ 会顶掉 rasterio 自带的库，必须先隔离
configure_gdal()

try:
    import numpy as np
    import rasterio
    from rasterio.transform import array_bounds, from_origin
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window

    HAVE_RASTERIO = True
except ImportError:  # pragma: no cover
    HAVE_RASTERIO = False


@unittest.skipUnless(HAVE_RASTERIO, "需要 rasterio")
class CropTest(unittest.TestCase):
    width = 200
    height = 100

    def _make_raster(self, path: Path, *, crs: str | None = "EPSG:3857") -> None:
        transform = from_origin(0.0, 1000.0, 10.0, 10.0)   # 10 m/px，从 (0,1000) 起
        data = np.arange(self.width * self.height, dtype="uint16").reshape(
            1, self.height, self.width
        )
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=self.width,
            height=self.height,
            count=1,
            dtype="uint16",
            crs=crs,
            transform=transform,
        ) as dst:
            dst.write(data)

    @staticmethod
    def _wgs84_of(window: Window, transform) -> tuple[float, float, float, float]:
        bounds = array_bounds(
            int(window.height), int(window.width), rasterio.windows.transform(window, transform)
        )
        return transform_bounds("EPSG:3857", "EPSG:4326", *bounds, densify_pts=21)

    def test_crop_keeps_crs_and_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.tif"
            dst = Path(tmp) / "out" / "crop.tif"
            self._make_raster(src)

            with rasterio.open(src) as ds:
                transform = ds.transform
            window = Window(20, 10, 100, 50)
            target_bounds = self._wgs84_of(window, transform)

            result = crop.crop_to_bounds(src, dst, target_bounds)

            self.assertTrue(dst.exists())
            self.assertEqual(result.crs, "EPSG:3857")
            self.assertIn(result.width, (99, 100, 101))
            self.assertFalse(result.clipped)

            with rasterio.open(src) as source, rasterio.open(dst) as written:
                self.assertEqual(written.crs, source.crs)
                col, row, w, h = result.window
                expected = source.read(1, window=Window(col, row, w, h))
                actual = written.read(1)
                self.assertTrue(np.array_equal(expected, actual))

                # 输出的地理参考要和裁剪窗口对得上
                out_left, out_bottom, out_right, out_top = written.bounds
                self.assertAlmostEqual(out_left, col * 10.0, places=3)
                self.assertAlmostEqual(out_top, 1000.0 - row * 10.0, places=3)

    def test_outside_extent_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.tif"
            self._make_raster(src)
            with self.assertRaises(crop.CropError):
                crop.crop_to_bounds(src, Path(tmp) / "x.tif", (-10.0, -10.0, -9.9, -9.9))

    def test_existing_target_needs_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.tif"
            dst = Path(tmp) / "crop.tif"
            self._make_raster(src)
            dst.write_bytes(b"existing")

            with rasterio.open(src) as ds:
                bounds = self._wgs84_of(Window(0, 0, 50, 50), ds.transform)

            with self.assertRaises(crop.CropError):
                crop.crop_to_bounds(src, dst, bounds)

            result = crop.crop_to_bounds(src, dst, bounds, overwrite=True)
            self.assertEqual(result.width, 50)

    def test_missing_crs_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "nocrs.tif"
            self._make_raster(src, crs=None)
            with self.assertRaises(crop.CropError):
                crop.crop_to_bounds(src, Path(tmp) / "x.tif", (0.0, 0.0, 0.001, 0.001))

    def test_request_larger_than_image_is_clipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.tif"
            dst = Path(tmp) / "crop.tif"
            self._make_raster(src)
            result = crop.crop_to_bounds(src, dst, (-180.0, -85.0, 180.0, 85.0))
            self.assertTrue(result.clipped)
            self.assertEqual((result.width, result.height), (self.width, self.height))

    def test_progress_reaches_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.tif"
            self._make_raster(src)
            with rasterio.open(src) as ds:
                bounds = self._wgs84_of(Window(0, 0, 80, 80), ds.transform)
            seen: list[float] = []
            crop.crop_to_bounds(src, Path(tmp) / "p.tif", bounds, progress=seen.append)
            self.assertTrue(seen)
            self.assertEqual(seen[-1], 1.0)

    def test_reproject_4326_makes_square_pixel_block(self) -> None:
        """经纬度影像裁 10 km 地面方块：重投影后像素宽高相等，不重投影就是长方形。"""
        lon, lat = 116.3, 40.0
        bounds = frames.square_bounds(lon, lat, 10000.0)

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "geo.tif"
            # 影像要能装下 0.117° × 0.090° 的 10 km 方块，否则会被裁到边界
            with rasterio.open(
                src, "w", driver="GTiff", width=1400, height=1100, count=1,
                dtype="uint8", crs="EPSG:4326",
                transform=from_origin(lon - 0.07, lat + 0.055, 0.0001, 0.0001),
            ) as dst:
                dst.write(np.zeros((1, 1100, 1400), dtype="uint8"))

            plain = crop.crop_to_bounds(src, Path(tmp) / "plain.tif", bounds)
            self.assertFalse(plain.reprojected)
            self.assertEqual(plain.crs, "EPSG:4326")
            # 经纬度文件里 10 km 地面方块是长方形：宽/高 ≈ 1/cos(40°) ≈ 1.30
            self.assertGreater(plain.width / plain.height, 1.2)

            projected = crop.crop_to_bounds(
                src, Path(tmp) / "merc.tif", bounds, reproject_4326=True
            )
            self.assertTrue(projected.reprojected)
            self.assertEqual(projected.crs, "EPSG:3857")
            self.assertEqual(projected.width, projected.height)

            with rasterio.open(projected.path) as ds:
                self.assertAlmostEqual(ds.res[0] / ds.res[1], 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
