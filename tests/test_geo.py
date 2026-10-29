"""投影 / 瓦片数学的单元测试。"""

import unittest

from gismapshelper.core import geo


class MercatorTest(unittest.TestCase):
    def test_world_pixel_roundtrip(self) -> None:
        for lon, lat in ((116.397, 39.908), (-73.98, 40.75), (151.2, -33.87), (0.0, 0.0)):
            for zoom in (0, 4, 12, 18):
                px, py = geo.lonlat_to_world(lon, lat, zoom)
                back_lon, back_lat = geo.world_to_lonlat(px, py, zoom)
                self.assertAlmostEqual(back_lon, lon, places=9)
                self.assertAlmostEqual(back_lat, lat, places=9)

    def test_world_corners(self) -> None:
        self.assertAlmostEqual(geo.lonlat_to_world(-180, 85.05112877980659, 0)[0], 0.0)
        size = geo.world_size(3)
        self.assertAlmostEqual(geo.lonlat_to_world(180, 0, 3)[0], size)

    def test_mercator_axis(self) -> None:
        x, y = geo.lonlat_to_merc(0, 0)
        self.assertAlmostEqual(x, 0.0)
        self.assertAlmostEqual(y, 0.0)
        self.assertAlmostEqual(geo.merc_to_lon(geo.ORIGIN), 180.0)
        self.assertAlmostEqual(geo.merc_to_lat(0.0), 0.0)

    def test_lat_is_clamped(self) -> None:
        self.assertAlmostEqual(geo.clamp_lat(90.0), geo.MAX_LAT)
        self.assertAlmostEqual(geo.clamp_lat(-90.0), -geo.MAX_LAT)

    def test_tile_bounds(self) -> None:
        west, south, east, north = geo.tile_bounds_merc(0, 0, 0)
        self.assertAlmostEqual(west, -geo.ORIGIN)
        self.assertAlmostEqual(north, geo.ORIGIN)
        self.assertAlmostEqual(east, geo.ORIGIN)
        self.assertAlmostEqual(south, -geo.ORIGIN)

    def test_ground_resolution(self) -> None:
        self.assertAlmostEqual(geo.ground_resolution_m(0, 0), 156543.03392804097, places=4)
        self.assertAlmostEqual(geo.ground_resolution_m(0, 1), 78271.51696402048, places=4)
        # 高纬度每像素对应的地面距离更小
        self.assertLess(geo.ground_resolution_m(60, 10), geo.ground_resolution_m(0, 10))

    def test_native_zoom_inverse(self) -> None:
        resolution = geo.ground_resolution_m(0, 16)
        self.assertAlmostEqual(geo.native_zoom_of(resolution), 16.0, places=6)
        self.assertEqual(geo.native_zoom_of(0), 0.0)

    def test_spherical_area(self) -> None:
        area = geo.spherical_area_km2(0.0, 0.0, 1.0, 1.0)
        self.assertGreater(area, 12000)
        self.assertLess(area, 12500)
        self.assertEqual(geo.spherical_area_km2(0, 0, 0, 0), 0.0)


class Gcj02Test(unittest.TestCase):
    def test_offset_in_china(self) -> None:
        lon, lat = geo.wgs84_to_gcj02(116.397, 39.908)
        self.assertGreater(abs(lon - 116.397), 0.001)
        self.assertLess(abs(lon - 116.397), 0.02)
        self.assertLess(abs(lat - 39.908), 0.02)

    def test_roundtrip(self) -> None:
        for lon, lat in ((116.397, 39.908), (121.47, 31.23), (113.26, 23.13)):
            back = geo.gcj02_to_wgs84(*geo.wgs84_to_gcj02(lon, lat))
            self.assertAlmostEqual(back[0], lon, places=7)
            self.assertAlmostEqual(back[1], lat, places=7)

    def test_outside_china_is_identity(self) -> None:
        self.assertEqual(geo.wgs84_to_gcj02(-73.98, 40.75), (-73.98, 40.75))


if __name__ == "__main__":
    unittest.main()
