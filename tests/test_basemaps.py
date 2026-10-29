"""底图目录的完整性检查。"""

import unittest

from gismapshelper.core.basemaps import (
    BASEMAPS,
    DEFAULT_BASEMAP_ID,
    get_basemap,
    basemap_ids,
)


class BasemapCatalogTest(unittest.TestCase):
    def test_ids_are_unique(self) -> None:
        ids = basemap_ids()
        self.assertEqual(len(ids), len(set(ids)))

    def test_default_exists(self) -> None:
        self.assertIn(DEFAULT_BASEMAP_ID, basemap_ids())

    def test_urls_have_xyz_placeholders(self) -> None:
        for basemap in BASEMAPS:
            if not basemap.url:
                continue
            for token in ("{z}", "{x}", "{y}"):
                self.assertIn(token, basemap.url, f"{basemap.id} 缺少 {token}")

    def test_urls_are_https(self) -> None:
        for basemap in BASEMAPS:
            if basemap.url:
                self.assertTrue(basemap.url.startswith("https://"), basemap.id)

    def test_key_basemaps_have_placeholder(self) -> None:
        for basemap in BASEMAPS:
            if basemap.requires_key:
                self.assertIn("{key}", basemap.url, basemap.id)

    def test_subdomains_present_when_used(self) -> None:
        for basemap in BASEMAPS:
            if "{s}" in basemap.url:
                self.assertTrue(basemap.subdomains, basemap.id)

    def test_metadata_is_filled(self) -> None:
        for basemap in BASEMAPS:
            self.assertTrue(basemap.name, basemap.id)
            self.assertTrue(basemap.attribution, basemap.id)
            self.assertTrue(basemap.kind, basemap.id)
            self.assertIn(basemap.datum, {"WGS-84", "GCJ-02", "CGCS2000"}, basemap.id)
            self.assertGreater(basemap.max_zoom, 0, basemap.id)

    def test_unknown_id_falls_back_to_default(self) -> None:
        self.assertEqual(get_basemap("no-such-basemap").id, DEFAULT_BASEMAP_ID)

    def test_has_offline_option(self) -> None:
        self.assertIsNotNone(get_basemap("blank"))
        self.assertTrue(get_basemap("blank").is_blank)


if __name__ == "__main__":
    unittest.main()
