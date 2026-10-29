"""TIFF / BigTIFF 文件头解析：只读 IFD 标签，不解码任何像素。

GeoTIFF 的地理范围全部记录在标签里（ModelPixelScale / ModelTiepoint /
ModelTransformation / GeoKeyDirectory），所以读几 KB 就能得到范围。
即便是几十 GB 的影像，也不会被载入内存——这正是本工具「只读范围」的关键。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

CLASSIC_MAGIC = 42
BIGTIFF_MAGIC = 43

# TIFF 字段类型：字节宽度 与 struct 格式
_TYPE_SIZE = {
    1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2,
    9: 4, 10: 8, 11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8,
}
_TYPE_CODE = {
    1: "B", 3: "H", 4: "I", 6: "b", 7: "B", 8: "h", 9: "i",
    11: "f", 12: "d", 13: "I", 16: "Q", 17: "q", 18: "Q",
}
_RATIONAL = {5: "II", 10: "ii"}

# 只读取需要的标签，避免把几千条 StripOffsets 全部搬进内存
_WANTED = {
    256,    # ImageWidth
    257,    # ImageLength
    258,    # BitsPerSample
    259,    # Compression
    262,    # PhotometricInterpretation
    277,    # SamplesPerPixel
    278,    # RowsPerStrip
    282,    # XResolution
    283,    # YResolution
    296,    # ResolutionUnit
    305,    # Software
    322,    # TileWidth
    323,    # TileLength
    339,    # SampleFormat
    33550,  # ModelPixelScale
    33922,  # ModelTiepoint
    34264,  # ModelTransformation
    34735,  # GeoKeyDirectory
    34736,  # GeoDoubleParams
    34737,  # GeoAsciiParams
    42112,  # GDAL_METADATA
    42113,  # GDAL_NODATA
}

_MAX_VALUE_BYTES = 4 * 1024 * 1024     # 单个标签最多读 4 MB，防畸形文件
_MAX_IFD_ENTRIES = 4096


class TiffError(Exception):
    """TIFF 结构异常（不是 TIFF、被截断、字段越界等）。"""


@dataclass
class TiffHeader:
    """TIFF 文件的头信息与已解析的标签。"""

    path: Path
    byte_order: str          # "little" / "big"
    big_tiff: bool
    width: int
    height: int
    tags: dict[int, object]

    def tag(self, tag_id: int, default=None):
        return self.tags.get(tag_id, default)

    def first(self, tag_id: int, default=None):
        """取标量标签的第一个值（兼容单值与数组两种存法）。"""
        value = self.tags.get(tag_id, default)
        if isinstance(value, tuple):
            return value[0] if value else default
        return value


def _decode(byte_order: str, type_id: int, count: int, data: bytes):
    if type_id == 2:                                   # ASCII
        return data.split(b"\0")[0].decode("utf-8", "replace").strip()
    if type_id in _RATIONAL:
        fmt = _RATIONAL[type_id]
        values = []
        for index in range(count):
            numerator, denominator = struct.unpack_from(byte_order + fmt, data, index * 8)
            values.append(numerator / denominator if denominator else 0.0)
        return values[0] if count == 1 else tuple(values)
    code = _TYPE_CODE.get(type_id)
    if code is None:
        return None
    values = struct.unpack(byte_order + code * count, data)
    return values[0] if count == 1 else values


def read_tiff_header(path: str | Path) -> TiffHeader:
    """读取 TIFF 头部与第一个 IFD 的标签，不触碰像素数据。"""
    path = Path(path)
    with open(path, "rb") as handle:
        head = handle.read(16)
        if len(head) < 8:
            raise TiffError(f"{path.name} 太小，不是有效的 TIFF 文件")

        order = head[:2]
        if order == b"II":
            byte_order = "<"
        elif order == b"MM":
            byte_order = ">"
        else:
            raise TiffError(f"{path.name} 不是 TIFF 文件（文件头应为 II 或 MM）")

        magic = struct.unpack(byte_order + "H", head[2:4])[0]
        if magic == CLASSIC_MAGIC:
            big_tiff = False
            ifd_offset = struct.unpack(byte_order + "I", head[4:8])[0]
            entry_size, inline = 12, 4
        elif magic == BIGTIFF_MAGIC:
            big_tiff = True
            if len(head) < 16:
                raise TiffError(f"{path.name} 的 BigTIFF 头被截断")
            offset_size = struct.unpack(byte_order + "H", head[4:6])[0]
            if offset_size != 8:
                raise TiffError(f"{path.name}: 不支持的 BigTIFF 偏移宽度 {offset_size}")
            ifd_offset = struct.unpack(byte_order + "Q", head[8:16])[0]
            entry_size, inline = 20, 8
        else:
            raise TiffError(f"{path.name} 不是 TIFF 文件（magic={magic}）")

        if ifd_offset <= 0:
            raise TiffError(f"{path.name} 没有指向有效的 IFD")

        handle.seek(ifd_offset)
        count_raw = handle.read(8 if big_tiff else 2)
        if len(count_raw) < (8 if big_tiff else 2):
            raise TiffError(f"{path.name} 的 IFD 被截断")
        entry_count = struct.unpack(byte_order + ("Q" if big_tiff else "H"), count_raw)[0]
        if entry_count > _MAX_IFD_ENTRIES:
            raise TiffError(f"{path.name} 的 IFD 条目数异常（{entry_count}），可能已损坏")

        raw = handle.read(entry_count * entry_size)
        if len(raw) < entry_count * entry_size:
            raise TiffError(f"{path.name} 的 IFD 被截断")

        tags: dict[int, object] = {}
        for index in range(entry_count):
            block = raw[index * entry_size:(index + 1) * entry_size]
            if big_tiff:
                tag_id, type_id, count = struct.unpack(byte_order + "HHQ", block[:12])
                value_field = block[12:20]
            else:
                tag_id, type_id, count = struct.unpack(byte_order + "HHI", block[:8])
                value_field = block[8:12]
            if tag_id not in _WANTED:
                continue
            unit = _TYPE_SIZE.get(type_id)
            if not unit or count <= 0:
                continue
            total = unit * count
            if total > _MAX_VALUE_BYTES:
                continue
            if total <= inline:
                data = value_field[:total]
            else:
                offset_fmt = "Q" if big_tiff else "I"
                offset = struct.unpack(byte_order + offset_fmt, value_field)[0]
                handle.seek(offset)
                data = handle.read(total)
                if len(data) < total:
                    continue
            try:
                tags[tag_id] = _decode(byte_order, type_id, count, data)
            except (struct.error, ValueError):
                continue

    width = tags.get(256)
    height = tags.get(257)
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise TiffError(f"{path.name} 缺少有效的图像尺寸标签")

    return TiffHeader(
        path=path,
        byte_order="little" if byte_order == "<" else "big",
        big_tiff=big_tiff,
        width=width,
        height=height,
        tags=tags,
    )
