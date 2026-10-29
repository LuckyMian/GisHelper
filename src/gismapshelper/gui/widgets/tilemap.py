"""原生滑动的 Web 墨卡托底图控件（不需要 QtWebEngine）。

只做四件事：按 XYZ 地址取瓦片、拖动/滚轮平移缩放、叠加若干矩形与原点标记、
在需要时进入「点选原点」模式。图层数据由页面传入，控件本身不持有业务状态。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QToolButton, QWidget

from ...core import geo
from ...core.basemaps import DEFAULT_BASEMAP_ID, Basemap, get_basemap

TILE = geo.TILE_SIZE
MAX_PARALLEL = 6
FAILURE_COOLDOWN = 20.0        # 秒；失败的瓦片在这段时间内不重试
USER_AGENT = "GisMapsHelper/0.3 (+PySide6; desktop GIS helper)"

_BG = QColor("#e8edf4")
_PLACEHOLDER = QColor("#f1f5f9")
_PLACEHOLDER_LINE = QColor("#dbe3ec")


@dataclass(frozen=True)
class MapOverlay:
    """一个需要画在地图上的对象：矩形或原点。坐标一律是 WGS-84 经纬度。"""

    key: str
    kind: str                                   # "rect" / "origin"
    label: str = ""
    color: str = "#2563eb"
    bounds: tuple[float, float, float, float] | None = None
    point: tuple[float, float] | None = None
    visible: bool = True


class TileMapWidget(QWidget):
    """一个够用的 2D 瓦片地图：拖动平移、滚轮缩放到光标、叠加范围框与原点。"""

    view_changed = Signal(float, float, int)   # 中心经度、纬度、层级
    cursor_moved = Signal(float, float)        # 鼠标所在经纬度
    origin_picked = Signal(float, float)       # 点选模式下点击的经纬度
    pick_cancelled = Signal()                  # 按 Esc 取消点选

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TileMap")
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self._basemap: Basemap = get_basemap(DEFAULT_BASEMAP_ID)
        self._token = ""
        self._center: tuple[float, float] = (104.0, 35.0)
        self._zoom = 4
        self._overlays: list[MapOverlay] = []
        self._hint = ""          # 页面设置的状态提示（例如「未检测到地理范围」）
        self._net_hint = ""      # 网络/密钥类的临时提示，成功后自动消失
        self._picking = False

        self._cache: dict[tuple[str, int, int, int], QPixmap] = {}
        self._queue: list[tuple[str, int, int, int]] = []
        self._pending: set[tuple[str, int, int, int]] = set()
        self._failed: dict[tuple[str, int, int, int], float] = {}
        self._active: dict[QNetworkReply, tuple[str, int, int, int]] = {}
        self._errors = 0

        self._dragging = False
        self._drag_pos = QPointF()
        self._drag_center = QPointF()

        self._nam = QNetworkAccessManager(self)
        self._nam.setRedirectPolicy(QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        self._nam.finished.connect(self._on_reply)

        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.setInterval(int(FAILURE_COOLDOWN * 1000))
        self._retry_timer.timeout.connect(self._retry_failed)

        self._btn_in = self._make_button("+", "放大", self.zoom_in)
        self._btn_out = self._make_button("−", "缩小", self.zoom_out)

    # ------------------------------------------------------------------ 基础属性
    @property
    def basemap(self) -> Basemap:
        return self._basemap

    def zoom(self) -> int:
        return self._zoom

    def center(self) -> tuple[float, float]:
        return self._center

    def set_hint(self, text: str) -> None:
        self._hint = text
        self.update()

    @property
    def current_hint(self) -> str:
        return self._net_hint or self._hint

    # ------------------------------------------------------------------ 视图操作
    def set_view(self, lon: float, lat: float, zoom: int) -> None:
        zoom = max(self._basemap.min_zoom, min(self._basemap.max_zoom, int(zoom)))
        self._center = (self._wrap_lon(lon), geo.clamp_lat(lat))
        self._zoom = zoom
        self._after_view_change()

    def set_center(self, lon: float, lat: float) -> None:
        self._center = (self._wrap_lon(lon), geo.clamp_lat(lat))
        self._after_view_change()

    def zoom_in(self) -> None:
        self._zoom_by(1, QPointF(self.width() / 2.0, self.height() / 2.0))

    def zoom_out(self) -> None:
        self._zoom_by(-1, QPointF(self.width() / 2.0, self.height() / 2.0))

    def set_basemap(self, basemap: Basemap, token: str = "") -> None:
        self._basemap = basemap
        self._token = token or ""
        self._zoom = max(basemap.min_zoom, min(basemap.max_zoom, self._zoom))
        self._queue.clear()
        self._errors = 0
        self.refresh()

    def set_token(self, token: str) -> None:
        self._token = token or ""
        self.refresh()

    def refresh(self) -> None:
        """清掉失败记录，重新取当前视图的瓦片。"""
        self._failed.clear()
        self._net_hint = ""
        self._request_tiles()
        self.update()

    def fit_bounds(
        self, bounds: tuple[float, float, float, float] | None, padding: int = 56
    ) -> None:
        """选一个能完整容纳该矩形的最小层级，并把视图居中。"""
        if bounds is None:
            return
        west, south, east, north = bounds
        if west > east:
            west, east = east, west
        if south > north:
            south, north = north, south
        avail_w = max(64, self.width() - 2 * padding)
        avail_h = max(64, self.height() - 2 * padding)

        chosen = self._basemap.min_zoom
        for candidate in range(self._basemap.max_zoom, self._basemap.min_zoom - 1, -1):
            x0, y0 = geo.lonlat_to_world(west, north, candidate)
            x1, y1 = geo.lonlat_to_world(east, south, candidate)
            if (x1 - x0) <= avail_w and (y1 - y0) <= avail_h:
                chosen = candidate
                break
        self.set_view((west + east) / 2.0, (south + north) / 2.0, chosen)

    # ------------------------------------------------------------------ 叠加图层
    def set_overlays(self, overlays: list[MapOverlay]) -> None:
        self._overlays = list(overlays)
        self.update()

    def overlays(self) -> list[MapOverlay]:
        return list(self._overlays)

    def set_rect(self, bounds: tuple[float, float, float, float] | None) -> None:
        """兼容旧接口：只放一个矩形。"""
        if bounds is None:
            self.clear_rect()
            return
        self.set_overlays(
            [MapOverlay(key="tif", kind="rect", label="TIF 范围", bounds=bounds)]
        )

    def clear_rect(self) -> None:
        self.set_overlays([item for item in self._overlays if item.kind != "rect"])

    def bounds(self) -> tuple[float, float, float, float] | None:
        """第一个矩形叠加物的范围（兼容旧接口）。"""
        for overlay in self._overlays:
            if overlay.kind == "rect" and overlay.bounds is not None:
                return overlay.bounds
        return None

    # ------------------------------------------------------------------ 点选原点
    def start_origin_pick(self) -> None:
        self._picking = True
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    def cancel_origin_pick(self) -> None:
        if not self._picking:
            return
        self._picking = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.update()

    def is_picking_origin(self) -> bool:
        return self._picking

    # ------------------------------------------------------------------ 内部：坐标
    @staticmethod
    def _wrap_lon(lon: float) -> float:
        return max(-180.0, min(180.0, float(lon)))

    def _top_left(self) -> tuple[float, float]:
        px, py = geo.lonlat_to_world(self._center[0], self._center[1], self._zoom)
        return px - self.width() / 2.0, py - self.height() / 2.0

    def _paint_origin_xy(self) -> tuple[float, float]:
        """绘制用的左上角（取整，避免瓦片之间出现缝隙）。"""
        left, top = self._top_left()
        return float(round(left)), float(round(top))

    def _lonlat_at(self, point: QPointF) -> tuple[float, float]:
        left, top = self._paint_origin_xy()
        return geo.world_to_lonlat(left + point.x(), top + point.y(), self._zoom)

    def _visible_ranges(self) -> tuple[range, range]:
        z = self._zoom
        count = 1 << z
        left, top = self._paint_origin_xy()
        x0 = max(0, int(math.floor(left / TILE)) - 1)
        x1 = min(count - 1, int(math.floor((left + self.width()) / TILE)) + 1)
        y0 = max(0, int(math.floor(top / TILE)) - 1)
        y1 = min(count - 1, int(math.floor((top + self.height()) / TILE)) + 1)
        return range(x0, x1 + 1), range(y0, y1 + 1)

    def _after_view_change(self) -> None:
        if self.isVisible():
            self._request_tiles()
        self.update()
        self.view_changed.emit(self._center[0], self._center[1], self._zoom)

    def _zoom_by(self, step: int, anchor: QPointF) -> None:
        target = max(self._basemap.min_zoom, min(self._basemap.max_zoom, self._zoom + step))
        if target == self._zoom:
            return
        lon, lat = self._lonlat_at(anchor)
        self._zoom = target
        px, py = geo.lonlat_to_world(lon, lat, target)
        center_x = px - anchor.x() + self.width() / 2.0
        center_y = py - anchor.y() + self.height() / 2.0
        lon2, lat2 = geo.world_to_lonlat(center_x, center_y, target)
        self._center = (self._wrap_lon(lon2), geo.clamp_lat(lat2))
        self._after_view_change()

    # ------------------------------------------------------------------ 内部：瓦片
    def _tile_url(self, basemap: Basemap, z: int, x: int, y: int) -> str:
        url = basemap.url
        if not url:
            return ""
        subdomains = basemap.subdomains or ("a",)
        subdomain = subdomains[(x + y) % len(subdomains)]
        retina = "@2x" if self.devicePixelRatioF() >= 2 else ""
        return (
            url.replace("{s}", subdomain)
            .replace("{r}", retina)
            .replace("{key}", self._token)
            .replace("{z}", str(z))
            .replace("{x}", str(x))
            .replace("{y}", str(y))
        )

    def _request_tiles(self) -> None:
        if not self.isVisible():
            return
        basemap = self._basemap
        if basemap.is_blank:
            self._queue.clear()
            return
        if basemap.requires_key and not self._token:
            self._net_hint = "该底图需要服务密钥（tk），填写后自动加载"
            self.update()
            return

        self._queue.clear()
        z = self._zoom
        now = time.monotonic()
        x_range, y_range = self._visible_ranges()
        for x in x_range:
            for y in y_range:
                key = (basemap.id, z, x, y)
                if key in self._cache or key in self._pending:
                    continue
                if self._failed.get(key, 0.0) > now - FAILURE_COOLDOWN:
                    continue
                self._queue.append(key)
        self._pump()

    def _pump(self) -> None:
        while self._queue and len(self._active) < MAX_PARALLEL:
            key = self._queue.pop(0)
            if key in self._pending:
                continue
            basemap_id, z, x, y = key
            url = self._tile_url(get_basemap(basemap_id), z, x, y)
            if not url:
                continue
            request = QNetworkRequest(QUrl(url))
            request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, USER_AGENT)
            request.setTransferTimeout(20000)
            reply = self._nam.get(request)
            self._pending.add(key)
            self._active[reply] = key

    def _on_reply(self, reply: QNetworkReply | None = None) -> None:
        if reply is None:
            reply = self.sender()
        if not isinstance(reply, QNetworkReply):
            return
        key = self._active.pop(reply, None)
        if key is None:
            reply.deleteLater()
            return

        ok = reply.error() == QNetworkReply.NetworkError.NoError
        payload = bytes(reply.readAll()) if ok else b""
        reply.deleteLater()
        self._pending.discard(key)

        pixmap = QPixmap()
        if ok and payload and pixmap.loadFromData(payload) and not pixmap.isNull():
            self._store(key, pixmap)
            self._errors = 0
            self._net_hint = ""
        else:
            self._failed[key] = time.monotonic()
            self._errors += 1
            if self._errors == 4:
                self._net_hint = "瓦片加载失败：请检查网络 / 代理，或换一个底图"
                self._retry_timer.start()

        self.update()
        self._pump()

    def _retry_failed(self) -> None:
        """冷却时间到了再试一次，网络恢复后无需手动刷新。"""
        if not self.isVisible() or not self._failed:
            return
        self._failed.clear()
        self._errors = 0
        self._request_tiles()
        self.update()

    def _store(self, key: tuple[str, int, int, int], pixmap: QPixmap) -> None:
        self._cache[key] = pixmap
        if len(self._cache) > 800:
            for stale in list(self._cache)[:200]:
                self._cache.pop(stale, None)

    # ------------------------------------------------------------------ 绘制
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._place_buttons()
        if self.isVisible():
            self._request_tiles()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._place_buttons()
        self._request_tiles()

    def _make_button(self, text: str, tip: str, slot) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("MapButton")
        button.setText(text)
        button.setToolTip(tip)
        button.setFixedSize(30, 30)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        button.clicked.connect(slot)
        return button

    def _place_buttons(self) -> None:
        self._btn_in.move(max(0, self.width() - 40), 10)
        self._btn_out.move(max(0, self.width() - 40), 44)
        self._btn_in.raise_()
        self._btn_out.raise_()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), _BG)

        left, top = self._paint_origin_xy()
        z = self._zoom
        x_range, y_range = self._visible_ranges()
        for x in x_range:
            for y in y_range:
                rect = QRectF(x * TILE - left, y * TILE - top, TILE, TILE)
                pixmap = self._cache.get((self._basemap.id, z, x, y))
                if pixmap is not None and not pixmap.isNull():
                    painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
                else:
                    painter.fillRect(rect, _PLACEHOLDER)
                    painter.setPen(QPen(_PLACEHOLDER_LINE, 1))
                    painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

        # 先画矩形，再画原点，保证原点在最上层
        for overlay in self._overlays:
            if overlay.visible and overlay.kind == "rect" and overlay.bounds is not None:
                self._draw_rect_overlay(painter, overlay, left, top)
        for overlay in self._overlays:
            if overlay.visible and overlay.kind == "origin" and overlay.point is not None:
                self._draw_origin_overlay(painter, overlay, left, top)

        self._draw_scale(painter)
        self._draw_attribution(painter)
        if self._basemap.is_blank:
            self._draw_center_label(painter, "空白底图 · 不加载网络瓦片")
        hint = self.current_hint
        if hint:
            self._draw_hint(painter, hint)

    def _draw_rect_overlay(
        self, painter: QPainter, overlay: MapOverlay, left: float, top: float
    ) -> None:
        assert overlay.bounds is not None
        west, south, east, north = overlay.bounds
        x0, y0 = geo.lonlat_to_world(west, north, self._zoom)
        x1, y1 = geo.lonlat_to_world(east, south, self._zoom)
        rect = QRectF(x0 - left, y0 - top, x1 - x0, y1 - y0)
        if rect.width() < 3.0:
            rect.setWidth(3.0)
        if rect.height() < 3.0:
            rect.setHeight(3.0)

        color = QColor(overlay.color)
        fill = QColor(color)
        fill.setAlpha(32)
        painter.setBrush(fill)
        painter.setPen(QPen(color, 2))
        painter.drawRect(rect)

        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        for corner in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
            painter.drawRect(QRectF(corner.x() - 2.5, corner.y() - 2.5, 5, 5))

        if overlay.label and rect.intersects(QRectF(self.rect())):
            chip_y = rect.top() - 24 if rect.top() > 30 else rect.top() + 4
            self._draw_chip(painter, overlay.label, rect.left(), chip_y, overlay.color)

    def _draw_origin_overlay(
        self, painter: QPainter, overlay: MapOverlay, left: float, top: float
    ) -> None:
        assert overlay.point is not None
        lon, lat = overlay.point
        px, py = geo.lonlat_to_world(lon, lat, self._zoom)
        x, y = px - left, py - top
        if x < -40 or y < -40 or x > self.width() + 40 or y > self.height() + 40:
            return

        color = QColor(overlay.color)
        guide = QColor(color)
        guide.setAlpha(110)
        pen = QPen(guide, 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        painter.drawLine(QPointF(0, y), QPointF(self.width(), y))

        painter.setPen(QPen(color, 2))
        painter.setBrush(QColor(255, 255, 255, 235))
        painter.drawEllipse(QPointF(x, y), 9, 9)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(x, y), 3.5, 3.5)

        self._draw_chip(painter, overlay.label or "原点 0,0,0", x + 14, y - 26, overlay.color)

    def _draw_chip(
        self, painter: QPainter, text: str, x: float, y: float, color: str
    ) -> None:
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1))
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(text) + 14
        chip_x = max(4.0, min(self.width() - width - 4.0, float(x)))
        chip_y = max(4.0, min(self.height() - 22.0, float(y)))
        chip = QRectF(chip_x, chip_y, width, 18)

        fill = QColor(color)
        fill.setAlpha(225)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(chip, 4, 4)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_scale(self, painter: QPainter) -> None:
        resolution = geo.ground_resolution_m(self._center[1], self._zoom)
        if resolution <= 0:
            return
        meters = _nice_distance(resolution * 120.0)
        bar_width = meters / resolution
        x, y = 14.0, self.height() - 22.0

        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1))
        painter.setFont(font)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 205))
        painter.drawRoundedRect(QRectF(x - 6, y - 24, bar_width + 12, 32), 4, 4)

        painter.setPen(QPen(QColor("#334155"), 1.6))
        painter.drawLine(QPointF(x, y), QPointF(x + bar_width, y))
        painter.drawLine(QPointF(x, y - 5), QPointF(x, y))
        painter.drawLine(QPointF(x + bar_width, y - 5), QPointF(x + bar_width, y))
        painter.setPen(QColor("#334155"))
        painter.drawText(
            QRectF(x, y - 22, max(bar_width, 60), 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            _format_distance(meters),
        )

    def _draw_attribution(self, painter: QPainter) -> None:
        text = self._basemap.attribution
        if not text:
            return
        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() - 2))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 12
        rect = QRectF(self.width() - width - 6, self.height() - 22, width, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QColor("#475569"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_hint(self, painter: QPainter, text: str) -> None:
        font = QFont(self.font())
        font.setPointSizeF(max(8.5, font.pointSizeF() - 0.5))
        painter.setFont(font)
        width = min(
            self.width() - 40,
            max(220, painter.fontMetrics().horizontalAdvance(text) + 28),
        )
        rect = QRectF((self.width() - width) / 2.0, 12, width, 34)
        painter.setPen(QPen(QColor("#fed7aa"), 1))
        painter.setBrush(QColor(255, 247, 237, 238))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QColor("#9a3412"))
        flags = int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap)
        painter.drawText(rect.adjusted(12, 0, -12, 0), flags, text)

    def _draw_center_label(self, painter: QPainter, text: str) -> None:
        font = QFont(self.font())
        font.setPointSizeF(max(9.0, font.pointSizeF()))
        painter.setFont(font)
        painter.setPen(QColor("#8b96a5"))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)

    # ------------------------------------------------------------------ 交互
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self._picking:
                lon, lat = self._lonlat_at(event.position())
                self._picking = False
                self.setCursor(Qt.CursorShape.OpenHandCursor)
                self.update()
                self.origin_picked.emit(lon, lat)
                event.accept()
                return
            self._dragging = True
            self._drag_pos = event.position()
            px, py = geo.lonlat_to_world(self._center[0], self._center[1], self._zoom)
            self._drag_center = QPointF(px, py)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            delta = event.position() - self._drag_pos
            lon, lat = geo.world_to_lonlat(
                self._drag_center.x() - delta.x(),
                self._drag_center.y() - delta.y(),
                self._zoom,
            )
            self.set_center(lon, lat)
            event.accept()
            return
        lon, lat = self._lonlat_at(event.position())
        self.cursor_moved.emit(lon, lat)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self._picking:
            self._zoom_by(1, event.position())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta:
            self._zoom_by(1 if delta > 0 else -1, event.position())
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape and self._picking:
            self._picking = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.update()
            self.pick_cancelled.emit()
            event.accept()
            return

        step = 80.0
        left, top = self._top_left()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            dx = -step if key == Qt.Key.Key_Left else step if key == Qt.Key.Key_Right else 0.0
            dy = -step if key == Qt.Key.Key_Up else step if key == Qt.Key.Key_Down else 0.0
            lon, lat = geo.world_to_lonlat(
                left + self.width() / 2.0 + dx, top + self.height() / 2.0 + dy, self._zoom
            )
            self.set_center(lon, lat)
            event.accept()
            return
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_in()
            event.accept()
            return
        if key == Qt.Key.Key_Minus:
            self.zoom_out()
            event.accept()
            return
        if key in (Qt.Key.Key_Home, Qt.Key.Key_0):
            self.fit_bounds(self.bounds())
            event.accept()
            return
        super().keyPressEvent(event)


# --------------------------------------------------------------------------- 小工具
def _nice_distance(meters: float) -> float:
    """把任意距离收敛到 1/2/5 × 10^n，让比例尺长度好看。"""
    if meters <= 0:
        return 1.0
    exponent = math.floor(math.log10(meters))
    base = 10.0 ** exponent
    for multiplier in (5.0, 2.0, 1.0):
        if base * multiplier <= meters:
            return base * multiplier
    return base


def _format_distance(meters: float) -> str:
    if meters >= 1000:
        return f"{meters / 1000.0:g} km"
    return f"{meters:g} m"
