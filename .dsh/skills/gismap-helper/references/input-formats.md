# 输入格式与坐标来源

`gismap.py` 对一张图片按固定顺序找地理范围，命中即停。`inspect --json` 的 `bounds_source` 会告诉你命中的是哪一种。

## 查找顺序

1. **GeoTIFF 内部标签**（仅 `.tif/.tiff`）
2. **世界文件**：`.tfw/.jgw/.pgw/.wld/.bpw/.jtw/.eww`，与图片同名
3. **`.aux.xml`**（GDAL 辅助文件，读 `GeoTransform`）
4. **侧车文本**：`.map` / `.xml` / `.json` / `.txt` / `.csv`，按关键字扫描
5. **`.prj`**：只用来确定坐标系，不含范围

命中的是 5 而没有范围时，必须用 `--bounds` 补齐。

## 世界文件（最常见）

六行，含义固定：

```
像素宽（度或米）       A
行旋转                  D   通常 0
列旋转                  B   通常 0
像素高（负数）          E   北向下为正则应为负
左上角像素中心的 X      C
左上角像素中心的 Y      F
```

换算：

```
west  = C - A/2
north = F - E/2
east  = west + A * 宽
south = north + E * 高
```

脚本读世界文件时不看扩展名后缀，`.tfw` 配 `.jpg` 也能认——这是下载器的常见写法，不要因为扩展名不匹配就判定"没有坐标"。

## GeoTIFF

- `33550 ModelPixelScaleTag`：`(sx, sy, sz)`
- `33922 ModelTiepointTag`：`(i, j, k, x, y, z)`，像素 (i,j) 对应模型坐标 (x,y)
- `34264 ModelTransformationTag`：完整 4x4 变换（本脚本不解析，遇到会回落到世界文件）
- `34735 GeoKeyDirectory`：`3072` = 投影坐标系（3857 已支持），`2048` = 地理坐标系（4326）

只支持北向上、`EPSG:4326` 或 `EPSG:3857`。其他投影（UTM、高斯克吕格、CGCS2000 带号）脚本会直接报错——这不是缺陷，重投影本身该用 GDAL：

```bash
gdalwarp -t_srs EPSG:3857 input.tif output_3857.tif
```

## 侧车文本

脚本用正则扫 `west/lon_min/left/xmin`、`east/right`、`south/bottom/ymin`、`north/top` 这类键名，`=` 或 `:` 或 XML 属性都可以；`.json` 还会找 `bbox/bounds/extent` 四元数组。所以大多数下载器导出的 `.map`、`.xml` 都能被认出来，不必预先知道是哪家的专有格式。

**认不出的情况**：直接在下载器界面上读四个角点，或用"左上角经纬度 + 右下角经纬度"，然后：

```bash
<py> gismap.py inspect "图.jpg" --bounds 116.30,39.90,116.34,39.93
```

若那四个数是米制（墨卡托），再加 `--source-crs 3857`。

## z/x/y 瓦片目录

```
<root>/
  14/
    13725/
      6250.png
      6251.png
```

- 子目录名必须是数字，叶子文件名必须是数字，扩展名 `.png/.jpg/.jpeg/.webp`。
- 目录范围 = 最大层级所有瓦片的并集，因此会比原图范围略大（整块瓦片）。这是正常的，`verify` 与 `inspect` 都会如实报告。
- TMS 命名（y 从南往北数）需加 `--input-tms`，否则上下颠倒。
- 拼接成整幅图时受 4 亿像素保护限制，超出会提示改用 `tile` 直接转换。

## MBTiles

标准只读解析：`tiles(zoom_level, tile_column, tile_row, tile_data)` 与 `metadata(name, value)`。

- `tile_row` 在 MBTiles 里是 **TMS 行号**（南起），脚本内部自动翻回 XYZ 行号。
- 也兼容 `tiles(x, y, z, ...)` 这种列名。
- 优先用 `metadata.bounds` 作为范围；没有就按瓦片并集推算。
- 输出时可同时写 `xyz,tms,mbtiles`，一次渲染三处落盘。
