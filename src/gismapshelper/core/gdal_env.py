"""把 PROJ / GDAL 数据目录指回 rasterio 自带的副本。

本机装过 OSGeo4W（QGIS 之类），系统环境变量 ``PROJ_LIB`` / ``GDAL_DATA`` 指向它的旧数据，
rasterio 读 proj.db 会报
「DATABASE.LAYOUT.VERSION.MINOR = 5 whereas a number >= 6 is expected」。
rasterio 的轮子自带匹配版本的 proj_data / gdal_data，统一指过去最省事。

必须先于任何 CRS 查询调用（rasterio 首次用时才真正加载 PROJ）。
"""

from __future__ import annotations

import os
from pathlib import Path

_configured = False


def configure() -> bool:
    """指向 rasterio 自带数据；返回 rasterio 是否可用。"""
    global _configured
    if _configured:
        return True

    try:
        import rasterio
    except ImportError:
        return False

    base = Path(rasterio.__file__).resolve().parent
    proj_data = base / "proj_data"
    gdal_data = base / "gdal_data"

    if (proj_data / "proj.db").is_file():
        os.environ["PROJ_DATA"] = str(proj_data)
        os.environ["PROJ_LIB"] = str(proj_data)
    if gdal_data.is_dir():
        os.environ["GDAL_DATA"] = str(gdal_data)
    # 旧版插件目录会把不兼容的 GDAL 驱动塞进来
    os.environ.pop("GDAL_DRIVER_PATH", None)

    _configured = True
    return True
