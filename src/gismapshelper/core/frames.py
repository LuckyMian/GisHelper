"""以项目原点为中心的切片方形范围。

两种方案：

* ``distance``：边长直接给地面米数（1 km / 2 km / 5 km / 10 km …）；
* ``pixels``：边长 = 像素数 × TIF 的投影像素分辨率，再按原点纬度换算成地面米。

所有范围都用 WGS-84 经纬度表达，既能直接画到瓦片底图上，也能
用 :func:`local_offsets` 换成以原点为 (0,0) 的本地米制坐标，导入三维软件。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .geo import R, clamp_lat

DISTANCE_PRESETS_M = (1000.0, 2000.0, 5000.0, 10000.0)
PIXEL_PRESETS = (1024, 2048, 4096, 8192, 16384)

MIN_EDGE_M = 10.0
MAX_EDGE_M = 5_000_000.0
MAX_PIXELS = 10_000_000

# 矩形相对参考点的位置：中心 / 四角。全项目默认都是中心。
ANCHORS = ("center", "nw", "ne", "sw", "se")
ANCHOR_LABELS = {
    "center": "中心",
    "nw": "左上",
    "ne": "右上",
    "sw": "左下",
    "se": "右下",
}
DEFAULT_ANCHOR = "center"


def meters_per_degree(lat: float) -> tuple[float, float]:
    """该纬度上 1° 经度 / 1° 纬度对应的地面米数（球面近似）。"""
    lat_rad = math.radians(clamp_lat(lat))
    per_degree = math.pi * R / 180.0
    return per_degree * math.cos(lat_rad), per_degree


def square_bounds(
    lon: float, lat: float, edge_m: float, anchor: str = DEFAULT_ANCHOR
) -> tuple[float, float, float, float]:
    """边长 ``edge_m``（地面米）的正方形范围。

    ``anchor`` 决定 (lon, lat) 落在矩形的哪个位置：``center``（默认）或四角
    ``nw`` / ``ne`` / ``sw`` / ``se``。
    """
    if anchor not in ANCHORS:
        raise ValueError(f"未知参考点：{anchor}")

    edge = max(MIN_EDGE_M, min(MAX_EDGE_M, float(edge_m)))
    m_per_deg_lon, m_per_deg_lat = meters_per_degree(lat)
    dlat = edge / m_per_deg_lat
    dlon = edge / m_per_deg_lon if m_per_deg_lon > 1e-9 else 180.0

    west, south = lon, lat
    if anchor == "center":
        west, south = lon - dlon / 2.0, lat - dlat / 2.0
    elif anchor == "nw":
        west, south = lon, lat - dlat
    elif anchor == "ne":
        west, south = lon - dlon, lat - dlat
    elif anchor == "sw":
        west, south = lon, lat
    else:  # se
        west, south = lon - dlon, lat

    return (
        max(-180.0, west),
        max(-89.9, south),
        min(180.0, west + dlon),
        min(89.9, south + dlat),
    )


def anchor_point(
    bounds: tuple[float, float, float, float], anchor: str = DEFAULT_ANCHOR
) -> tuple[float, float]:
    """取矩形上某个参考点的经纬度（默认中心）。"""
    west, south, east, north = bounds
    if anchor == "center":
        return (west + east) / 2.0, (south + north) / 2.0
    if anchor == "nw":
        return west, north
    if anchor == "ne":
        return east, north
    if anchor == "sw":
        return west, south
    if anchor == "se":
        return east, south
    raise ValueError(f"未知参考点：{anchor}")


def edge_from_pixels(pixels: float, resolution_m_per_px: float, lat: float) -> float:
    """像素边长 -> 地面米边长。

    ``resolution_m_per_px`` 是 Web 墨卡托（投影像素）米/像素；地面尺寸还要乘
    ``cos(纬度)``，否则在 40°N 会把 1 km 画成 0.77 km。
    """
    return float(pixels) * float(resolution_m_per_px) * math.cos(math.radians(clamp_lat(lat)))


def local_offsets(
    bounds: tuple[float, float, float, float], origin_lon: float, origin_lat: float
) -> tuple[float, float, float, float]:
    """矩形四边相对原点的米偏移 (西, 南, 东, 北)，东/北为正。

    三维软件里把原点当 (0,0)，这就是四边形的地面坐标范围。
    """
    west, south, east, north = bounds
    m_per_deg_lon, m_per_deg_lat = meters_per_degree(origin_lat)
    return (
        (west - origin_lon) * m_per_deg_lon,
        (south - origin_lat) * m_per_deg_lat,
        (east - origin_lon) * m_per_deg_lon,
        (north - origin_lat) * m_per_deg_lat,
    )


def format_distance(meters: float) -> str:
    """把米数写成便于阅读的形式：800 m / 1.5 km / 12 km。"""
    if meters >= 1000.0:
        return f"{meters / 1000.0:g} km"
    return f"{meters:g} m"


@dataclass(frozen=True)
class SliceSpec:
    """一种切片方形方案：``distance`` 用米，``pixels`` 用 TIF 像素。"""

    kind: str
    value: float
    anchor: str = DEFAULT_ANCHOR

    def edge_m(self, resolution_m_per_px: float | None, lat: float) -> float | None:
        if self.kind == "distance":
            return max(MIN_EDGE_M, min(MAX_EDGE_M, float(self.value)))
        if resolution_m_per_px is None or resolution_m_per_px <= 0:
            return None
        return edge_from_pixels(self.value, resolution_m_per_px, lat)

    def title(self) -> str:
        suffix = "" if self.anchor == DEFAULT_ANCHOR else f"·{ANCHOR_LABELS[self.anchor]}"
        if self.kind == "distance":
            return f"{format_distance(self.value)} 方案{suffix}"
        return f"{self.value:g} px 方案{suffix}"
