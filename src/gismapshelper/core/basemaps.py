"""可直接调用的免费 / 开源在线底图目录。

每个条目就是一个 XYZ（或 WMTS）瓦片地址模板，界面只负责把
``{s}`` / ``{z}`` / ``{x}`` / ``{y}`` / ``{key}`` 展开。

说明
----
* **开源**：数据或服务本身以开放许可发布（OSM 系为 ODbL，OpenTopoMap 为 CC-BY-SA），
  免费使用但必须署名，且禁止把公共瓦片服务当成离线下载源批量抓取。
* **免费**：可以免费调用，但数据不开放，条款由服务方决定（Esri、高德、天地图）。
* 坐标基准写在这里，是为了让界面能提示 GCJ-02 偏移，别把「偏了」当成切片错。
* 天地图需要免费申请服务密钥（tk），地址里的 ``{key}`` 由界面填写。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Basemap:
    """一个可用的在线底图。"""

    id: str
    name: str
    url: str
    attribution: str
    license: str
    kind: str                      # "开源" / "免费"
    datum: str                     # "WGS-84" / "GCJ-02" / "CGCS2000"
    max_zoom: int = 19
    min_zoom: int = 0
    subdomains: tuple[str, ...] = ("a", "b", "c")
    requires_key: bool = False
    note: str = ""

    @property
    def is_blank(self) -> bool:
        return not self.url


BASEMAPS: tuple[Basemap, ...] = (
    # ---------------------------------------------------------------- 开源
    Basemap(
        id="osm",
        name="OpenStreetMap 标准",
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors",
        license="ODbL（数据）/ 瓦片免费但有使用政策",
        kind="开源",
        datum="WGS-84",
        max_zoom=19,
        note="官方瓦片服务器，适合开发调试；正式使用请自建或换商业服务，禁止批量抓取。",
    ),
    Basemap(
        id="osm_hot",
        name="OSM 人道主义（HOT）",
        url="https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors, Tiles: HOT",
        license="ODbL / CC-BY-SA",
        kind="开源",
        datum="WGS-84",
        max_zoom=20,
        note="对比度更高，适合看建筑与路网，常用于救灾制图。",
    ),
    Basemap(
        id="opentopomap",
        name="OpenTopoMap 地形",
        url="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attribution="© OpenTopoMap (CC-BY-SA), © OpenStreetMap contributors",
        license="CC-BY-SA",
        kind="开源",
        datum="WGS-84",
        max_zoom=17,
        note="等高线与山体阴影，检查山区影像范围时很好用。",
    ),
    Basemap(
        id="cyclosm",
        name="CyclOSM",
        url="https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
        attribution="© CyclOSM, © OpenStreetMap contributors",
        license="ODbL / CC-BY-SA",
        kind="开源",
        datum="WGS-84",
        max_zoom=20,
        note="面向骑行与步道，标注细致。",
    ),
    Basemap(
        id="carto_light",
        name="CARTO Positron 浅色",
        url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        attribution="© OpenStreetMap contributors, © CARTO",
        license="CC-BY 4.0（非商业可免费）",
        kind="开源",
        datum="WGS-84",
        max_zoom=20,
        subdomains=("a", "b", "c", "d"),
        note="浅色底图，叠加矩形/线要素最清晰；商业用途需看 CARTO 条款。",
    ),
    Basemap(
        id="carto_dark",
        name="CARTO Dark Matter 深色",
        url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        attribution="© OpenStreetMap contributors, © CARTO",
        license="CC-BY 4.0（非商业可免费）",
        kind="开源",
        datum="WGS-84",
        max_zoom=20,
        subdomains=("a", "b", "c", "d"),
        note="深色主题，适合夜间演示。",
    ),
    Basemap(
        id="carto_voyager",
        name="CARTO Voyager",
        url="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        attribution="© OpenStreetMap contributors, © CARTO",
        license="CC-BY 4.0（非商业可免费）",
        kind="开源",
        datum="WGS-84",
        max_zoom=20,
        subdomains=("a", "b", "c", "d"),
        note="配色接近谷歌地图，迁移体验平顺。",
    ),
    # ---------------------------------------------------------------- 免费
    Basemap(
        id="esri_img",
        name="Esri 卫星影像（World Imagery）",
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attribution="Esri, Maxar, Earthstar Geographics",
        license="免费调用（非开源，需署名，禁止批量下载）",
        kind="免费",
        datum="WGS-84",
        max_zoom=19,
        note="与谷歌卫星影像观感最接近，核对影像范围的首选；商用请先读 Esri 条款。",
    ),
    Basemap(
        id="esri_topo",
        name="Esri 世界地形",
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        attribution="Esri, HERE, Garmin, USGS",
        license="免费调用（非开源，需署名）",
        kind="免费",
        datum="WGS-84",
        max_zoom=19,
        note="带地名与等高线的地形底图。",
    ),
    Basemap(
        id="esri_gray",
        name="Esri 浅灰画布",
        url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attribution="Esri, HERE, Garmin",
        license="免费调用（非开源，需署名）",
        kind="免费",
        datum="WGS-84",
        max_zoom=16,
        note="最中性的底图，突出叠加的影像范围。",
    ),
    Basemap(
        id="gaode_vec",
        name="高德地图 矢量",
        url="https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
        attribution="© 高德地图",
        license="免费公开瓦片（非开源，商用需授权）",
        kind="免费",
        datum="GCJ-02",
        max_zoom=18,
        subdomains=("1", "2", "3", "4"),
        note="国内访问最快；坐标是 GCJ-02，本页会自动换算矩形以免偏移。",
    ),
    Basemap(
        id="gaode_sat",
        name="高德地图 卫星",
        url="https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}",
        attribution="© 高德地图",
        license="免费公开瓦片（非开源，商用需授权）",
        kind="免费",
        datum="GCJ-02",
        max_zoom=18,
        subdomains=("1", "2", "3", "4"),
        note="国内卫星影像，GCJ-02；与谷歌影像对比时约有 100~600 m 偏移。",
    ),
    Basemap(
        id="tianditu_vec",
        name="天地图 矢量",
        url=(
            "https://t{s}.tianditu.gov.cn/vec_w/wmts?SERVICE=WMTS&REQUEST=GetTile"
            "&VERSION=1.0.0&LAYER=vec&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles"
            "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk={key}"
        ),
        attribution="© 天地图（国家地理信息公共服务平台）",
        license="免费（需申请 tk，非开源）",
        kind="免费",
        datum="CGCS2000",
        max_zoom=18,
        subdomains=("0", "1", "2", "3", "4", "5", "6", "7"),
        requires_key=True,
        note="官方权威底图，球面墨卡托与 WGS-84 差异在厘米级；key 在 tianditu.gov.cn 免费申请。",
    ),
    Basemap(
        id="tianditu_img",
        name="天地图 影像",
        url=(
            "https://t{s}.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile"
            "&VERSION=1.0.0&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles"
            "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk={key}"
        ),
        attribution="© 天地图（国家地理信息公共服务平台）",
        license="免费（需申请 tk，非开源）",
        kind="免费",
        datum="CGCS2000",
        max_zoom=18,
        subdomains=("0", "1", "2", "3", "4", "5", "6", "7"),
        requires_key=True,
        note="国内卫星影像的正规来源，需免费申请 key。",
    ),
    Basemap(
        id="tianditu_ter",
        name="天地图 地形",
        url=(
            "https://t{s}.tianditu.gov.cn/ter_w/wmts?SERVICE=WMTS&REQUEST=GetTile"
            "&VERSION=1.0.0&LAYER=ter&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles"
            "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk={key}"
        ),
        attribution="© 天地图（国家地理信息公共服务平台）",
        license="免费（需申请 tk，非开源）",
        kind="免费",
        datum="CGCS2000",
        max_zoom=18,
        subdomains=("0", "1", "2", "3", "4", "5", "6", "7"),
        requires_key=True,
        note="晕渲地形，判断影像覆盖的山地范围时有用。",
    ),
    # ---------------------------------------------------------------- 离线
    Basemap(
        id="blank",
        name="空白底图（不联网）",
        url="",
        attribution="离线模式",
        license="—",
        kind="离线",
        datum="WGS-84",
        max_zoom=19,
        note="不加载任何网络瓦片，只显示网格与范围矩形；内网或断网时用。",
    ),
)

DEFAULT_BASEMAP_ID = "esri_img"

_BY_ID = {item.id: item for item in BASEMAPS}


def get_basemap(basemap_id: str) -> Basemap:
    """按 id 取底图；未知 id 退回默认底图，界面不会因此崩掉。"""
    return _BY_ID.get(basemap_id) or _BY_ID[DEFAULT_BASEMAP_ID]


def basemap_ids() -> list[str]:
    return [item.id for item in BASEMAPS]
