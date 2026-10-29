"""重投影：经纬度影像 → Web 墨卡托。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from gismapshelper.core import reproject
from gismapshelper.core.gdal_env import configure as configure_gdal

configure_gdal()

try:
    import rasterio
    from rasterio.transform import from_origin

    HAVE_RASTERIO = True
except ImportError:  # pragma: no cover
    HAVE_RASTERIO = False


def make_geo_raster(path: Path, width: int = 200, height: int = 200, value: int = 7) -> Path:
    data = np.full((1, height, width), value, dtype="uint8")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(116.15, 40.20, 0.0001, 0.0001),
    ) as dst:
        dst.write(data)
    return path


@unittest.skipUnless(HAVE_RASTERIO, "需要 rasterio")
class ReprojectTest(unittest.TestCase):
    def test_to_mercator_keeps_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = make_geo_raster(Path(tmp) / "geo.tif")
            dst = Path(tmp) / "out" / "merc.tif"

            result = reproject.to_mercator(src, dst)
            self.assertTrue(dst.exists())
            self.assertEqual(result.crs, "EPSG:3857")
            self.assertGreater(result.resolution_m, 1.0)

            with rasterio.open(dst) as ds:
                self.assertEqual(str(ds.crs), "EPSG:3857")
                data = ds.read(1)
                # 常值影像重投影后仍是同一个值（边缘空值除外）
                interior = data[5:-5, 5:-5]
                self.assertTrue(np.all(interior == 7))

    def test_rejects_already_mercator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "merc.tif"
            with rasterio.open(
                src, "w", driver="GTiff", width=10, height=10, count=1, dtype="uint8",
                crs="EPSG:3857", transform=from_origin(12930000, 4900000, 10, 10),
            ) as dst:
                dst.write(np.zeros((1, 10, 10), dtype="uint8"))
            with self.assertRaises(reproject.ReprojectError):
                reproject.to_mercator(src, Path(tmp) / "x.tif")

    def test_progress_reaches_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = make_geo_raster(Path(tmp) / "geo.tif")
            seen: list[float] = []
            reproject.to_mercator(src, Path(tmp) / "m.tif", progress=seen.append)
            self.assertTrue(seen)
            self.assertEqual(seen[-1], 1.0)


if __name__ == "__main__":
    unittest.main()
