"""按矩形裁剪 GeoTIFF，并写出带地理参考的新 TIFF。

走 rasterio（GDAL）：按窗口读取、分块写出，几十 GB 的影像也不会一次性读进内存。
没有安装 rasterio 时只影响裁剪功能，其它页面照常可用。
"""

from __future__ import annotations

import math
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import gdal_env

try:  # 可选依赖
    import rasterio
    from rasterio.transform import array_bounds
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    RASTERIO_AVAILABLE = True
except ImportError:  # pragma: no cover - 没装依赖时走这条
    rasterio = None  # type: ignore[assignment]
    RASTERIO_AVAILABLE = False


def _isolate_gdal_environment() -> None:
    """见 :mod:`gismapshelper.core.gdal_env`：把 PROJ / GDAL 指回 rasterio 自带的那份。"""
    gdal_env.configure()


if RASTERIO_AVAILABLE:
    _isolate_gdal_environment()


class CropError(Exception):
    """裁剪无法进行：缺依赖、无交集、缺坐标系、目标已存在等。"""


@dataclass(frozen=True)
class CropResult:
    path: Path
    width: int
    height: int
    crs: str
    bounds: tuple[float, float, float, float]   # 输出影像在自身 CRS 下的范围
    clipped: bool                               # 请求范围超出影像、被裁到边界
    window: tuple[int, int, int, int]           # 源影像里的窗口 (col, row, width, height)
    reprojected: bool = False                   # 是否先重投影到了 EPSG:3857


def _require_rasterio() -> None:
    if not RASTERIO_AVAILABLE:
        raise CropError("裁剪需要 rasterio：.venv\\Scripts\\python.exe -m pip install rasterio")


def crop_to_bounds(
    src_path: str | Path,
    dst_path: str | Path,
    bounds_wgs84: tuple[float, float, float, float],
    *,
    block_rows: int = 512,
    progress: Callable[[float], None] | None = None,
    overwrite: bool = False,
    reproject_4326: bool = False,
) -> CropResult:
    """把 ``src_path`` 按 WGS-84 矩形裁一块到 ``dst_path``。

    窗口会向外取整到像素边界，所以输出范围可能比请求范围略大（最多一个像素）。
    ``reproject_4326`` 为真且源影像是经纬度时，先重投影到 EPSG:3857 再裁，
    这样输出是米制方像素块（10 km 地面方块在文件里也方正）。
    """
    _require_rasterio()
    source = Path(src_path)
    target = Path(dst_path)

    if not source.is_file():
        raise CropError(f"找不到源文件：{source}")
    if target.exists() and not overwrite:
        raise CropError(f"目标已存在（换个名字，或允许覆盖）：{target}")

    working = source
    reprojected = False
    temp_dir: Path | None = None
    try:
        if reproject_4326:
            with rasterio.open(source) as probe:
                needs = str(probe.crs) == "EPSG:4326"
            if needs:
                from . import reproject as reproject_mod

                temp_dir = Path(tempfile.mkdtemp(prefix="gismaps-crop-3857-"))
                working = temp_dir / f"{source.stem}_3857.tif"
                reproject_mod.to_mercator(source, working)
                reprojected = True

        return _crop_open(working, target, bounds_wgs84, block_rows, progress, reprojected)
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _crop_open(
    source: Path,
    target: Path,
    bounds_wgs84: tuple[float, float, float, float],
    block_rows: int,
    progress: Callable[[float], None] | None,
    reprojected: bool,
) -> CropResult:
    with rasterio.open(source) as src:
        if src.crs is None:
            raise CropError("源影像没有坐标系，裁剪后无法保留地理信息")

        left, bottom, right, top = transform_bounds(
            "EPSG:4326", src.crs, *bounds_wgs84, densify_pts=21
        )
        window = from_bounds(left, bottom, right, top, transform=src.transform)

        # 坐标来回换算会带 1e-9 级的误差，直接 floor/ceil 会多出整整一行一列，
        # 所以先加一个远小于一个像素的容差再取整。
        tolerance = 1e-6
        col_off = max(0, math.floor(window.col_off + tolerance))
        row_off = max(0, math.floor(window.row_off + tolerance))
        col_end = min(src.width, math.ceil(window.col_off + window.width - tolerance))
        row_end = min(src.height, math.ceil(window.row_off + window.height - tolerance))

        width = col_end - col_off
        height = row_end - row_off
        if width <= 0 or height <= 0:
            raise CropError("裁剪范围与影像没有交集")

        # 请求的是正方形（3857 米制下宽高相等）时，把像素窗口也凑成正方形：
        # 四舍五入常让两个方向差 1 像素，而用户要的就是「10 km 的方块」。
        span_x = right - left
        span_y = top - bottom
        if (
            span_x > 0
            and span_y > 0
            and abs(span_x - span_y) / max(span_x, span_y) <= 0.005
            and width != height
        ):
            side = max(width, height)
            if side <= src.width and side <= src.height:
                col_off = max(0, min(src.width - side, col_off - (side - width) // 2))
                row_off = max(0, min(src.height - side, row_off - (side - height) // 2))
                width = height = side

        clipped = (
            window.col_off < 0
            or window.row_off < 0
            or window.col_off + window.width > src.width
            or window.row_off + window.height > src.height
        )

        dtypes = {dtype for dtype in src.dtypes}
        if len(dtypes) > 1:
            raise CropError(f"各波段数据类型不一致（{sorted(dtypes)}），暂不支持裁剪")

        crop_window = Window(col_off, row_off, width, height)
        result = _write_window(
            source, target, crop_window, block_rows=block_rows, progress=progress
        )
        return CropResult(
            path=result.path,
            width=result.width,
            height=result.height,
            crs=result.crs,
            bounds=result.bounds,
            clipped=clipped,
            window=result.window,
            reprojected=reprojected,
        )


def crop_to_window(
    src_path: str | Path,
    dst_path: str | Path,
    col: int,
    row: int,
    width: int,
    height: int,
    *,
    block_rows: int = 512,
    progress: Callable[[float], None] | None = None,
    overwrite: bool = False,
) -> CropResult:
    """按**像素窗口**裁剪（网格切片用），不做坐标换算，边界绝对精确。"""
    _require_rasterio()
    source = Path(src_path)
    target = Path(dst_path)

    if not source.is_file():
        raise CropError(f"找不到源文件：{source}")
    if target.exists() and not overwrite:
        raise CropError(f"目标已存在：{target}")

    with rasterio.open(source) as src:
        if col < 0 or row < 0 or width <= 0 or height <= 0:
            raise CropError("像素窗口不合法")
        if col + width > src.width or row + height > src.height:
            raise CropError("像素窗口超出影像范围")
        window = Window(int(col), int(row), int(width), int(height))
        return _write_window(source, target, window, block_rows=block_rows, progress=progress)


def _write_window(
    source: Path,
    target: Path,
    window: "Window",
    *,
    block_rows: int = 512,
    progress: Callable[[float], None] | None = None,
) -> CropResult:
    """把 ``window`` 写成一个新的 GeoTIFF，地理参考与源文件一致。"""
    with rasterio.open(source) as src:
        if src.crs is None:
            raise CropError("源影像没有坐标系，裁剪后无法保留地理信息")

        width = int(window.width)
        height = int(window.height)

        dtypes = {dtype for dtype in src.dtypes}
        if len(dtypes) > 1:
            raise CropError(f"各波段数据类型不一致（{sorted(dtypes)}），暂不支持裁剪")

        out_transform = src.window_transform(window)
        target.parent.mkdir(parents=True, exist_ok=True)

        profile = {
            "driver": "GTiff",
            "width": width,
            "height": height,
            "count": src.count,
            "dtype": src.dtypes[0],
            "crs": src.crs,
            "transform": out_transform,
            "compress": src.compression or "deflate",
            "bigtiff": "IF_SAFER",
        }
        if src.nodata is not None:
            profile["nodata"] = src.nodata

        try:
            with rasterio.open(target, "w", **profile) as dst:
                done = 0
                for row in range(0, height, block_rows):
                    rows = min(block_rows, height - row)
                    chunk = src.read(window=Window(window.col_off, window.row_off + row, width, rows))
                    dst.write(chunk, window=Window(0, row, width, rows))
                    done += rows
                    if progress is not None:
                        progress(done / height)
        except Exception:
            target.unlink(missing_ok=True)
            raise

        if progress is not None:
            progress(1.0)

        out_bounds = array_bounds(height, width, out_transform)
        return CropResult(
            path=target,
            width=width,
            height=height,
            crs=str(src.crs),
            bounds=(out_bounds[0], out_bounds[1], out_bounds[2], out_bounds[3]),
            clipped=False,
            window=(int(window.col_off), int(window.row_off), width, height),
        )
