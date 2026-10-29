"""把经纬度（EPSG:4326）影像重投影到 Web 墨卡托（EPSG:3857）。

切片前先投影，切出来的块在文件里就是方形的（米制、像素等宽高），
也不会出现「地面上 10 km，文件里却是 0.117° × 0.090°」的观感问题。

用 WarpedVRT 分块读写，几十 GB 的影像也不会一次性读进内存。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import gdal_env

try:  # 可选依赖
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.warp import calculate_default_transform

    RASTERIO_AVAILABLE = True
except ImportError:  # pragma: no cover
    rasterio = None  # type: ignore[assignment]
    RASTERIO_AVAILABLE = False

TARGET_CRS = "EPSG:3857"


class ReprojectError(Exception):
    """重投影无法进行。"""


@dataclass(frozen=True)
class ReprojectResult:
    path: Path
    width: int
    height: int
    crs: str
    resolution_m: float


def to_mercator(
    src_path: str | Path,
    dst_path: str | Path,
    *,
    resampling: str = "cubic",
    block_rows: int = 512,
    progress: Callable[[float], None] | None = None,
) -> ReprojectResult:
    """把影像重投影到 EPSG:3857 并写到 ``dst_path``。"""
    if not RASTERIO_AVAILABLE:
        raise ReprojectError("重投影需要 rasterio：pip install rasterio")
    gdal_env.configure()

    source = Path(src_path)
    target = Path(dst_path)
    if not source.is_file():
        raise ReprojectError(f"找不到源文件：{source}")

    with rasterio.open(source) as src:
        if src.crs is None:
            raise ReprojectError("源影像没有坐标系，无法重投影")
        if str(src.crs) == TARGET_CRS:
            raise ReprojectError("源影像已经是 EPSG:3857")

        dtypes = {dtype for dtype in src.dtypes}
        if len(dtypes) > 1:
            raise ReprojectError(f"各波段数据类型不一致（{sorted(dtypes)}），暂不支持重投影")

        transform, width, height = calculate_default_transform(
            src.crs, TARGET_CRS, src.width, src.height, *src.bounds
        )
        profile = {
            "driver": "GTiff",
            "width": width,
            "height": height,
            "count": src.count,
            "dtype": src.dtypes[0],
            "crs": TARGET_CRS,
            "transform": transform,
            "compress": "deflate",
            "bigtiff": "IF_SAFER",
        }
        if src.nodata is not None:
            profile["nodata"] = src.nodata

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with WarpedVRT(
                src,
                crs=TARGET_CRS,
                transform=transform,
                width=width,
                height=height,
                resampling=getattr(Resampling, resampling),
            ) as vrt:
                with rasterio.open(target, "w", **profile) as dst:
                    done = 0
                    for row in range(0, height, block_rows):
                        rows = min(block_rows, height - row)
                        from rasterio.windows import Window

                        window = Window(0, row, width, rows)
                        dst.write(vrt.read(window=window), window=window)
                        done += rows
                        if progress is not None:
                            progress(done / height)
        except Exception:
            target.unlink(missing_ok=True)
            raise

    if progress is not None:
        progress(1.0)

    return ReprojectResult(
        path=target,
        width=width,
        height=height,
        crs=TARGET_CRS,
        resolution_m=float(abs(transform.a)),
    )
