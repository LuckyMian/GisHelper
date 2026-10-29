"""应用入口。

默认启动 GUI；``--env`` 在终端打印运行环境，``--inspect`` 只读方式打印一个
GeoTIFF 的基本信息与范围（不启动界面），``--version`` 打印版本号。
打包成 exe 时也以这里的 ``main`` 作为入口。
"""

from __future__ import annotations

import platform
import sys
import unicodedata

from . import __version__


def environment_rows() -> list[tuple[str, str]]:
    """返回 (名称, 值) 形式的环境信息，供终端与首页共用。"""
    in_venv = sys.prefix != sys.base_prefix
    frozen = getattr(sys, "frozen", False)
    return [
        ("版本", f"v{__version__}"),
        ("Python", f"{platform.python_version()} ({platform.architecture()[0]})"),
        ("解释器", sys.executable),
        ("虚拟环境", "是" if in_venv else "否"),
        ("平台", f"{platform.system()} {platform.release()}"),
        ("运行方式", "已打包 exe" if frozen else "源码运行"),
    ]


def _display_width(text: str) -> int:
    """按终端显示宽度计数：中日韩全角字符占 2 列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1 for ch in text)


def describe_environment() -> str:
    """纯文本版环境信息，中文标签也能对齐。"""
    rows = environment_rows()
    width = max(_display_width(name) for name, _ in rows) + 2
    return "\n".join(
        f"{name}{' ' * (width - _display_width(name))}{value}" for name, value in rows
    )


def _inspect_file(filename: str) -> int:
    """``--inspect``：只读文件头，打印 GeoTIFF 的基本信息与范围。"""
    from .core.raster import RasterInfoError, describe, read_raster_info

    try:
        info = read_raster_info(filename)
    except (RasterInfoError, OSError) as exc:
        print(f"读取失败：{exc}", file=sys.stderr)
        return 1
    print(describe(info))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    first = args[0] if args else ""

    if first in {"-V", "--version"}:
        print(__version__)
        return 0
    if first in {"-e", "--env"}:
        print(describe_environment())
        return 0
    if first in {"-i", "--inspect"}:
        if len(args) < 2:
            print("用法: gismaps-helper --inspect <文件.tif>", file=sys.stderr)
            return 2
        return _inspect_file(args[1])
    if first in {"-h", "--help"}:
        print(
            "用法: gismaps-helper [--version | --env | --inspect <文件.tif> | --help]\n"
            "不带参数则启动图形界面。"
        )
        return 0

    try:
        from .gui.app import run_gui
    except ImportError as exc:  # PySide6 未安装
        print(f"无法启动图形界面：{exc}\n请先安装依赖：pip install -r requirements.txt", file=sys.stderr)
        return 1

    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
