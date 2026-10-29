"""项目原点与方形切片范围的几何测试。"""

import math
import unittest

from gismapshelper.core import frames


class MetersPerDegreeTest(unittest.TestCase):
    def test_equator(self) -> None:
        m_lon, m_lat = frames.meters_per_degree(0.0)
        self.assertAlmostEqual(m_lat, math.pi * 6378137.0 / 180.0, places=6)
        self.assertAlmostEqual(m_lon, m_lat, places=6)

    def test_longitude_shrinks_with_latitude(self) -> None:
        m_lon, m_lat = frames.meters_per_degree(60.0)
        equator_lon, _ = frames.meters_per_degree(0.0)
        self.assertAlmostEqual(m_lon, equator_lon * 0.5, places=6)
        self.assertAlmostEqual(m_lat, math.pi * 6378137.0 / 180.0, places=6)


class SquareBoundsTest(unittest.TestCase):
    def test_centered_and_square(self) -> None:
        bounds = frames.square_bounds(116.4, 39.9, 5000.0)
        west, south, east, north = bounds
        self.assertAlmostEqual((west + east) / 2.0, 116.4, places=9)
        self.assertAlmostEqual((south + north) / 2.0, 39.9, places=9)

        offsets = frames.local_offsets(bounds, 116.4, 39.9)
        self.assertAlmostEqual(offsets[0], -2500.0, places=3)
        self.assertAlmostEqual(offsets[1], -2500.0, places=3)
        self.assertAlmostEqual(offsets[2], 2500.0, places=3)
        self.assertAlmostEqual(offsets[3], 2500.0, places=3)

    def test_minimum_edge_is_enforced(self) -> None:
        bounds = frames.square_bounds(0.0, 0.0, 1.0)
        offsets = frames.local_offsets(bounds, 0.0, 0.0)
        self.assertGreaterEqual(offsets[2] - offsets[0], frames.MIN_EDGE_M - 1e-6)

    def test_pole_is_clamped(self) -> None:
        bounds = frames.square_bounds(0.0, 89.99, 100000.0)
        self.assertLessEqual(bounds[3], 89.9)
        self.assertGreaterEqual(bounds[1], -89.9)


class PixelEdgeTest(unittest.TestCase):
    def test_equator(self) -> None:
        self.assertAlmostEqual(frames.edge_from_pixels(4096, 1.2, 0.0), 4096 * 1.2)

    def test_latitude_factor(self) -> None:
        self.assertAlmostEqual(frames.edge_from_pixels(1000, 1.0, 60.0), 500.0, places=6)
        self.assertAlmostEqual(frames.edge_from_pixels(1000, 1.0, 0.0), 1000.0, places=6)

    def test_slice_spec(self) -> None:
        distance = frames.SliceSpec("distance", 2000.0)
        self.assertEqual(distance.edge_m(None, 30.0), 2000.0)
        self.assertIn("2 km", distance.title())

        pixels = frames.SliceSpec("pixels", 1024.0)
        self.assertIsNone(pixels.edge_m(None, 30.0))
        self.assertAlmostEqual(pixels.edge_m(2.0, 0.0), 2048.0)
        self.assertIn("1024", pixels.title())


class FormatTest(unittest.TestCase):
    def test_format_distance(self) -> None:
        self.assertEqual(frames.format_distance(800), "800 m")
        self.assertEqual(frames.format_distance(1000), "1 km")
        self.assertEqual(frames.format_distance(2500), "2.5 km")
        self.assertEqual(frames.format_distance(12000), "12 km")


if __name__ == "__main__":
    unittest.main()
