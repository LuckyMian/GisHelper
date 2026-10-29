"""读取本地 GeoTIFF 的基本信息与地理范围（不载入像素）。

只解析 TIFF 头与 GeoKey，再沿 ``GeoTIFF 标签 → 世界文件(.tfw/.jgw) → .prj`` 的顺序
补齐坐标系，最后换算成经纬度、地面分辨率、原生层级等派生值。
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .geo import (
    clamp_lat,
    lonlat_to_merc,
    merc_to_lat,
    merc_to_lon,
    native_zoom_of,
    spherical_area_km2,
)
from .tiff import TiffError, read_tiff_header

TAG_MODEL_PIXEL_SCALE = 33550
TAG_MODEL_TIEPOINT = 33922
TAG_MODEL_TRANSFORMATION = 34264
TAG_GEOKEY_DIRECTORY = 34735
TAG_GEO_DOUBLES = 34736
TAG_GEO_ASCII = 34737
TAG_GDAL_METADATA = 42112
TAG_GDAL_NODATA = 42113

GEOKEY_MODEL_TYPE = 1024
GEOKEY_RASTER_TYPE = 1025
GEOKEY_GEOGRAPHIC_TYPE = 2048
GEOKEY_PROJECTED_TYPE = 3072
GEOKEY_PCS_CITATION = 3073

WORLD_EXTS = (".tfw", ".jgw", ".pgw", ".wld", ".bpw", ".jtw", ".eww")

COMPRESSION_NAMES = {
    1: "无压缩",
    2: "CCITT RLE",
    3: "CCITT G3",
    4: "CCITT G4",
    5: "LZW",
    6: "JPEG（旧）",
    7: "JPEG",
    8: "Deflate",
    9: "JBIG",
    10: "JBIG",
    32773: "PackBits",
    32946: "Deflate（旧）",
    34712: "JPEG 2000",
    34887: "LERC",
    50000: "ZSTD",
    50001: "WEBP",
}

PHOTOMETRIC_NAMES = {
    0: "灰度（WhiteIsZero）",
    1: "灰度（BlackIsZero）",
    2: "RGB",
    3: "调色板",
    4: "透明遮罩",
    5: "CMYK",
    6: "YCbCr",
    8: "CIELab",
}

SAMPLE_FORMAT_NAMES = {1: "uint", 2: "int", 3: "float", 4: "void"}

# 常见地理坐标系（度为单位）；4xxx 段绝大多数是地理坐标系，
# 但 4491~4554 是 CGCS2000 的高斯-克吕格投影，必须单独排除。
GEOGRAPHIC_EPSG = {4326, 4490, 4214, 4610, 4686, 4258, 4283}
CGCS2000_PROJECTED_EPSG = range(4491, 4555)


class RasterInfoError(Exception):
    """无法解析该文件，或缺少必要的地理参考信息。"""


# --------------------------------------------------------------------------- 数据结构
@dataclass(frozen=True)
class Bounds:
    """一个矩形范围，坐标单位由上下文决定（度或米）。"""

    west: float
    south: float
    east: float
    north: float

    @property
    def width(self) -> float:
        return self.east - self.west

    @property
    def height(self) -> float:
        return self.north - self.south

    @property
    def center(self) -> tuple[float, float]:
        return (self.west + self.east) / 2.0, (self.south + self.north) / 2.0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.west, self.south, self.east, self.north


@dataclass(frozen=True)
class GeoTransform:
    """北向上（或带旋转）的仿射变换：像素 (col,row) -> 地图坐标 (x,y)。"""

    a: float          # x 随列变化
    b: float          # x 随行变化
    c: float          # 左上角 x
    d: float          # y 随列变化
    e: float          # y 随行变化
    f: float          # 左上角 y

    @property
    def rotated(self) -> bool:
        return abs(self.b) > 1e-9 or abs(self.d) > 1e-9

    def apply(self, col: float, row: float) -> tuple[float, float]:
        return self.a * col + self.b * row + self.c, self.d * col + self.e * row + self.f

    def bounds(self, width: int, height: int) -> Bounds:
        corners = [
            self.apply(0, 0),
            self.apply(width, 0),
            self.apply(0, height),
            self.apply(width, height),
        ]
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        return Bounds(min(xs), min(ys), max(xs), max(ys))


@dataclass
class RasterInfo:
    """一个 GeoTIFF 的元数据；除路径与尺寸外都可能为空，取决于文件本身。"""

    path: Path
    file_size: int
    width: int
    height: int

    big_tiff: bool = False
    samples: int = 1
    bits: int = 8
    sample_format: str = "uint"
    compression: str = "未知"
    photometric: str = "未知"
    layout: str = "条带"
    nodata: str | None = None
    software: str | None = None
    raster_type: str = "PixelIsArea"

    crs: str | None = None
    crs_source: str = ""
    bounds: Bounds | None = None
    bounds_source: str | None = None
    transform: GeoTransform | None = None

    lonlat: Bounds | None = None
    center: tuple[float, float] | None = None
    area_km2: float | None = None
    ground_resolution_m: float | None = None      # 投影（Web 墨卡托）米/像素
    terrain_resolution_m: float | None = None     # 实地米/像素（按中心纬度换算）
    native_zoom: float | None = None
    suggested_zooms: tuple[int, int] | None = None
    map_ready: bool = False

    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def megapixels(self) -> float:
        return self.width * self.height / 1e6


# --------------------------------------------------------------------------- 坐标参考系
def crs_category(crs: str | None) -> str:
    """把 EPSG 代码粗分成 mercator / geographic / projected / utm / unknown。"""
    if not crs:
        return "unknown"
    if crs.upper() == "EPSG:3857":
        return "mercator"
    digits = crs.split(":")[-1]
    if not digits.isdigit():
        return "unknown"
    code = int(digits)
    if 32601 <= code <= 32660 or 32701 <= code <= 32760:
        return "utm"
    if code in CGCS2000_PROJECTED_EPSG:
        return "projected"
    if code in GEOGRAPHIC_EPSG or 4000 <= code < 5000:
        return "geographic"
    if 2000 <= code < 4000 or 5000 <= code < 6000:
        return "projected"
    return "unknown"


def _crs_from_wkt(text: str) -> str | None:
    """从 WKT / .prj / citation 文本里认出常见坐标系。

    UTM 放在 WGS-84 判断之前，否则 "WGS_1984_UTM_Zone_50N" 会被误认成 4326。
    """
    low = text.lower()
    if "web_mercator" in low or "web mercator" in low or "auxiliary_sphere" in low or "3857" in low:
        return "EPSG:3857"
    match = re.search(r"utm[_ ]?(?:zone[_ ]?)?(\d{1,2})\s*([ns])\b", low)
    if match:
        zone = int(match.group(1))
        if 1 <= zone <= 60:
            return f"EPSG:{32600 + zone if match.group(2) == 'n' else 32700 + zone}"
    if "projcs" in low or "projected" in low:
        # 其他投影（高斯-克吕格、Lambert 等）不做映射，交给用户重投影
        return None
    if "4490" in low or "cgcs2000" in low or "china_geodetic" in low:
        return "EPSG:4490"
    if "4326" in low:
        return "EPSG:4326"
    if "wgs_1984" in low or "wgs 1984" in low or "wgs84" in low or "gcs_" in low:
        return "EPSG:4326"
    return None


def _geokeys(tags: dict[int, object]) -> dict[int, object]:
    """把 GeoKeyDirectory 解析成 {key_id: value}。"""
    raw = tags.get(TAG_GEOKEY_DIRECTORY)
    if not isinstance(raw, tuple) or len(raw) < 4:
        return {}
    doubles = tags.get(TAG_GEO_DOUBLES)
    ascii_params = tags.get(TAG_GEO_ASCII)
    key_count = int(raw[3])
    keys: dict[int, object] = {}
    for index in range(key_count):
        base = 4 + index * 4
        if base + 3 >= len(raw):
            break
        key_id, location, count, offset = (int(v) for v in raw[base:base + 4])
        if location == 0:
            keys[key_id] = offset
        elif location == TAG_GEO_DOUBLES and isinstance(doubles, tuple):
            if 0 <= offset < len(doubles):
                keys[key_id] = doubles[offset]
        elif location == TAG_GEO_ASCII and isinstance(ascii_params, str):
            keys[key_id] = ascii_params[offset:offset + count].strip("\0 |")
    return keys


def _crs_from_geokeys(keys: dict[int, object], tags: dict[int, object]) -> tuple[str | None, str]:
    projected = keys.get(GEOKEY_PROJECTED_TYPE)
    if isinstance(projected, int) and projected not in (0, 32767):
        return f"EPSG:{projected}", "GeoTIFF GeoKey 3072（ProjectedCSType）"
    if isinstance(projected, int) and projected == 32767:
        citation = keys.get(GEOKEY_PCS_CITATION)
        if isinstance(citation, str):
            found = _crs_from_wkt(citation)
            if found:
                return found, "GeoTIFF GeoKey 3073（PCSCitation）"
    geographic = keys.get(GEOKEY_GEOGRAPHIC_TYPE)
    if isinstance(geographic, int) and geographic not in (0, 32767):
        return f"EPSG:{geographic}", "GeoTIFF GeoKey 2048（GeographicType）"
    metadata = tags.get(TAG_GDAL_METADATA)
    if isinstance(metadata, str):
        found = _crs_from_wkt(metadata)
        if found:
            return found, "GeoTIFF GDAL_METADATA"
    return None, ""


# --------------------------------------------------------------------------- 地理参考
def _transform_from_tags(tags: dict[int, object]) -> tuple[GeoTransform | None, str]:
    matrix = tags.get(TAG_MODEL_TRANSFORMATION)
    if isinstance(matrix, tuple) and len(matrix) >= 16:
        # 4x4 行主序矩阵，取左上 2x2 与平移列
        return (
            GeoTransform(matrix[0], matrix[1], matrix[3], matrix[4], matrix[5], matrix[7]),
            "GeoTIFF ModelTransformation",
        )
    scale = tags.get(TAG_MODEL_PIXEL_SCALE)
    tie = tags.get(TAG_MODEL_TIEPOINT)
    if (
        isinstance(scale, tuple)
        and isinstance(tie, tuple)
        and len(scale) >= 2
        and len(tie) >= 6
    ):
        sx, sy = float(scale[0]), float(scale[1])
        i, j = float(tie[0]), float(tie[1])
        x, y = float(tie[3]), float(tie[4])
        return (
            GeoTransform(sx, 0.0, x - i * sx, 0.0, -sy, y + j * sy),
            "GeoTIFF ModelPixelScale + ModelTiepoint",
        )
    return None, ""


def find_worldfile(image: Path) -> Path | None:
    """按常见扩展名查找世界文件（.tfw / .jgw / .wld …）。"""
    stem = image.with_suffix("")
    for ext in WORLD_EXTS:
        candidate = stem.with_suffix(ext)
        if candidate.is_file():
            return candidate
    for ext in (".tfw", ".wld"):
        candidate = image.with_name(image.name + ext)
        if candidate.is_file():
            return candidate
    return None


def find_prj(image: Path) -> Path | None:
    for candidate in (image.with_suffix(".prj"), image.with_name(image.name + ".prj")):
        if candidate.is_file():
            return candidate
    return None


def _read_worldfile(path: Path) -> GeoTransform:
    """读世界文件；它的 C/F 是左上像素的中心，需要换算成外框左上角。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    values = [float(v) for v in re.split(r"[,\s]+", text.strip()) if v]
    if len(values) < 6:
        raise RasterInfoError(f"世界文件 {path.name} 不足 6 行，无法确定范围")
    a, d, b, e, c, f = values[:6]
    return GeoTransform(
        a, b, c - a / 2.0 - b / 2.0,
        d, e, f - d / 2.0 - e / 2.0,
    )


def _read_prj_crs(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return _crs_from_wkt(text)


def _infer_crs(bounds: Bounds) -> tuple[str, str]:
    """没有显式坐标系时按量级推断：超过 180 基本是米制的 3857。"""
    values = bounds.as_tuple()
    if any(abs(v) > 180.0 for v in values):
        return "EPSG:3857", "按坐标量级推断（米制）"
    return "EPSG:4326", "按坐标量级推断（度）"


# --------------------------------------------------------------------------- 主入口
def read_raster_info(path: str | Path) -> RasterInfo:
    """读取一个 TIFF/GeoTIFF 的元数据与范围；不会解码像素。"""
    path = Path(path)
    if not path.exists():
        raise RasterInfoError(f"文件不存在：{path}")
    if not path.is_file():
        raise RasterInfoError(f"不是文件：{path}")
    if path.suffix.lower() not in (".tif", ".tiff", ".gtif"):
        raise RasterInfoError(
            f"当前只解析 TIFF / GeoTIFF（.tif / .tiff），收到 {path.suffix or '无扩展名'}。\n"
            "其他格式请先转成 GeoTIFF。"
        )

    try:
        header = read_tiff_header(path)
    except TiffError as exc:
        raise RasterInfoError(str(exc)) from exc

    tags = header.tags
    info = RasterInfo(
        path=path,
        file_size=path.stat().st_size,
        width=header.width,
        height=header.height,
        big_tiff=header.big_tiff,
        nodata=_ascii_tag(tags.get(TAG_GDAL_NODATA)),
        software=_ascii_tag(tags.get(305)),
    )

    bits = tags.get(258)
    if isinstance(bits, tuple) and bits:
        info.bits = max(int(v) for v in bits)
    elif isinstance(bits, int):
        info.bits = int(bits)
    samples = header.first(277)
    if isinstance(bits, tuple) and len(bits) > 1:
        info.samples = len(bits)
    elif isinstance(samples, int):
        info.samples = max(1, samples)

    info.sample_format = SAMPLE_FORMAT_NAMES.get(int(header.first(339, 1) or 1), "未知")
    info.compression = COMPRESSION_NAMES.get(int(header.first(259, 1) or 1), "未知")
    info.photometric = PHOTOMETRIC_NAMES.get(int(header.first(262, 1) or 1), "未知")
    if header.tag(322) is not None and header.tag(323) is not None:
        info.layout = f"平铺 {int(header.first(322))}×{int(header.first(323))}"
    else:
        info.layout = "条带"

    keys = _geokeys(tags)
    info.raster_type = (
        "PixelIsPoint"
        if int(keys.get(GEOKEY_RASTER_TYPE, 1) or 1) == 2
        else "PixelIsArea"
    )

    transform, transform_source = _transform_from_tags(tags)
    crs, crs_source = _crs_from_geokeys(keys, tags)
    bounds_source = transform_source or None

    prj = find_prj(path)
    if crs is None and prj is not None:
        crs = _read_prj_crs(prj)
        if crs:
            crs_source = f"{prj.name}（投影参数文件）"

    if transform is None:
        worldfile = find_worldfile(path)
        if worldfile is not None:
            try:
                transform = _read_worldfile(worldfile)
                bounds_source = f"世界文件 {worldfile.name}"
            except (RasterInfoError, ValueError) as exc:
                info.warnings.append(f"世界文件 {worldfile.name} 解析失败：{exc}")

    if transform is not None:
        bounds = transform.bounds(info.width, info.height)
        if info.raster_type == "PixelIsPoint":
            # 控制点指像元中心：向外扩半像素才是像元外框
            half_x = abs(transform.a) / 2.0
            half_y = abs(transform.e) / 2.0
            bounds = Bounds(
                bounds.west - half_x, bounds.south - half_y,
                bounds.east + half_x, bounds.north + half_y,
            )
            info.notes.append("控制点类型为 PixelIsPoint，范围已按半像素外扩。")
        info.bounds = bounds
        info.transform = transform
        info.bounds_source = bounds_source
        if transform.rotated:
            info.notes.append("影像带旋转（非北向上），范围取四角外接矩形。")
        if crs is None:
            if prj is not None:
                info.warnings.append(
                    f"检出投影文件 {prj.name}，但无法识别其中的坐标系；"
                    "为避免把投影米当成墨卡托米，这里不做猜测。"
                )
            else:
                crs, crs_source = _infer_crs(bounds)
                info.notes.append("文件未标注坐标系，以上按坐标数值推断，请自行确认基准面。")
    else:
        info.warnings.append(
            "未找到地理范围：既没有 GeoTIFF 标签，也没有 .tfw / .jgw 等世界文件。\n"
            "下载器通常会把坐标单独存成同名 .tfw，确认它和 .tif 在同一目录。"
        )

    info.crs = crs
    info.crs_source = crs_source
    if not crs_source and crs:
        info.crs_source = "推断"

    _fill_derived(info)
    return info


def _ascii_tag(value) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip().strip("\0")
        return cleaned or None
    return None


def _fill_derived(info: RasterInfo) -> None:
    """按 CRS 类别计算经纬度范围、面积、分辨率、层级，并生成提示。"""
    if info.bounds is None:
        return

    category = crs_category(info.crs)
    west, south, east, north = info.bounds.as_tuple()
    if west > east or south > north:
        west, east = min(west, east), max(west, east)
        south, north = min(south, north), max(south, north)
        info.bounds = Bounds(west, south, east, north)

    if category == "mercator":
        info.lonlat = Bounds(
            merc_to_lon(west), merc_to_lat(south),
            merc_to_lon(east), merc_to_lat(north),
        )
    elif category == "geographic":
        info.lonlat = Bounds(west, south, east, north)
    else:
        if category == "utm":
            info.warnings.append(
                f"该文件是 UTM 投影（{info.crs}），不能直接叠加到 Web 墨卡托底图。\n"
                "请先重投影：gdalwarp -t_srs EPSG:3857 in.tif out.tif"
            )
        elif category == "projected":
            info.warnings.append(
                f"该文件是投影坐标系（{info.crs}），页面只支持 EPSG:4326 / EPSG:3857。\n"
                "请先重投影：gdalwarp -t_srs EPSG:3857 in.tif out.tif"
            )
        else:
            info.warnings.append(
                f"坐标系 {info.crs or '未知'} 无法换算到经纬度，范围只能按原始数值显示。"
            )
        return

    lonlat = info.lonlat
    assert lonlat is not None
    if lonlat.north > 90 or lonlat.south < -90 or lonlat.west < -180 or lonlat.east > 180:
        info.warnings.append("换算后的经纬度超出常规范围，坐标系标注可能有误。")
        return

    clamped = Bounds(
        lonlat.west, clamp_lat(lonlat.south), lonlat.east, clamp_lat(lonlat.north)
    )
    info.center = clamped.center
    info.area_km2 = spherical_area_km2(*clamped.as_tuple())

    west_m, south_m = lonlat_to_merc(clamped.west, clamped.south)
    east_m, north_m = lonlat_to_merc(clamped.east, clamped.north)
    res_x = (east_m - west_m) / max(1, info.width)
    res_y = (north_m - south_m) / max(1, info.height)
    resolution = (res_x + res_y) / 2.0
    info.ground_resolution_m = resolution
    info.terrain_resolution_m = resolution * math.cos(math.radians(clamped.center[1]))
    native = native_zoom_of(resolution)
    info.native_zoom = native
    base = max(0, int(math.floor(native)))
    info.suggested_zooms = (max(0, base - 2), base)
    info.map_ready = True

    if info.megapixels >= 200:
        info.notes.append(
            f"影像约 {info.megapixels:.0f} MP，切片或导出时注意内存（先降采样更稳）。"
        )
    if info.file_size >= 2 * 1024**3:
        info.notes.append("文件超过 2 GB，读取只发生在文件头，不会影响本页速度。")


# --------------------------------------------------------------------------- 输出
def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _format_coord(value: float, category: str) -> str:
    if category in ("mercator", "projected", "utm"):
        return f"{value:,.2f}"
    return f"{value:.6f}"


def info_rows(info: RasterInfo) -> list[tuple[str, str]]:
    """界面与终端共用的 (标签, 值) 列表。"""
    category = crs_category(info.crs)
    rows: list[tuple[str, str]] = [
        ("文件", str(info.path)),
        ("文件大小", format_bytes(info.file_size)),
        ("像素尺寸", f"{info.width:,} × {info.height:,}（{info.megapixels:.1f} MP）"),
        (
            "存储格式",
            f"{'BigTIFF' if info.big_tiff else 'GeoTIFF'} · {info.layout} · "
            f"{info.bits} 位 × {info.samples} 波段",
        ),
        ("数据类型", f"{info.sample_format}{info.bits}"),
        ("压缩方式", info.compression),
        ("色彩解释", info.photometric),
        ("NODATA", info.nodata or "未标注"),
        ("坐标系", info.crs or "未识别"),
        ("坐标系来源", info.crs_source or "—"),
    ]

    if info.bounds is not None:
        b = info.bounds
        unit = "米" if category in ("mercator", "projected", "utm") else "度"
        rows.append((
            "原始范围",
            f"西 {_format_coord(b.west, category)}，南 {_format_coord(b.south, category)}，"
            f"东 {_format_coord(b.east, category)}，北 {_format_coord(b.north, category)}（{unit}）",
        ))
    else:
        rows.append(("原始范围", "未获取"))

    if info.lonlat is not None:
        ll = info.lonlat
        rows.append(("经度范围", f"{ll.west:.6f} → {ll.east:.6f}"))
        rows.append(("纬度范围", f"{ll.south:.6f} → {ll.north:.6f}"))
        rows.append(("中心点", f"{ll.center[0]:.6f}, {ll.center[1]:.6f}"))
    else:
        rows.append(("经纬度范围", "无法换算（坐标系不受支持）"))

    if info.area_km2 is not None:
        rows.append(("覆盖面积", f"{info.area_km2:,.2f} km²"))
    if info.ground_resolution_m is not None:
        rows.append(("分辨率（投影）", f"{info.ground_resolution_m:.3f} 米/像素"))
    if info.terrain_resolution_m is not None:
        rows.append(("分辨率（实地）", f"{info.terrain_resolution_m:.3f} 米/像素"))
    if info.native_zoom is not None:
        rows.append(("原生层级", f"z{info.native_zoom:.2f}"))
    if info.suggested_zooms is not None:
        rows.append(("建议切片层级", f"z{info.suggested_zooms[0]} ~ z{info.suggested_zooms[1]}"))
    if info.bounds_source:
        rows.append(("范围来源", info.bounds_source))
    if info.software:
        rows.append(("生成软件", info.software))
    if info.notes:
        rows.append(("提示", "\n".join(info.notes)))
    return rows


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1 for ch in text)


def describe(info: RasterInfo) -> str:
    """纯文本报告：终端 ``--inspect`` 与「复制信息」共用。"""
    rows = info_rows(info)
    width = max(_display_width(name) for name, _ in rows) + 2
    lines = []
    for name, value in rows:
        padding = " " * (width - _display_width(name))
        lines.append(f"{name}{padding}{value}")
    if info.warnings:
        lines.append("")
        lines.append("⚠ 需要注意：")
        lines.extend(f"  · {item}" for item in info.warnings)
    return "\n".join(lines)
