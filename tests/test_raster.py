"""GeoTIFF 元数据读取的单元测试。

测试用的 TIFF 由本文件现场拼装（只写文件头和标签，没有像素数据），
因此不依赖 Pillow / GDAL，也不会在仓库里塞二进制样本。
"""

import struct
import tempfile
import unittest
from pathlib import Path

from gismapshelper.core import geo
from gismapshelper.core.raster import (
    Bounds,
    RasterInfoError,
    crs_category,
    describe,
    format_bytes,
    info_rows,
    read_raster_info,
)

WKT_3857 = (
    'PROJCS["WGS_1984_Web_Mercator_Auxiliary_Sphere",'
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Mercator_Auxiliary_Sphere"],'
    'PARAMETER["False_Easting",0.0],PARAMETER["False_Northing",0.0],'
    'PARAMETER["Central_Meridian",0.0],PARAMETER["Standard_Parallel_1",0.0],'
    'PARAMETER["Auxiliary_Sphere_Type",0.0],UNIT["Meter",1.0]]'
)


def _encode(byte_order: str, type_id: int, values) -> tuple[int, bytes]:
    if type_id == 2:                                    # ASCII
        data = str(values).encode("ascii") + b"\0"
        return len(data), data
    formats = {3: "H", 4: "I", 12: "d"}
    fmt = formats[type_id]
    return len(values), struct.pack(byte_order + fmt * len(values), *values)


def build_tiff(
    path: Path,
    width: int,
    height: int,
    *,
    epsg: int | None = None,
    geographic: bool = False,
    scale: tuple[float, float] = (2.0, 2.0),
    origin: tuple[float, float] = (12930000.0, 4880000.0),
    georeferenced: bool = True,
    software: str = "GisMapsHelperTest",
) -> Path:
    """写一个最小的 GeoTIFF：小端、经典 TIFF、只含文件头与 IFD。"""
    byte_order = "<"
    entries: list[tuple[int, int, int, bytes]] = []

    def add(tag: int, type_id: int, values) -> None:
        count, data = _encode(byte_order, type_id, values)
        entries.append((tag, type_id, count, data))

    add(256, 4, [width])            # ImageWidth
    add(257, 4, [height])           # ImageLength
    add(258, 3, [8, 8, 8])          # BitsPerSample
    add(259, 3, [1])                # Compression: none
    add(262, 3, [2])                # Photometric: RGB
    add(277, 3, [3])                # SamplesPerPixel
    add(278, 4, [height])           # RowsPerStrip
    add(305, 2, software)           # Software
    if georeferenced:
        add(33550, 12, [scale[0], scale[1], 0.0])
        add(33922, 12, [0.0, 0.0, 0.0, origin[0], origin[1], 0.0])
        if epsg is not None:
            keys = [1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1]
            keys += [2048, 0, 1, epsg] if geographic else [3072, 0, 1, epsg]
            add(34735, 3, keys)

    entries.sort(key=lambda item: item[0])
    ifd_offset = 8
    ifd_size = 2 + len(entries) * 12 + 4
    data_offset = ifd_offset + ifd_size

    out = bytearray(b"II" + struct.pack("<H", 42) + struct.pack("<I", ifd_offset))
    out += struct.pack("<H", len(entries))
    blobs = bytearray()
    for tag, type_id, count, data in entries:
        out += struct.pack("<HHI", tag, type_id, count)
        if len(data) <= 4:
            out += data + b"\0" * (4 - len(data))
        else:
            out += struct.pack("<I", data_offset + len(blobs))
            blobs += data
            if len(blobs) % 2:
                blobs += b"\0"
    out += struct.pack("<I", 0)
    out += blobs
    path.write_bytes(bytes(out))
    return path


class GeoTiffTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_mercator_geotiff(self) -> None:
        path = build_tiff(self.root / "mercator.tif", 1200, 800, epsg=3857)
        info = read_raster_info(path)

        self.assertEqual((info.width, info.height), (1200, 800))
        self.assertEqual((info.samples, info.bits), (3, 8))
        self.assertEqual(info.crs, "EPSG:3857")
        self.assertTrue(info.map_ready)

        self.assertAlmostEqual(info.bounds.west, 12930000.0)
        self.assertAlmostEqual(info.bounds.north, 4880000.0)
        self.assertAlmostEqual(info.bounds.east, 12930000.0 + 2.0 * 1200)
        self.assertAlmostEqual(info.bounds.south, 4880000.0 - 2.0 * 800)

        self.assertAlmostEqual(info.ground_resolution_m, 2.0, places=6)
        self.assertIsNotNone(info.terrain_resolution_m)
        self.assertLess(info.terrain_resolution_m, info.ground_resolution_m)
        self.assertAlmostEqual(info.lonlat.west, geo.merc_to_lon(12930000.0), places=9)
        self.assertAlmostEqual(info.native_zoom, 16.26, places=2)
        self.assertEqual(info.suggested_zooms, (14, 16))
        self.assertGreater(info.area_km2, 0)
        self.assertEqual(info.compression, "无压缩")
        self.assertEqual(info.bounds_source, "GeoTIFF ModelPixelScale + ModelTiepoint")

    def test_geographic_geotiff(self) -> None:
        path = build_tiff(
            self.root / "wgs84.tif", 1000, 1000,
            epsg=4326, geographic=True, scale=(0.0001, 0.0001), origin=(116.3, 40.0),
        )
        info = read_raster_info(path)

        self.assertEqual(info.crs, "EPSG:4326")
        self.assertEqual(crs_category(info.crs), "geographic")
        self.assertAlmostEqual(info.bounds.west, 116.3)
        self.assertAlmostEqual(info.bounds.north, 40.0)
        self.assertTrue(info.map_ready)
        # 0.0001 度 ≈ 11 米（赤道），经度方向在北京约 8.5 米，平均 12~13 米
        self.assertGreater(info.ground_resolution_m, 10.0)
        self.assertLess(info.ground_resolution_m, 16.0)

    def test_worldfile_fallback(self) -> None:
        path = build_tiff(self.root / "plain.tif", 600, 400, georeferenced=False)
        path.with_suffix(".tfw").write_text(
            "2.0\n0.0\n0.0\n-2.0\n12930001.0\n4879999.0\n", encoding="ascii"
        )
        path.with_suffix(".prj").write_text(WKT_3857, encoding="ascii")

        info = read_raster_info(path)

        self.assertTrue(info.map_ready)
        self.assertEqual(info.crs, "EPSG:3857")
        self.assertAlmostEqual(info.bounds.west, 12930000.0)
        self.assertAlmostEqual(info.bounds.north, 4880000.0)
        self.assertAlmostEqual(info.bounds.east, 12930000.0 + 2.0 * 600)
        self.assertAlmostEqual(info.bounds.south, 4880000.0 - 2.0 * 400)
        self.assertIn("世界文件", info.bounds_source)
        self.assertIn(".prj", info.crs_source)

    def test_missing_georeference(self) -> None:
        path = build_tiff(self.root / "nogeo.tif", 100, 100, georeferenced=False)
        info = read_raster_info(path)

        self.assertIsNone(info.bounds)
        self.assertFalse(info.map_ready)
        self.assertTrue(info.warnings)
        self.assertIn("未找到地理范围", info.warnings[0])

    def test_rejects_non_tiff(self) -> None:
        path = self.root / "fake.tif"
        path.write_bytes(b"not a tiff at all")
        with self.assertRaises(RasterInfoError):
            read_raster_info(path)

    def test_rejects_other_suffix(self) -> None:
        path = self.root / "photo.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        with self.assertRaises(RasterInfoError):
            read_raster_info(path)

    def test_missing_file(self) -> None:
        with self.assertRaises(RasterInfoError):
            read_raster_info(self.root / "nope.tif")

    def test_info_rows_and_describe(self) -> None:
        path = build_tiff(self.root / "rows.tif", 300, 200, epsg=3857)
        info = read_raster_info(path)
        rows = dict(info_rows(info))
        self.assertEqual(rows["坐标系"], "EPSG:3857")
        self.assertIn("米", rows["原始范围"])
        self.assertIn("分辨率（投影）", rows)
        self.assertIn("分辨率（实地）", rows)
        text = describe(info)
        self.assertIn("EPSG:3857", text)
        self.assertIn("分辨率", text)


class HelperTest(unittest.TestCase):
    def test_crs_category(self) -> None:
        self.assertEqual(crs_category("EPSG:3857"), "mercator")
        self.assertEqual(crs_category("EPSG:4326"), "geographic")
        self.assertEqual(crs_category("EPSG:4490"), "geographic")
        self.assertEqual(crs_category("EPSG:32650"), "utm")
        self.assertEqual(crs_category("EPSG:4547"), "projected")
        self.assertEqual(crs_category(None), "unknown")

    def test_format_bytes(self) -> None:
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1536), "1.5 KB")
        self.assertEqual(format_bytes(3 * 1024**3), "3.0 GB")

    def test_bounds_geometry(self) -> None:
        bounds = Bounds(1.0, 2.0, 5.0, 8.0)
        self.assertEqual(bounds.width, 4.0)
        self.assertEqual(bounds.height, 6.0)
        self.assertEqual(bounds.center, (3.0, 5.0))


if __name__ == "__main__":
    unittest.main()
