#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gismap.py 的端到端自检。

用一张「像素颜色=归一化坐标」的合成图作为真值：
合成图 (i, j) 的颜色 R = i/(W-1)*255, G = j/(H-1)*255。
因此任意经纬度都能反推出应该出现的颜色，从而严格验证切片的地理对齐。

运行： <内置 python> selftest.py
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLI = HERE / "gismap.py"

sys.dont_write_bytecode = True  # 不在技能目录里留下 __pycache__
sys.path.insert(0, str(HERE))
import gismap  # noqa: E402  (同目录，纯工具函数)

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  [PASS] {name}{(' — ' + detail) if detail else ''}")
    else:
        print(f"  [FAIL] {name}{(' — ' + detail) if detail else ''}")
        FAILS.append(name)


def run(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(CLI), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
    return proc


def make_fixture(path: Path, width: int, height: int, bounds) -> None:
    from PIL import Image

    img = Image.new("RGB", (width, height))
    px = img.load()
    for j in range(height):
        g = round(j / (height - 1) * 255)
        for i in range(width):
            px[i, j] = (round(i / (width - 1) * 255), g, 0)
    img.save(path, quality=96, subsampling=0)

    west, south, east, north = bounds
    a = (east - west) / width
    e_ = -(north - south) / height
    c = west + a / 2.0
    f = north + e_ / 2.0
    path.with_suffix(".tfw").write_text(
        f"{a:.12f}\n0.0\n0.0\n{e_:.12f}\n{c:.12f}\n{f:.12f}\n", encoding="ascii"
    )


def expected_rgb(lon: float, lat: float, bounds, size) -> tuple[int, int]:
    west, south, east, north = bounds
    W, H = size
    col = (lon - west) / (east - west) * (W - 1)
    row = (north - lat) / (north - south) * (H - 1)
    return round(col / (W - 1) * 255), round(row / (H - 1) * 255)


def tile_pixel_lonlat(z: int, x: int, y: int, u: float, v: float, ts: int = 256):
    n = 1 << z
    gx = x * ts + u
    gy = y * ts + v
    lon = gx / (ts * n) * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * gy / (ts * n)))))
    return lon, lat


def tile_geometry_mae(tile_path: Path, bounds, size, ts: int = 256):
    """独立几何校验：由瓦片像素反算经纬度，与合成图理论颜色对比。

    只采用落在源图范围内的采样点，因此边缘补白瓦片会被自动跳过。
    """
    from PIL import Image

    parts = tile_path.parts
    z, x = int(parts[-3]), int(parts[-2])
    y = int(Path(parts[-1]).stem)
    w, s, e, n = bounds
    diffs: list[float] = []
    with Image.open(tile_path) as im:
        rgb = im.convert("RGB")
        for gu in range(1, 6):
            for gv in range(1, 6):
                u, v = int(ts * gu / 6), int(ts * gv / 6)
                lon, lat = tile_pixel_lonlat(z, x, y, u + 0.5, v + 0.5, ts)
                if not (w < lon < e and s < lat < n):
                    continue
                er, eg = expected_rgb(lon, lat, bounds, size)
                px = rgb.getpixel((u, v))
                diffs.append(abs(px[0] - er))
                diffs.append(abs(px[1] - eg))
    if len(diffs) < 6:
        return None
    return sum(diffs) / len(diffs)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="gismap-selftest-"))
    try:
        return _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp: Path) -> int:
    # ---------------------------------------------------------------- 夹具
    W, H = 1024, 768
    # 北京附近约 0.02° 见方的一块
    bounds = (116.300, 39.900, 116.340, 39.930)
    src = tmp / "google_sat.jpg"
    make_fixture(src, W, H, bounds)
    print(f"夹具：{src}  {W}x{H}  bounds={bounds}")

    # ------------------------------------------------------------ 1. inspect
    print("\n[1] inspect 自动识别")
    proc = run("inspect", str(src), "--json")
    check("inspect 退出码为 0", proc.returncode == 0)
    rep = json.loads(proc.stdout) if proc.returncode == 0 else {}
    check("识别为 image", rep.get("kind") == "image")
    check("坐标系 EPSG:4326", rep.get("crs") == "EPSG:4326", str(rep.get("crs")))
    b = rep.get("bounds") or {}
    check(
        "范围与 .tfw 一致",
        b and abs(b["west"] - bounds[0]) < 1e-7 and abs(b["north"] - bounds[3]) < 1e-7,
        f"{b}",
    )
    native = rep.get("native_zoom", 0)
    # 0.04° 经度 / 1024 px ≈ 4.3 m/px ≈ z15.2
    check("原生层级落在 z15 附近", 14.5 < native < 16.0, f"z{native}")
    check("范围来源为世界文件", str(rep.get("bounds_source", "")).startswith("世界文件"))
    if proc.returncode != 0:
        print("\n自检无法继续：inspect 失败。")
        return 1

    # ----------------------------------------------------------------- 2. tile
    print("\n[2] tile 生成 XYZ 瓦片")
    zmax = max(1, int(math.floor(native)))
    out = tmp / "xyz"
    proc = run("tile", str(src), "-o", str(out), "--zooms", f"{zmax-1}-{zmax}",
               "--format", "png", "--workers", "2")
    check("tile 退出码为 0", proc.returncode == 0)
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    check("manifest 记录两层", len(man["zooms"]) == 2, str([z["z"] for z in man["zooms"]]))
    check("瓦片数与计划一致", man["tiles"] == sum(z["tiles"] for z in man["zooms"]),
          f"{man['tiles']}")
    check("生成了预览页", (out / "preview.html").exists())
    html = (out / "preview.html").read_text(encoding="utf-8")
    check("预览页带瓦片 URL 与扩展名", 'tiles/{z}/{x}/{y}.png' in html)
    check("预览页无未替换占位", "{{CONF}}" not in html and "{{TITLE}}" not in html)

    # 几何校验：由瓦片像素反算经纬度，与理论颜色比对
    tiles = sorted((out / "tiles").rglob("*.png"))
    check("落盘瓦片数等于 manifest", len(tiles) == man["tiles"], f"{len(tiles)}")
    worst = 0.0
    checked = 0
    for p in tiles:
        m = tile_geometry_mae(p, bounds, (W, H))
        if m is None:
            continue
        checked += 1
        worst = max(worst, m)
        if checked >= 12:
            break
    check("瓦片像素反算经纬度与理论值吻合（MAE<8）", checked >= 2 and worst < 8,
          f"样本={checked} worst={worst:.2f}")

    # ----------------------------------------------------------------- 3. verify
    print("\n[3] verify 校验")
    proc = run("verify", str(out), "--source", str(src), "--json")
    check("verify 退出码为 0", proc.returncode == 0, proc.stdout.strip()[:200])
    vr = json.loads(proc.stdout) if proc.returncode == 0 else {}
    check("verify 无 problem", not vr.get("problems"), str(vr.get("problems")))
    check("中心抽查差异很小", float(vr.get("center_tile_mae", 99)) < 14,
          f"mae={vr.get('center_tile_mae')}")

    # --------------------------------------------------------------- 4. mbtiles
    print("\n[4] MBTiles 输出与 TMS 输出")
    out2 = tmp / "mb"
    proc = run("tile", str(src), "-o", str(out2), "--zooms", str(zmax),
               "--scheme", "mbtiles,tms", "--format", "jpg", "--quality", "92",
               "--no-preview")
    check("mbtiles+tms 退出码为 0", proc.returncode == 0)
    mb = out2 / "google_sat.mbtiles"
    check("生成 .mbtiles", mb.exists())
    if mb.exists():
        con = sqlite3.connect(mb)
        cnt = con.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        meta = dict(con.execute("SELECT name, value FROM metadata").fetchall())
        con.close()
        check("mbtiles 瓦片数正确", cnt == man["zooms"][-1]["tiles"], f"{cnt}")
        check("mbtiles 带 bounds 元数据", meta.get("bounds", "").startswith("116.3"),
              meta.get("bounds", ""))

    tms_dir = out2 / "tiles_tms" / str(zmax)
    flipped_ok = True
    sample_z = out2 / "tiles_tms" / str(zmax)
    for xdir in sample_z.iterdir():
        for f in xdir.iterdir():
            row = int(f.stem)
            if row < 0 or row >= (1 << zmax):
                flipped_ok = False
    check("TMS 行号在合法范围", flipped_ok)

    proc = run("verify", str(out2), "--source", str(src), "--json")
    check("TMS 输出可被 verify 识别", proc.returncode == 0, proc.stdout[-200:])
    if proc.returncode == 0:
        vr2 = json.loads(proc.stdout)
        check("verify 识别出 tms 方案", vr2.get("scheme") == "tms", str(vr2.get("scheme")))
        check("TMS 中心抽查差异很小", float(vr2.get("center_tile_mae", 99)) < 14,
              f"mae={vr2.get('center_tile_mae')}")

    # ------------------------------------------------------------- 5. 瓦片目录回流
    print("\n[5] 以瓦片目录为输入再切片")
    out3 = tmp / "retile"
    proc = run("tile", str(out / "tiles"), "-o", str(out3),
               "--zooms", str(zmax), "--no-preview")
    check("tiledir 输入退出码为 0", proc.returncode == 0, proc.stderr.strip()[:300])
    if proc.returncode == 0:
        same = 0
        total = 0
        for p in sorted((out / "tiles" / str(zmax)).rglob("*.png"))[:8]:
            rel = p.relative_to(out / "tiles")
            q = out3 / "tiles" / rel
            total += 1
            if q.exists():
                from PIL import Image, ImageChops
                with Image.open(p) as a, Image.open(q) as c:
                    a, c = a.convert("RGB"), c.convert("RGB")
                    if a.size == c.size and ImageChops.difference(a, c).getbbox() is None:
                        same += 1
        check("同层级回流像素完全一致", same == total and total > 0, f"{same}/{total}")

    # ------------------------------------------------------------- 6. inspect 目录
    print("\n[6] inspect 瓦片目录")
    proc = run("inspect", str(out / "tiles"), "--json")
    check("目录 inspect 成功", proc.returncode == 0)
    if proc.returncode == 0:
        r2 = json.loads(proc.stdout)
        check("识别为 tiledir", r2.get("kind") == "tiledir")
        check("层级列表包含 zmax", zmax in r2.get("zooms_available", []))
        bb = r2["bounds"]
        # 瓦片目录覆盖的是整块瓦片，允许外扩，但必须包含源图范围且外扩不超过一块瓦片
        one_tile = 360.0 / (1 << zmax)
        check("目录范围包含源图范围",
              bb["west"] <= bounds[0] + 1e-9 and bb["east"] >= bounds[2] - 1e-9
              and bb["south"] <= bounds[1] + 1e-9 and bb["north"] >= bounds[3] - 1e-9,
              str(bb))
        check("目录范围外扩不超过一块瓦片",
              bounds[0] - bb["west"] < one_tile and bb["east"] - bounds[2] < one_tile,
              f"扩 {(bounds[0]-bb['west'])/one_tile:.2f} 块")

    # ------------------------------------------------------------ 7. export 分幅
    print("\n[7] export 分幅 2x2")
    big = tmp / "big.jpg"
    proc = run("export", str(src), "-o", str(big), "--split", "2", "--quality", "92")
    check("export 退出码为 0", proc.returncode == 0, proc.stderr.strip()[:300])
    parts = sorted(tmp.glob("big_r*c*.jpg"))
    check("生成 4 个分幅", len(parts) == 4, f"{len(parts)}")
    check("分幅带世界文件", all(p.with_suffix(".jgw").exists() for p in parts))
    check("分幅带投影文件", all(p.with_suffix(".prj").exists() for p in parts))
    idx = tmp / "big_index.csv"
    check("生成分幅索引", idx.exists())

    # 分幅范围应正好铺满原范围：每行经度合计=原宽，每列纬度合计=原高
    from PIL import Image

    row_spans: dict[int, float] = {}
    col_heights: dict[int, float] = {}
    row_north: dict[int, float] = {}
    for p in parts:
        m = re.match(r"big_r(\d+)c(\d+)", p.stem)
        r, c = int(m.group(1)), int(m.group(2))
        vals = [float(v) for v in p.with_suffix(".jgw").read_text().split()]
        A, E, C, F = vals[0], vals[3], vals[4], vals[5]
        with Image.open(p) as im:
            wpx, hpx = im.size
        row_spans[r] = row_spans.get(r, 0.0) + A * wpx
        col_heights[c] = col_heights.get(c, 0.0) - E * hpx
        row_north[r] = max(row_north.get(r, -1e9), F - E / 2.0)

    target_w = bounds[2] - bounds[0]
    target_h = bounds[3] - bounds[1]
    check("每行经度合计 ≈ 原范围宽",
          all(abs(v - target_w) < 5e-4 for v in row_spans.values()),
          f"{[round(v, 6) for v in row_spans.values()]}")
    check("每列纬度合计 ≈ 原范围高",
          all(abs(v - target_h) < 5e-4 for v in col_heights.values()),
          f"{[round(v, 6) for v in col_heights.values()]}")
    check("首行北边界 ≈ 原范围北边界", abs(row_north[min(row_north)] - bounds[3]) < 5e-5,
          f"{row_north[min(row_north)]:.6f}")

    # ---------------------------------------------------------- 8. 3857 与 GCJ02
    print("\n[8] 3857 输入 / GCJ-02 换算")
    from PIL import Image

    merc_img = tmp / "merc.tif"
    img = Image.new("RGB", (512, 512), (10, 200, 30))
    x0, y0 = gismap.lonlat_to_merc(116.30, 39.93)
    x1, y1 = gismap.lonlat_to_merc(116.34, 39.90)
    # 用 .tfw 表达米制范围
    img.save(merc_img, "TIFF")
    px = (x1 - x0) / 512
    merc_img.with_suffix(".tfw").write_text(
        f"{px:.6f}\n0.0\n0.0\n{-px:.6f}\n{x0 + px/2:.6f}\n{y0 - px/2:.6f}\n", encoding="ascii"
    )
    proc = run("inspect", str(merc_img), "--json")
    check("米制世界文件识别为 EPSG:3857",
          proc.returncode == 0 and json.loads(proc.stdout)["crs"] == "EPSG:3857",
          proc.stdout[:120])

    glon, glat = gismap.wgs84_to_gcj02(116.32, 39.915)
    blon, blat = gismap.gcj02_to_wgs84(glon, glat)
    check("GCJ-02 往返误差 < 1e-7", abs(blon - 116.32) < 1e-7 and abs(blat - 39.915) < 1e-7,
          f"{blon-116.32:.2e}, {blat-39.915:.2e}")
    check("GCJ-02 偏移量在合理区间（百米级）",
          0.002 < abs(glon - 116.32) < 0.01, f"dLon={glon-116.32:.5f}")

    # 用 --bounds-datum gcj02 反算后应回到原范围
    proc = run("inspect", str(src), "--json",
               "--bounds", f"{glon:.8f},{glat:.8f},{glon+0.04:.8f},{glat+0.03:.8f}",
               "--bounds-datum", "gcj02")
    if proc.returncode == 0:
        rb = json.loads(proc.stdout)["bounds"]
        # 传入的是 GCJ-02 坐标，反算后应回到 WGS-84 的 116.32 / 39.915
        check("gcj02 反算后回到 WGS-84",
              abs(rb["west"] - 116.32) < 2e-4 and abs(rb["south"] - 39.915) < 2e-4,
              f"{rb['west']:.6f},{rb['south']:.6f}")

    # ------------------------------------------------------------------ 汇总
    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部自检通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
