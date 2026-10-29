"""网格切片导出：N×N 子影像 + 一个 GeoJSON + 一个 CAD 文件。

CAD 里是若干个闭合矩形（不填充），坐标以「原点」为 (0,0) 的本地米制，
原点默认取影像中心，也可以选四角。
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import cad, crop, frames, geojson, grid, reproject
from .raster import RasterInfoError, read_raster_info

ProgressCallback = Callable[[float, str], None]


class GridExportError(Exception):
    """切片导出无法进行。"""


@dataclass(frozen=True)
class GridExportResult:
    output_dir: Path
    tiles: list[Path]
    geojson_path: Path
    dxf_path: Path
    dwg_path: Path | None
    origin_lonlat: tuple[float, float]
    origin_anchor: str
    cells: list[grid.GridCell]
    reprojected: bool = False
    source_crs: str | None = None


def export_grid(
    src_path: str | Path,
    output_dir: str | Path,
    n: int,
    *,
    origin_anchor: str = frames.DEFAULT_ANCHOR,
    auto_reproject: bool = True,
    progress: ProgressCallback | None = None,
) -> GridExportResult:
    """把影像切成 n×n 块，并写出 GeoJSON 与 CAD 文件。

    源影像是经纬度（EPSG:4326）且 ``auto_reproject`` 为真时，先重投影到
    EPSG:3857 再切，保证输出的块是米制方像素块。

    输出目录：``<output_dir>/<影像名>_<n>x<n>/``
    """
    source = Path(src_path)
    if not source.is_file():
        raise GridExportError(f"找不到源文件：{source}")
    if n not in grid.GRID_PRESETS:
        raise GridExportError(f"不支持的网格：{n}×{n}")
    if origin_anchor not in frames.ANCHORS:
        raise GridExportError(f"未知原点位置：{origin_anchor}")
    if not crop.RASTERIO_AVAILABLE:
        raise GridExportError("切片需要 rasterio：pip install rasterio")

    try:
        info = read_raster_info(source)
    except (RasterInfoError, OSError) as exc:
        raise GridExportError(str(exc)) from exc

    if info.lonlat is None:
        raise GridExportError("影像没有地理范围，无法切片")
    if info.crs not in ("EPSG:4326", "EPSG:3857"):
        raise GridExportError(f"暂不支持 {info.crs}；需要 EPSG:4326 或 EPSG:3857")

    source_crs = info.crs
    working = source
    reprojected = False
    temp_dir: Path | None = None

    if auto_reproject and info.crs == "EPSG:4326":
        if not reproject.RASTERIO_AVAILABLE:
            raise GridExportError("重投影需要 rasterio")
        temp_dir = Path(tempfile.mkdtemp(prefix="gismaps-3857-"))
        working = temp_dir / f"{source.stem}_3857.tif"
        if progress is not None:
            progress(0.0, "重投影到 EPSG:3857…")
        try:
            reproject.to_mercator(source, working)
        except (reproject.ReprojectError, OSError) as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise GridExportError(str(exc)) from exc
        info = read_raster_info(working)
        reprojected = True

    try:
        return _export_cells(
            working,
            info,
            output_dir,
            n,
            source_stem=source.stem,
            origin_anchor=origin_anchor,
            progress=progress,
            reprojected=reprojected,
            source_crs=source_crs,
        )
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _export_cells(
    working: Path,
    info,
    output_dir: str | Path,
    n: int,
    *,
    source_stem: str,
    origin_anchor: str,
    progress: ProgressCallback | None,
    reprojected: bool,
    source_crs: str | None,
) -> GridExportResult:
    cells = grid.cells(info, n)
    target_dir = Path(output_dir) / f"{source_stem}_{n}x{n}"
    target_dir.mkdir(parents=True, exist_ok=True)

    total = len(cells) + 2
    done = 0

    def report(message: str) -> None:
        nonlocal done
        done += 1
        if progress is not None:
            progress(min(1.0, done / total), message)

    tiles: list[Path] = []
    for cell in cells:
        tile = target_dir / f"{source_stem}_{cell.name}.tif"
        crop.crop_to_window(
            working,
            tile,
            *cell.window,
            overwrite=True,
        )
        tiles.append(tile)
        report(f"切片 {cell.name}")

    # 1) GeoJSON：每块一个 Polygon
    features = []
    for cell in cells:
        features.append(
            geojson.feature_from_bounds(
                cell.bounds_wgs84,
                {
                    "name": f"{source_stem}_{cell.name}",
                    "kind": "grid",
                    "row": cell.row + 1,
                    "col": cell.col + 1,
                    "grid": f"{n}x{n}",
                },
            )
        )
    geojson_path = geojson.write_collection(
        target_dir / f"{source_stem}_{n}x{n}.geojson",
        geojson.build_collection(features, name=f"GisMapsHelper · {source_stem} {n}x{n}"),
    )
    report("写 GeoJSON")

    # 2) CAD：以选定的原点为 (0,0)，矩形为本地米制
    origin = frames.anchor_point(info.lonlat.as_tuple(), origin_anchor)
    rects = [
        cad.CadRect(
            name=f"{source_stem}_{cell.name}",
            x0=offsets[0],
            y0=offsets[1],
            x1=offsets[2],
            y1=offsets[3],
        )
        for cell in cells
        for offsets in [frames.local_offsets(cell.bounds_wgs84, origin[0], origin[1])]
    ]
    dxf_path = cad.write_dxf(target_dir / f"{source_stem}_{n}x{n}.dxf", rects)

    dwg_path: Path | None = None
    converter = cad.find_oda_converter()
    if converter is not None:
        try:
            dwg_path = cad.convert_to_dwg(
                dxf_path, target_dir / f"{source_stem}_{n}x{n}.dwg", converter
            )
        except cad.CadError:
            dwg_path = None
    report("写 CAD")

    return GridExportResult(
        output_dir=target_dir,
        tiles=tiles,
        geojson_path=geojson_path,
        dxf_path=dxf_path,
        dwg_path=dwg_path,
        origin_lonlat=origin,
        origin_anchor=origin_anchor,
        cells=cells,
        reprojected=reprojected,
        source_crs=source_crs,
    )
