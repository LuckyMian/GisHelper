"""业务核心层：不依赖 Qt，可单独测试。

* :mod:`gismapshelper.core.geo`       —— 经纬度 / 墨卡托 / 瓦片数学
* :mod:`gismapshelper.core.tiff`      —— TIFF 头标签读取
* :mod:`gismapshelper.core.raster`    —— GeoTIFF 基本信息与范围
* :mod:`gismapshelper.core.frames`    —— 项目原点与方形切片范围
* :mod:`gismapshelper.core.geojson`   —— 图层导出 GeoJSON
* :mod:`gismapshelper.core.crop`      —— 按矩形裁剪 GeoTIFF（需要 rasterio）
* :mod:`gismapshelper.core.basemaps`  —— 免费 / 开源在线底图目录
"""

from __future__ import annotations

from . import geo
from .basemaps import BASEMAPS, DEFAULT_BASEMAP_ID, Basemap, get_basemap
from .frames import (
    DISTANCE_PRESETS_M,
    PIXEL_PRESETS,
    SliceSpec,
    format_distance,
    local_offsets,
    meters_per_degree,
    square_bounds,
)
from .raster import (
    Bounds,
    GeoTransform,
    RasterInfo,
    RasterInfoError,
    describe,
    format_bytes,
    info_rows,
    read_raster_info,
)
from .tiff import TiffError, read_tiff_header

__all__ = [
    "BASEMAPS",
    "DEFAULT_BASEMAP_ID",
    "DISTANCE_PRESETS_M",
    "PIXEL_PRESETS",
    "Basemap",
    "Bounds",
    "GeoTransform",
    "RasterInfo",
    "RasterInfoError",
    "SliceSpec",
    "TiffError",
    "describe",
    "format_bytes",
    "format_distance",
    "geo",
    "get_basemap",
    "info_rows",
    "local_offsets",
    "meters_per_degree",
    "read_raster_info",
    "read_tiff_header",
    "square_bounds",
]
