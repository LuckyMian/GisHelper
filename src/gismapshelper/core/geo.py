"""经纬度 / Web 墨卡托 / 瓦片编号的纯数学工具。

只依赖标准库，界面层与核心层共用；所有函数都是无状态的，
方便直接写单元测试。
"""

from __future__ import annotations

import math

R = 6378137.0                      # WGS-84 长半轴（Web 墨卡托按球体处理）
ORIGIN = math.pi * R               # 20037508.342789244
TILE_MERC = 2.0 * ORIGIN           # 40075016.685578488，赤道周长
MAX_LAT = 85.05112877980659        # Web 墨卡托能表示的纬度上限
TILE_SIZE = 256                    # 标准瓦片边长（像素）


# --------------------------------------------------------------------------- 墨卡托
def clamp_lat(lat: float) -> float:
    """把纬度夹到 Web 墨卡托可表示的范围。"""
    return max(-MAX_LAT, min(MAX_LAT, float(lat)))


def lonlat_to_merc(lon: float, lat: float) -> tuple[float, float]:
    """经纬度 -> Web 墨卡托米。"""
    lat = clamp_lat(lat)
    x = R * math.radians(lon)
    y = R * math.asinh(math.tan(math.radians(lat)))
    return x, y


def merc_to_lon(x: float) -> float:
    return math.degrees(x / R)


def merc_to_lat(y: float) -> float:
    return math.degrees(math.atan(math.sinh(y / R)))


def merc_to_lonlat(x: float, y: float) -> tuple[float, float]:
    return merc_to_lon(x), merc_to_lat(y)


# --------------------------------------------------------------------------- 世界像素
def world_size(zoom: int, tile_size: int = TILE_SIZE) -> float:
    """该层级整幅世界地图的像素边长。"""
    return float(tile_size) * (1 << int(zoom))


def lonlat_to_world(
    lon: float, lat: float, zoom: int, tile_size: int = TILE_SIZE
) -> tuple[float, float]:
    """经纬度 -> 该层级下的世界像素坐标（左上角为原点，向右向下增大）。"""
    size = world_size(zoom, tile_size)
    x, y = lonlat_to_merc(lon, lat)
    return (x + ORIGIN) / TILE_MERC * size, (ORIGIN - y) / TILE_MERC * size


def world_to_lonlat(
    px: float, py: float, zoom: int, tile_size: int = TILE_SIZE
) -> tuple[float, float]:
    """世界像素坐标 -> 经纬度。"""
    size = world_size(zoom, tile_size)
    x = px / size * TILE_MERC - ORIGIN
    y = ORIGIN - py / size * TILE_MERC
    return merc_to_lonlat(x, y)


def tile_bounds_merc(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """瓦片 (z, x, y) 的墨卡托范围 (west, south, east, north)。"""
    span = TILE_MERC / (1 << z)
    west = -ORIGIN + x * span
    north = ORIGIN - y * span
    return west, north - span, west + span, north


# --------------------------------------------------------------------------- 分辨率
def ground_resolution_m(lat: float, zoom: int, tile_size: int = TILE_SIZE) -> float:
    """某个纬度、某个层级下，每像素代表的地面米数。"""
    return TILE_MERC * math.cos(math.radians(clamp_lat(lat))) / (tile_size * (1 << int(zoom)))


def native_zoom_of(resolution_m: float, tile_size: int = TILE_SIZE) -> float:
    """由「米/像素」反推该数据真实精度对应的层级（可带小数）。"""
    if resolution_m <= 0:
        return 0.0
    return math.log2(TILE_MERC / (tile_size * resolution_m))


# --------------------------------------------------------------------------- 面积
def spherical_area_km2(west: float, south: float, east: float, north: float) -> float:
    """经纬度矩形的球面面积（平方公里）。"""
    north = clamp_lat(north)
    south = clamp_lat(south)
    lam = math.radians(east - west)
    phi1 = math.radians(south)
    phi2 = math.radians(north)
    area_m2 = (R * R) * abs(lam) * abs(math.sin(phi2) - math.sin(phi1))
    return area_m2 / 1e6


# --------------------------------------------------------------------------- GCJ-02
# 高德 / 腾讯等国内底图使用 GCJ-02（火星坐标）；与 WGS-84 直接套合会偏 100~600 米。
_GJ_A = 6378245.0
_GJ_EE = 0.00669342162296594323


def _out_of_china(lon: float, lat: float) -> bool:
    return not (73.66 < lon < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    """WGS-84 -> GCJ-02。"""
    if _out_of_china(lon, lat):
        return lon, lat
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    radlat = math.radians(lat)
    magic = 1 - _GJ_EE * math.sin(radlat) ** 2
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_GJ_A * (1 - _GJ_EE)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (_GJ_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lon + dlon, lat + dlat


def gcj02_to_wgs84(lon: float, lat: float) -> tuple[float, float]:
    """GCJ-02 -> WGS-84（迭代反解）。"""
    if _out_of_china(lon, lat):
        return lon, lat
    wlon, wlat = lon, lat
    for _ in range(8):
        glon, glat = wgs84_to_gcj02(wlon, wlat)
        dlon, dlat = glon - lon, glat - lat
        wlon -= dlon
        wlat -= dlat
        if abs(dlon) < 1e-10 and abs(dlat) < 1e-10:
            break
    return wlon, wlat
