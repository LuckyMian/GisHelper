"""把影像按 N×N 网格切开。

只算几何：像素窗口 + 对应的 WGS-84 范围；读像素、写文件在 :mod:`grid_export`。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import geo
from .raster import RasterInfo

# 界面上的预设：2×2 / 3×3 / 6×6 / 9×9
GRID_PRESETS = (2, 3, 6, 9)

# 宽高相差多少以内算「正方形」
SQUARE_TOLERANCE = 0.01

_SUPPORTED_CRS = ("EPSG:4326", "EPSG:3857")


class GridError(Exception):
    """网格切分无法进行。"""


@dataclass(frozen=True)
class GridCell:
    index: int
    row: int
    col: int
    window: tuple[int, int, int, int]              # 源像素 (col, row, width, height)
    bounds_wgs84: tuple[float, float, float, float]

    @property
    def name(self) -> str:
        return f"r{self.row + 1}c{self.col + 1}"


def cells(info: RasterInfo, n: int) -> list[GridCell]:
    """把 ``info`` 的范围等分成 n×n 个格子，边界按像素取整、互不重叠。"""
    if n < 1:
        raise GridError("网格数必须 ≥ 1")
    if info.transform is None:
        raise GridError("缺少仿射变换，无法计算网格")
    if info.crs not in _SUPPORTED_CRS:
        raise GridError(f"暂不支持 {info.crs}；需要 EPSG:4326 或 EPSG:3857")

    columns = _split(info.width, n)
    rows = _split(info.height, n)

    result: list[GridCell] = []
    for row_index, (top, bottom) in enumerate(rows):
        for col_index, (left, right) in enumerate(columns):
            window = (left, top, right - left, bottom - top)
            bounds = _window_bounds(info, window)
            result.append(
                GridCell(
                    index=len(result),
                    row=row_index,
                    col=col_index,
                    window=window,
                    bounds_wgs84=bounds,
                )
            )
    return result


def _split(total: int, n: int) -> list[tuple[int, int]]:
    """把总像素数切成 n 段，按四舍五入对齐像素边界。"""
    edges = [round(i * total / n) for i in range(n + 1)]
    return [(edges[i], edges[i + 1]) for i in range(n)]


def _window_bounds(
    info: RasterInfo, window: tuple[int, int, int, int]
) -> tuple[float, float, float, float]:
    """像素窗口 → WGS-84 矩形（按窗口外沿）。"""
    transform = info.transform
    if transform is None:  # pragma: no cover - 调用前已检查
        raise GridError("缺少仿射变换")

    col, row, width, height = window
    x0 = transform.a * col + transform.b * row + transform.c
    y0 = transform.d * col + transform.e * row + transform.f
    x1 = transform.a * (col + width) + transform.b * (row + height) + transform.c
    y1 = transform.d * (col + width) + transform.e * (row + height) + transform.f

    left, right = sorted((x0, x1))
    bottom, top = sorted((y0, y1))

    if info.crs == "EPSG:4326":
        return (left, bottom, right, top)

    west, south = geo.merc_to_lonlat(left, bottom)
    east, north = geo.merc_to_lonlat(right, top)
    return (min(west, east), min(south, north), max(west, east), max(south, north))


def size_3857(info: RasterInfo) -> tuple[float, float] | None:
    """影像在 EPSG:3857 下的宽高（米）；没有经纬度范围时返回 None。"""
    if info.lonlat is None:
        return None
    west, south, east, north = info.lonlat.as_tuple()
    x0, y0 = geo.lonlat_to_merc(west, south)
    x1, y1 = geo.lonlat_to_merc(east, north)
    return abs(x1 - x0), abs(y1 - y0)


def squareness(info: RasterInfo) -> tuple[bool, float, float]:
    """(是否正方形, 宽米, 高米)；宽高相差超过 ``SQUARE_TOLERANCE`` 就算不是。"""
    size = size_3857(info)
    if size is None:
        return False, 0.0, 0.0
    width, height = size
    if width <= 0 or height <= 0:
        return False, width, height
    ratio = abs(width - height) / max(width, height)
    return ratio <= SQUARE_TOLERANCE, width, height
