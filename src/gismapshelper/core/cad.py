"""CAD 输出：写 DXF，装了 ODA File Converter 时顺手转成 DWG。

Python 没法直接写 DWG（格式闭源）。DXF 是所有 CAD 都能打开的中转格式，
AutoCAD / SketchUp / Rhino / 3ds Max 都能直接导入；要真正的 .dwg 就装一个
免费的 ODA File Converter，程序会自己找它并转换。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# ezdxf 导入时就会找缓存目录、扫系统字体；本机沙箱下 ~/.cache 不可写会刷一堆警告。
os.environ.setdefault(
    "XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "gismapshelper-ezdxf-cache")
)
logging.getLogger("ezdxf").setLevel(logging.ERROR)
for _name in ("fontTools", "fontTools.ttLib", "fontTools.ttLib.tables._n_a_m_e"):
    logging.getLogger(_name).setLevel(logging.ERROR)

try:  # 可选依赖
    import ezdxf

    EZDXF_AVAILABLE = True
except ImportError:  # pragma: no cover
    ezdxf = None  # type: ignore[assignment]
    EZDXF_AVAILABLE = False

CAD_LAYER = "GISMAP"
DXF_VERSION = "R2010"
DWG_VERSION = "ACAD2018"

_ODA_PATTERNS = (
    r"C:\Program Files\ODA\*\ODAFileConverter.exe",
    r"C:\Program Files (x86)\ODA\*\ODAFileConverter.exe",
    r"D:\Program Files\ODA\*\ODAFileConverter.exe",
    r"C:\Program Files\ODA\ODAFileConverter.exe",
)


class CadError(Exception):
    """写 DXF / 转 DWG 失败。"""


@dataclass(frozen=True)
class CadRect:
    """一个矩形，坐标是 CAD 里的本地米制坐标（左下 / 右上）。"""

    name: str
    x0: float
    y0: float
    x1: float
    y1: float


def write_dxf(path: str | Path, rects: list[CadRect], layer: str = CAD_LAYER) -> Path:
    """把矩形写成闭合多段线（LWPOLYLINE），不填充、不画图案。"""
    if not EZDXF_AVAILABLE:
        raise CadError("写 DXF 需要 ezdxf：.venv\\Scripts\\python.exe -m pip install ezdxf")
    if not rects:
        raise CadError("没有可写的矩形")

    doc = ezdxf.new(DXF_VERSION, setup=False)
    doc.layers.add(layer, color=7)
    model = doc.modelspace()

    for rect in rects:
        model.add_lwpolyline(
            [
                (rect.x0, rect.y0),
                (rect.x1, rect.y0),
                (rect.x1, rect.y1),
                (rect.x0, rect.y1),
            ],
            close=True,
            dxfattribs={"layer": layer},
        )

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(target)
    return target


def find_oda_converter(extra: str | Path | None = None) -> Path | None:
    """找一个可用的 ODA File Converter；找不到就返回 None（只出 DXF）。"""
    if extra:
        candidate = Path(extra)
        if candidate.is_file():
            return candidate

    for pattern in _ODA_PATTERNS:
        found = _expand(pattern)
        if found is not None:
            return found
    return None


def _expand(pattern: str) -> Path | None:
    parts = Path(pattern).parts
    current = [Path(parts[0])]
    for part in parts[1:]:
        nxt: list[Path] = []
        for base in current:
            if "*" in part:
                try:
                    nxt.extend(sorted(p for p in base.glob(part) if p.is_dir() or p.is_file()))
                except OSError:
                    continue
            else:
                candidate = base / part
                if candidate.exists():
                    nxt.append(candidate)
        current = nxt
        if not current:
            return None
    for candidate in current:
        if candidate.is_file() and candidate.name.lower() == "odafileconverter.exe":
            return candidate
    return None


def convert_to_dwg(
    dxf_path: str | Path,
    dwg_path: str | Path,
    converter: str | Path,
    *,
    timeout: int = 180,
) -> Path:
    """用 ODA File Converter 把 DXF 转成 DWG（它只接受文件夹，所以走临时目录）。"""
    source = Path(dxf_path)
    target = Path(dwg_path)
    tool = Path(converter)
    if not source.is_file():
        raise CadError(f"找不到 DXF：{source}")
    if not tool.is_file():
        raise CadError(f"找不到 ODA File Converter：{tool}")

    with tempfile.TemporaryDirectory() as tmp:
        in_dir = Path(tmp) / "in"
        out_dir = Path(tmp) / "out"
        in_dir.mkdir()
        out_dir.mkdir()
        shutil.copy2(source, in_dir / source.name)

        command = [
            str(tool),
            str(in_dir),
            str(out_dir),
            DWG_VERSION,
            "DWG",
            "0",
            "1",
            source.name,
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CadError(f"调用 ODA File Converter 失败：{exc}") from exc

        produced = out_dir / f"{source.stem}.dwg"
        if not produced.is_file():
            detail = (result.stderr or result.stdout or b"").decode("utf-8", "ignore").strip()
            raise CadError(f"转换没有产出 DWG（退出码 {result.returncode}）{('：' + detail) if detail else ''}")

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced), target)
    return target
