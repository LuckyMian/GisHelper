# 换机部署手册（GisMapsHelper v0.3.0）

目标：在**另一台 Windows 电脑**上从零跑起本项目（源码包 → 虚拟环境 → 依赖 → 自检 → 界面）。

一句话流程：装 **Python 3.13 x64** → 解压到 `D:\GisMapsHelper` → 建 `.venv` →
`pip install -e .` → `--inspect` 自检 → `-m gismapshelper` 启动 → 跑一遍测试。
正常情况 10 分钟，**不需要管理员权限**，也不需要 QGIS / OSGeo4W / GDAL。

> 两个「不要」：
> 1. **不要拷本机的 `.venv`**——它绑定了本机 Python 路径和平台，换机必坏；
> 2. **不要拷 `testmap_切片 10 km.tif`（660 MB）**——那是切片产物，不是源码，压缩包里已排除。

---

## 0. 包里的东西

| 路径 | 内容 | 必需 |
| --- | --- | --- |
| `src/gismapshelper/` | 应用源码（`core/` 业务核心 + `gui/` 界面） | 是 |
| `tests/` | 111 个 unittest 用例 | 推荐 |
| `pyproject.toml` / `requirements.txt` / `requirements-dev.txt` | 依赖与工具配置 | 是 |
| `README.md` | 功能说明、实现要点、常见问题 | 是 |
| `docs/deploy.md`（本文件）、`docs/basemaps.md`、`docs/*.png` | 部署与底图文档、界面截图 | 参考 |
| `.dsh/skills/gismap-helper/` | AI 技能（切片命令 + 代码更新规则 `references/dev-rules.md`） | 否 |
| `.vscode/` | 调试与测试配置（F5 可用） | 否 |
| `testmap_切片 1 km.tif` | 1 km 样图（约 1 km²，EPSG:3857），用于自检 | 推荐 |
| `testmap.geojson` | 历史导出的图层样例，可用来对照格式 | 否 |

压缩包里**没有**：`.venv/`、`__pycache__/`、660 MB 的 10 km 样图、切片输出目录、ezdxf 缓存。

---

## 1. 目标机要求

| 项目 | 要求 | 本机参考 |
| --- | --- | --- |
| 系统 | Windows 10 / 11 64 位 | Windows 11，实测 |
| Python | 3.13 x64（`pyproject.toml` 最低 3.10） | 3.13.5，实测 |
| 磁盘 | 源码 < 10 MB；依赖约 1.2–1.5 GB | — |
| 网络 | 首次装依赖需要；底图需要 | 内网可用「空白底图」 |
| 权限 | 普通用户即可（能写安装目录） | — |

不需要：Visual Studio、GDAL、PROJ、QGIS、OSGeo4W。
`rasterio` 的 wheel 自带 GDAL / PROJ 数据，`PySide6` 自带 Qt 运行时。

---

## 2. 解压与放置

```powershell
# 建议放在盘根目录下的短路径，避免 OneDrive 同步盘与超长中文路径
Expand-Archive .\GisMapsHelper-v0.3.0.zip -DestinationPath D:\
# 若压缩包自带一层目录，展开后应是 D:\GisMapsHelper\
cd D:\GisMapsHelper
```

解压后确认这三样都在：`pyproject.toml`、`src\gismapshelper\__init__.py`、
`docs\deploy.md`（本文件）。`src\...` 少一层或包名写成 `src\src`，说明解压层级错了。

不要把项目放到 `C:\Program Files`：建 `.venv` 需要写权限，普通用户会在那里失败。

---

## 3. 建虚拟环境

```powershell
cd D:\GisMapsHelper
# 有 py 启动器就用它（能精确选版本，本机同时装过 3.6，别选错）
py -3.13 -m venv .venv

# 没有 py 启动器时，用完整路径
# & "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m venv .venv
```

激活虚拟环境（三种方式，任选）：

```powershell
# 1) 临时放开脚本执行策略（只影响当前窗口，关闭即失效）
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\.venv\Scripts\Activate.ps1

# 2) 用 cmd 的批处理，不受 PowerShell 执行策略影响
.\.venv\Scripts\activate.bat

# 3) 干脆不激活 —— 下文所有命令都用这种方式
.\.venv\Scripts\python.exe --version
```

> 如果 `.ps1` 提示「未对文件进行数字签名，无法在当前系统上运行」：项目被 PowerShell 判为
> 不受信任位置（本机 `E:` 盘就踩过）。用方式 1 的 `-Scope Process` 放开即可，
> **不要**用 `-Scope LocalMachine` 改全局策略。

---

## 4. 安装依赖

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

- `pip install -e .` 从 `pyproject.toml` 装 **PySide6 / rasterio / numpy / ezdxf** 四个运行时依赖，
  并把包以**可编辑模式**装进 `.venv`，同时注册 `gismaps-helper` 命令。
- 等同于 `pip install -r requirements.txt` + `pip install -e .`，一步到位，推荐。
- 预期结尾出现 `Successfully installed ... gismapshelper-0.3.0 ...`。

> 若报 `tomllib.TOMLDecodeError: Invalid statement (at line 1, column 1)`：
> `pyproject.toml` 被编辑器加了 **UTF-8 BOM**。用 VS Code 另存为「UTF-8」（无 BOM）再重试。
> 压缩包里的这份已经是不带 BOM 的。

**不要做**：`pip install --upgrade pip`（本机踩过：替换正在运行的 pip 会挂起），
`pip install -e .[dev]` 只在需要 ruff / mypy / pytest / pyinstaller 时再装（见 §7）。

验证：

```powershell
.\.venv\Scripts\python.exe -m pip list | Select-String "PySide6|rasterio|ezdxf|numpy|gismapshelper"
.\.venv\Scripts\python.exe -c "import PySide6, rasterio, ezdxf, numpy; print(PySide6.__version__, rasterio.__version__, ezdxf.__version__, numpy.__version__)"
```

本机实测版本（可作对照）：`PySide6 6.12.0`、`rasterio 1.5.2`、`ezdxf 1.4.5`、`numpy 2.5.4`。

### 离线 / 内网安装

在**有网**的机器上下载全部 wheel（要同操作系统、同 Python 大版本、同位数）：

```powershell
pip download -d wheels -r requirements.txt
```

拷 `wheels/` 到目标机后：

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --find-links .\wheels -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-build-isolation
```

### 装不上 / 卡住

- `pip` 走到安装阶段静默挂死：多半是 pip 缓存损坏（本机 `%LOCALAPPDATA%\pip\Cache` 坏过一次），
  清缓存或加 `--no-cache-dir`；
- `rasterio` 找不到 wheel：确认是 **64 位** Python、版本在 3.10–3.13，且没装 32 位版；
  必要时加 `--only-binary=:all:` 强制用 wheel，别让它去编译 GDAL。

---

## 5. 终端自检

**先设一次输出编码**，否则 `--inspect` 会在 GBK 控制台崩（输出里有 `km²`）：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
```

```powershell
.\.venv\Scripts\python.exe -m gismapshelper --version
.\.venv\Scripts\python.exe -m gismapshelper --env
.\.venv\Scripts\python.exe -m gismapshelper --inspect "testmap_切片 1 km.tif"
```

预期 `--version` 输出 `0.3.0`；`--env` 类似：

```
版本      v0.3.0
Python    3.13.5 (64bit)
解释器    D:\GisMapsHelper\.venv\Scripts\python.exe
虚拟环境  是
平台      Windows 11
运行方式  源码运行
```

`--inspect` 读 1 km 样图应得到（数值会随样图而定，关键看「坐标系 / 范围 / 分辨率 / 原生层级」不是空的）：

```
文件            testmap_切片 1 km.tif
文件大小        7.3 MB
像素尺寸        3,800 × 3,801（14.4 MP）
存储格式        GeoTIFF · 条带 · 8 位 × 3 波段
数据类型        uint8
压缩方式        JPEG
色彩解释        RGB
NODATA          未标注
坐标系          EPSG:3857
坐标系来源      GeoTIFF GeoKey 3073（PCSCitation）
原始范围        西 12,568,259.89，南 3,272,628.52，东 12,569,394.50，北 3,273,763.43（米）
经度范围        112.902600 → 112.912792
纬度范围        28.187458 → 28.196443
中心点          112.907696, 28.191950
覆盖面积        1.00 km²
分辨率（投影）  0.299 米/像素
分辨率（实地）  0.263 米/像素
原生层级        z19.00
建议切片层级    z16 ~ z18
范围来源        GeoTIFF ModelPixelScale + ModelTiepoint
```

`--inspect` 只读文件头、不解码像素，几十 GB 的影像也是秒开。

---

## 6. 启动界面

```powershell
.\.venv\Scripts\python.exe -m gismapshelper
# 或者用注册好的命令（等价）
.\.venv\Scripts\gismaps-helper.exe
```

VS Code 打开项目后按 **F5** 即可（`.vscode/launch.json` 已按 `-m gismapshelper` 配好）。

> **不要** `python src\gismapshelper\app.py`：文件被当脚本执行时没有父包，会报
> `attempted relative import with no known parent package`。

### 界面自检清单

| 步骤 | 预期 |
| --- | --- |
| 把 `testmap_切片 1 km.tif` 拖进窗口（或「浏览…」） | 地图缩放到影像范围，左侧「影像信息」出现尺寸、坐标系、经纬度范围；蓝色矩形框住影像 |
| 顶部底图下拉切「空白底图」 | 不联网也显示网格与矩形（验证界面本身正常） |
| 切回「Esri 卫星影像」 | 几秒内出图（需联网；失败 4 张会在顶部提示） |
| 右栏「原点与切片」→「在地图上点选」→ 点一下 | 地图出现红点，按钮右侧显示原点经纬度（Esc 可取消） |
| 选「1 km」→「创建」 | 左侧图层多一条「1 km 方案」，地图出现橙色方框 |
| 选中该方形 → 右栏「裁剪 TIF」→「裁剪 TIF…」 | 源文件旁生成 `testmap_切片 1 km_1 km 方案.tif`，状态栏报像素尺寸；再用 `--inspect` 能读 |
| 右栏「切片 TIF」→ 选 3×3 → 选输出目录 → 开始 | 生成 `<影像名>_3x3/`：9 个 GeoTIFF + 1 个 GeoJSON + 1 个 DXF |
| 右栏「导出 GeoJSON」→ 保存 | 得到一个 FeatureCollection 文件，QGIS / geojson.io 可打开 |

---

## 7. 跑测试（推荐）

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'   # 无显示器也能跑界面测试
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

预期：`Ran 111 tests in ~1.2s` + `OK`（本机实测 111 个用例全绿，2026-10-28）。
测试不联网（地图固定用空白底图），TIFF 样本现场生成，不需要额外数据。

需要 ruff / mypy / pytest / pyinstaller 时：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

它们**不是**运行依赖，`.venv` 里默认没有，不影响 `python -m gismapshelper`。

---

## 8. 可选组件

| 组件 | 作用 | 装法 |
| --- | --- | --- |
| 天地图 tk | 「天地图 矢量 / 影像 / 地形」底图需要密钥 | <https://console.tianditu.gov.cn/> 免费申请，填进页面「tk」输入框，记在本机 `QSettings`（注册表 `HKCU\Software\GisMapsHelper\GisMapsHelper`） |
| ODA File Converter | 网格切片时额外输出 `.dwg`（AutoCAD 原生格式） | 免费版 <https://www.opendesign.com/guestfiles/oda_file_converter>，默认装到 `C:\Program Files\ODA\...` 即可被自动发现；没装只出 `.dxf`，属预期降级 |
| 断网 / 内网 | 只核对范围时 | 底图切「空白底图」，不加载任何网络瓦片 |

> 底图瓦片来自第三方服务，**不可**作为离线下载源；批量下载违反各服务条款。
> 有离线底图需求请用 `gismap-helper` 技能处理**已下载**的影像，或自建瓦片服务。

---

## 9. 换机常见问题

### 9.1 `--inspect` 报 `UnicodeEncodeError: 'gbk' codec can't encode ...`

Windows 控制台默认 GBK，而报告里有 `km²`。设 `$env:PYTHONIOENCODING='utf-8'`（当前会话）即可。
如果长期需要，可在「系统属性 → 环境变量」里加用户变量 `PYTHONIOENCODING=utf-8`。
代码侧尚未统一 stdout 编码，属已知问题（见技能 `references/dev-rules.md` §11）。

### 9.2 PowerShell 拒绝执行 `Activate.ps1`

见 §3 的三种方式；优先 `-Scope Process -ExecutionPolicy Bypass`。

### 9.3 `python` 指向了别的版本（本机就有 3.6）

用 `py -0p` 看装了哪些版本，建环境时写死 `py -3.13 -m venv .venv`；
运行时始终用 `.\.venv\Scripts\python.exe`，不依赖 PATH。

### 9.4 pip 卡住不动 / 缓存损坏

```powershell
.\.venv\Scripts\python.exe -m pip cache purge
.\.venv\Scripts\python.exe -m pip install -e . --no-cache-dir
```

再不行就删掉 `.venv` 从头来；**不要** `pip install --upgrade pip`。

### 9.5 裁剪切片时报 `PROJ: proj_create_from_database ... DATABASE.LAYOUT.VERSION.MINOR = 5`

目标机装过 OSGeo4W / QGIS，系统 `PROJ_LIB` 指向它的旧 `proj.db`（rasterio 要 ≥ 6）。
本项目已自动隔离（`core/gdal_env.py` 在导入 rasterio 前把 `PROJ_DATA` / `GDAL_DATA` 指回 wheel 自带数据，
并清掉 `GDAL_DRIVER_PATH`）。只有你自己写脚本直接用 rasterio 时才需要先
`import gismapshelper.core.crop`，或手工设 `PROJ_DATA` 指向
`.venv\Lib\site-packages\rasterio\proj_data`。

### 9.6 启动报 `Could not find the Qt platform plugin "windows"`

PySide6 的 wheel 与 Python 位数/版本不匹配（或与 conda 的 Qt 混装）。处理：
删掉 `.venv`，用同一个 `python.exe` 重建，`pip install -e .`；不要混用 conda 环境。

### 9.7 地图一片灰、没有瓦片

- 确认能访问底图服务（Esri 在部分网络下需要代理）；
- 天地图必须先填 tk；
- 切「空白底图」验证界面本身；
- 瓦片失败 4 张后顶部会有提示，换底图或稍等自动重试。

### 9.8 缺 `VCRUNTIME140.dll`

装 **Microsoft Visual C++ 2015–2022 Redistributable (x64)**；`numpy` / `rasterio` 的 wheel 依赖它。

### 9.9 中文目录 / TEMP 指向工作区

`TEMP` 若被指到项目目录，pip 会留下 `pip-build-env-*` 等目录（已在 `.gitignore` 里）。
建议保持系统默认 `%LOCALAPPDATA%\Temp`。

---

## 10. 迁移检查表

```
[ ] py -0p 能看到 Python 3.13 x64（或 3.10–3.13 的 64 位版本）
[ ] 解压后 pyproject.toml、src\gismapshelper、docs\deploy.md 都在
[ ] .venv 是本机新建的（不是从旧机拷来的）
[ ] pip install -e . 成功，pip list 里有 gismapshelper 0.3.0
[ ] python -m gismapshelper --version 输出 0.3.0
[ ] PYTHONIOENCODING=utf-8 后 --inspect 样图输出完整（含 km²）
[ ] GUI 启动，样图能载入并出图（底图切过 Esri 与空白各一次）
[ ] unittest discover：Ran 111 tests / OK
[ ] 可选：天地图 tk、ODA File Converter
```

---

## 11. 打包成 exe（可选，本次未验证）

项目里目前没有 `.spec`，也没在目标机上验证过这条路径；以下是按现有代码整理的步骤，
**跑之前先接受「可能要调」**：

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
# 需要一个入口脚本，例如 build_tools\run.py：
#   import sys
#   from gismapshelper.app import main
#   sys.exit(main())
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --name GisMapsHelper `
  --paths src `
  --collect-data rasterio `
  --add-data "src\gismapshelper;gismapshelper" `
  --exclude-module PySide6.QtWebEngineCore `
  --exclude-module PySide6.QtWebEngineWidgets `
  --exclude-module PySide6.QtQuick `
  --exclude-module PySide6.Qt3DCore `
  build_tools\run.py
```

要点（详细规则见 `.dsh\skills\gismap-helper\references\dev-rules.md` §10）：

- 地图是 QPainter 实现，**没有** QtWebEngine，别为了地图把 WebEngine 加回来（多 200 MB）；
- rasterio 的 GDAL 插件与 `proj_data` / `gdal_data` 必须作为数据目录打进去，否则裁剪时报 PROJ 版本错；
- ezdxf 首次导入会扫字体、建缓存，打包版要靠 `XDG_CACHE_HOME`（`cad.py` 已指到 temp）；
- 打包后**双击实测**：载入样图 → 切 2×2 → 导出 GeoJSON → 打开 JPEG 压缩的 TIF（确认解码 DLL 在包里）。
