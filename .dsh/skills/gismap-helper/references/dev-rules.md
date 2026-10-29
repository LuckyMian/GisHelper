# 代码更新规则（GisMapsHelper 工作区）

本文件是 `gismap-helper` 技能的项目侧约定，归档截至 **v0.3.0（2026-10-28）**。
动手改 `E:\DeepSeek\GisMapsHelper` 之前先读一遍；里面的每条规则都对应现有代码里的一个实现，
不是理想化建议。与 `SKILL.md` 分工：切片语义（`inspect → tile/export → verify`）以 `SKILL.md` 为准，
桌面端代码怎么改以本文件为准。

## 1. 分层与依赖边界

- `src/gismapshelper/core/` **不 import Qt**（PySide6 一行都不出现），可脱离界面单测。
- `src/gismapshelper/gui/` **不直接 import rasterio / ezdxf / numpy**。需要它们的能力都包在 core 里，
  用 `RASTERIO_AVAILABLE` / `EZDXF_AVAILABLE` 标志向外暴露（`core/crop.py`、`core/cad.py`）；
  界面只读标志、置灰按钮、给出安装命令（样板：`tif_map._update_grid_hint`）。
- `QMessageBox` / `QSettings` / `QApplication.processEvents` 只允许出现在 `gui/`；core 抛自定义异常
  （`RasterInfoError`、`CropError`、`GridError`、`GridExportError`、`CadError`、`ReprojectError`），由页面翻译成弹窗。
- 新增业务能力先落到 core（纯函数 / 数据类 + 单测），再在页面接线；页面里不写几何算法。
- 几何只有一个出口：`core/geo.py`（经纬度 / 墨卡托 / 瓦片 / GCJ-02）、`core/frames.py`（原点与方形）、
  `core/grid.py`（N×N 网格）、`core/raster.py`（范围与派生值）。界面层禁止自己算方形、分辨率或瓦片编号。

## 2. 坐标系与基准（最容易出错，改动前先看这段）

- **内存里只存 WGS-84**。屏幕显示 / 命中判断前走 `TifMapPage._to_display()`；点选回传的坐标先
  `_to_wgs84()`（全项目只有这两处做基准换算）。
- GCJ-02 只是「显示基准」和「导出选项」：不进 `LayerEntry`、不进 manifest、不进 GeoJSON 默认值。
  GeoJSON 默认 `wgs84`，用户在导出对话框里选了才换算（`geojson.bounds_in_datum`）。
- 只支持 **EPSG:4326 / EPSG:3857**。UTM、高斯-克吕格等一律明确报错并让用户
  `gdalwarp -t_srs EPSG:3857`，**禁止猜范围**。判定入口是 `raster.crs_category`，收白名单的地方在
  `grid._SUPPORTED_CRS` 与 `grid_export` 的 CRS 检查，两处要同时改。
- 出现偏移先查基准面（底图 `datum`、`--bounds-datum gcj02`），**不要改 `bounds` 数值硬凑**。
- 面积用球面公式（`geo.spherical_area_km2`），距离用 `frames.meters_per_degree`，不要用平面近似。

## 3. 数值口径

- `RasterInfo.ground_resolution_m` = **Web 墨卡托米/像素**（与 QGIS 显示一致）；
  `terrain_resolution_m = ground × cos(中心纬度)`。
- 按像素切片的边长 = 像素数 × `ground_resolution_m` × `cos(原点纬度)`（`frames.edge_from_pixels`）。
  误用 `terrain_resolution_m` 会重复扣一次 cos，40°N 附近能差 23%。
- 距离方案给的是**地面米**；DXF 坐标是**以原点为 (0,0) 的本地米**（`frames.local_offsets`，东 / 北为正）。
- 参考点五选一：`frames.ANCHORS = center / nw / ne / sw / se`，默认 `center`。
  新增位置要同步 `ANCHOR_LABELS`、`square_bounds`、`anchor_point`，以及界面上的两个下拉框。

## 4. 状态与几何缓存

- 切片图层只存方案（`LayerEntry.spec`），矩形每次 `_refresh_overlays()` 用**当前原点 + 当前 TIF** 重算。
  不要为了省事把 `bounds` 缓存进图层——那会让「换原点后方形不动」变成默认行为。
  `tif` / `origin` 两类是静态几何，可以直接存 `bounds` / `point`。
- 地图控件不持业务状态：`TileMapWidget` 只认
  `MapOverlay(key, kind, label, color, bounds|point, visible)`。新增叠加类型 = 新 `kind` +
  `paintEvent` 里的一个分支 + 一个 `_draw_*` 方法，不要在控件里读页面状态。
- 默认没有工程文件：图层只在内存里，关窗即清空。要持久化就落 `QSettings`，键名沿用
  `basemap/id`、`basemap/key/<id>`、`last_dir` 的风格。
- 一次性操作（切片 / 裁剪 / 导出 / 重投影）必须 `QProgressDialog` + 回调里 `processEvents` + 可取消；
  取消时抛 core 的异常，由页面捕获，不让半成品文件留在磁盘上。

## 5. 大文件与资源

- 读元数据只读文件头（`core/tiff.py` + `core/raster.py`），**不得为「看范围」引入像素解码**。
  几十 GB 的影像要秒开，这是本项目的核心卖点。
- 读写像素一律按 512 行分块（`crop._write_window`、`reproject.to_mercator`），禁止整图 `read()`。
  大图先降采样或重投影，再切片。
- 临时目录用 `tempfile.mkdtemp` / `TemporaryDirectory` + `try/finally` 清理；
  写盘失败要删半成品（`_write_window` 里的 `except: target.unlink()`）。
- 输出默认不覆盖：core 层 `overwrite=False`，界面先问用户。

## 6. GDAL / PROJ 环境

- 任何 CRS 查询之前先 `gdal_env.configure()`（`crop.py`、`reproject.py` 在导入期已经调用）。
  新增用 rasterio 的模块照抄这两行。
- 本机装过 OSGeo4W/QGIS，系统 `PROJ_LIB` 指向旧 `proj.db`（版本 5，rasterio 要 ≥ 6）。
  **不要把本机 OSGeo4W 路径写进代码**，以 rasterio 自带的 `proj_data` / `gdal_data` 为准，并清掉
  `GDAL_DRIVER_PATH`。
- 缺 rasterio / ezdxf 必须是「少一个功能」，不是「程序起不来」：`try/except ImportError` + `*_AVAILABLE`，
  页面据此置灰并给出 `pip install` 提示。

## 7. 界面约定

- 新页面：继承 `gui/pages/base.py:BasePage`，声明 `title` / `subtitle`，在
  `gui/pages/__init__.py:NAV_PAGES` 登记（**列表顺序即导航顺序**，当前第一项是 `TifMapPage`）。
  页面不碰 `QMainWindow` 的标题，主窗口切换时自动读类属性。
- 右侧功能区加一项：`_build_right_panel` 里 `_func_menu` 加名字 + `_func_stack` 加面板，
  **两者顺序必须一致**（下标即映射）；`_func_menu` 的高度按项数写死，加项要同步。
- 颜色 / 字号只改 `gui/theme.py`（`COLORS` + `STYLESHEET`，靠 objectName 匹配），布局代码里不写色值。
  Windows 深色模式下 Qt 默认调色板是黑的，`theme.build_palette()` 覆盖了它，**不要删**。
- 下拉框一律用 `gui/widgets/combo.py:DownwardComboBox`（Qt 默认把列表往输入框上方顶）。
- 用户可见文案用简体中文，带单位与精度（`1.00 km²`、`12.54 米/像素`）；坐标统一 6 位小数、米制 2 位。
- Qt 事件 / 虚函数保持驼峰并标 `# noqa: N802`（`dragEnterEvent`、`paintEvent` 等）。
- 改 objectName 前先搜 `tests/`：`findChild(..., "Nav")` 之类的界面测试依赖它。

## 8. 测试与验证（交付门槛）

```powershell
# 必跑：core + 界面全量
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 手工复核（至少一遍）
.\.venv\Scripts\python.exe -m gismapshelper --inspect "testmap_切片 1 km.tif"
.\.venv\Scripts\python.exe -m gismapshelper
```

- 界面测试靠 `QT_QPA_PLATFORM=offscreen`（`test_gui.py` / `test_map.py` 顶部已 `setdefault`），
  不需要显示器；地图统一用「空白底图」，**测试不联网**。
- 仓库里不塞二进制：TIFF 样本现场拼文件头（`tests/test_raster.py`）或用 rasterio 现写小图
  （`tests/test_crop.py`）。需要样本影像时用 `scripts/gismap.py export` 现造，别把 tif 提交上去。
- 几何 / 数值改动必须补 core 单测（样板：`tests/test_frames.py`、`tests/test_grid.py`）；
  界面联动补 `tests/test_gui.py`。
- `ruff` / `mypy` 已配置在 `pyproject.toml`（line-length 100），但 `.venv` 默认**没装**：
  要跑先 `pip install -r requirements-dev.txt`，别把它们当成现有 CI 门槛。
- 当前基线：**111 个用例全绿**（2026-10-28，`.venv` Python 3.13.5 + PySide6 6.12.0 + rasterio 1.5.2）。
  提交前跑一遍，数字对不上就说明改坏了东西。

## 9. 版本与提交

- 版本号三处同步：`pyproject.toml`、`src/gismapshelper/__init__.py`、`README.md` 首段与环境表。
  当前 0.3.0（`.venv` 的 editable 元数据已用 `pip install -e .` 重建为 0.3.0 + 四依赖）。
- `pyproject.toml` **不能带 UTF-8 BOM**：带 BOM 时 `pip install -e .` 直接
  `TOMLDecodeError: Invalid statement (at line 1, column 1)`。本次已修，之后编辑保存要确认是「UTF-8（无 BOM）」。
- 提交信息用 Conventional Commits + 中文：`feat|fix|docs|refactor|test|chore(scope): 说明`，
  正文写「为什么」；一次提交只做一件事，不混无关格式化。
- `*.tif` / `*.tiff` / `*.mbtiles` / `output/` 已在 `.gitignore`；样图靠本技能与部署文档说明来源，
  不要 `-f` 加进仓库。
- 改了技能侧脚本（`scripts/gismap.py`）要跑 `scripts/selftest.py` 做端到端回归。

## 10. 打包（PyInstaller）

- `--windowed`；spec 里排除 `PySide6.QtWebEngine*`、`Qt3D*`、`QtQuick*`（地图是 QPainter 实现，
  没有 WebEngine，别为地图又把它加回来）。
- rasterio 的 GDAL 驱动与 `proj_data` / `gdal_data` 要作为数据目录一起收；漏了会在裁剪时报
  `proj.db` 版本错或找不到驱动。
- ezdxf 首次导入会扫系统字体、建缓存：打包版必须设 `XDG_CACHE_HOME`（`cad.py` 已指到 temp），
  否则只读目录会刷一堆警告。
- 打包后**双击实测**：载入 TIF → 切 2×2 → 导出 GeoJSON → 打开 JPEG 压缩的 TIF（确认解码 DLL 在包里）。

## 11. 已知坑（本机实测，改之前先确认）

- `--inspect` 在 GBK 控制台会 `UnicodeEncodeError`（输出里有 `km²`）。临时规避：
  `$env:PYTHONIOENCODING='utf-8'`；代码尚未统一 stdout 编码，要改就改 `app._inspect_file` 的写出方式，
  别在别处零散 `encode`。
- `pyproject.toml` 曾被存成带 BOM 的 UTF-8（`EF BB BF`），已修；再动这个文件后按首字节自查一次，
  否则换机执行 `pip install -e .` 会直接失败。
- `E:\DeepSeek\GisMapsHelper` 下的 `.ps1` 被 PowerShell 判为不受信任位置，`Activate.ps1` 报「未数字签名」：
  用 `-Scope Process -ExecutionPolicy Bypass`、`activate.bat` 或直接调 `python.exe`。
- `%LOCALAPPDATA%\pip\Cache` 曾损坏导致 `pip install` 静默挂死：清缓存或 `--no-cache-dir`；
  不要 `pip install --upgrade pip`。
- 系统 `PROJ_LIB` 指向 OSGeo4W 旧库时的报错与解法见 `README.md` 常见问题。
- 没装 ODA File Converter 时只出 DXF，是**预期降级**，不要当 bug 报错。

## 12. 收尾清单

1. `unittest discover` 全绿（数字要能对上基线）；
2. `--inspect` 样图 + GUI 手测：载入 → 点原点 → 建方形 → 裁剪 → 网格切片 → 导出 GeoJSON；
3. 受影响的说明同步：`README.md`、本文件、`docs/deploy.md`；
4. 发布则升版本号三处 + Conventional Commit；
5. 改了 `scripts/gismap.py` 再跑 `scripts/selftest.py`。
