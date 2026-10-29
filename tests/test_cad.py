"""CAD 输出：闭合矩形、无填充、ODA 转换的降级行为。"""

import tempfile
import unittest
from pathlib import Path

from gismapshelper.core import cad

try:
    import ezdxf

    HAVE_EZDXF = True
except ImportError:  # pragma: no cover
    HAVE_EZDXF = False


@unittest.skipUnless(HAVE_EZDXF, "需要 ezdxf")
class DxfTest(unittest.TestCase):
    def test_writes_closed_rectangles_without_hatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "grid.dxf"
            rects = [
                cad.CadRect("a", 0.0, 0.0, 10.0, 10.0),
                cad.CadRect("b", 10.0, 0.0, 20.0, 10.0),
            ]
            written = cad.write_dxf(target, rects)
            self.assertTrue(written.exists())

            doc = ezdxf.readfile(written)
            model = doc.modelspace()
            polylines = list(model.query("LWPOLYLINE"))
            self.assertEqual(len(polylines), 2)
            self.assertTrue(all(entity.closed for entity in polylines))
            self.assertEqual(len(list(model.query("HATCH"))), 0)
            self.assertEqual({entity.dxf.layer for entity in polylines}, {cad.CAD_LAYER})

            corners = [(round(x, 6), round(y, 6)) for x, y in polylines[0].get_points("xy")]
            self.assertEqual(corners, [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])

    def test_empty_rect_list_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(cad.CadError):
                cad.write_dxf(Path(tmp) / "empty.dxf", [])


class OdaTest(unittest.TestCase):
    def test_lookup_returns_none_or_existing_file(self) -> None:
        found = cad.find_oda_converter(r"C:\definitely-not-here\ODAFileConverter.exe")
        self.assertTrue(found is None or found.is_file())

    def test_convert_without_tool_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dxf = Path(tmp) / "a.dxf"
            dxf.write_text("0\nSECTION\n0\nEOF\n", encoding="ascii")
            with self.assertRaises(cad.CadError):
                cad.convert_to_dwg(dxf, Path(tmp) / "a.dwg", r"C:\nope\ODAFileConverter.exe")


if __name__ == "__main__":
    unittest.main()
