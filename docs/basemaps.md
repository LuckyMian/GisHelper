# 免费 / 开源在线底图清单

「TIF 范围」页的下拉框就是从这个清单来的（代码在 `src/gismapshelper/core/basemaps.py`）。
所有条目都是标准 XYZ / WMTS 瓦片地址，可以直接拼进任何地图客户端：
Leaflet、OpenLayers、Cesium、QGIS、MapLibre 都认这套 `{z}/{x}/{y}` 约定。

> **先看结论**：核对影像范围用 **Esri 卫星影像**；国内正式项目用 **天地图**（要免费申请 tk）；
> 想要可离线、零依赖就选 **空白底图**。高德/腾讯系是 GCJ-02，叠 WGS-84 影像必然偏 100~600 米，
> 不是数据错，本页会自动换算矩形。

## 开源（数据开放，必须署名）

| id | 名称 | 层级 | 基准 | 许可 / 限制 |
| --- | --- | --- | --- | --- |
| `osm` | OpenStreetMap 标准 | z0–19 | WGS-84 | ODbL；瓦片免费但有[使用政策](https://operations.osmfoundation.org/policies/tiles/)，禁止批量下载 |
| `osm_hot` | OSM 人道主义（HOT） | z0–20 | WGS-84 | ODbL / CC-BY-SA，建筑与路网更醒目 |
| `opentopomap` | OpenTopoMap 地形 | z0–17 | WGS-84 | CC-BY-SA，等高线 + 山体阴影 |
| `cyclosm` | CyclOSM | z0–20 | WGS-84 | ODbL / CC-BY-SA，骑行与步道 |
| `carto_light` | CARTO Positron（浅色） | z0–20 | WGS-84 | CC-BY 4.0；非商业免费，商业看 CARTO 条款 |
| `carto_dark` | CARTO Dark Matter（深色） | z0–20 | WGS-84 | 同上 |
| `carto_voyager` | CARTO Voyager | z0–20 | WGS-84 | 同上，配色接近谷歌地图 |

## 免费（可调用，但数据不开放）

| id | 名称 | 层级 | 基准 | 许可 / 限制 |
| --- | --- | --- | --- | --- |
| `esri_img` | Esri 卫星影像 World Imagery | z0–19 | WGS-84 | 免费调用、需署名；非开源，禁止批量下载，商用先读 Esri 条款 |
| `esri_topo` | Esri 世界地形 | z0–19 | WGS-84 | 同上，带地名与等高线 |
| `esri_gray` | Esri 浅灰画布 | z0–16 | WGS-84 | 同上，最中性的底图 |
| `gaode_vec` | 高德 矢量 | z0–18 | **GCJ-02** | 公开瓦片，非开源；商用需授权 |
| `gaode_sat` | 高德 卫星 | z0–18 | **GCJ-02** | 同上 |
| `tianditu_vec` | 天地图 矢量 | z0–18 | CGCS2000 | 免费，**需申请 tk**；球面墨卡托 |
| `tianditu_img` | 天地图 影像 | z0–18 | CGCS2000 | 同上 |
| `tianditu_ter` | 天地图 地形 | z0–18 | CGCS2000 | 同上 |

天地图 key 在 <https://console.tianditu.gov.cn/> 免费申请，填进页面「tk」输入框后会记在本机。

## 离线

| id | 名称 | 说明 |
| --- | --- | --- |
| `blank` | 空白底图 | 不联网，只画网格与范围矩形；内网、断网、截图时用 |

## 地址模板

```
OpenStreetMap   https://tile.openstreetmap.org/{z}/{x}/{y}.png
HOT             https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png
OpenTopoMap     https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png
CARTO           https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png
Esri 影像       https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}
高德 矢量       https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}
高德 卫星       https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}
天地图 影像     https://t{s}.tianditu.gov.cn/img_w/wmts?...&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk={key}
```

注意 Esri 的路径是 `{z}/{y}/{x}`（行在前），别的服务基本都是 `{z}/{x}/{y}`。
`{s}` 是子域，`{r}` 在高分屏下展开成 `@2x`（只有 CARTO 支持）。

## 为什么不做 Cesium？

CesiumJS 本身是 Apache-2.0 开源的，但它是 **3D 地球引擎**：

* 影像仍然要接 XYZ / WMTS / Cesium ion，**ion 不是免费的**（要申请 token，有配额）；
* 运行时是 WebGL + 几 MB 的 JS，桌面端必须塞 QtWebEngine，exe 直接多出 200 MB；
* 本项目当前只需要「把矩形画在底图上」，2D 瓦片地图足够，原生 QPainter 实现还能离线缓存。

什么时候值得上 Cesium：需要 3D 地形、倾斜摄影、模型叠加、时间轴动画时。
那时候建议单独做一个 Cesium 视图，通过 `QWebEngineView` 加载本地页面，影像继续用上面这些
XYZ 地址，避免依赖 ion。

### 在 Cesium 里直接用这些地址

```js
const viewer = new Cesium.Viewer("cesiumContainer", {
  baseLayer: Cesium.ImageryLayer.fromProviderAsync(
    Cesium.UrlTemplateImageryProvider.fromUrl(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
    )
  ),
});
```

Leaflet 里就一行：

```js
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap contributors",
}).addTo(map);
```

## 使用底线

1. 公共瓦片服务**不是**下载源。要离线使用，请按各自条款申请授权或自建瓦片；
2. 必须保留署名（`attribution` 字段就是给界面用的）；
3. 高德 / 腾讯是 GCJ-02，天地图是 CGCS2000，谷歌影像与 OSM 是 WGS-84，混用前先想清楚基准面；
4. 服务地址随时可能变，本清单只是常用默认值，正式项目请以服务方文档为准。
