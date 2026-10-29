# GisMap Helper 技能交接手册

本文整理自工作区 `.dsh/skills/gismap-helper` 下的 8 个源文件，面向后续维护、排障和桌面端接入。文档只描述源文件已经实现的能力；源文件之间不一致、或实现与说明有差异的地方，集中列在“已知矛盾与风险”一节。

整理日期：2026-10-29  
技能快照：`gismap-helper`，对应桌面端文档口径 v0.3.0（2026-10-28）

## 1. 技能定位与边界

`gismap-helper` 是一个处理**已经下载到本地**的谷歌卫星影像/航拍底图的命令行工具。核心程序是 `scripts/gismap.py`，用 Pillow 完成读取、重采样、切片、拼图和校验。

它适合以下工作：

- 把“单张大图 + 坐标文件”转成 Web 墨卡托 XYZ 瓦片。
- 识别 GeoTIFF 内部地理参考，或从世界文件、`.aux.xml`、侧车文本中恢复范围。
- 把现成的 `z/x/y` 目录或 MBTiles 转换成 XYZ、TMS、MBTiles 中的一种或多种格式。
- 把瓦片目录或 MBTiles 拼成整幅图，或按行列分幅导出。
- 生成可在浏览器中检查的瓦片预览页、`manifest.json`，并校验瓦片数量和解码情况。
- 对中心瓦片与源图做一致性抽查，协助定位范围、缩放或基准面错误。

不适用或明确禁止的行为：

- 不联网抓取、下载谷歌瓦片或其他底图；下载属于用户既有工具的职责。
- 不处理与瓦片产出无关的通用 GIS 分析。
- 不为“修偏移”反复加减 `--bounds` 数值；先判断坐标系和基准面。
- 不在输出目录非空时盲目加 `--force`；先确认目录内容是否仍需保留。
- 不把裁切、切片等与瓦片无关的任务说成该技能的能力。

## 2. 目录与文件职责

技能目录为 `<工作区>/.dsh/skills/gismap-helper`。本文中的 `gismap.py` 均指该目录下的 `scripts/gismap.py`。

| 路径 | 职责 | 维护要点 |
|---|---|---|
| `SKILL.md` | 技能入口、执行顺序、输入输出、常用命令、风险和桌面端约束 | 改动工作流或用户可见语义时优先同步此文件 |
| `references/input-formats.md` | 坐标来源查找顺序、世界文件公式、GeoTIFF 标签、TMS/MBTiles 规则 | 新增输入格式时同步更新 |
| `references/alignment.md` | 4326/3857、WGS-84/GCJ-02、范围错误的排查顺序 | 不得把基准面问题简化为常数平移 |
| `references/troubleshooting.md` | 常见报错、原因和处理方法；自检覆盖范围 | 新增错误类型时补对应现象 |
| `references/dev-rules.md` | GisMapsHelper 桌面端的分层、坐标、缓存、测试、版本和打包规则 | 改桌面端代码前必须先读 |
| `scripts/gismap.py` | `inspect`、`tile`、`export`、`verify` 的唯一 CLI 实现 | 只依赖 Pillow；改动后跑端到端自检 |
| `scripts/selftest.py` | 用合成像素图验证识别、几何、格式、回流、分幅和 GCJ-02 | 不依赖真实影像或网络 |
| `scripts/preview_template.html` | Leaflet 预览页模板，显示层级、范围、瓦片信息和在线底图叠加开关 | 模板占位符为 `{{TITLE}}`、`{{CONF}}` |

`gismap.py` 的主要内部模块：

- 坐标数学：WGS-84、Web 墨卡托、瓦片边界和瓦片索引范围。
- 地理参考解析：GeoTIFF、世界文件、`.aux.xml`、侧车文本和 `.prj`。
- 数据源适配：单图 `RasterSource`、瓦片目录 `TileDirSource`、MBTiles `MBTilesSource`。
- 输出去向：XYZ/TMS 目录 `DirSink`、MBTiles `MBTilesSink`。
- 四个子命令和统一的 argparse 参数定义。

## 3. 推荐工作顺序

必须按 `inspect → tile/export → verify → present` 推进。`inspect` 在动手之前决定坐标系、范围、原生层级和推荐层级；跳过它直接猜 `--bounds` 是最常见的错位来源。

| 步骤 | 目的 | 主要产物或判断依据 |
|---|---|---|
| `inspect` | 识别输入类型、地理范围和 CRS；确认范围来源和源数据真实精度 | JSON/文本中的 `kind`、`crs`、`bounds`、`bounds_source`、`native_zoom`、`suggested` |
| `tile` 或 `export` | 要瓦片服务/离线底图时用 `tile`；要整图或分幅图片时用 `export` | XYZ/TMS/MBTiles、预览页、`manifest.json`；整图或分幅图及其地理参考侧车 |
| `verify` | 检查瓦片数量、尺寸、可解码性，并与源图做中心瓦片抽样比对 | `problems` 必须为空；`warnings` 和 `center_tile_mae` 用于定位错位 |
| `present` | 交付预览、清单和瓦片数据库 | `preview.html` 必须经 HTTP 打开；同时交付 `manifest.json` 和可选 `.mbtiles` |

`tile` 与 `export` 的职责不要混用：前者生产瓦片金字塔，后者生产整图或分幅图片。若只需要转换瓦片格式或方案，应直接以瓦片目录/MBTiles 为输入执行 `tile`，通常不必先拼成大图。

## 4. 输入、坐标来源与输出布局

### 4.1 四类输入

| `kind` | 输入 | 处理方式 |
|---|---|---|
| `image` | 单张大图，扩展名可为 `.png/.jpg/.jpeg/.webp/.tif/.tiff/.bmp` | 自动查找地理范围；找到后执行 `tile` 或 `export` |
| `tiledir` | 标准 `z/x/y` 目录，叶子扩展名可为 `.png/.jpg/.jpeg/.webp/.tif/.tiff/.bmp` | 转格式/换方案，或拼图导出 |
| `mbtiles` | 标准 MBTiles 单文件，也接受 `.sqlite/.sqlite3` 扩展名 | 转格式/换方案，或拼图导出 |
| 识别失败 | 单图没有可识别的地理参考 | 向用户索取“西,南,东,北”，用 `--bounds` 补齐 |

瓦片目录应符合 `<root>/<z>/<x>/<y>.<ext>`。目录名和叶子文件名必须是数字；范围取最高可用层级所有瓦片的并集，因此通常略大于源图范围，这是整块瓦片覆盖的正常结果。

MBTiles 至少需要 `tiles(zoom_level, tile_column, tile_row, tile_data)` 和 `metadata(name, value)`；脚本也兼容 `tiles(x, y, z, ...)` 列名。MBTiles 的 `tile_row` 是 TMS 行号，脚本内部转换为 XYZ 行号。范围优先使用 `metadata.bounds`，缺失时按瓦片并集推算。

### 4.2 单图地理范围查找顺序

脚本按以下顺序查找，命中即停止；`inspect --json` 的 `bounds_source` 会报告实际命中项。

1. `.tif/.tiff` 的 GeoTIFF 内部标签。
2. 与图像同名的世界文件：`.tfw/.jgw/.pgw/.wld/.bpw/.jtw/.eww`。
3. `<图像完整文件名>.aux.xml` 中的 `GeoTransform`。
4. 同名侧车文件：`.map/.xml/.json/.txt/.csv`。
5. `.prj`：只用于判断 CRS，不提供范围。

世界文件前六项含义固定：

```text
A = 像素宽（度或米）
D = 行旋转，通常为 0
B = 列旋转，通常为 0
E = 像素高，北向上时通常为负数
C = 左上角像素中心的 X
F = 左上角像素中心的 Y
```

范围换算：

```text
west  = C - A / 2
north = F - E / 2
east  = west + A * width
south = north + E * height
```

GeoTIFF 读取 `ModelPixelScaleTag (33550)`、`ModelTiepointTag (33922)` 和 `GeoKeyDirectoryTag (34735)`。当前实现只接受北向上、`EPSG:4326` 或 `EPSG:3857`。`ModelTransformationTag (34264)` 不解析，会回落到世界文件。UTM、高斯-克吕格等其他投影会明确报错，应先重投影：

```powershell
gdalwarp -t_srs EPSG:3857 input.tif output_3857.tif
```

侧车文本会扫描 `west/lon_min/left/xmin`、`east/right`、`south/bottom/ymin`、`north/top` 等键名；值可以跟在 `=`、`:` 或 XML 属性后。JSON 还识别 `bbox`、`bounds`、`extent`、`lonlatbbox`、`boundingbox` 四元数组。

只有 `.prj` 或只有 `crs` 而没有 `bounds` 时，仍必须补范围。数值绝对值大于 180 时自动判为 `EPSG:3857`，否则判为 `EPSG:4326`；小范围米制坐标可能误判，这时用 `--source-crs 3857` 强制指定。

### 4.3 瓦片输出布局

执行 `tile` 时，顶层输出目录会包含以下与所选方案相符的内容：

```text
<out>/
├── tiles/<z>/<x>/<y>.<ext>             # 选择 xyz 时
├── tiles_tms/<z>/<x>/<y>.<ext>         # 选择 tms 时，y 轴翻转
├── <name>.mbtiles                      # 选择 mbtiles 时
├── preview.html                        # 未加 --no-preview 时
└── manifest.json
```

`manifest.json` 记录源路径、名称、输出 CRS、方案、格式、瓦片尺寸、层级、经纬度范围、实际写出瓦片数、跳过空瓦片数、逐层索引范围及创建时间。预览页优先使用 `tiles`；如果只输出 TMS，则使用 `tiles_tms`。

### 4.4 整图与分幅输出

`export` 的 `-o` 指定目标文件。格式优先取扩展名；扩展名不是 `jpg/jpeg/png/webp/tif/tiff` 时改用 `--format`。整图输出名为 `-o` 指定的文件；分幅输出使用 `<stem>_r<行>c<列>.<ext>`，并生成 `<stem>_index.csv`。

每张已保存图片都会由 `write_sidecars` 写出同名世界文件和 `.prj`，分幅 CSV 使用 UTF-8 BOM。CSV 字段为 `part,west,south,east,north,width,height,crs`。

## 5. 常用命令、参数和资源限制

下面的 `<py>` 必须替换为 `load_workspace_dependencies` 返回的内置 Python 的 `python.exe`，不是桌面端 `.venv` 解释器。

### 5.1 识别

```powershell
<py> gismap.py inspect "图.jpg" --json

# 没有可靠地理参考时显式补范围
<py> gismap.py inspect "图.jpg" --bounds 116.30,39.90,116.34,39.93

# 范围来自高德/腾讯等 GCJ-02 底图
<py> gismap.py inspect "图.jpg" --bounds 116.32,39.91,116.36,39.94 --bounds-datum gcj02

# 米制范围被误判为经纬度时
<py> gismap.py inspect "图.tif" --bounds 12900000,4850000,12910000,4860000 --source-crs 3857
```

先看 `bounds_source`。若 `bounds_source` 为空而 `crs` 有值，说明只识别了 CRS，范围仍需补齐。

### 5.2 切片

```powershell
# 默认 XYZ，自动从原生层级向下生成两级缩略
<py> gismap.py tile "图.jpg" -o out

# 先检查层级、索引范围和瓦片总数，不写文件
<py> gismap.py tile "图.jpg" -o out --zooms 14-17 --dry-run

# JPEG 体积较小；PNG 支持透明边缘
<py> gismap.py tile "图.jpg" -o out --format jpg --quality 88

# 同时输出三种方案
<py> gismap.py tile "图.jpg" -o out --scheme xyz,tms,mbtiles --name 北京影像

# 输入目录是 TMS 命名
<py> gismap.py tile "tiles_tms" -o out --input-tms --zooms 15
```

关键切片参数：

| 参数 | 作用与默认值 | 风险/注意 |
|---|---|---|
| `--bounds 西,南,东,北` | 覆盖自动识别范围；要求西<东、南<北 | 只应作为已核实范围或用户提供范围的入口 |
| `--bounds-datum`：`wgs84` / `gcj02` | 解释 `--bounds` 的基准面，默认 `wgs84`；`gcj02` 会反算为 WGS-84 | 只影响命令行 `--bounds`，不会自动重解释世界文件/GeoTIFF |
| `--source-crs`：`4326` / `3857` | 强制源 CRS | 只在自动判断不可靠时使用 |
| `--input-tms` | 把输入目录 / MBTiles 之外的瓦片目录按 TMS 解释 | 漏加会导致上下颠倒 |
| `--minzoom` / `--maxzoom` | 指定层级边界 | 超过 `native_zoom` 只是插值放大（overzoom） |
| `--zooms` | 直接指定 `17` 或 `14-17` | 会绕过 `--minzoom/--maxzoom/--overview/--overzoom` 的自动选择 |
| `--overview N` | 自动向下多生成 N 级，默认 2 | 层级总数会增加，瓦片数按约 4 倍递增 |
| `--overzoom N` | 在原生层级之上放大 N 级，默认 0 | 不增加真实细节，只增加文件数量和体积 |
| `--scheme` | `xyz,tms,mbtiles` 可组合，默认 `xyz` | 所有选中方案会重复写同一批渲染结果 |
| `--format` | `png/jpg/webp`，默认 `png` | JPEG 没有透明通道 |
| `--quality` | 切片默认 88，导出默认 90 | 具体有效范围由 Pillow 编码器决定 |
| `--tilesize` | `256` 或 `512`，默认 256 | 512 像素瓦片会显著增加单块体积和内存 |
| `--edge` | `transparent/white/black`，默认透明 | JPEG 恒为白底；透明只对非 JPEG 有意义 |
| `--workers` | 默认 `min(4, CPU 数)` | 文档建议超大任务最多提到 8，避免内存和磁盘争用 |
| `--dry-run` | 只输出 JSON 计划 | 安全的首选预检方式 |
| `--force` | 绕过非空目录和 30 万瓦片保护 | 不清理旧瓦片；只覆盖本次命中的文件 |
| `--no-preview` | 不生成 `preview.html` | 仍会生成 manifest 和所选瓦片输出 |

默认缩放选择规则：基准原生层级为 `floor(native_zoom)`；未指定最大层级时，最大层级为基准加 `--overzoom`，并封顶 z22；未指定最小层级时，从最大层级向下扣除 `--overview` 和 `--overzoom`，最低为 z0。

### 5.3 导出整图与分幅

```powershell
# 整幅导出
<py> gismap.py export "图.jpg" -o 图_整幅.jpg

# 3x3
<py> gismap.py export "图.jpg" -o 图.jpg --split 3

# 2 行 3 列
<py> gismap.py export "图.jpg" -o 图.jpg --split 2x3

# 限制长边并控制体积
<py> gismap.py export "图.jpg" -o 图_预览.jpg --max-size 8000

# 大图先降采样
<py> gismap.py export "图.jpg" -o 图_半幅.jpg --scale 0.5
```

`--split` 实现上接受 `1~50` 的行列数，可写 `3` 或 `2x3`。日常交接以 2x2、3x3、4x4 为主。`--scale` 与 `--max-size` 共同决定输出尺寸，实际采用更小的缩放比例；输出超过 6 亿像素会拒绝。

### 5.4 校验和预览

```powershell
<py> gismap.py verify out --source "图.jpg" --json

cd out
<py> -m http.server 8000
```

浏览器访问 `http://localhost:8000/preview.html`。不要直接双击 `preview.html`；浏览器对 `file://` 的脚本或瓦片访问限制会导致白屏。

`verify` 的行为：

- 对目录，优先检查顶层 `tiles`，其次 `tiles_tms`，并核对 manifest 中逐层瓦片数量。
- 抽查最多 200 个瓦片是否可解码、是否为方形。
- 传入 `--source` 时，对最高层级中心瓦片与源图重渲染结果做平均绝对差比较。
- 对 MBTiles，读取各层瓦片数和 `metadata.bounds`。
- `problems` 非空时退出码为 1；`center_tile_mae > 12` 会产生错位警告。

### 5.5 量化保护线

| 场景 | 源文件中的限制或阈值 |
|---|---|
| 切片总数 | 超过 300,000 块时拒绝，需显式 `--force` |
| 自动层级上限 | z22 |
| 单图源尺寸提示 | 超过 2 亿像素（200 MP）时在 `inspect` 提示先降采样 |
| `export` 输出尺寸 | 超过 6 亿像素（600 MP）时拒绝 |
| 瓦片目录拼接为整图 | 默认超过 4 亿像素（400 MP）时拒绝 |
| 校验解码样本 | 目录模式最多抽查 200 块 |
| `split` 行列范围 | 每维 1~50 |
| 纬度范围 | Web 墨卡托运算裁剪到约 ±85.05112878° |
| 并发 | 默认最多 4；文档建议超大任务最多 8 |

## 6. 坐标系、基准面与对齐

### 6.1 CRS 和输出网格

- 输入只支持 `EPSG:4326`（经纬度）和 `EPSG:3857`（Web 墨卡托米）。
- 输出瓦片始终按 Web 墨卡托 XYZ 网格生成；4326 输入会转换经度和非线性纬度。
- 瓦片范围数值在经纬度区间内时通常是 4326；达到百万级时通常是 3857。
- UTM、高斯-克吕格、CGCS2000 带号等投影必须先用 GDAL/QGIS 重投影，禁止伪造范围。

### 6.2 WGS-84、GCJ-02 与 BD-09

- 谷歌卫星影像、GPS 和 WGS-84 底图使用同一坐标基准。
- 高德、腾讯、谷歌中国道路图通常使用 GCJ-02，相对 WGS-84 有约 100~600 米的非线性偏移。
- 百度使用 BD-09，源文件没有为它提供转换选项。
- 与 GPS/谷歌卫星/天地图影像对齐时保持 WGS-84。
- 与高德/腾讯底图对齐时，如果用户输入的是 GCJ-02 范围，用 `--bounds-datum gcj02` 让脚本反算为 WGS-84。
- 桌面端内存和持久数据只存 WGS-84；GCJ-02 仅作为当前显示基准或用户明确选择的导出基准。

GCJ-02 反算采用迭代法，最多 8 次。`--bounds-datum gcj02` 只处理命令行传入的四个边界值；它不会改变世界文件、GeoTIFF 标签或已有侧车文件中的数值。

### 6.3 对齐排查顺序

1. 先用 `inspect --json` 检查 `crs`、`bounds`、`bounds_source` 和 `native_zoom`。
2. 确认用户拿什么底图比对：WGS-84/GPS/谷歌卫星，还是 GCJ-02 的高德/腾讯。
3. 若偏移为百米级，先按基准面问题处理，不要改 `--bounds` 硬凑。
4. 若整体按固定比例缩放或平移，再检查范围来源、世界文件的像素中心/角点语义和米制/度制混用。
5. 运行 `verify --source`。`center_tile_mae` 小于 8 基本正常，大于 12 需要排查；它只能证明“瓦片与源图内部一致”，不能证明“源图范围真实正确”。
6. 打开预览页，勾选在线底图叠加，用路口、桥等地物确认范围；这是对“范围是否正确”的直接检查。
7. 仍不正时，用已知点核对：把瓦片像素反算为经纬度，再与实际坐标比较。

瓦片像素到经纬度的核对公式：

```text
lon = x / 2^z * 360 - 180
lat = atan(sinh(pi * (1 - 2*y/2^z))) * 180/pi
```

## 7. GisMapsHelper 桌面端边界与接入约束

### 7.1 两套 Python 环境

| 用途 | 解释器 | 依赖与边界 |
|---|---|---|
| 运行 `scripts/gismap.py` | `load_workspace_dependencies` 返回的内置 Python | 只依赖 Pillow；不要把 PySide6、rasterio 或 GIS 库装进去 |
| 运行桌面应用和测试 | `.venv\Scripts\python.exe` | 文档口径 Python 3.13.5、PySide6 6.12，`src/` 布局，已经 editable 安装 |

当前项目版本口径为 v0.3.0。桌面端启动命令是：

```powershell
.\.venv\Scripts\python.exe -m gismapshelper
```

`--env`、`--version`、`--inspect <tif>` 只输出到终端。不要直接执行 `python src\gismapshelper\app.py`，否则没有父包，会触发相对导入错误。

### 7.2 当前代码职责

- `src/gismapshelper/core/`：不 import Qt，承载 TIFF/栅格元数据、坐标、框架、网格、裁剪、重投影、GeoJSON 和 CAD 等纯业务能力，可脱离界面单测。
- `src/gismapshelper/gui/`：PySide6 界面、页面、地图控件、主题和用户交互；不直接 import rasterio、ezdxf 或 numpy。
- `src/gismapshelper/core/tiff.py`：用标准库读取 TIFF/BigTIFF 文件头。
- `src/gismapshelper/core/raster.py`：解析 GeoKey、世界文件、`.prj` 和派生值；只读元数据时不加载整张像素。
- `src/gismapshelper/core/geo.py`：经纬度、墨卡托、瓦片和 GCJ-02 换算的统一出口。
- `src/gismapshelper/core/frames.py`：原点、锚点、方形范围、地面米和本地偏移。
- `src/gismapshelper/core/grid.py`、`grid_export.py`：N×N 网格及其导出。
- `src/gismapshelper/gui/widgets/tilemap.py`：纯 QPainter 地图控件，只接收 `MapOverlay`，不持有页面业务状态。
- `src/gismapshelper/gui/pages/tif_map.py`：TIF 范围、项目原点、切片方形、图层、裁剪、网格、GeoJSON/CAD 等交互编排。

界面第一项已是“TIF 范围”，第二个页面“首页”只显示运行环境。新增页面应继承 `gui/pages/base.py:BasePage`，声明 `title/subtitle`，并登记到 `gui/pages/__init__.py:NAV_PAGES`；列表顺序就是导航顺序。

### 7.3 后续接入时不能破坏的约束

- 切片算法应继续从命令行技能抽到 `src/gismapshelper/core/`；界面只收集参数、显示进度和处理用户提示。
- CLI 与桌面端共用 `inspect → tile/export → verify` 语义，不在界面层重复计算范围或层级。
- 内存中的坐标只存 WGS-84。显示到 GCJ-02 底图前由 `TifMapPage._to_display()` 转换；点选回传经 `_to_wgs84()` 转回。
- 切片图层只保存方案（`LayerEntry.spec`），矩形在 `_refresh_overlays()` 时用当前原点和当前 TIF 重算；不要把派生范围缓存进切片图层。
- `tif` 和 `origin` 两类是静态图层，可以保存 `bounds/point`；地图控件仍只接收 `MapOverlay`。
- 距离方案直接使用地面米。像素方案边长为 `像素数 × ground_resolution_m × cos(原点纬度)`；`ground_resolution_m` 是 Web 墨卡托米/像素，不能误用已经乘过 cos 的 `terrain_resolution_m`。
- 项目原点支持 `center/nw/ne/sw/se` 五个锚点；增加锚点时要同步几何、标签和两个下拉框。
- `.tif/.tiff` 的范围读取必须保持“只读文件头”，不能为了看范围解码整张像素。
- 批量读写像素应按 512 行分块；临时目录用受控的临时目录并在 `try/finally` 中清理，失败时删除半成品。
- 一次性操作使用 `QProgressDialog + processEvents + 可取消`，取消异常由页面捕获。
- 输出默认不覆盖；core 层 `overwrite=False`，界面先征求用户同意。
- 新增 CRS 白名单时必须同步 `raster.crs_category`、`grid._SUPPORTED_CRS` 和 `grid_export` 检查。
- `QMessageBox/QSettings/QApplication.processEvents` 只留在 GUI 层；core 抛自定义异常，由页面翻译为提示。

## 8. 开发、测试、版本与打包规则

### 8.1 分层与数值口径

- 几何只有统一出口：`core/geo.py`、`core/frames.py`、`core/grid.py`、`core/raster.py`；页面不得自写方形、分辨率或瓦片编号算法。
- 球面面积使用 `geo.spherical_area_km2`，距离换米使用 `frames.meters_per_degree`，不用平面近似替代。
- DXF 坐标为以项目原点为 `(0,0)` 的本地米，东/北为正。
- GCJ-02 不进入 `LayerEntry`、manifest 或 GeoJSON 默认值；用户选择后才在导出时换算。
- rasterio/ezdxf 缺失应表现为功能置灰，而不是应用无法启动。可选依赖通过 `RASTERIO_AVAILABLE/EZDXF_AVAILABLE` 暴露。
- 使用 rasterio 前先 `gdal_env.configure()`；清掉错误的 `PROJ_LIB/GDAL_DRIVER_PATH` 影响，以 rasterio 自带数据目录为准，不把本机 OSGeo4W 路径写死。

### 8.2 状态、界面和资源

- 默认没有工程文件，图层只在内存中，关闭窗口即清空；需要持久化时落 `QSettings`。
- 主题颜色和字号只在 `gui/theme.py` 修改；布局里不写色值。不要删除 `build_palette()`，它解决 Windows 深色模式调色板问题。
- 右侧功能菜单与 `_func_stack` 面板顺序必须一致，因为下标就是映射；新增菜单项时同步高度。
- 下拉框使用 `DownwardComboBox`。用户文案用简体中文；坐标统一 6 位小数，米制统一 2 位，并带单位。
- Qt 事件/虚函数保持驼峰并写 `# noqa: N802`。修改 objectName 前先查测试是否依赖。

### 8.3 测试门槛

```powershell
# core + GUI 全量
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 手工复核
.\.venv\Scripts\python.exe -m gismapshelper --inspect "testmap_切片 1 km.tif"
.\.venv\Scripts\python.exe -m gismapshelper

# 兼容 pytest 时也可运行
.\.venv\Scripts\python.exe -m pytest
```

- GUI 测试通过 `QT_QPA_PLATFORM=offscreen` 在无显示器环境执行，地图使用空白底图，测试不联网。
- 仓库不提交二进制样图；TIFF 样本应现场构造，或需要影像时用 `gismap.py export` 临时生成。
- 几何/数值改动必须补 core 单测；界面联动补 `test_gui.py`。
- `dev-rules.md` 记录 2026-10-28 的基线为 111 个用例全绿；当前 `tests/` 中可数得 111 个 `test_` 函数，与文档基线一致。
- `ruff/mypy` 已配置在 `pyproject.toml`，但 `.venv` 默认未安装；先安装 `requirements-dev.txt` 才可运行，它们不是现有 CI 门槛。
- 改 `scripts/gismap.py` 后必须运行 `scripts/selftest.py`。自检覆盖世界文件、4326/3857、几何像素反算、XYZ/TMS/MBTiles、瓦片回流、分幅范围和 GCJ-02 往返。

本次文档核对使用工作区内置 Python 运行了 `scripts/selftest.py`，8 组自检全部通过；没有运行真实影像切片，也没有联网下载瓦片。

### 8.4 版本、提交与打包

- 版本号三处同步：`pyproject.toml`、`src/gismapshelper/__init__.py`、`README.md` 首段与环境表。
- `pyproject.toml` 必须保存为 UTF-8 无 BOM；带 BOM 会使 `pip install -e .` 报 `TOMLDecodeError`。
- 提交使用 Conventional Commits 加中文说明：`feat|fix|docs|refactor|test|chore(scope): 说明`；正文解释“为什么”；一次提交只做一件事。
- `.tif/.tiff/.mbtiles/output/` 属于不应提交的生成物。
- PyInstaller 使用 `--windowed`；排除未使用的 `PySide6.QtWebEngine*`、`Qt3D*`、`QtQuick*`。
- 打包时必须收集 rasterio 的 GDAL 驱动和 `proj_data/gdal_data`；ezdxf 打包版需把 `XDG_CACHE_HOME` 指向可写临时目录。
- Pillow 的 JPEG/WEBP 解码 DLL 必须在包内；打包后不能只看构建成功，要双击完成载入 TIF、切 2×2、导出 GeoJSON、打开 JPEG 压缩 TIF 的实测。

## 9. 常见问题与排障

| 现象或报错 | 原因 | 处理 |
|---|---|---|
| `没能从 xxx 检测到地理范围` | 没有可识别世界文件、GeoTIFF 标签或侧车 | 查看 `inspect` 的 `crs`；用 `--bounds` 补范围，米制再加 `--source-crs 3857` |
| `只支持 4326 与 3857` | 输入是 UTM、高斯-克吕格等投影 | 先 `gdalwarp -t_srs EPSG:3857`，不要伪造范围 |
| 预计瓦片超过 30 万 | 层级或范围过大 | 先 `--dry-run`；缩小范围或降最大层级；确认后才用 `--force` |
| 输出目录非空 | 防止误覆盖 | 换目录，或核实旧内容后 `--force` |
| 当前范围/layer 没有可生成瓦片 | 边界反向或 `--zooms` 无交集 | 检查西<东、南<北和层级 |
| 拼接像素超过保护线 | 瓦片目录拼整图过大 | 直接用 `tile` 转格式，不必先拼大图 |
| 导出超过 6 亿像素 | 放大倍数或源图过大 | 加 `--scale 0.5` 或 `--max-size` |
| `--bounds` 数量错误 | 带了数组括号或缺少分量 | 使用四段裸数字：西,南,东,北 |
| `--split` 格式错误 | 传了不支持的写法或超范围 | 用 `2`、`3`、`4` 或 `2x3`，每维 1~50 |
| 找不到 z/x/y 瓦片 | 传错根目录或下载器多套一层 | 传入包含数字 `z/x` 的目录 |
| 无法读取 MBTiles | 文件损坏或表结构不符合标准 | 用 DB Browser 检查 `tiles` 和 `metadata` |
| 预览页白屏 | 直接以 `file://` 打开 | 在输出目录运行 HTTP server，再访问 localhost |
| 预览提示无法加载 Leaflet | 无网络或 CDN 不可达 | 瓦片通常完好；改用 QGIS 或提供本地 Leaflet 副本 |
| 边缘透明/白边 | 地理范围不占满整块瓦片 | 正常补边；JPEG 恒为白底 |
| 瓦片上下颠倒 | 输入目录是 TMS 命名 | 加 `--input-tms` |
| 影像整体偏移百米级 | GCJ-02 基准面差异 | 查比对底图，使用 `--bounds-datum gcj02`，不要改 bounds 硬凑 |
| 放大后模糊 | 超过原生层级的 overzoom 只是插值 | 以 `inspect` 的 `native_zoom` 为准 |
| `verify` 数量不一致 | 生成中断、目录被改或磁盘不足 | 清点 space 和文件数，输出到新目录重跑 |
| 内存飙升或进程被杀 | 源图过大 | 先降采样或缩减任务，再切片 |
| 中文路径/控制台编码错误 | 控制台不是 UTF-8 | 加 `-X utf8` 或设置 `$env:PYTHONIOENCODING='utf-8'` |
| 桌面端 `--inspect` 在 GBK 控制台报 `UnicodeEncodeError` | 输出中含 `km²` | 临时设置 `PYTHONIOENCODING=utf-8`；若要修复，集中改 `app._inspect_file` |
| PowerShell 拒绝 `Activate.ps1` | 脚本所在位置不受信任 | 用 `-Scope Process -ExecutionPolicy Bypass`、`activate.bat` 或直接调 `.venv\Scripts\python.exe` |
| `pip install` 静默挂起 | 本机 pip 缓存曾损坏（435 MB） | 清缓存或 `--no-cache-dir`；不要执行 `pip install --upgrade pip` |

## 10. 已知矛盾与风险

以下问题来自源文件与当前实现的交叉核对，后续修改时应确认并择机统一：

1. **资源路径有历史漂移。** `SKILL.md` 使用工作区相对路径，`dev-rules.md` 多处写 `E:\DeepSeek\GisMapsHelper`，当前任务工作目录是 `F:\code\GisMapsHelper`。文档命令应优先使用相对路径，避免把本机旧绝对路径当成交接契约。
2. **整图导出并非“只有单个文件”。** `SKILL.md` 说整幅导出只有 `-o` 文件，但 `cmd_export()` 中的 `write_sidecars()` 对整图和分幅都会写同名世界文件与 `.prj`。按当前代码，整图也会多出两个侧车文件。
3. **MBTiles 直接配合 `--source` 校验存在实现缺口。** `cmd_verify()` 只在目录目标中设置 `maxz`；目标是 `.mbtiles` 且传 `--source` 时会用 `int(maxz)`，存在 `TypeError` 风险。结构校验可不带 `--source`，内容对齐校验优先对 XYZ/TMS 目录执行。
4. **预览页默认不是完全离线。** 本地瓦片离线，但 `preview_template.html` 从 `unpkg.com` 加载 Leaflet；无网络时模板会提示改用 QGIS/OpenLayers/Leaflet。若交付要求“完全离线预览”，必须另备本地 Leaflet。
5. **`--force` 不是清理开关。** 它绕过保护，但不会删除旧层级、旧扩展名或不再生成的瓦片；复用目录可能混入过期文件。除已有 MBTiles 单文件会被重建外，最稳妥做法始终是输出到新目录。
6. **`--bounds-datum gcj02` 的适用范围容易误解。** 它只转换命令行 `--bounds` 的四个边界，不重写世界文件或 GeoTIFF 元数据。未传 `--bounds` 时该参数没有实际作用。
7. **“自动识别 TMS”措辞不够准确。** `TileDirSource` 需要调用方传 `--input-tms`，并未根据 y 值范围自动判定 TMS。操作时必须按实际命名显式加开关。
8. **输入 CRS 的支持边界窄于桌面端依赖栈。** 技能脚本自身只支持 4326/3857；桌面端虽然有 rasterio 可用于重投影，但不能据此宣称 CLI 能直接读取 UTM 并正确切片。
9. **当前检出目录的 `.venv` 已失效。** `.venv\pyvenv.cfg` 指向不存在的 `C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe`，且创建记录仍指向旧的 `F:\DSH\GisMapsHelper`；因此文档中的 `.venv\Scripts\python.exe` 测试命令在当前快照中无法启动。迁移或验收前应使用目标机现有 Python 重建虚拟环境并重新安装 `requirements.txt`/`requirements-dev.txt`。

## 11. 来源索引

| 本文主题 | 主要来源 |
|---|---|
| 技能定位、适用/不适用边界 | `SKILL.md` 开头、`## 不要做` |
| 目录与文件职责 | 目录清单；`gismap.py` 顶部说明、类与子命令分段；`selftest.py`；`preview_template.html` |
| `inspect → tile/export → verify → present` | `SKILL.md` 的“必须遵守的顺序”与各步骤；`gismap.py` 的 `cmd_inspect/cmd_tile/cmd_export/cmd_verify` |
| 输入类型与坐标来源顺序 | `references/input-formats.md`；`gismap.py` 的 `resolve_georef()`、`open_source()`；`SKILL.md` 的输入类型表 |
| 输出布局与 manifest | `gismap.py` 的 `cmd_tile()`、`cmd_export()`、`DirSink`、`MBTilesSink`；`SKILL.md` 的“产物布局” |
| 常用参数与量化限制 | `gismap.py` 的 `build_parser()`、`choose_zooms()`、`cmd_tile()`、`to_raster()`、`cmd_export()`、`cmd_verify()`；`SKILL.md`；`troubleshooting.md` |
| 坐标系、投影和 GCJ-02 | `references/alignment.md`；`references/input-formats.md`；`gismap.py` 的坐标数学与 `apply_datum()` |
| 对齐排查 | `references/alignment.md`；`SKILL.md` 的“三个最容易踩的坑”；`troubleshooting.md` |
| 桌面端代码边界和双 Python | `SKILL.md` 的“本工作区：GisMapsHelper 桌面端”；`references/dev-rules.md` 第 1~7 节及第 11 节；当前 `src/gismapshelper/` 结构核对 |
| 测试门槛 | `references/dev-rules.md` 第 8 节；`scripts/selftest.py`；当前 `tests/` 文件核对 |
| 版本、提交和打包 | `references/dev-rules.md` 第 9~12 节；`SKILL.md` 的桌面端打包说明；当前 `pyproject.toml`/`README.md` 核对 |
| 排障表 | `references/troubleshooting.md`；`SKILL.md` 的“三个最容易踩的坑”和“不要做”；`references/dev-rules.md` 的已知坑 |
| 矛盾与风险 | `SKILL.md`、`references/input-formats.md`、`references/dev-rules.md`、`gismap.py`、`preview_template.html` 的交叉核对 |
