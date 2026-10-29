#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gismap.py —— 谷歌卫星地图切片 / 分幅 / 格式转换工具。

子命令
    inspect  识别输入（大图+坐标文件 / GeoTIFF / 瓦片目录 / MBTiles），报告范围与推荐层级
    tile     按 Web 墨卡托 XYZ 规则切片，输出 XYZ 目录、TMS 目录、MBTiles 及预览页
    export   导出整幅大图（可缩放），或按 2x2 / 3x3 / 4x4 等分幅导出，附带 .jgw/.prj
    verify   校验切片结果（数量、尺寸、与源图的一致性）

只依赖 Pillow（DSH 自带）。
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageFile, ImageStat
except ImportError:  # pragma: no cover
    sys.exit(
        "缺少 Pillow。请先调用 load_workspace_dependencies 取得内置 Python 路径，"
        "再用该 python.exe 运行本脚本；不要自行安装包。"
    )

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = None  # 允许超大图；内存风险由本脚本自行提示

_RES = getattr(Image, "Resampling", Image)
LANCZOS = _RES.LANCZOS
BILINEAR = _RES.BILINEAR
BICUBIC = _RES.BICUBIC
AFFINE = getattr(Image, "Transform", Image).AFFINE

R = 6378137.0
ORIGIN = math.pi * R                 # 20037508.342789244
TILE_MERC = 2.0 * ORIGIN             # 40075016.685578488
MAX_LAT = 85.05112877980659

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
WORLD_EXT = {
    ".jpg": ".jgw", ".jpeg": ".jgw", ".png": ".pgw",
    ".tif": ".tfw", ".tiff": ".tfw", ".webp": ".wld",
}
# 下载器命名不统一：.tfw 配 .jpg 非常常见，所以查找时把所有变体都试一遍
WORLD_EXTS_ALL = (".tfw", ".jgw", ".pgw", ".wld", ".bpw", ".jtw", ".eww")
SIDE_EXT = {".tfw", ".jgw", ".pgw", ".wld", ".aux", ".xml", ".map", ".json", ".txt", ".prj", ".csv"}


# ----------------------------------------------------------------------------
# 坐标数学
# ----------------------------------------------------------------------------

def clamp_lat(lat: float) -> float:
    return max(-MAX_LAT, min(MAX_LAT, float(lat)))


def lonlat_to_merc(lon: float, lat: float) -> tuple[float, float]:
    lat = clamp_lat(lat)
    x = R * math.radians(lon)
    y = R * math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))
    return x, y


def merc_to_lon(x: float) -> float:
    return math.degrees(x / R)


def merc_to_lat(y: float) -> float:
    return math.degrees(2.0 * math.atan(math.exp(y / R)) - math.pi / 2.0)


def tile_bounds_merc(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """返回瓦片的墨卡托范围 (west, south, east, north)。"""
    span = TILE_MERC / (1 << z)
    west = -ORIGIN + x * span
    north = ORIGIN - y * span
    return west, north - span, west + span, north


def _tx(x: float, z: int) -> float:
    return (x + ORIGIN) / TILE_MERC * (1 << z)


def _ty(y: float, z: int) -> float:
    return (ORIGIN - y) / TILE_MERC * (1 << z)


def tile_range_for_bounds(west: float, south: float, east: float, north: float, z: int):
    """经纬度范围 -> 该层级覆盖的瓦片索引范围（含端点，已裁剪到合法范围）。"""
    n = 1 << z
    mw, ms = lonlat_to_merc(west, south)
    me, mn = lonlat_to_merc(east, north)
    x0 = int(math.floor(_tx(mw, z) + 1e-9))
    x1 = int(math.ceil(_tx(me, z) - 1e-9)) - 1
    y0 = int(math.floor(_ty(mn, z) + 1e-9))
    y1 = int(math.ceil(_ty(ms, z) - 1e-9)) - 1
    x0 = max(0, min(n - 1, x0))
    x1 = max(0, min(n - 1, x1))
    y0 = max(0, min(n - 1, y0))
    y1 = max(0, min(n - 1, y1))
    if x1 < x0 or y1 < y0:
        return None
    return x0, x1, y0, y1


# ----------------------------------------------------------------------------
# GCJ-02 / WGS-84 互转（国内底图对齐必备）
# ----------------------------------------------------------------------------

_GJ_A = 6378245.0
_GJ_EE = 0.00669342162296594323


def _out_of_china(lon: float, lat: float) -> bool:
    return not (73.66 < lon < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    if _out_of_china(lon, lat):
        return lon, lat
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _GJ_EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_GJ_A * (1 - _GJ_EE)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (_GJ_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lon + dlon, lat + dlat


def gcj02_to_wgs84(lon: float, lat: float) -> tuple[float, float]:
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


# ----------------------------------------------------------------------------
# 读文本 / 侧车文件解析
# ----------------------------------------------------------------------------

def read_text_guess(path: str | Path, limit: int = 4_000_000) -> str:
    with open(path, "rb") as fh:
        raw = fh.read(limit)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", "ignore")


def parse_bounds_text(text: str):
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if len(parts) != 4:
        raise SystemExit(f"--bounds 需要 4 个数（西,南,东,北），收到：{text!r}")
    west, south, east, north = (float(p) for p in parts)
    if west >= east or south >= north:
        raise SystemExit("--bounds 要求 西<东 且 南<北")
    return west, south, east, north


def guess_crs_from_numbers(values) -> str:
    """按坐标绝对值猜 CRS：大于 180 基本是米（3857），否则是度（4326）。"""
    for v in values:
        if abs(float(v)) > 180.0:
            return "EPSG:3857"
    return "EPSG:4326"


def crs_from_prj(text: str) -> str | None:
    low = text.lower()
    if "3857" in low or "web_mercator" in low or "pseudo-mercator" in low or "auxiliary_sphere" in low:
        return "EPSG:3857"
    if "4326" in low or "wgs_1984" in low or "wgs84" in low:
        return "EPSG:4326"
    return None


def bounds_from_worldfile(path: str | Path, width: int, height: int):
    vals = [float(v) for v in re.split(r"[,\s]+", read_text_guess(path).strip()) if v]
    if len(vals) < 6:
        raise ValueError("世界文件不足 6 行")
    a, _d, _b, e, c, f = vals[:6]
    west = c - a / 2.0
    north = f - e / 2.0
    east = west + a * width
    south = north + e * height
    crs = guess_crs_from_numbers([west, north, east, south])
    return (west, south, east, north), crs


def bounds_from_aux_xml(path: str | Path, width: int, height: int):
    txt = read_text_guess(path)
    m = re.search(r"<GeoTransform>\s*([^<]+?)\s*</GeoTransform>", txt, re.I)
    if not m:
        return None
    gt = [float(v) for v in m.group(1).split(",")]
    if len(gt) < 6:
        return None
    x0, pxw, _r1, y0, _r2, pxh = gt[:6]
    west, north = x0, y0
    east = west + pxw * width
    south = north + pxh * height
    crs = None
    m2 = re.search(r"<SRS[^>]*>([^<]+)</SRS>", txt, re.I)
    if m2:
        crs = crs_from_prj(m2.group(1))
    return ((west, south, east, north), crs or guess_crs_from_numbers([west, north, east, south]))


def geotiff_info(img: Image.Image):
    """从 GeoTIFF 内部标签读取范围与 CRS。仅支持北向上的标准标签。"""
    tags = getattr(img, "tag_v2", None)
    if tags is None:
        return None
    try:
        scale = tags.get(33550)
        tie = tags.get(33922)
    except Exception:
        return None
    if not scale or not tie:
        return None
    try:
        sx, sy = float(scale[0]), float(scale[1])
        i, j = float(tie[0]), float(tie[1])
        px, py = float(tie[3]), float(tie[4])
    except (TypeError, ValueError, IndexError):
        return None
    west = px - i * sx
    north = py + j * sy
    east = west + img.width * sx
    south = north - img.height * sy

    crs = None
    try:
        keys = tags.get(34735)
        if keys:
            kv = list(keys)
            for k in range(4, len(kv) - 3, 4):
                key_id, _loc, _cnt, val = kv[k], kv[k + 1], kv[k + 2], kv[k + 3]
                if key_id == 3072:      # ProjectedCSTypeGeoKey
                    if val == 3857:
                        crs = "EPSG:3857"
                    elif val not in (0, 32767):
                        crs = f"EPSG:{val}"
                elif key_id == 2048 and crs is None:  # GeographicTypeGeoKey
                    if val == 4326:
                        crs = "EPSG:4326"
    except Exception:
        pass
    return (west, south, east, north), crs or guess_crs_from_numbers([west, north, east, south])


_KEY_SETS = {
    "west": r"(?:west|lon_?west|left|min_?lon|lon_?min|x_?min|xmin|westbound|minx)",
    "east": r"(?:east|lon_?east|right|max_?lon|lon_?max|x_?max|xmax|eastbound|maxx)",
    "south": r"(?:south|lat_?south|bottom|min_?lat|lat_?min|y_?min|ymin|southbound|miny)",
    "north": r"(?:north|lat_?north|top|max_?lat|lat_?max|y_?max|ymax|northbound|maxy)",
}
_NUM = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"


def _scan_key(text: str, keys: str):
    m = re.search(rf"(?:{keys})\s*(?:=|:|>|\")\s*[\"']?\s*({_NUM})", text, re.I)
    return float(m.group(1)) if m else None


def _find_json_bounds(obj):
    if isinstance(obj, dict):
        low = {str(k).lower(): v for k, v in obj.items()}
        got = {}
        for side, keys in _KEY_SETS.items():
            for k, v in low.items():
                if re.search(rf"^(?:{keys})$", k) and isinstance(v, (int, float)):
                    got[side] = float(v)
                    break
        if len(got) == 4:
            return got["west"], got["south"], got["east"], got["north"]
        for key in ("bbox", "bounds", "extent", "lonlatbbox", "boundingbox"):
            v = low.get(key)
            if isinstance(v, (list, tuple)) and len(v) == 4 and all(
                isinstance(x, (int, float)) for x in v
            ):
                return float(v[0]), float(v[1]), float(v[2]), float(v[3])
        for v in obj.values():
            r = _find_json_bounds(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_json_bounds(v)
            if r:
                return r
    return None


def bounds_from_sidecar_text(path: str | Path):
    txt = read_text_guess(path)
    if not txt.strip():
        return None
    if path.lower().endswith(".json") or txt.lstrip()[:1] in "{[":
        try:
            r = _find_json_bounds(json.loads(txt))
            if r:
                return r, guess_crs_from_numbers(r)
        except json.JSONDecodeError:
            pass
    got = {side: _scan_key(txt, keys) for side, keys in _KEY_SETS.items()}
    if all(got[s] is not None for s in got):
        b = (got["west"], got["south"], got["east"], got["north"])
        if b[0] < b[2] and b[1] < b[3]:
            return b, guess_crs_from_numbers(b)
    return None


def find_worldfile(stem: Path) -> Path | None:
    for ext in WORLD_EXTS_ALL:
        p = stem.with_suffix(ext)
        if p.exists() and p.is_file():
            return p
    return None


def resolve_georef(img_path: Path, img: Image.Image):
    """返回 (bounds, crs, 来源说明)。找不到时返回 (None, None, None)。"""
    if img_path.suffix.lower() in {".tif", ".tiff"}:
        info = geotiff_info(img)
        if info:
            b, crs = info
            if crs and crs.startswith("EPSG:") and crs not in ("EPSG:4326", "EPSG:3857"):
                raise SystemExit(
                    f"输入 GeoTIFF 的坐标系是 {crs}，本工具只支持 EPSG:4326 与 EPSG:3857。"
                    " 请先用 QGIS/GDAL 重投影：gdalwarp -t_srs EPSG:3857 in.tif out.tif"
                )
            return b, crs, "GeoTIFF 内部标签"

    stem = img_path.with_suffix("")
    prj_crs = None
    for p in list(stem.parent.glob(stem.name + ".*")):
        if p.suffix.lower() == ".prj":
            try:
                prj_crs = crs_from_prj(read_text_guess(p))
            except OSError:
                pass

    wf = find_worldfile(stem)
    if wf is not None:
        b, crs = bounds_from_worldfile(wf, img.width, img.height)
        return b, prj_crs or crs, f"世界文件 {wf.name}"

    aux = img_path.with_name(img_path.name + ".aux.xml")
    if aux.exists():
        r = bounds_from_aux_xml(aux, img.width, img.height)
        if r:
            return r[0], prj_crs or r[1], f"辅助文件 {aux.name}"

    for ext in (".map", ".xml", ".json", ".txt", ".csv"):
        p = stem.with_suffix(ext)
        if not p.exists():
            continue
        try:
            r = bounds_from_sidecar_text(p)
        except OSError:
            continue
        if r:
            return r[0], prj_crs or r[1], f"侧车文件 {p.name}"

    if prj_crs:
        return None, prj_crs, None
    return None, None, None


# ----------------------------------------------------------------------------
# 源数据（大图 / 瓦片目录 / MBTiles）
# ----------------------------------------------------------------------------

def fill_rgba(edge: str, mode: str):
    if edge == "transparent" and mode == "RGBA":
        return (0, 0, 0, 0)
    if edge == "black":
        return (0, 0, 0) if mode == "RGB" else (0, 0, 0, 255)
    return (255, 255, 255) if mode == "RGB" else (255, 255, 255, 255)


def warp_tile(img, west, south, east, north, crs, z, x, y, size, mode, edge, resample=BILINEAR):
    """把一块地理范围渲染成 (z,x,y) 瓦片；完全无覆盖返回 None。"""
    mw, ms, me, mn = tile_bounds_merc(z, x, y)
    if crs == "EPSG:3857":
        left, right, top, bottom = mw, me, mn, ms
    else:
        left, right = merc_to_lon(mw), merc_to_lon(me)
        top, bottom = merc_to_lat(mn), merc_to_lat(ms)

    W, H = img.size
    col_l = (left - west) / (east - west) * W
    col_r = (right - west) / (east - west) * W
    row_t = (north - top) / (north - south) * H
    row_b = (north - bottom) / (north - south) * H

    if col_r <= 0 or col_l >= W or row_b <= 0 or row_t >= H:
        return None

    a = (col_r - col_l) / size
    e = (row_b - row_t) / size
    coeffs = (a, 0.0, col_l, 0.0, e, row_t)
    return img.transform(
        (size, size), AFFINE, coeffs, resample=resample, fillcolor=fill_rgba(edge, mode)
    )


class RasterSource:
    kind = "image"

    def __init__(self, img: Image.Image, bounds, crs: str, name: str, meta=None):
        self._img = img
        self.west, self.south, self.east, self.north = (float(v) for v in bounds)
        self.crs = crs
        self.name = name
        self.meta = dict(meta or {})
        self.mode = img.mode if img.mode in ("RGB", "RGBA") else "RGB"
        if img.mode not in ("RGB", "RGBA"):
            self._img = img.convert(self.mode)
        self._mips = {}

    # -- 基础 -------------------------------------------------------------
    def set_mode(self, mode: str):
        if mode != self.mode:
            self._img = self._img.convert(mode)
            self.mode = mode
            self._mips = {}

    def prepare(self):
        self._img.load()

    @property
    def size(self):
        return self._img.size

    def merc_bounds(self):
        if self.crs == "EPSG:4326":
            x0, y0 = lonlat_to_merc(self.west, self.south)
            x1, y1 = lonlat_to_merc(self.east, self.north)
            return x0, y0, x1, y1
        return self.west, self.south, self.east, self.north

    def lonlat_bounds(self):
        if self.crs == "EPSG:4326":
            return self.west, self.south, self.east, self.north
        return (
            merc_to_lon(self.west), merc_to_lat(self.south),
            merc_to_lon(self.east), merc_to_lat(self.north),
        )

    def resolution(self) -> tuple[float, float]:
        """(x 方向米/像素, y 方向米/像素)"""
        x0, y0, x1, y1 = self.merc_bounds()
        W, H = self.size
        return (x1 - x0) / max(1, W), (y1 - y0) / max(1, H)

    def native_zoom(self) -> float:
        rx, ry = self.resolution()
        res = (rx + ry) / 2.0
        if res <= 0:
            return 0.0
        return math.log2(TILE_MERC / (256.0 * res))

    # -- 金字塔 -----------------------------------------------------------
    def reduction_for(self, z: int) -> int:
        k = int(math.floor(self.native_zoom() - z))
        return max(0, k)

    def _level(self, k: int):
        if k <= 0:
            return self._img
        if k in self._mips:
            return self._mips[k]
        base = self._level(k - 1)
        img = base.resize((max(1, base.width // 2), max(1, base.height // 2)), LANCZOS)
        self._mips[k] = img
        return img

    # -- 出图 -------------------------------------------------------------
    def tile_image(self, z, x, y, size, edge):
        img = self._level(self.reduction_for(z))
        return warp_tile(
            img, self.west, self.south, self.east, self.north, self.crs,
            z, x, y, size, self.mode, edge,
        )


class TileDirSource:
    """z/x/y 目录（XYZ 或 TMS 命名均可，自动识别）。"""

    kind = "tiledir"

    def __init__(self, root: Path, mode: str = "RGB", input_tms: bool = False):
        self.root = Path(root)
        self.mode = mode
        self.name = self.root.name
        self._cache = {}
        self.zooms = self._scan_zooms()
        if not self.zooms:
            raise SystemExit(f"{self.root} 里没有找到 z/x/y 结构的瓦片")
        self._tile_size = self._detect_tile_size()
        self._ext = self._detect_ext()
        self._index = {}
        self._tms = bool(input_tms)
        self.meta = {"tile_size": self._tile_size, "ext": self._ext, "input_tms": self._tms}

    def _scan_zooms(self):
        out = []
        try:
            for d in os.scandir(self.root):
                if d.is_dir() and d.name.isdigit():
                    out.append(int(d.name))
        except OSError as exc:
            raise SystemExit(f"无法读取 {self.root}: {exc}")
        return sorted(out)

    def _detect_ext(self):
        z = self.zooms[-1]
        zdir = self.root / str(z)
        for xd in os.scandir(zdir):
            if not xd.is_dir():
                continue
            for f in os.scandir(xd.path):
                p = Path(f.name)
                if p.suffix.lower() in IMG_EXTS:
                    return p.suffix.lower()
        return ".png"

    def _detect_tile_size(self):
        z = self.zooms[-1]
        for xd in os.scandir(self.root / str(z)):
            if not xd.is_dir():
                continue
            for f in os.scandir(xd.path):
                if Path(f.name).suffix.lower() in IMG_EXTS:
                    try:
                        with Image.open(f.path) as im:
                            return im.width
                    except OSError:
                        continue
        return 256

    def prepare(self):
        """瓦片源按需读取，无需预加载。"""

    def set_mode(self, mode: str):
        self.mode = mode

    @property
    def tile_size(self):
        return self._tile_size

    def _index_of(self, z):
        if z not in self._index:
            s = set()
            d = self.root / str(z)
            if d.is_dir():
                for xd in os.scandir(d):
                    if xd.is_dir() and xd.name.lstrip("-").isdigit():
                        xi = int(xd.name)
                        for f in os.scandir(xd.path):
                            stem = Path(f.name).stem
                            if stem.isdigit():
                                row = int(stem)
                                yy = ((1 << z) - 1 - row) if self._tms else row
                                s.add((xi, yy))
            self._index[z] = s
        return self._index[z]

    def _path_for(self, z, x, y):
        yy = ((1 << z) - 1 - y) if self._tms else y
        return self.root / str(z) / str(x) / f"{yy}{self._ext}"

    def has_tile(self, z, x, y):
        return (x, y) in self._index_of(z)

    def availability(self, z):
        return self._index_of(z)

    def lonlat_bounds(self):
        z = self.zooms[-1]
        idx = self._index_of(z)
        if not idx:
            for z in reversed(self.zooms):
                idx = self._index_of(z)
                if idx:
                    break
        if not idx:
            return (0, 0, 0, 0)
        xs = [t[0] for t in idx]
        ys = [t[1] for t in idx]
        w, s, _, _ = tile_bounds_merc(z, min(xs), max(ys))
        _, _, e, n = tile_bounds_merc(z, max(xs), min(ys))
        return merc_to_lon(w), merc_to_lat(s), merc_to_lon(e), merc_to_lat(n)

    def merc_bounds(self):
        ll = self.lonlat_bounds()
        x0, y0 = lonlat_to_merc(ll[0], ll[1])
        x1, y1 = lonlat_to_merc(ll[2], ll[3])
        return x0, y0, x1, y1

    def native_zoom(self):
        return float(self.zooms[-1])

    def _pick_zoom(self, z):
        avail = self.zooms
        lower = [a for a in avail if a <= z]
        if lower:
            return max(lower)
        return min(avail)

    def _read_tile(self, z, x, y):
        p = self._path_for(z, x, y)
        if not p.exists():
            return None
        try:
            with Image.open(p) as im:
                im.load()
                return im.convert(self.mode)
        except OSError:
            return None

    def tile_image(self, z, x, y, size, edge):
        if z in self.zooms and self.has_tile(z, x, y) and size == self.tile_size:
            img = self._read_tile(z, x, y)
            if img is not None:
                return img
        zs = self._pick_zoom(z)
        return self._composite(zs, z, x, y, size, edge)

    def _composite(self, zs, z, x, y, size, edge):
        mw, ms, me, mn = tile_bounds_merc(z, x, y)
        n = 1 << zs
        tx0 = max(0, min(n - 1, int(math.floor(_tx(mw, zs)))))
        tx1 = max(0, min(n - 1, int(math.ceil(_tx(me, zs) - 1e-9)) - 1))
        ty0 = max(0, min(n - 1, int(math.floor(_ty(mn, zs)))))
        ty1 = max(0, min(n - 1, int(math.ceil(_ty(ms, zs) - 1e-9)) - 1))
        if tx1 < tx0 or ty1 < ty0:
            return None
        ts = self.tile_size
        canvas = Image.new(self.mode, ((tx1 - tx0 + 1) * ts, (ty1 - ty0 + 1) * ts),
                           fill_rgba("white", self.mode))
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                img = self._read_tile(zs, tx, ty)
                if img is not None:
                    if img.size != (ts, ts):
                        img = img.resize((ts, ts), BICUBIC)
                    canvas.paste(img, ((tx - tx0) * ts, (ty - ty0) * ts))
        w, s, e, nn = tile_bounds_merc(zs, tx0, ty0)
        e2 = tile_bounds_merc(zs, tx1, ty0)[2]
        s2 = tile_bounds_merc(zs, tx0, ty1)[1]
        return warp_tile(canvas, w, s2, e2, nn, "EPSG:3857", z, x, y, size, self.mode, edge)

    def to_raster(self, max_pixels: float = 4.0e8, z: int | None = None) -> "RasterSource":
        """把瓦片目录拼成一张大图，返回可统一处理的 RasterSource。"""
        z = z if z is not None else self.zooms[-1]
        idx = self._index_of(z)
        for fallback in reversed(self.zooms):
            if idx:
                break
            z = fallback
            idx = self._index_of(z)
        if not idx:
            raise SystemExit(f"{self.root} 在可用层级里没有瓦片")
        xs = [t[0] for t in idx]
        ys = [t[1] for t in idx]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        ts = self.tile_size
        W, H = (x1 - x0 + 1) * ts, (y1 - y0 + 1) * ts
        if W * H > max_pixels:
            raise SystemExit(
                f"拼接后约 {W}x{H} 像素（{W*H/1e6:.0f} MP），超过内存保护阈值。\n"
                "  请先用 tile 子命令直接转换，或缩小范围。"
            )
        canvas = Image.new("RGB", (W, H), (255, 255, 255))
        missing = 0
        for (x, y) in idx:
            img = self._read_tile(z, x, y)
            if img is None:
                missing += 1
                continue
            if img.size != (ts, ts):
                img = img.resize((ts, ts), BICUBIC)
            canvas.paste(img.convert("RGB"), ((x - x0) * ts, (y - y0) * ts))
        west, south, _, _ = tile_bounds_merc(z, x0, y1)
        _, _, east, north = tile_bounds_merc(z, x1, y0)
        meta = {"bounds_source": f"瓦片目录 z{z}（{len(idx)} 块，缺 {missing} 块）"}
        src = RasterSource(canvas, (west, south, east, north), "EPSG:3857", self.name, meta)
        if missing:
            src.meta["bounds_source"] += "，缺失处填白"
        return src


class MBTilesSource(TileDirSource):
    kind = "mbtiles"

    def __init__(self, path: Path, mode: str = "RGB"):
        self.path = Path(path)
        self.root = self.path
        self.mode = mode
        self.name = self.path.stem
        self._cache = {}
        self._tms = True
        self._index = {}
        self._meta = {}
        self._local = threading.local()
        try:
            con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            try:
                for k, v in con.execute("SELECT name, value FROM metadata"):
                    self._meta[str(k)] = str(v)
            except sqlite3.DatabaseError:
                pass
            self.zooms = sorted(
                r[0] for r in con.execute("SELECT DISTINCT zoom_level FROM tiles")
            )
            con.close()
        except sqlite3.DatabaseError as exc:
            raise SystemExit(f"无法读取 MBTiles {self.path}: {exc}")
        if not self.zooms:
            raise SystemExit(f"{self.path} 里没有瓦片")
        self._tile_size = int(self._meta.get("tile_size") or 256)
        fmt = (self._meta.get("format") or "png").lower()
        self._ext = ".jpg" if fmt in ("jpg", "jpeg") else "." + fmt
        self.columns = self._detect_columns()
        self.meta = dict(self._meta)

    def _detect_columns(self):
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(tiles)")]
        finally:
            con.close()
        return ("tile_column", "tile_row") if "tile_column" in cols else ("x", "y")

    def _con(self):
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            self._local.con = con
        return con

    def _index_of(self, z):
        if z not in self._index:
            col, row = self.columns
            con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            try:
                rows = con.execute(
                    f"SELECT {col}, {row} FROM tiles WHERE zoom_level=?", (z,)
                ).fetchall()
            finally:
                con.close()
            self._index[z] = {(int(a), int(b)) for a, b in rows}
        return self._index[z]

    def _read_tile(self, z, x, y):
        col, row = self.columns
        ty = ((1 << z) - 1 - y) if self._tms else y
        try:
            r = self._con().execute(
                f"SELECT tile_data FROM tiles WHERE zoom_level=? AND {col}=? AND {row}=?",
                (z, x, ty),
            ).fetchone()
        except sqlite3.DatabaseError:
            return None
        if not r:
            return None
        try:
            with Image.open(io.BytesIO(r[0])) as im:
                im.load()
                return im.convert(self.mode)
        except OSError:
            return None

    def lonlat_bounds(self):
        b = self._meta.get("bounds")
        if b:
            try:
                w, s, e, n = (float(v) for v in b.split(",")[:4])
                return w, s, e, n
            except ValueError:
                pass
        return super().lonlat_bounds()


def is_tile_dir(p: Path) -> bool:
    try:
        for d in os.scandir(p):
            if not (d.is_dir() and d.name.isdigit()):
                continue
            for xd in os.scandir(d.path):
                if xd.is_dir() and xd.name.isdigit():
                    for f in os.scandir(xd.path):
                        if Path(f.name).suffix.lower() in IMG_EXTS:
                            return True
    except OSError:
        return False
    return False


def open_source(path: str | Path, mode: str = "RGB", input_tms: bool = False):
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"找不到输入：{p}")
    if p.is_dir():
        if is_tile_dir(p):
            return TileDirSource(p, mode, input_tms)
        raise SystemExit(f"{p} 不是 z/x/y 瓦片目录，也不是可识别的输入。")
    ext = p.suffix.lower()
    if ext in (".mbtiles", ".sqlite", ".sqlite3"):
        return MBTilesSource(p, mode)
    if ext in IMG_EXTS:
        img = Image.open(p)
        bounds, crs, src = resolve_georef(p, img)
        meta = {"bounds_source": src, "path": str(p)}
        if not bounds:
            raise SystemExit(
                f"没能从 {p.name} 检测到地理范围。\n"
                "  请显式给出范围：--bounds 西,南,东,北（或 --source-crs 3857 --bounds 米制坐标）"
            )
        return RasterSource(img.convert(mode), bounds, crs, p.stem, meta)
    raise SystemExit(f"不支持的输入类型：{p}")


# ----------------------------------------------------------------------------
# 输出端
# ----------------------------------------------------------------------------

class DirSink:
    def __init__(self, root: Path, ext: str, tms: bool):
        self.root = Path(root)
        self.ext = ext
        self.tms = tms
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, z, x, y, data: bytes):
        yy = ((1 << z) - 1 - y) if self.tms else y
        d = self.root / str(z) / str(x)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"{yy}.{self.ext}", "wb") as fh:
            fh.write(data)


class MBTilesSink:
    def __init__(self, path: Path, fmt: str):
        self.path = Path(path)
        if self.path.exists():
            self.path.unlink()
        self.con = sqlite3.connect(str(self.path))
        self.con.execute("PRAGMA journal_mode=OFF")
        self.con.execute("PRAGMA synchronous=OFF")
        self.con.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
        self.con.execute(
            "CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, "
            "tile_row INTEGER, tile_data BLOB)"
        )
        self.fmt = fmt
        self._buf = []

    def write(self, z, x, y, data: bytes):
        self._buf.append((z, x, (1 << z) - 1 - y, data))
        if len(self._buf) >= 2000:
            self.flush()

    def flush(self):
        if self._buf:
            self.con.executemany(
                "INSERT INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)",
                self._buf,
            )
            self._buf.clear()

    def close(self, metadata: dict):
        self.flush()
        self.con.execute(
            "CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)"
        )
        self.con.executemany(
            "INSERT INTO metadata (name, value) VALUES (?,?)",
            [(str(k), str(v)) for k, v in metadata.items()],
        )
        self.con.commit()
        self.con.close()


def encode_tile(img: Image.Image, fmt: str, quality: int) -> bytes:
    buf = io.BytesIO()
    if fmt == "jpg":
        img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    elif fmt == "webp":
        img.save(buf, "WEBP", quality=quality, method=4)
    else:
        img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ----------------------------------------------------------------------------
# 预览页
# ----------------------------------------------------------------------------

_TEMPLATE_FALLBACK = """<!doctype html><html><head><meta charset="utf-8"><title>{{TITLE}}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#map{margin:0;height:100%}</style></head><body><div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const C={{CONF}};const m=L.map('map');L.tileLayer(C.tileUrl,{tms:C.tms,maxZoom:C.maxzoom,
minZoom:C.minzoom,tileSize:C.tileSize,noWrap:true}).addTo(m);L.rectangle(C.bounds,{color:'#f43'}).addTo(m);
m.fitBounds(C.bounds);</script></body></html>"""


def write_preview(outdir: Path, conf: dict, template: Path | None):
    text = None
    if template and template.exists():
        try:
            text = template.read_text(encoding="utf-8")
        except OSError:
            text = None
    if text is None:
        text = _TEMPLATE_FALLBACK
    title = str(conf.get("name") or "瓦片").replace("<", "&lt;")
    html = text.replace("{{TITLE}}", title).replace(
        "{{CONF}}", json.dumps(conf, ensure_ascii=False)
    )
    (outdir / "preview.html").write_text(html, encoding="utf-8")


def build_preview_conf(name, tile_rel, tms, bounds, minzoom, maxzoom, tile_size, scheme_note, ext):
    w, s, e, n = bounds
    return {
        "name": name,
        "tileUrl": tile_rel + "/{z}/{x}/{y}." + ext,
        "tms": bool(tms),
        "bounds": [[s, w], [n, e]],
        "minzoom": int(minzoom),
        "maxzoom": int(maxzoom),
        "tileSize": int(tile_size),
        "center": [(s + n) / 2.0, (w + e) / 2.0],
        "schemeNote": scheme_note,
    }


# ----------------------------------------------------------------------------
# 命令：inspect
# ----------------------------------------------------------------------------

def build_report(src) -> dict:
    if src.kind in ("tiledir", "mbtiles"):
        w, s, e, n = src.lonlat_bounds()
        native = src.native_zoom()
        return {
            "kind": src.kind,
            "path": str(getattr(src, "root", "")),
            "crs": "EPSG:3857",
            "bounds": {"west": w, "south": s, "east": e, "north": n},
            "zooms_available": getattr(src, "zooms", []),
            "tile_size": getattr(src, "tile_size", 256),
            "native_zoom": round(native, 2),
            "suggested": {"minzoom": max(0, int(native) - 2), "maxzoom": int(native)},
            "meta": {k: v for k, v in (src.meta or {}).items() if k != "path"},
            "notes": [],
        }
    w, s, e, n = src.lonlat_bounds()
    native = src.native_zoom()
    rx, ry = src.resolution()
    W, H = src.size
    notes = []
    if src.crs not in ("EPSG:4326", "EPSG:3857"):
        notes.append(f"源坐标系为 {src.crs}，按原值处理可能不准。")
    if W * H > 2.0e8:
        notes.append(f"源图 {W}x{H} 像素较大（约 {W*H/1e6:.0f} MP），切片时注意内存。")
    return {
        "kind": src.kind,
        "path": src.meta.get("path", ""),
        "image": {"width": W, "height": H, "mode": src.mode},
        "crs": src.crs,
        "bounds": {"west": w, "south": s, "east": e, "north": n},
        "bounds_source": src.meta.get("bounds_source"),
        "ground_resolution_m": round((rx + ry) / 2.0, 4),
        "native_zoom": round(native, 2),
        "suggested": {"minzoom": max(0, int(math.floor(native)) - 2),
                      "maxzoom": max(0, int(math.floor(native)))},
        "notes": notes,
    }


def cmd_inspect(args) -> int:
    src = open_source(args.source, input_tms=getattr(args, "input_tms", False))
    if args.bounds:
        b = parse_bounds_text(args.bounds)
        if args.bounds_datum == "gcj02":
            b = apply_datum(b, "gcj02")
        src.west, src.south, src.east, src.north = b
        if args.source_crs:
            src.crs = f"EPSG:{args.source_crs}"
        src.meta["bounds_source"] = "命令行 --bounds"
    report = build_report(src)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    b = report["bounds"]
    print(f"输入类型 : {report['kind']}")
    if report.get("image"):
        im = report["image"]
        print(f"图像尺寸 : {im['width']} x {im['height']} 像素（{im['mode']}）")
    print(f"坐标系   : {report['crs']}")
    print(f"地理范围 : 西 {b['west']:.7f}  南 {b['south']:.7f}  东 {b['east']:.7f}  北 {b['north']:.7f}")
    if report.get("bounds_source"):
        print(f"范围来源 : {report['bounds_source']}")
    if report.get("ground_resolution_m"):
        print(f"地面分辨率: {report['ground_resolution_m']} 米/像素")
    if report.get("zooms_available"):
        print(f"已有层级 : {report['zooms_available']}")
    print(f"原生层级 : z{report['native_zoom']}")
    print(f"建议切片 : z{report['suggested']['minzoom']} ~ z{report['suggested']['maxzoom']}")
    for note in report.get("notes", []):
        print(f"提示     : {note}")
    return 0


def apply_datum(bounds, datum: str):
    if datum != "gcj02":
        return bounds
    w, s, e, n = bounds
    w2, s2 = gcj02_to_wgs84(w, s)
    e2, n2 = gcj02_to_wgs84(e, n)
    return w2, s2, e2, n2


# ----------------------------------------------------------------------------
# 命令：tile
# ----------------------------------------------------------------------------

def choose_zooms(native: float, minzoom, maxzoom, overview: int, overzoom: int):
    base = max(0, int(math.floor(native + 1e-9)))
    if maxzoom is None:
        maxzoom = min(22, base + max(0, overzoom))
    if minzoom is None:
        minzoom = max(0, maxzoom - max(0, overview) - max(0, overzoom))
    minzoom = max(0, min(int(minzoom), int(maxzoom)))
    maxzoom = max(minzoom, min(22, int(maxzoom)))
    return minzoom, maxzoom


def cmd_tile(args) -> int:
    fmt = args.format
    need_alpha = args.edge == "transparent" and fmt != "jpg"
    mode = "RGBA" if need_alpha else "RGB"
    src = open_source(args.source, mode, input_tms=getattr(args, "input_tms", False))

    if args.bounds:
        b = parse_bounds_text(args.bounds)
        b = apply_datum(b, args.bounds_datum)
        src.west, src.south, src.east, src.north = b
        if args.source_crs:
            src.crs = f"EPSG:{args.source_crs}"
        src.meta["bounds_source"] = "命令行 --bounds"

    minzoom, maxzoom = choose_zooms(
        src.native_zoom(), args.minzoom, args.maxzoom, args.overview, args.overzoom
    )
    if args.zooms:
        lo, hi = (int(v) for v in args.zooms.split("-")) if "-" in args.zooms else (
            int(args.zooms), int(args.zooms))
        minzoom, maxzoom = lo, hi

    west, south, east, north = src.lonlat_bounds()
    plan = []
    total = 0
    for z in range(minzoom, maxzoom + 1):
        r = tile_range_for_bounds(west, south, east, north, z)
        if r is None:
            continue
        x0, x1, y0, y1 = r
        cnt = (x1 - x0 + 1) * (y1 - y0 + 1)
        plan.append((z, x0, x1, y0, y1, cnt))
        total += cnt

    if total == 0:
        raise SystemExit("按当前范围与层级没有可生成的瓦片，请检查 --bounds / --minzoom / --maxzoom。")

    outdir = Path(args.out)
    name = args.name or src.name or "gismap"

    if args.dry_run:
        report = build_report(src)
        print(json.dumps({
            "source": report,
            "output": str(outdir),
            "format": fmt,
            "tile_size": args.tilesize,
            "zooms": [{"z": z, "x": [x0, x1], "y": [y0, y1], "tiles": c}
                      for z, x0, x1, y0, y1, c in plan],
            "total_tiles": total,
        }, ensure_ascii=False, indent=2))
        return 0

    if total > 300000 and not args.force:
        raise SystemExit(
            f"预计生成 {total} 个瓦片，超过 30 万保护阈值。\n"
            "  确认无误请加 --force，或缩小范围 / 降低层级。"
        )
    if outdir.exists() and any(outdir.iterdir()) and not args.force:
        raise SystemExit(f"输出目录 {outdir} 非空。换个目录，或加 --force 覆盖。")
    outdir.mkdir(parents=True, exist_ok=True)

    schemes = [s.strip().lower() for s in args.scheme.split(",") if s.strip()]
    for s in schemes:
        if s not in ("xyz", "tms", "mbtiles"):
            raise SystemExit(f"未知的 --scheme：{s}（可选 xyz / tms / mbtiles）")

    ext = "jpg" if fmt == "jpg" else ("webp" if fmt == "webp" else "png")
    sinks = {}
    if "xyz" in schemes:
        sinks["xyz"] = DirSink(outdir / "tiles", ext, tms=False)
    if "tms" in schemes:
        sinks["tms"] = DirSink(outdir / "tiles_tms", ext, tms=True)
    mb = None
    if "mbtiles" in schemes:
        mb = MBTilesSink(outdir / f"{name}.mbtiles", ext)
        sinks["mbtiles"] = mb

    # 预算好各级金字塔，避免多线程反复构建
    for z, *_ in plan:
        if isinstance(src, RasterSource):
            src._level(src.reduction_for(z))
    src.prepare()

    started = time.time()
    written = 0
    skipped = 0

    def worker(task):
        z, x, y = task
        img = src.tile_image(z, x, y, args.tilesize, args.edge)
        if img is None:
            return z, x, y, None
        if img.size != (args.tilesize, args.tilesize):
            img = img.resize((args.tilesize, args.tilesize), BICUBIC)
        return z, x, y, encode_tile(img, fmt, args.quality)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for z, x0, x1, y0, y1, cnt in plan:
            tasks = ((z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))
            for _z, _x, _y, data in pool.map(worker, tasks, chunksize=32):
                if data is None:
                    skipped += 1
                    continue
                for sink in sinks.values():
                    sink.write(_z, _x, _y, data)
                written += 1
            print(f"  z{z}: {cnt} 个瓦片完成", flush=True)

    w, s, e, n = src.lonlat_bounds()
    meta = {
        "name": name,
        "format": ext,
        "bounds": f"{w:.8f},{s:.8f},{e:.8f},{n:.8f}",
        "center": f"{(w+e)/2:.8f},{(s+n)/2:.8f},{maxzoom}",
        "minzoom": minzoom,
        "maxzoom": maxzoom,
        "type": "baselayer",
        "version": "1.0",
        "description": f"由 gismap-helper 切片生成，源：{Path(args.source).name}",
        "attribution": "Google satellite imagery, processed offline",
    }
    if mb is not None:
        mb.close(meta)

    manifest = {
        "source": str(Path(args.source).resolve()),
        "name": name,
        "crs": "EPSG:3857",
        "scheme": schemes,
        "format": ext,
        "tile_size": args.tilesize,
        "minzoom": minzoom,
        "maxzoom": maxzoom,
        "bounds_lonlat": {"west": w, "south": s, "east": e, "north": n},
        "tiles": written,
        "skipped_empty": skipped,
        "zooms": [{"z": z, "x": [x0, x1], "y": [y0, y1], "tiles": c}
                  for z, x0, x1, y0, y1, c in plan],
        "created": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    (outdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not args.no_preview:
        tile_rel = "tiles" if "xyz" in schemes else "tiles_tms"
        conf = build_preview_conf(
            name, tile_rel, "xyz" not in schemes, (w, s, e, n),
            minzoom, maxzoom, args.tilesize, ",".join(schemes), ext,
        )
        write_preview(outdir, conf, Path(__file__).with_name("preview_template.html"))

    elapsed = time.time() - started
    print(f"完成：{written} 个瓦片，{elapsed:.1f} 秒，输出 {outdir}")
    print(f"  瓦片目录：{', '.join(sorted(k for k in sinks if k != 'mbtiles')) or '无'}")
    if mb is not None:
        print(f"  MBTiles ：{outdir / (name + '.mbtiles')}")
    if not args.no_preview:
        print(f"  预览    ：{outdir / 'preview.html'}")
        print(f"  预览需经 HTTP 打开：在 {outdir} 下执行 python -m http.server 8000")
    return 0


# ----------------------------------------------------------------------------
# 命令：export（大图 / 分幅）
# ----------------------------------------------------------------------------

def parse_split(text: str):
    m = re.fullmatch(r"\s*(\d+)\s*(?:[xX*×]\s*(\d+))?\s*", str(text))
    if not m:
        raise SystemExit(f"--split 格式应为 2 / 3 / 4 或 2x3（行x列），收到：{text!r}")
    rows = int(m.group(1))
    cols = int(m.group(2) or rows)
    if rows < 1 or cols < 1 or rows > 50 or cols > 50:
        raise SystemExit("--split 行列数需在 1~50 之间")
    return rows, cols


WKT = {
    "EPSG:4326": 'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
                 'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
                 'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]',
    "EPSG:3857": 'PROJCS["WGS_1984_Web_Mercator_Auxiliary_Sphere",'
                 'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
                 'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
                 'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
                 'PROJECTION["Mercator_Auxiliary_Sphere"],'
                 'PARAMETER["False_Easting",0.0],PARAMETER["False_Northing",0.0],'
                 'PARAMETER["Central_Meridian",0.0],PARAMETER["Standard_Parallel_1",0.0],'
                 'PARAMETER["Auxiliary_Sphere_Type",0.0],UNIT["Meter",1.0]]',
}


def write_sidecars(stem: Path, ext: str, west, south, east, north, width, height, crs):
    px = (east - west) / width
    py = (north - south) / height
    wf = stem.with_suffix(WORLD_EXT.get(ext, ".wld"))
    lines = [f"{px:.12f}", "0.0", "0.0", f"{-py:.12f}",
             f"{west + px / 2.0:.12f}", f"{north - py / 2.0:.12f}"]
    wf.write_text("\n".join(lines) + "\n", encoding="ascii")
    stem.with_suffix(".prj").write_text(WKT.get(crs, WKT["EPSG:4326"]), encoding="ascii")
    return wf


def cmd_export(args) -> int:
    src = open_source(args.source, "RGB", input_tms=getattr(args, "input_tms", False))
    if not isinstance(src, RasterSource):
        src = src.to_raster()
    if args.bounds:
        b = parse_bounds_text(args.bounds)
        b = apply_datum(b, args.bounds_datum)
        src.west, src.south, src.east, src.north = b
        if args.source_crs:
            src.crs = f"EPSG:{args.source_crs}"

    out = Path(args.out)
    ext = out.suffix.lower().lstrip(".")
    if ext not in ("jpg", "jpeg", "png", "webp", "tif", "tiff"):
        ext = args.format
        out = out.with_suffix("." + ext)
    if ext == "jpeg":
        ext = "jpg"
    if out.exists() and not args.force:
        raise SystemExit(f"{out} 已存在，加 --force 覆盖。")
    out.parent.mkdir(parents=True, exist_ok=True)

    img = src._img
    W, H = img.size
    scale = float(args.scale or 1.0)
    if args.max_size:
        scale = min(scale, args.max_size / float(max(W, H)))
    scale = max(1e-6, scale)
    newW, newH = max(1, round(W * scale)), max(1, round(H * scale))
    if newW * newH > 6.0e8:
        raise SystemExit(
            f"导出尺寸 {newW}x{newH} 超过 6 亿像素，容易内存不足。"
            " 请加 --scale 0.5 或 --max-size 20000。"
        )
    if (newW, newH) != (W, H):
        img = img.resize((newW, newH), LANCZOS)

    west, south, east, north = src.west, src.south, src.east, src.north
    rows, cols = parse_split(args.split) if args.split else (1, 1)
    stem = out.with_suffix("")

    saved = []
    index_rows = ["part,west,south,east,north,width,height,crs"]
    for r in range(rows):
        for c in range(cols):
            y0 = round(newH * r / rows)
            y1 = round(newH * (r + 1) / rows)
            x0 = round(newW * c / cols)
            x1 = round(newW * (c + 1) / cols)
            if x1 <= x0 or y1 <= y0:
                continue
            part = img.crop((x0, y0, x1, y1))
            pw = (east - west) * (x1 - x0) / newW
            ph = (north - south) * (y1 - y0) / newH
            pw_ = west + (east - west) * x0 / newW
            pn = north - (north - south) * y0 / newH
            if rows == 1 and cols == 1:
                path = out
            else:
                path = stem.parent / f"{stem.name}_r{r+1}c{c+1}.{ext}"
            save_kwargs = {}
            if ext == "jpg":
                save_kwargs = dict(quality=args.quality, optimize=True)
                part = part.convert("RGB")
            part.save(path, **save_kwargs)
            wf = write_sidecars(
                path.with_suffix(""), "." + ext, pw_, pn - ph, pw_ + pw, pn,
                part.width, part.height, src.crs,
            )
            saved.append(path)
            index_rows.append(
                f"{path.name},{pw_:.8f},{pn-ph:.8f},{pw_+pw:.8f},{pn:.8f},"
                f"{part.width},{part.height},{src.crs}"
            )

    if rows * cols > 1:
        (stem.parent / f"{stem.name}_index.csv").write_text(
            "\n".join(index_rows) + "\n", encoding="utf-8-sig"
        )

    print(f"已导出 {len(saved)} 个文件到 {out.parent}")
    for p in saved:
        with Image.open(p) as im:
            size = im.size
        print(f"  {p.name}  {size[0]}x{size[1]}")
    if rows * cols > 1:
        print(f"  分幅索引：{stem.name}_index.csv")
    print(f"  坐标系：{src.crs}")
    return 0


# ----------------------------------------------------------------------------
# 命令：verify
# ----------------------------------------------------------------------------

def collect_dir_tiles(root: Path):
    counts = {}
    for zdir in os.scandir(root):
        if not (zdir.is_dir() and zdir.name.isdigit()):
            continue
        z = int(zdir.name)
        n = 0
        for xd in os.scandir(zdir.path):
            if not xd.is_dir():
                continue
            for f in os.scandir(xd.path):
                if Path(f.name).suffix.lower() in IMG_EXTS:
                    n += 1
        counts[z] = n
    return counts


def cmd_verify(args) -> int:
    target = Path(args.target)
    result = {"target": str(target), "problems": [], "warnings": []}

    if target.is_dir():
        if (target / "tiles").is_dir():
            root = target / "tiles"
        elif (target / "tiles_tms").is_dir():
            root = target / "tiles_tms"
        else:
            root = target
        is_tms = root.name.endswith("_tms")
        counts = collect_dir_tiles(root)
        if not counts:
            raise SystemExit(f"{root} 下没有瓦片")
        total = sum(counts.values())
        result["tiles"] = total
        result["per_zoom"] = {str(k): v for k, v in sorted(counts.items())}
        if is_tms:
            result["scheme"] = "tms"
        manifest = target / "manifest.json"
        if manifest.exists():
            m = json.loads(manifest.read_text(encoding="utf-8"))
            expected = {str(item["z"]): item["tiles"] for item in m.get("zooms", [])}
            for z, cnt in expected.items():
                got = counts.get(int(z), 0)
                if got != cnt:
                    result["problems"].append(f"z{z} 期望 {cnt} 个瓦片，实际 {got} 个")
            result["bounds"] = m.get("bounds_lonlat")
            result["minzoom"], result["maxzoom"] = m.get("minzoom"), m.get("maxzoom")
        # 抽查尺寸
        bad = 0
        sample = 0
        for zdir in os.scandir(root):
            if not zdir.is_dir() or not zdir.name.isdigit():
                continue
            for xd in os.scandir(zdir.path):
                for f in os.scandir(xd.path):
                    if Path(f.name).suffix.lower() not in IMG_EXTS:
                        continue
                    sample += 1
                    try:
                        with Image.open(f.path) as im:
                            if im.width != im.height:
                                bad += 1
                    except OSError:
                        bad += 1
                    if sample >= 200:
                        break
                if sample >= 200:
                    break
            if sample >= 200:
                break
        if bad:
            result["problems"].append(f"抽查 {sample} 个瓦片中有 {bad} 个非方形或无法解码")
    elif target.suffix.lower() in (".mbtiles", ".sqlite", ".sqlite3"):
        con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT zoom_level, COUNT(*) FROM tiles GROUP BY zoom_level ORDER BY zoom_level"
            ).fetchall()
            meta = dict(con.execute("SELECT name, value FROM metadata").fetchall())
        finally:
            con.close()
        result["tiles"] = sum(r[1] for r in rows)
        result["per_zoom"] = {str(r[0]): r[1] for r in rows}
        result["bounds"] = meta.get("bounds")
    else:
        raise SystemExit(f"{target} 既不是目录也不是 .mbtiles")

    if args.source:
        src = open_source(args.source, "RGB")
        maxz = None
        if target.is_dir():
            data = json.loads((target / "manifest.json").read_text(encoding="utf-8")) \
                if (target / "manifest.json").exists() else None
            maxz = data.get("maxzoom") if data else None
            tile_size = data.get("tile_size", 256) if data else 256
            root = target / "tiles" if (target / "tiles").is_dir() else (
                target / "tiles_tms" if (target / "tiles_tms").is_dir() else target)
            tms_flip = root.name.endswith("_tms")
            zdirs = [d.name for d in os.scandir(root) if d.is_dir() and d.name.isdigit()]
            if not maxz and zdirs:
                maxz = max(int(z) for z in zdirs)
            w, s, e, n = src.lonlat_bounds()
            r = tile_range_for_bounds(w, s, e, n, int(maxz))
            if r:
                x0, x1, y0, y1 = r
                cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
                expected = src.tile_image(int(maxz), cx, cy, tile_size, "white")
                rows = {cy, (1 << int(maxz)) - 1 - cy} if tms_flip else {cy}
                got = None
                xdir = root / str(maxz) / str(cx)
                if xdir.is_dir():
                    for f in xdir.iterdir():
                        if Path(f.name).stem.isdigit() and int(Path(f.name).stem) in rows:
                            with Image.open(f) as im:
                                got = im.convert("RGB")
                            break
                if expected is not None and got is not None:
                    diff = ImageChops.difference(
                        expected.convert("RGB").resize(got.size), got
                    )
                    mae = sum(ImageStat.Stat(diff).mean) / 3.0
                    result["center_tile_mae"] = round(mae, 3)
                    if mae > 12:
                        result["warnings"].append(
                            f"中心瓦片与源图平均差异 {mae:.1f}（0-255），可能存在偏移或缩放错位"
                        )
                else:
                    result["warnings"].append("中心瓦片缺失，无法做一致性抽查")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"目标   : {result['target']}")
        print(f"瓦片数 : {result['tiles']}")
        print(f"层级   : {result['per_zoom']}")
        if result.get("bounds"):
            print(f"范围   : {result['bounds']}")
        if result.get("center_tile_mae") is not None:
            print(f"中心抽查: 平均差异 {result['center_tile_mae']}")
        for p in result["problems"]:
            print(f"问题   : {p}")
        for w in result["warnings"]:
            print(f"注意   : {w}")
        if not result["problems"]:
            print("结论   : 通过")
    return 1 if result["problems"] else 0


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="gismap.py",
        description="谷歌卫星地图切片 / 分幅 / 格式转换（仅依赖 Pillow）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp, with_bounds=True):
        if with_bounds:
            sp.add_argument("--bounds", help="覆盖地理范围：西,南,东,北")
            sp.add_argument("--bounds-datum", default="wgs84", choices=["wgs84", "gcj02"],
                            help="--bounds 的基准面；国内底图常用 gcj02（会被反算成 wgs84）")
            sp.add_argument("--source-crs", choices=["4326", "3857"],
                            help="强制指定源坐标系（默认自动推断）")
            sp.add_argument("--input-tms", action="store_true",
                            help="输入瓦片目录是 TMS 命名（y 轴翻转）时加此开关")

    c = sub.add_parser("inspect", help="识别输入并报告范围与推荐层级")
    c.add_argument("source")
    c.add_argument("--json", action="store_true")
    add_common(c)
    c.set_defaults(func=cmd_inspect)

    t = sub.add_parser("tile", help="切片：XYZ / TMS / MBTiles")
    t.add_argument("source")
    t.add_argument("-o", "--out", required=True, help="输出目录")
    t.add_argument("--scheme", default="xyz", help="xyz,tms,mbtiles，可逗号组合")
    t.add_argument("--format", default="png", choices=["png", "jpg", "webp"])
    t.add_argument("--quality", type=int, default=88)
    t.add_argument("--tilesize", type=int, default=256, choices=[256, 512])
    t.add_argument("--minzoom", type=int)
    t.add_argument("--maxzoom", type=int)
    t.add_argument("--zooms", help="直接指定层级，如 15-18 或 17")
    t.add_argument("--overview", type=int, default=2, help="自动向下生成几级缩略（默认 2）")
    t.add_argument("--overzoom", type=int, default=0, help="在原生层级之上再放大几级")
    t.add_argument("--edge", default="transparent", choices=["transparent", "white", "black"])
    t.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    t.add_argument("--name")
    t.add_argument("--force", action="store_true")
    t.add_argument("--dry-run", action="store_true", help="只打印计划，不生成")
    t.add_argument("--no-preview", action="store_true")
    add_common(t)
    t.set_defaults(func=cmd_tile)

    e = sub.add_parser("export", help="导出整幅大图或 2x2/3x3/4x4 分幅")
    e.add_argument("source")
    e.add_argument("-o", "--out", required=True, help="输出文件路径")
    e.add_argument("--scale", type=float, default=1.0, help="缩放比例，如 0.5")
    e.add_argument("--max-size", type=int, help="限制长边像素")
    e.add_argument("--split", help="分幅：2 / 3 / 4 或 行列如 2x3")
    e.add_argument("--format", default="jpg", choices=["jpg", "png", "webp"])
    e.add_argument("--quality", type=int, default=90)
    e.add_argument("--force", action="store_true")
    add_common(e)
    e.set_defaults(func=cmd_export)

    v = sub.add_parser("verify", help="校验切片结果")
    v.add_argument("target")
    v.add_argument("--source")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_verify)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
