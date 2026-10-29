"""TIF 范围页：读取 GeoTIFF 范围、设置项目原点、生成切片方形并管理图层。

只解析文件头，不渲染影像。页面状态（原点、切片方案、图层可见性）都在本模块，
地图控件只负责把 :class:`MapOverlay` 画出来；几何一律存 WGS-84。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, QStandardPaths, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core import cad, crop, frames, geo, geojson, grid, grid_export
from ...core.basemaps import BASEMAPS, DEFAULT_BASEMAP_ID, Basemap, get_basemap
from ...core.raster import RasterInfo, RasterInfoError, describe, info_rows, read_raster_info
from ...core.tiff import TiffError
from .. import theme
from ..widgets.combo import DownwardComboBox
from ..widgets.tilemap import MapOverlay, TileMapWidget
from .base import BasePage

_TIF_SUFFIXES = (".tif", ".tiff", ".gtif")
_ORG = "GisMapsHelper"
_APP = "GisMapsHelper"

COLOR_TIF = "#2563eb"
COLOR_ORIGIN = "#dc2626"
COLOR_SLICES = ("#ea580c", "#7c3aed", "#0d9488", "#db2777", "#0891b2", "#4338ca")


@dataclass
class LayerEntry:
    """图层面板里的一个条目；几何一律存 WGS-84。"""

    key: str
    kind: str                                   # "tif" / "origin" / "slice"
    title: str
    color: str
    visible: bool = True
    detail: str = ""
    bounds: tuple[float, float, float, float] | None = None
    point: tuple[float, float] | None = None
    spec: frames.SliceSpec | None = None


class TifMapPage(BasePage):
    title = "TIF 范围"
    subtitle = "读取 GeoTIFF 范围、设置项目原点、生成切片方形并按图层管理（只解析文件头，不渲染像素）"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info: RasterInfo | None = None
        self._layers: list[LayerEntry] = []
        self._basemap: Basemap = get_basemap(DEFAULT_BASEMAP_ID)
        self._settings = QSettings(_ORG, _APP)
        self._base_hint = ""
        self._seq = 0
        self.setAcceptDrops(True)

        self._build()
        self._populate_sizes("distance")
        self._restore_basemap()
        self._fill_info(None)
        self._rebuild_layer_list()
        self._update_origin_label()
        self._update_slice_hint()

    @property
    def current_info(self) -> RasterInfo | None:
        return self._info

    def layers(self) -> list[LayerEntry]:
        return list(self._layers)

    # ------------------------------------------------------------------ 界面
    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 12, 0, 0)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)
        splitter.addWidget(self._build_panel())
        splitter.addWidget(self._build_map_area())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([370, 640, 330])
        outer.addWidget(splitter)

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(350)
        panel.setMaximumWidth(700)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(10)

        layout.addLayout(self._build_file_row())
        layout.addWidget(self._build_basemap_box())

        self._tabs = QTabWidget()
        self._tabs.setObjectName("PanelTabs")
        self._tabs.addTab(self._build_layers_tab(), "图层")
        self._info_tab_index = self._tabs.addTab(self._build_info_tab(), "影像信息")
        layout.addWidget(self._tabs, 1)
        return panel

    def _build_file_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._path_edit = QLineEdit()
        self._path_edit.setObjectName("PathEdit")
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText("尚未选择 GeoTIFF 文件（也可直接拖入窗口）")
        self._browse_button = QPushButton("浏览…")
        self._browse_button.setObjectName("Primary")
        self._browse_button.clicked.connect(self._choose_file)
        row.addWidget(self._path_edit, 1)
        row.addWidget(self._browse_button)
        return row

    def _build_basemap_box(self) -> QGroupBox:
        box = QGroupBox("底图")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 20, 14, 12)
        layout.setSpacing(8)

        self._combo = DownwardComboBox()
        previous_kind = ""
        for basemap in BASEMAPS:
            if previous_kind and basemap.kind != previous_kind:
                self._combo.insertSeparator(self._combo.count())
            self._combo.addItem(self._combo_label(basemap), basemap.id)
            previous_kind = basemap.kind
        self._combo.currentIndexChanged.connect(self._on_basemap_changed)
        help_button = QPushButton("说明")
        help_button.setToolTip("查看免费 / 开源底图清单与使用条款")
        help_button.clicked.connect(self._show_basemap_help)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._combo, 1)
        row.addWidget(help_button)
        layout.addLayout(row)

        self._key_row = QWidget()
        key_layout = QHBoxLayout(self._key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(8)
        key_layout.addWidget(QLabel("tk"))
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("天地图服务密钥（免费申请）")
        self._key_edit.editingFinished.connect(self._on_key_changed)
        key_layout.addWidget(self._key_edit, 1)
        self._key_row.setVisible(False)
        layout.addWidget(self._key_row)

        self._datum_hint = QLabel()
        self._datum_hint.setObjectName("SectionHint")
        self._datum_hint.setWordWrap(True)
        self._datum_hint.setVisible(False)
        layout.addWidget(self._datum_hint)
        return box

    def _build_layers_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 14, 12, 10)
        layout.setSpacing(10)
        layout.addWidget(self._build_layer_box(), 1)
        return page

    def _build_slice_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 14, 12, 10)
        layout.setSpacing(10)
        layout.addWidget(self._build_origin_box())
        layout.addWidget(self._build_slice_box())
        tip = QLabel(
            "方形以项目原点为中心；原点移动或换 TIF 后，已有方形会自动重算，"
            "不需要删掉重建。"
        )
        tip.setObjectName("SectionHint")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addStretch(1)
        return page

    def _build_origin_box(self) -> QGroupBox:
        box = QGroupBox("项目原点（本地 0,0,0）")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 18, 12, 10)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._pick_button = QPushButton("在地图上点选")
        self._pick_button.setCheckable(True)
        self._pick_button.toggled.connect(self._on_pick_toggled)
        self._origin_label = QLabel("未设置")
        self._origin_label.setObjectName("OriginValue")
        self._origin_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self._pick_button)
        row.addWidget(self._origin_label, 1)
        layout.addLayout(row)

        tip = QLabel("设好原点后，创建的方形范围都以它为几何中心；导出三维软件时它就是 (0,0,0)。")
        tip.setObjectName("SectionHint")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        return box

    def _build_slice_box(self) -> QGroupBox:
        box = QGroupBox("切片方形范围")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 18, 12, 10)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("方案"))
        self._scheme_combo = DownwardComboBox()
        self._scheme_combo.addItem("按距离（米）", "distance")
        self._scheme_combo.addItem("按 TIF 像素", "pixels")
        self._scheme_combo.currentIndexChanged.connect(self._on_scheme_changed)
        row.addWidget(self._scheme_combo, 1)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(QLabel("边长"))
        self._size_combo = DownwardComboBox()
        self._size_combo.setEditable(True)
        self._size_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._size_combo.setMinimumWidth(110)
        self._size_combo.currentTextChanged.connect(lambda _text: self._update_slice_hint())
        self._size_combo.lineEdit().returnPressed.connect(self._create_slice)
        row2.addWidget(self._size_combo, 1)
        self._create_button = QPushButton("创建")
        self._create_button.setObjectName("Primary")
        self._create_button.clicked.connect(self._create_slice)
        row2.addWidget(self._create_button)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(8)
        row3.addWidget(QLabel("参考点"))
        self._anchor_combo = DownwardComboBox()
        for key in frames.ANCHORS:
            self._anchor_combo.addItem(frames.ANCHOR_LABELS[key], key)
        self._anchor_combo.setCurrentIndex(
            self._anchor_combo.findData(frames.DEFAULT_ANCHOR)
        )
        self._anchor_combo.setToolTip("项目原点落在矩形的哪个位置；默认中心")
        self._anchor_combo.currentIndexChanged.connect(self._on_anchor_changed)
        row3.addWidget(self._anchor_combo, 1)
        layout.addLayout(row3)

        self._slice_hint = QLabel()
        self._slice_hint.setObjectName("SectionHint")
        self._slice_hint.setWordWrap(True)
        layout.addWidget(self._slice_hint)
        return box

    def _build_layer_box(self) -> QGroupBox:
        box = QGroupBox("图层")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 18, 10, 8)
        layout.setSpacing(8)

        self._layer_list = QListWidget()
        self._layer_list.setObjectName("LayerList")
        self._layer_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._layer_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._layer_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._layer_list.itemChanged.connect(self._on_layer_item_changed)
        self._layer_list.itemSelectionChanged.connect(self._on_layer_selection)
        self._layer_list.itemDoubleClicked.connect(self._fit_layer_by_item)
        layout.addWidget(self._layer_list, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._fit_layer_button = QPushButton("定位")
        self._fit_layer_button.setToolTip("缩放到选中图层")
        self._fit_layer_button.clicked.connect(self._fit_selected_layer)
        self._delete_layer_button = QPushButton("删除")
        self._delete_layer_button.clicked.connect(self._delete_selected_layer)
        self._show_all_button = QPushButton("全部显示")
        self._show_all_button.clicked.connect(self._show_all_layers)
        self._fit_layer_button.setEnabled(False)
        self._delete_layer_button.setEnabled(False)
        row.addWidget(self._fit_layer_button)
        row.addWidget(self._delete_layer_button)
        row.addWidget(self._show_all_button)
        row.addStretch(1)
        layout.addLayout(row)

        self._copy_layers_button = QPushButton("复制图层清单（含原点）")
        self._copy_layers_button.setToolTip(
            "复制 JSON：原点经纬度、各矩形 WGS-84 范围与相对原点的米偏移"
        )
        self._copy_layers_button.clicked.connect(self._copy_layers)
        layout.addWidget(self._copy_layers_button)
        return box

    # ------------------------------------------------------------------ 右侧功能区
    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("FuncPanel")
        panel.setMinimumWidth(300)
        panel.setMaximumWidth(460)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(8)

        title = QLabel("功能")
        title.setObjectName("FuncTitle")
        layout.addWidget(title)

        self._func_menu = QListWidget()
        self._func_menu.setObjectName("FuncMenu")
        self._func_menu.setFixedHeight(4 * 38 + 10)
        self._func_menu.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for name in ("原点与切片", "切片 TIF", "裁剪 TIF", "导出 GeoJSON"):
            item = QListWidgetItem(name)
            item.setSizeHint(QSize(0, 38))
            self._func_menu.addItem(item)

        self._func_stack = QStackedWidget()
        self._func_stack.addWidget(self._build_slice_tab())
        self._func_stack.addWidget(self._build_grid_panel())
        self._func_stack.addWidget(self._build_crop_panel())
        self._func_stack.addWidget(self._build_export_panel())

        self._func_menu.currentRowChanged.connect(self._func_stack.setCurrentIndex)
        self._func_menu.setCurrentRow(0)

        layout.addWidget(self._func_menu)
        layout.addWidget(self._func_stack, 1)
        return panel

    def _build_grid_panel(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 14, 12, 10)
        layout.setSpacing(10)

        box = QGroupBox("按网格切片")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(12, 18, 12, 10)
        inner.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("网格"))
        self._grid_combo = DownwardComboBox()
        for n in grid.GRID_PRESETS:
            self._grid_combo.addItem(f"{n} × {n}", n)
        self._grid_combo.setCurrentIndex(self._grid_combo.findData(3))
        self._grid_combo.currentIndexChanged.connect(lambda _index: self._update_grid_hint())
        row.addWidget(self._grid_combo, 1)
        inner.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(QLabel("CAD 原点"))
        self._grid_anchor_combo = DownwardComboBox()
        for key in frames.ANCHORS:
            self._grid_anchor_combo.addItem(frames.ANCHOR_LABELS[key], key)
        self._grid_anchor_combo.setCurrentIndex(
            self._grid_anchor_combo.findData(frames.DEFAULT_ANCHOR)
        )
        self._grid_anchor_combo.setToolTip("DXF / DWG 的 (0,0) 落在影像的哪个位置；默认中心")
        self._grid_anchor_combo.currentIndexChanged.connect(lambda _index: self._update_grid_hint())
        row2.addWidget(self._grid_anchor_combo, 1)
        inner.addLayout(row2)

        self._grid_hint = QLabel()
        self._grid_hint.setObjectName("SectionHint")
        self._grid_hint.setWordWrap(True)
        inner.addWidget(self._grid_hint)

        self._grid_reproject = QCheckBox("经纬度影像先重投影到 EPSG:3857")
        self._grid_reproject.setChecked(True)
        self._grid_reproject.setToolTip(
            "源影像为 EPSG:4326 时先转成 Web 墨卡托再切，输出的块是米制坐标"
        )
        self._grid_reproject.toggled.connect(lambda _checked: self._update_grid_hint())
        inner.addWidget(self._grid_reproject)

        self._grid_warn = QLabel()
        self._grid_warn.setObjectName("WarnTip")
        self._grid_warn.setWordWrap(True)
        self._grid_warn.setVisible(False)
        inner.addWidget(self._grid_warn)

        self._grid_button = QPushButton("开始切片…")
        self._grid_button.setObjectName("Primary")
        self._grid_button.setEnabled(False)
        self._grid_button.clicked.connect(self._run_grid_export)
        inner.addWidget(self._grid_button)
        layout.addWidget(box)

        self._grid_result = QLabel()
        self._grid_result.setObjectName("SectionHint")
        self._grid_result.setWordWrap(True)
        layout.addWidget(self._grid_result)

        tip = QLabel(
            "输出到子目录：N×N 个 GeoTIFF + 1 个 GeoJSON（每块一个矩形）"
            "+ 1 个 DXF（闭合矩形、不填充）。\n"
            "装了免费的 ODA File Converter 时，会再自动转一份 .dwg。"
        )
        tip.setObjectName("SectionHint")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addStretch(1)
        return page

    def _build_crop_panel(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 14, 12, 10)
        layout.setSpacing(10)

        box = QGroupBox("按选中矩形裁剪")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(12, 18, 12, 10)
        inner.setSpacing(8)

        self._crop_target_label = QLabel("未选中图层")
        self._crop_target_label.setObjectName("OriginValue")
        self._crop_target_label.setWordWrap(True)
        inner.addWidget(self._crop_target_label)

        self._crop_bounds_label = QLabel("")
        self._crop_bounds_label.setObjectName("SectionHint")
        self._crop_bounds_label.setWordWrap(True)
        inner.addWidget(self._crop_bounds_label)

        self._crop_button = QPushButton("裁剪 TIF…")
        self._crop_button.setObjectName("Primary")
        self._crop_button.setToolTip(
            "按选中的矩形图层裁剪当前 TIF，输出保留坐标系与地理参考的新 GeoTIFF"
        )
        self._crop_button.setEnabled(False)
        self._crop_button.clicked.connect(self._crop_selected_layer)
        inner.addWidget(self._crop_button)

        self._crop_reproject = QCheckBox("经纬度影像先重投影到 EPSG:3857")
        self._crop_reproject.setChecked(True)
        self._crop_reproject.setToolTip(
            "源影像为 EPSG:4326 时，先转成 Web 墨卡托再裁：输出是米制方像素块，"
            "10 km 地面方块在文件里也是正方形（代价是重采样一次）"
        )
        inner.addWidget(self._crop_reproject)
        layout.addWidget(box)

        tip = QLabel(
            "窗口按源影像的像素边界向外取整；超出影像会自动裁到交集。\n"
            "输出保留坐标系、分辨率、波段、nodata 与压缩方式。"
        )
        tip.setObjectName("SectionHint")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addStretch(1)
        return page

    def _build_export_panel(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 14, 12, 10)
        layout.setSpacing(10)

        box = QGroupBox("图层 → GeoJSON")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(12, 18, 12, 10)
        inner.setSpacing(8)

        self._export_button = QPushButton("导出全部图层…")
        self._export_button.setObjectName("Primary")
        self._export_button.setToolTip(
            "把图层列表里的所有图层写进同一个 GeoJSON：矩形为面，原点为点"
        )
        self._export_button.setEnabled(False)
        self._export_button.clicked.connect(self._export_all_geojson)
        inner.addWidget(self._export_button)
        layout.addWidget(box)

        tip = QLabel(
            "一个文件包含全部图层，顺序与图层列表一致；矩形为 Polygon，原点为 Point。\n"
            "底图是 GCJ-02 时会先问用哪种基准：WGS-84（GeoJSON 标准）或与底图对齐。"
        )
        tip.setObjectName("SectionHint")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addStretch(1)
        return page

    def _build_info_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 14, 12, 10)
        layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setObjectName("InfoScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        holder = QWidget()
        self._form = QFormLayout(holder)
        self._form.setContentsMargins(2, 2, 8, 2)
        self._form.setHorizontalSpacing(14)
        self._form.setVerticalSpacing(7)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)

        self._warning_label = QLabel()
        self._warning_label.setObjectName("WarnTip")
        self._warning_label.setWordWrap(True)
        self._warning_label.setVisible(False)
        layout.addWidget(self._warning_label)

        row = QHBoxLayout()
        self._copy_button = QPushButton("复制信息")
        self._copy_button.setEnabled(False)
        self._copy_button.clicked.connect(self._copy_info)
        row.addWidget(self._copy_button)
        row.addStretch(1)
        layout.addLayout(row)
        return page

    def _build_map_area(self) -> QWidget:
        area = QWidget()
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        tip = QLabel("拖动平移 · 滚轮缩放 · 双击放大")
        tip.setObjectName("SectionHint")
        self._coord_label = QLabel("中心 104.0000, 35.0000 · z4")
        self._coord_label.setObjectName("SectionHint")
        self._fit_button = QPushButton("缩放到全部")
        self._fit_button.setEnabled(False)
        self._fit_button.clicked.connect(self._fit_to_all)
        bar.addWidget(tip)
        bar.addStretch(1)
        bar.addWidget(self._coord_label)
        bar.addWidget(self._fit_button)
        layout.addLayout(bar)

        self.map = TileMapWidget()
        self.map.cursor_moved.connect(self._on_cursor_moved)
        self.map.view_changed.connect(self._on_view_changed)
        self.map.origin_picked.connect(self._on_origin_picked)
        self.map.pick_cancelled.connect(self._on_pick_cancelled)
        layout.addWidget(self.map, 1)
        return area

    # ------------------------------------------------------------------ 底图
    @staticmethod
    def _combo_label(basemap: Basemap) -> str:
        suffix = ""
        if basemap.datum == "GCJ-02":
            suffix = "（GCJ-02）"
        elif basemap.requires_key:
            suffix = "（需 key）"
        return f"{basemap.name}{suffix}"

    def _restore_basemap(self) -> None:
        saved = str(self._settings.value("basemap/id", DEFAULT_BASEMAP_ID) or DEFAULT_BASEMAP_ID)
        index = self._combo.findData(saved)
        if index < 0:
            index = self._combo.findData(DEFAULT_BASEMAP_ID)
        self._combo.setCurrentIndex(index)
        self._on_basemap_changed()

    def _on_basemap_changed(self, _index: int | None = None) -> None:
        basemap = get_basemap(str(self._combo.currentData()))
        self._basemap = basemap
        self._key_row.setVisible(basemap.requires_key)
        if basemap.requires_key:
            saved_key = str(self._settings.value(f"basemap/key/{basemap.id}", "") or "")
            self._key_edit.setText(saved_key)
        else:
            self._key_edit.clear()

        self.map.set_basemap(basemap, self._key_edit.text().strip())
        self._update_datum_hint()
        self._settings.setValue("basemap/id", basemap.id)
        self._refresh_overlays()

    def _on_key_changed(self) -> None:
        token = self._key_edit.text().strip()
        self._settings.setValue(f"basemap/key/{self._basemap.id}", token)
        self.map.set_token(token)
        self._update_datum_hint()

    def _update_datum_hint(self) -> None:
        basemap = self._basemap
        text = ""
        if basemap.datum == "GCJ-02":
            text = "⚠ 该底图为 GCJ-02（火星坐标），原点与矩形已自动换算，避免约 100~600 m 偏移。"
        elif basemap.requires_key and not self._key_edit.text().strip():
            text = "该底图需要免费申请的 tk 密钥，填入后才会加载瓦片。"
        elif basemap.kind == "离线":
            text = "不加载网络瓦片，只显示网格与范围，适合内网 / 断网环境。"
        elif basemap.datum == "CGCS2000":
            text = "CGCS2000 与 WGS-84 差异在厘米级，可直接套合。"
        self._datum_hint.setText(text)
        self._datum_hint.setVisible(bool(text))

    # ------------------------------------------------------------------ 载入文件
    def _choose_file(self) -> None:
        start = str(self._settings.value("last_dir", str(Path.home())) or Path.home())
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "选择 GeoTIFF 文件",
            start,
            "GeoTIFF (*.tif *.tiff *.gtif);;所有文件 (*)",
        )
        if filename:
            self.load_path(filename)

    def load_path(self, path: str | Path) -> bool:
        """读取并显示一个 TIFF；返回是否成功。"""
        path = Path(path)
        try:
            info = read_raster_info(path)
        except (RasterInfoError, TiffError, OSError) as exc:
            QMessageBox.warning(self, "无法读取文件", f"{path.name}\n\n{exc}")
            return False

        self._info = info
        self._path_edit.setText(str(info.path))
        self._path_edit.setCursorPosition(0)   # 长路径从盘符开始显示，而不是尾部
        self._path_edit.setToolTip(str(info.path))
        self._settings.setValue("last_dir", str(info.path.parent))
        self._copy_button.setEnabled(True)
        self._fill_info(info)
        self._base_hint = self._hint_for(info)

        if info.lonlat is not None and info.map_ready:
            entry = self._find_layer("tif")
            if entry is None:
                entry = LayerEntry(key="tif", kind="tif", title="TIF 范围", color=COLOR_TIF)
                self._layers.insert(0, entry)
            entry.title = f"TIF 范围 · {info.path.name}"
            entry.bounds = info.lonlat.as_tuple()
            entry.detail = f"{info.width}×{info.height} px"
            self._rebuild_layer_list(select="tif")
            self._refresh_overlays()
            self.map.fit_bounds(self._display_bounds(entry.bounds))
        else:
            self._layers = [item for item in self._layers if item.kind != "tif"]
            self._rebuild_layer_list()
            self._refresh_overlays()

        self.map.set_hint(self._base_hint)
        self._update_slice_hint()
        self._status(f"已读取 {info.path.name}：{info.width}×{info.height} 像素")
        return True

    @staticmethod
    def _hint_for(info: RasterInfo) -> str:
        if info.bounds is None:
            return "未检测到地理范围：需要 GeoTIFF 标签或同名 .tfw 世界文件"
        if not info.map_ready:
            return f"{info.crs or '未知坐标系'} 不能直接叠加到 Web 墨卡托底图，请先重投影到 EPSG:3857"
        return ""

    # ------------------------------------------------------------------ 信息面板
    def _fill_info(self, info: RasterInfo | None) -> None:
        self._clear_form()
        if info is None:
            placeholder = QLabel(
                "选择一个 GeoTIFF 后，这里会列出文件大小、波段、坐标系、范围、"
                "分辨率与原生层级等信息。"
            )
            placeholder.setObjectName("InfoPlaceholder")
            placeholder.setWordWrap(True)
            self._form.addRow(placeholder)
            self._warning_label.setVisible(False)
            self._tabs.setTabText(self._info_tab_index, "影像信息")
            self._update_grid_hint()
            return

        for label, value in info_rows(info):
            key_label = QLabel(label)
            key_label.setObjectName("EnvKey")
            value_label = QLabel(value)
            value_label.setObjectName("EnvValue")
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._form.addRow(key_label, value_label)

        if info.warnings:
            self._warning_label.setText("⚠ " + "\n⚠ ".join(info.warnings))
            self._warning_label.setVisible(True)
            self._tabs.setTabText(self._info_tab_index, "影像信息 ⚠")
        else:
            self._warning_label.setVisible(False)
            self._tabs.setTabText(self._info_tab_index, "影像信息")

        self._update_grid_hint()

    def _clear_form(self) -> None:
        while self._form.count():
            item = self._form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _copy_info(self) -> None:
        if self._info is None:
            return
        QGuiApplication.clipboard().setText(describe(self._info))
        self._status("影像信息已复制到剪贴板")

    # ------------------------------------------------------------------ 项目原点
    def _origin_point(self) -> tuple[float, float] | None:
        for entry in self._layers:
            if entry.kind == "origin":
                return entry.point
        return None

    def set_origin(self, lon: float, lat: float) -> None:
        """设置项目原点（WGS-84），会更新图层面板并重画。"""
        entry = self._find_layer("origin")
        if entry is None:
            entry = LayerEntry(key="origin", kind="origin", title="项目原点", color=COLOR_ORIGIN)
            index = 1 if self._find_layer("tif") is not None else 0
            self._layers.insert(index, entry)
        entry.point = (float(lon), float(lat))
        self._update_origin_label()
        self._rebuild_layer_list(select="origin")
        self._refresh_overlays()
        self._update_slice_hint()

    def _update_origin_label(self) -> None:
        point = self._origin_point()
        if point is None:
            self._origin_label.setText("未设置")
        else:
            self._origin_label.setText(f"{point[0]:.6f}, {point[1]:.6f}")

    def _on_pick_toggled(self, checked: bool) -> None:
        if checked:
            self.map.start_origin_pick()
            self.map.set_hint("点击底图放置项目原点（Esc 取消）")
            self._status("请在地图上点击原点位置")
        else:
            self.map.cancel_origin_pick()
            self.map.set_hint(self._base_hint)

    def _set_pick_button(self, checked: bool) -> None:
        self._pick_button.blockSignals(True)
        self._pick_button.setChecked(checked)
        self._pick_button.blockSignals(False)

    def _on_origin_picked(self, lon: float, lat: float) -> None:
        self._set_pick_button(False)
        self.map.cancel_origin_pick()
        self.map.set_hint(self._base_hint)
        wlon, wlat = self._to_wgs84(lon, lat)
        self.set_origin(wlon, wlat)
        self._status(f"项目原点已设置：{wlon:.6f}, {wlat:.6f}")

    def _on_pick_cancelled(self) -> None:
        self._set_pick_button(False)
        self.map.set_hint(self._base_hint)
        self._status("已取消设置原点")

    # ------------------------------------------------------------------ 切片范围
    def _on_scheme_changed(self, _index: int | None = None) -> None:
        self._populate_sizes(str(self._scheme_combo.currentData() or "distance"))

    def _populate_sizes(self, kind: str) -> None:
        self._size_combo.blockSignals(True)
        self._size_combo.clear()
        if kind == "pixels":
            for value in frames.PIXEL_PRESETS:
                self._size_combo.addItem(f"{value} px", str(value))
            self._size_combo.setEditText(f"{frames.PIXEL_PRESETS[2]} px")
        else:
            for value in frames.DISTANCE_PRESETS_M:
                self._size_combo.addItem(frames.format_distance(value), str(value))
            self._size_combo.setEditText(frames.format_distance(frames.DISTANCE_PRESETS_M[0]))
        self._size_combo.blockSignals(False)
        self._update_slice_hint()

    def _on_anchor_changed(self, _index: int | None = None) -> None:
        self._update_slice_hint()

    def _parse_size(self, kind: str) -> float | None:
        text = self._size_combo.currentText().strip()
        if not text:
            return None
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text.replace(",", ""))
        if not match:
            return None
        try:
            value = float(match.group())
        except ValueError:
            return None
        if kind == "pixels":
            pixels = int(round(value))
            if pixels < 1 or pixels > frames.MAX_PIXELS:
                return None
            return float(pixels)
        if "km" in text.lower():
            value *= 1000.0
        if value < frames.MIN_EDGE_M or value > frames.MAX_EDGE_M:
            return None
        return value

    def _current_anchor(self) -> str:
        anchor = str(self._anchor_combo.currentData() or frames.DEFAULT_ANCHOR)
        return anchor if anchor in frames.ANCHORS else frames.DEFAULT_ANCHOR

    def _current_spec(self) -> frames.SliceSpec | None:
        kind = str(self._scheme_combo.currentData() or "distance")
        value = self._parse_size(kind)
        if value is None:
            return None
        return frames.SliceSpec(kind, value, self._current_anchor())

    def _update_slice_hint(self) -> None:
        spec = self._current_spec()
        if spec is None:
            self._slice_hint.setText("请输入边长：例如 2500 m、3 km 或 4096 px。")
            return
        anchor_label = frames.ANCHOR_LABELS.get(spec.anchor, "中心")
        where = "以原点为中心" if spec.anchor == frames.DEFAULT_ANCHOR else f"原点是矩形{anchor_label}角"
        if spec.kind == "distance":
            self._slice_hint.setText(
                f"边长 {frames.format_distance(spec.value)}，{where}生成正方形。"
            )
            return
        if self._info is None or self._info.ground_resolution_m is None:
            self._slice_hint.setText("按像素计算需要先加载带地理范围的 TIF。")
            return
        origin = self._origin_point()
        lat = origin[1] if origin else (self._info.center[1] if self._info.center else 0.0)
        edge = spec.edge_m(self._info.ground_resolution_m, lat)
        if edge is None:
            self._slice_hint.setText("无法按当前 TIF 换算边长。")
            return
        self._slice_hint.setText(
            f"{spec.value:g} px × {self._info.ground_resolution_m:.3f} m/px × "
            f"cos({lat:.2f}°) ≈ {frames.format_distance(edge)} 边长，{where}"
        )

    def _create_slice(self) -> None:
        spec = self._current_spec()
        if spec is None:
            self._update_slice_hint()
            self._status("边长输入无效")
            return
        if self._origin_point() is None:
            QMessageBox.information(self, "需要项目原点", "请先点「在地图上点选」设置项目原点。")
            return
        if spec.kind == "pixels" and (
            self._info is None or self._info.ground_resolution_m is None
        ):
            QMessageBox.information(self, "需要 TIF", "按像素方案需要先加载一个带地理范围的 TIF。")
            return
        self.add_slice(spec.kind, spec.value)

    def add_slice(self, kind: str, value: float, anchor: str | None = None) -> bool:
        """创建一个以原点为参考的切片方形图层；返回是否成功。"""
        origin = self._origin_point()
        if origin is None:
            return False
        spec = frames.SliceSpec(str(kind), float(value), anchor or self._current_anchor())
        resolution = self._info.ground_resolution_m if self._info else None
        edge = spec.edge_m(resolution, origin[1])
        if edge is None:
            return False

        index = sum(1 for entry in self._layers if entry.kind == "slice")
        color = COLOR_SLICES[index % len(COLOR_SLICES)]
        if spec.kind == "distance":
            title = f"切片 {frames.format_distance(spec.value)}"
        else:
            title = f"切片 {spec.value:g} px"
        if spec.anchor != frames.DEFAULT_ANCHOR:
            title += f"·{frames.ANCHOR_LABELS[spec.anchor]}"
        self._seq += 1
        entry = LayerEntry(
            key=f"slice-{self._seq}",
            kind="slice",
            title=title,
            color=color,
            spec=spec,
            detail=f"边长 {frames.format_distance(edge)}",
        )
        self._layers.append(entry)
        self._rebuild_layer_list(select=entry.key)
        self._refresh_overlays()
        bounds = self._slice_bounds(entry)
        if bounds is not None:
            self.map.fit_bounds(self._display_bounds(bounds))
        self._status(f"已创建 {title}：边长 {frames.format_distance(edge)}（见「图层」页签）")
        return True

    def _slice_bounds(self, entry: LayerEntry) -> tuple[float, float, float, float] | None:
        origin = self._origin_point()
        if origin is None or entry.spec is None:
            return None
        resolution = self._info.ground_resolution_m if self._info else None
        edge = entry.spec.edge_m(resolution, origin[1])
        if edge is None:
            return None
        return frames.square_bounds(origin[0], origin[1], edge, entry.spec.anchor)

    def _slice_edge(self, entry: LayerEntry) -> float | None:
        origin = self._origin_point()
        if origin is None or entry.spec is None:
            return None
        resolution = self._info.ground_resolution_m if self._info else None
        return entry.spec.edge_m(resolution, origin[1])

    # ------------------------------------------------------------------ 图层
    def _find_layer(self, key: str) -> LayerEntry | None:
        for entry in self._layers:
            if entry.key == key:
                return entry
        return None

    def _layer_detail(self, entry: LayerEntry) -> str:
        if entry.kind == "origin" and entry.point is not None:
            return f"{entry.point[0]:.6f}, {entry.point[1]:.6f}"
        if entry.kind == "tif" and entry.bounds is not None:
            west, south, east, north = entry.bounds
            return f"{west:.4f}~{east:.4f}, {south:.4f}~{north:.4f}"
        if entry.kind == "slice":
            edge = self._slice_edge(entry)
            if edge is None:
                return "缺少原点或 TIF"
            return f"边长 {frames.format_distance(edge)}"
        return entry.detail

    @staticmethod
    def _color_icon(color: str) -> QIcon:
        pixmap = QPixmap(12, 12)
        pixmap.fill(QColor(color))
        return QIcon(pixmap)

    def _rebuild_layer_list(self, select: str | None = None) -> None:
        if select is None:
            current = self._layer_list.currentItem()
            if current is not None:
                data = current.data(Qt.ItemDataRole.UserRole)
                select = str(data) if data is not None else None

        self._layer_list.blockSignals(True)
        self._layer_list.clear()
        if not self._layers:
            placeholder = QListWidgetItem("还没有图层：加载 TIF、设置原点或创建切片")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._layer_list.addItem(placeholder)
        for entry in self._layers:
            detail = self._layer_detail(entry)
            item = QListWidgetItem(f"{entry.title} · {detail}")
            item.setData(Qt.ItemDataRole.UserRole, entry.key)
            item.setToolTip(f"{entry.title}\n{detail}")
            item.setIcon(self._color_icon(entry.color))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if entry.visible else Qt.CheckState.Unchecked
            )
            self._layer_list.addItem(item)
            if entry.key == select:
                self._layer_list.setCurrentItem(item)
        self._layer_list.blockSignals(False)
        self._on_layer_selection()

    def _on_layer_item_changed(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        if key is None:
            return
        entry = self._find_layer(str(key))
        if entry is None:
            return
        entry.visible = item.checkState() == Qt.CheckState.Checked
        self._refresh_overlays()

    def _on_layer_selection(self) -> None:
        item = self._layer_list.currentItem()
        has_selection = item is not None and item.data(Qt.ItemDataRole.UserRole) is not None
        entry = self._selected_layer()
        self._fit_layer_button.setEnabled(has_selection)
        self._delete_layer_button.setEnabled(has_selection)
        self._export_button.setEnabled(bool(self._layers))

        can_crop = (
            entry is not None
            and entry.kind in {"tif", "slice"}
            and self._info is not None
            and crop.RASTERIO_AVAILABLE
            and self._layer_bounds(entry) is not None
        )
        self._crop_button.setEnabled(can_crop)

        if can_crop and entry is not None:
            west, south, east, north = self._layer_bounds(entry)
            self._crop_target_label.setText(f"目标图层：{entry.title}")
            self._crop_bounds_label.setText(
                f"WGS-84 范围：{west:.6f}, {south:.6f} → {east:.6f}, {north:.6f}"
            )
        else:
            self._crop_target_label.setText("未选中可裁剪的矩形图层")
            reason = "先在左侧「图层」里选中一个切片方形"
            if not crop.RASTERIO_AVAILABLE:
                reason = "未安装 rasterio：pip install rasterio"
            elif self._info is None:
                reason = "还没有载入 TIF"
            self._crop_bounds_label.setText(reason)

    def _selected_layer(self) -> LayerEntry | None:
        item = self._layer_list.currentItem()
        if item is None:
            return None
        key = item.data(Qt.ItemDataRole.UserRole)
        if key is None:
            return None
        return self._find_layer(str(key))

    def _fit_layer_by_item(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        if key is None:
            return
        entry = self._find_layer(str(key))
        if entry is not None:
            self._fit_layer(entry)

    def _fit_selected_layer(self) -> None:
        entry = self._selected_layer()
        if entry is not None:
            self._fit_layer(entry)

    def _fit_layer(self, entry: LayerEntry) -> None:
        bounds = self._layer_bounds(entry)
        if bounds is not None:
            self.map.fit_bounds(self._display_bounds(bounds))

    def _layer_bounds(self, entry: LayerEntry) -> tuple[float, float, float, float] | None:
        if entry.kind == "tif":
            return entry.bounds
        if entry.kind == "slice":
            return self._slice_bounds(entry)
        if entry.kind == "origin" and entry.point is not None:
            return frames.square_bounds(entry.point[0], entry.point[1], 1000.0)
        return None

    def _delete_selected_layer(self) -> None:
        entry = self._selected_layer()
        if entry is None:
            return
        self._layers.remove(entry)
        if entry.kind == "origin":
            self._set_pick_button(False)
            self._update_origin_label()
        self._rebuild_layer_list()
        self._refresh_overlays()
        self._update_slice_hint()
        self._status(f"已删除图层：{entry.title}")

    def _show_all_layers(self) -> None:
        for entry in self._layers:
            entry.visible = True
        self._rebuild_layer_list()
        self._refresh_overlays()

    def _copy_layers(self) -> None:
        origin = self._origin_point()
        payload: dict = {"crs": "WGS84", "origin": None, "layers": []}
        if origin is not None:
            payload["origin"] = {
                "lon": round(origin[0], 8),
                "lat": round(origin[1], 8),
                "alt": 0.0,
            }
        for entry in self._layers:
            # 原点是一个点，不是区域；只在顶层给经纬度，不要编造一个方形范围
            bounds = None if entry.kind == "origin" else self._layer_bounds(entry)
            item: dict = {
                "kind": entry.kind,
                "name": entry.title,
                "visible": entry.visible,
            }
            if entry.kind == "origin" and entry.point is not None:
                item["point"] = [round(entry.point[0], 8), round(entry.point[1], 8)]
            if bounds is not None:
                item["bounds"] = [round(value, 8) for value in bounds]
                if origin is not None:
                    offsets = frames.local_offsets(bounds, origin[0], origin[1])
                    item["local_offsets_m"] = {
                        "west": round(offsets[0], 2),
                        "south": round(offsets[1], 2),
                        "east": round(offsets[2], 2),
                        "north": round(offsets[3], 2),
                    }
            payload["layers"].append(item)
        QGuiApplication.clipboard().setText(json.dumps(payload, ensure_ascii=False, indent=2))
        self._status("图层清单（JSON）已复制到剪贴板")

    # ------------------------------------------------------------------ 导出 GeoJSON
    def layer_feature(
        self, entry: LayerEntry, datum: str = geojson.DATUM_WGS84
    ) -> dict | None:
        """图层 → GeoJSON Feature：矩形为面，原点为点。"""
        properties: dict = {"name": entry.title, "kind": entry.kind, "visible": entry.visible}
        if entry.kind == "tif" and self._info is not None:
            properties["source"] = self._info.path.name
            if self._info.crs:
                properties["source_crs"] = self._info.crs
            if self._info.width and self._info.height:
                properties["pixels"] = [self._info.width, self._info.height]
            if self._info.ground_resolution_m is not None:
                properties["pixel_size_m"] = round(self._info.ground_resolution_m, 3)
            if self._info.native_zoom is not None:
                properties["native_zoom"] = round(self._info.native_zoom, 2)
            if self._info.area_km2 is not None:
                properties["area_km2"] = round(self._info.area_km2, 4)

        if entry.kind == "origin" and entry.point is not None:
            return geojson.feature_from_point(entry.point[0], entry.point[1], properties, datum)

        bounds = self._layer_bounds(entry)
        if bounds is None:
            return None
        properties.setdefault("area_km2", round(geo.spherical_area_km2(*bounds), 4))
        return geojson.feature_from_bounds(bounds, properties, datum)

    def export_all_geojson(self, path: str | Path, datum: str = geojson.DATUM_WGS84) -> Path:
        """把所有图层写进一个 GeoJSON 文件，返回写入路径。"""
        features = [
            feature
            for entry in self._layers
            if (feature := self.layer_feature(entry, datum)) is not None
        ]
        if not features:
            raise ValueError("当前没有可导出的图层")

        collection = geojson.build_collection(
            features, name=f"GisMapsHelper · 全部图层（{len(features)}）"
        )
        return geojson.write_collection(path, collection)

    def _export_all_geojson(self) -> None:
        if not self._layers:
            QMessageBox.information(self, "无法导出", "图层列表还是空的：先载入 TIF 或设置原点。")
            return

        datum = geojson.DATUM_WGS84
        if self._basemap.datum == "GCJ-02":
            chosen = self._ask_datum()
            if chosen is None:
                return
            datum = chosen

        start = self._export_start_dir()
        stem = self._info.path.stem if self._info is not None else "layers"
        safe_stem = re.sub(r'[\\/:*?"<>|]', "_", stem) or "layers"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "导出全部图层为 GeoJSON",
            str(start / f"{safe_stem}.geojson"),
            "GeoJSON (*.geojson);;所有文件 (*)",
        )
        if not filename:
            return

        target = Path(filename)
        if target.suffix.lower() not in {".geojson", ".json"}:
            target = target.with_suffix(".geojson")
        try:
            written = self.export_all_geojson(target, datum)
        except ValueError as exc:
            QMessageBox.warning(self, "导出失败", f"{target}\n\n{exc}")
            return
        except OSError as exc:
            QMessageBox.warning(
                self,
                "导出失败",
                f"{target}\n\n{exc}\n\n"
                "如果程序是从带沙箱的终端启动的，只有项目目录可写；"
                "换个目录保存，或用普通 PowerShell / cmd 窗口启动程序。",
            )
            return

        self._settings.setValue("last_dir", str(written.parent))
        self._status(
            f"已导出 {len(self._layers)} 个图层到 {written.name}"
            f"（{geojson.DATUM_LABELS[datum]}）"
        )

    def _export_start_dir(self) -> Path:
        """导出对话框的起始目录：优先可写的位置，别默认落到用户主目录根。

        受限沙箱里只有项目目录可写，直接给 ``Path.home()`` 会让保存对话框
        弹「你没有权限在此位置中保存文件」。
        """
        candidates: list[Path] = []
        if self._info is not None:
            candidates.append(self._info.path.parent)
        remembered = str(self._settings.value("last_dir", "") or "")
        if remembered:
            candidates.append(Path(remembered))
        documents = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        if documents:
            candidates.append(Path(documents))
        candidates.append(Path.cwd())

        for candidate in candidates:
            try:
                if candidate.is_dir() and os.access(candidate, os.W_OK):
                    return candidate
            except OSError:
                continue
        return Path.cwd()

    def _ask_datum(self) -> str | None:
        """底图是 GCJ-02 时问一句用哪个基准，避免导出的面悄悄偏移几百米。"""
        box = QMessageBox(self)
        box.setWindowTitle("选择坐标基准")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("当前底图使用 GCJ-02（火星坐标），与 WGS-84 相差约 100~600 米。")
        box.setInformativeText("导出的 GeoJSON 用哪种坐标基准？")
        wgs = box.addButton("WGS-84（GeoJSON 标准）", QMessageBox.ButtonRole.AcceptRole)
        gcj = box.addButton("GCJ-02（与当前底图对齐）", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(wgs)
        box.exec()

        clicked = box.clickedButton()
        if clicked is wgs:
            return geojson.DATUM_WGS84
        if clicked is gcj:
            return geojson.DATUM_GCJ02
        return None

    # ------------------------------------------------------------------ 网格切片
    def _grid_n(self) -> int:
        try:
            return int(self._grid_combo.currentData())
        except (TypeError, ValueError):
            return 3

    def _update_grid_hint(self) -> None:
        ready = (
            self._info is not None
            and crop.RASTERIO_AVAILABLE
            and cad.EZDXF_AVAILABLE
        )
        self._grid_button.setEnabled(ready)

        if self._info is None:
            self._grid_hint.setText("先载入一个带地理范围的 GeoTIFF。")
            return
        if not crop.RASTERIO_AVAILABLE:
            self._grid_hint.setText("缺少 rasterio：pip install rasterio")
            return
        if not cad.EZDXF_AVAILABLE:
            self._grid_hint.setText("缺少 ezdxf：pip install ezdxf")
            return

        n = self._grid_n()
        note = ""
        if self._info.crs == "EPSG:4326" and self._grid_reproject.isChecked():
            note = "（先重投影到 EPSG:3857 再切）"
        self._grid_hint.setText(
            f"切成 {n}×{n}：每块约 {self._info.width // n} × {self._info.height // n} 像素{note}"
        )

        is_square, width_m, height_m = grid.squareness(self._info)
        if is_square:
            self._grid_warn.setVisible(False)
            return
        self._grid_warn.setText(
            f"⚠ 影像在 EPSG:3857 下不是正方形"
            f"（{width_m / 1000:.2f} km × {height_m / 1000:.2f} km，"
            f"宽高比 {width_m / height_m:.3f}），切出来的块是矩形而不是正方形。"
        )
        self._grid_warn.setVisible(True)

    def _run_grid_export(self) -> None:
        if self._info is None:
            QMessageBox.information(self, "无法切片", "先载入一个带地理范围的 TIF。")
            return
        if not crop.RASTERIO_AVAILABLE:
            QMessageBox.warning(
                self,
                "缺少依赖",
                "切片需要 rasterio：\n.venv\\Scripts\\python.exe -m pip install rasterio",
            )
            return
        if not cad.EZDXF_AVAILABLE:
            QMessageBox.warning(
                self,
                "缺少依赖",
                "写 DXF 需要 ezdxf：\n.venv\\Scripts\\python.exe -m pip install ezdxf",
            )
            return

        folder = QFileDialog.getExistingDirectory(
            self, "选择输出目录", str(self._export_start_dir())
        )
        if not folder:
            return

        n = self._grid_n()
        anchor = str(self._grid_anchor_combo.currentData() or frames.DEFAULT_ANCHOR)

        progress = QProgressDialog(f"正在切成 {n}×{n}…", "取消", 0, 100, self)
        progress.setWindowTitle("切片 TIF")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def report(fraction: float, message: str) -> None:
            progress.setValue(int(fraction * 100))
            progress.setLabelText(message)
            QApplication.processEvents()
            if progress.wasCanceled():
                raise crop.CropError("已取消切片")

        try:
            result = grid_export.export_grid(
                self._info.path,
                folder,
                n,
                origin_anchor=anchor,
                auto_reproject=self._grid_reproject.isChecked(),
                progress=report,
            )
        except (grid_export.GridExportError, crop.CropError, cad.CadError, OSError) as exc:
            QMessageBox.warning(self, "切片失败", str(exc))
            return
        finally:
            progress.close()

        cad_note = f"DWG：{result.dwg_path.name}" if result.dwg_path else "DXF（未检测到 ODA File Converter）"
        crs_note = "已重投影到 3857" if result.reprojected else f"坐标系 {result.source_crs}"
        self._grid_result.setText(
            f"已输出 {len(result.tiles)} 块到 {result.output_dir.name}\\\n"
            f"{crs_note}\nGeoJSON：{result.geojson_path.name}\nCAD：{cad_note}"
        )
        self._status(f"已按 {n}×{n} 切片：{len(result.tiles)} 个 GeoTIFF + GeoJSON + CAD")

    # ------------------------------------------------------------------ 裁剪
    def _crop_selected_layer(self) -> None:
        entry = self._selected_layer()
        if entry is None:
            QMessageBox.information(self, "无法裁剪", "先在图层列表里选中一个矩形图层。")
            return
        if self._info is None:
            QMessageBox.information(self, "无法裁剪", "还没有载入 TIF。")
            return
        if not crop.RASTERIO_AVAILABLE:
            QMessageBox.warning(
                self,
                "缺少依赖",
                "裁剪需要 rasterio：\n.venv\\Scripts\\python.exe -m pip install rasterio",
            )
            return
        if entry.kind == "tif":
            QMessageBox.information(
                self,
                "无法裁剪",
                "选中的就是 TIF 本身。请先在「原点与切片」里创建切片方形，再选中它来裁剪。",
            )
            return

        bounds = self._layer_bounds(entry)
        if bounds is None:
            QMessageBox.information(self, "无法裁剪", "这个图层没有可用的矩形范围。")
            return

        source = self._info.path
        safe = re.sub(r'[\\/:*?"<>|]', "_", entry.title) or "crop"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "裁剪 TIF",
            str(self._export_start_dir() / f"{source.stem}_{safe}.tif"),
            "GeoTIFF (*.tif *.tiff);;所有文件 (*)",
        )
        if not filename:
            return

        target = Path(filename)
        if target.suffix.lower() not in {".tif", ".tiff"}:
            target = target.with_suffix(".tif")
        try:
            if target.resolve() == source.resolve():
                QMessageBox.warning(self, "无法裁剪", "输出文件不能与源文件相同。")
                return
        except OSError:
            pass

        overwrite = target.exists()
        if overwrite and (
            QMessageBox.question(self, "覆盖文件", f"{target.name} 已存在，覆盖它？")
            != QMessageBox.StandardButton.Yes
        ):
            return

        progress = QProgressDialog(f"正在裁剪 {source.name}…", "取消", 0, 100, self)
        progress.setWindowTitle("裁剪 TIF")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def report(fraction: float) -> None:
            progress.setValue(int(fraction * 100))
            QApplication.processEvents()
            if progress.wasCanceled():
                raise crop.CropError("已取消裁剪")

        try:
            result = crop.crop_to_bounds(
                source,
                target,
                bounds,
                progress=report,
                overwrite=overwrite,
                reproject_4326=self._crop_reproject.isChecked(),
            )
        except (crop.CropError, OSError) as exc:
            QMessageBox.warning(self, "裁剪失败", str(exc))
            return
        finally:
            progress.close()

        note = "（请求范围超出影像，已裁到边界）" if result.clipped else ""
        crs_note = "，已重投影到 3857" if result.reprojected else ""
        self._status(
            f"已裁剪 {result.width}×{result.height} 像素 → {result.path.name}{crs_note}{note}"
        )

    # ------------------------------------------------------------------ 叠加与投影
    def _to_display(self, lon: float, lat: float) -> tuple[float, float]:
        if self._basemap.datum == "GCJ-02":
            return geo.wgs84_to_gcj02(lon, lat)
        return lon, lat

    def _to_wgs84(self, lon: float, lat: float) -> tuple[float, float]:
        if self._basemap.datum == "GCJ-02":
            return geo.gcj02_to_wgs84(lon, lat)
        return lon, lat

    def _display_bounds(
        self, bounds: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        west, south = self._to_display(bounds[0], bounds[1])
        east, north = self._to_display(bounds[2], bounds[3])
        return west, south, east, north

    def _refresh_overlays(self) -> None:
        overlays: list[MapOverlay] = []
        for entry in self._layers:
            if entry.kind == "tif" and entry.bounds is not None:
                overlays.append(
                    MapOverlay(
                        key=entry.key,
                        kind="rect",
                        label="TIF 范围",
                        color=entry.color,
                        bounds=self._display_bounds(entry.bounds),
                        visible=entry.visible,
                    )
                )
            elif entry.kind == "slice":
                bounds = self._slice_bounds(entry)
                if bounds is None:
                    continue
                overlays.append(
                    MapOverlay(
                        key=entry.key,
                        kind="rect",
                        label=entry.title,
                        color=entry.color,
                        bounds=self._display_bounds(bounds),
                        visible=entry.visible,
                    )
                )
            elif entry.kind == "origin" and entry.point is not None:
                lon, lat = self._to_display(entry.point[0], entry.point[1])
                overlays.append(
                    MapOverlay(
                        key=entry.key,
                        kind="origin",
                        label="原点 0,0,0",
                        color=entry.color,
                        point=(lon, lat),
                        visible=entry.visible,
                    )
                )
        self.map.set_overlays(overlays)
        self._fit_button.setEnabled(bool(overlays))

    # ------------------------------------------------------------------ 视图
    def _fit_to_all(self) -> None:
        boxes = []
        for entry in self._layers:
            if not entry.visible:
                continue
            bounds = self._layer_bounds(entry)
            if bounds is not None:
                boxes.append(bounds)
        if not boxes:
            return
        union = (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )
        self.map.fit_bounds(self._display_bounds(union))

    def _status(self, message: str) -> None:
        window = self.window()
        status_bar = getattr(window, "statusBar", None)
        if callable(status_bar):
            status_bar().showMessage(message, 6000)

    def _on_cursor_moved(self, lon: float, lat: float) -> None:
        self._coord_label.setText(f"光标 {lon:.4f}, {lat:.4f}")

    def _on_view_changed(self, lon: float, lat: float, zoom: int) -> None:
        self._coord_label.setText(f"中心 {lon:.4f}, {lat:.4f} · z{zoom}")

    # ------------------------------------------------------------------ 拖放
    @staticmethod
    def _tif_from_mime(mime) -> Path | None:
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            local = url.toLocalFile()
            if local and Path(local).suffix.lower() in _TIF_SUFFIXES:
                return Path(local)
        return None

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._tif_from_mime(event.mimeData()) is not None:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._tif_from_mime(event.mimeData()) is not None:
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        path = self._tif_from_mime(event.mimeData())
        if path is not None:
            self.load_path(path)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    # ------------------------------------------------------------------ 说明
    def _show_basemap_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("免费 / 开源底图服务")
        dialog.resize(900, 580)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        intro = QLabel(
            "下面这些地址可以直接拼进任何 XYZ 瓦片客户端（Leaflet / OpenLayers / Cesium / QGIS）。\n"
            "「开源」条目数据开放、必须署名；「免费」条目可免费调用但不开放，商用前请读服务条款。\n"
            "需要 3D 地球或地形时用 CesiumJS（Apache-2.0）：影像照样可以挂这里的 XYZ 地址，"
            "但 Cesium 自带的 ion 服务需要申请 token，并非完全免费。"
        )
        intro.setObjectName("SectionHint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        table = QTableWidget(len(BASEMAPS), 5)
        table.setHorizontalHeaderLabels(["名称", "类型", "坐标基准", "最高层级", "授权与说明"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for row, basemap in enumerate(BASEMAPS):
            cells = [
                basemap.name,
                basemap.kind,
                basemap.datum,
                f"z{basemap.max_zoom}",
                f"{basemap.license}；{basemap.note}",
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 4 and basemap.url:
                    item.setToolTip(basemap.url)
                table.setItem(row, column, item)
        table.resizeColumnsToContents()
        layout.addWidget(table, 1)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)
        dialog.exec()
