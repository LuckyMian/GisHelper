"""GeoJSON 导出：几何顺序、坐标基准、文件落盘。"""

import json
import tempfile
import unittest
from pathlib import Path

from gismapshelper.core import geojson


class RingTest(unittest.TestCase):
    def test_ring_is_closed_counter_clockwise(self) -> None:
        ring = geojson.ring_from_bounds((116.1, 39.8, 116.5, 40.0))
        self.assertEqual(len(ring), 5)
        self.assertEqual(ring[0], ring[-1])
        self.assertEqual(ring[0], [116.1, 39.8])
        self.assertEqual(ring[2], [116.5, 40.0])

    def test_bounds_in_wgs84_are_identity(self) -> None:
        bounds = (116.1, 39.8, 116.5, 40.0)
        self.assertEqual(geojson.bounds_in_datum(bounds, geojson.DATUM_WGS84), bounds)

    def test_unknown_datum_raises(self) -> None:
        with self.assertRaises(ValueError):
            geojson.bounds_in_datum((0, 0, 1, 1), "bd09")


class FeatureTest(unittest.TestCase):
    def test_polygon_feature(self) -> None:
        feature = geojson.feature_from_bounds(
            (116.1, 39.8, 116.5, 40.0), {"name": "TIF 范围"}
        )
        self.assertEqual(feature["type"], "Feature")
        self.assertEqual(feature["geometry"]["type"], "Polygon")
        self.assertEqual(feature["properties"]["name"], "TIF 范围")
        self.assertEqual(feature["properties"]["datum"], "WGS84")
        self.assertEqual(feature["bbox"], [116.1, 39.8, 116.5, 40.0])
        self.assertEqual(len(feature["geometry"]["coordinates"][0]), 5)

    def test_gcj02_export_is_shifted_and_normalized(self) -> None:
        wgs = (116.3, 39.9, 116.4, 40.0)
        feature = geojson.feature_from_bounds(wgs, {}, geojson.DATUM_GCJ02)
        ring = feature["geometry"]["coordinates"][0]
        self.assertNotAlmostEqual(ring[0][0], wgs[0], places=4)
        west, south, east, north = feature["bbox"]
        self.assertLess(west, east)
        self.assertLess(south, north)
        self.assertEqual(feature["properties"]["datum"], "GCJ-02")

    def test_point_feature(self) -> None:
        feature = geojson.feature_from_point(116.3, 39.9, {"name": "原点"})
        self.assertEqual(feature["geometry"]["type"], "Point")
        self.assertEqual(feature["geometry"]["coordinates"], [116.3, 39.9])

    def test_properties_dict_is_not_mutated(self) -> None:
        props = {"name": "x"}
        geojson.feature_from_bounds((0, 0, 1, 1), props)
        self.assertEqual(props, {"name": "x"})


class WriteTest(unittest.TestCase):
    def test_write_creates_parent_and_round_trips(self) -> None:
        feature = geojson.feature_from_bounds((0.5, 0.25, 1.5, 1.25))
        collection = geojson.build_collection([feature], name="测试")

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "layer.geojson"
            written = geojson.write_collection(target, collection)
            self.assertTrue(written.exists())
            data = json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(data["type"], "FeatureCollection")
            self.assertEqual(len(data["features"]), 1)
            self.assertEqual(data["features"][0]["geometry"]["type"], "Polygon")

    def test_chinese_is_not_escaped(self) -> None:
        collection = geojson.build_collection([geojson.feature_from_bounds((0, 0, 1, 1), {"name": "北京"})])
        with tempfile.TemporaryDirectory() as tmp:
            written = geojson.write_collection(Path(tmp) / "a.geojson", collection)
            text = written.read_text(encoding="utf-8")
            self.assertIn("北京", text)
            self.assertNotIn("\\u5317", text)

    def test_coordinate_pair_stays_on_one_line(self) -> None:
        collection = geojson.build_collection(
            [geojson.feature_from_bounds((116.1, 39.8, 116.5, 40.0))]
        )
        with tempfile.TemporaryDirectory() as tmp:
            written = geojson.write_collection(Path(tmp) / "a.geojson", collection)
            text = written.read_text(encoding="utf-8")
            self.assertIn("[116.1, 39.8]", text)
            self.assertNotIn("116.1,\n", text)
            # 压缩后仍必须是合法 JSON，且数值不变
            data = json.loads(text)
            self.assertEqual(data["features"][0]["bbox"], [116.1, 39.8, 116.5, 40.0])


if __name__ == "__main__":
    unittest.main()
