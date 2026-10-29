"""GeoJSON 导出（RFC 7946）。

把图层几何写成标准 GeoJSON：矩形 → Polygon，原点 → Point。
不依赖 Qt，可单独测试；坐标一律以 WGS-84 传入，按需换成 GCJ-02。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import geo

BoundsTuple = tuple[float, float, float, float]

DATUM_WGS84 = "wgs84"
DATUM_GCJ02 = "gcj02"

DATUM_LABELS = {DATUM_WGS84: "WGS84", DATUM_GCJ02: "GCJ-02"}

# 7 位小数约 1 cm，足够表达影像范围，又不会把浮点噪声写进文件
_PRECISION = 7


def ring_from_bounds(bounds: BoundsTuple, precision: int = _PRECISION) -> list[list[float]]:
    """矩形 → 闭合环（左下 → 右下 → 右上 → 左上 → 回到起点）。"""
    west, south, east, north = bounds
    west, south, east, north = (
        round(west, precision),
        round(south, precision),
        round(east, precision),
        round(north, precision),
    )
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def bounds_in_datum(bounds: BoundsTuple, datum: str) -> BoundsTuple:
    """按目标基准返回矩形；GCJ-02 时换算四角后重新归一化。"""
    if datum == DATUM_WGS84:
        return bounds
    if datum != DATUM_GCJ02:
        raise ValueError(f"未知坐标基准：{datum}")

    west, south = geo.wgs84_to_gcj02(bounds[0], bounds[1])
    east, north = geo.wgs84_to_gcj02(bounds[2], bounds[3])
    return (
        min(west, east),
        min(south, north),
        max(west, east),
        max(south, north),
    )


def _with_common_properties(properties: dict, datum: str) -> dict:
    merged = dict(properties)
    merged["datum"] = DATUM_LABELS[datum]
    merged.setdefault("exported_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    return merged


def feature_from_bounds(
    bounds: BoundsTuple,
    properties: dict | None = None,
    datum: str = DATUM_WGS84,
) -> dict:
    """矩形 → Polygon Feature。"""
    target = bounds_in_datum(bounds, datum)
    ring = ring_from_bounds(target)
    west, south = ring[0]
    east, north = ring[2]
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": _with_common_properties(properties or {}, datum),
        "bbox": [west, south, east, north],
    }


def feature_from_point(
    lon: float,
    lat: float,
    properties: dict | None = None,
    datum: str = DATUM_WGS84,
) -> dict:
    """单点 → Point Feature（原点图层用）。"""
    if datum == DATUM_GCJ02:
        lon, lat = geo.wgs84_to_gcj02(lon, lat)
    else:
        if datum != DATUM_WGS84:
            raise ValueError(f"未知坐标基准：{datum}")
    lon, lat = round(lon, _PRECISION), round(lat, _PRECISION)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": _with_common_properties(properties or {}, datum),
        "bbox": [lon, lat, lon, lat],
    }


def build_collection(features: list[dict], name: str = "GisMapsHelper") -> dict:
    """包成 FeatureCollection（GeoJSON 的顶层对象）。"""
    return {"type": "FeatureCollection", "name": name, "features": list(features)}


_NUMBER_LINE = re.compile(r"^-?\d+(?:\.\d+)?,?$")


def _compact_number_arrays(text: str) -> str:
    """把只含数字的数组压成一行。

    json.dumps(indent=2) 会把每个坐标数字拆成单独一行，一个四至框要占 20 多行。
    这里让每个坐标点占一行、bbox 一行，同时保持环点的逐行结构便于 diff。
    """
    lines = text.split("\n")
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.rstrip().endswith("["):
            numbers: list[str] = []
            cursor = index + 1
            matched = True
            while cursor < len(lines):
                body = lines[cursor].strip()
                if body in ("]", "],"):
                    break
                if not _NUMBER_LINE.match(body):
                    matched = False
                    break
                numbers.append(body.rstrip(","))
                cursor += 1
            if matched and numbers and cursor < len(lines):
                prefix = line.rstrip()[:-1]
                suffix = "," if lines[cursor].strip() == "]," else ""
                result.append(f"{prefix}[{', '.join(numbers)}]{suffix}")
                index = cursor + 1
                continue
        result.append(line)
        index += 1
    return "\n".join(result)


def write_collection(path: str | Path, collection: dict) -> Path:
    """写 UTF-8 文件；目录不存在时自动创建。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(collection, ensure_ascii=False, indent=2)
    target.write_text(_compact_number_arrays(text) + "\n", encoding="utf-8", newline="\n")
    return target
