from __future__ import annotations

import sys
import traceback
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageQt

try:
    from PIL import ImageResampling

    _BILINEAR = ImageResampling.BILINEAR
    _LANCZOS = ImageResampling.LANCZOS
except ImportError:
    _BILINEAR = Image.BILINEAR  # type: ignore[attr-defined]
    _LANCZOS = Image.LANCZOS  # type: ignore[attr-defined]
from PySide6.QtCore import QMetaObject, QObject, QPointF, Qt, QThread, Signal, Slot
from PySide6.QtGui import QCloseEvent, QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from psd_tool import __version__
from psd_tool.core.export import (
    blend_template_guides_over_rgb,
    build_layer_and_overflow,
    write_psd,
)
from psd_tool.core.images import heif_available, open_image_pil
from psd_tool.core.layout import apply_manual, compute_contain, is_overflow, visible_alpha_bbox_rgba
from psd_tool.core.resolve import resolve_dimensions, ResolvedOutput
from psd_tool.core.template import composite_template_overlay_rgb, read_template_psd, TemplateInfo


def _qimage_from_pil(im: Image.Image):
    if im.mode != "RGB":
        im = im.convert("RGB")
    return ImageQt.ImageQt(im)


# プレビューラベルの背景 (#e8e8e8) とズーム時の余白を一致させる
_PREVIEW_BG_GREY = (232, 232, 232)


def _app_sidecar_dir() -> Path:
    """参照ダイアログの基準ディレクトリ。配布 exe では exe と同階層、開発時はカレントディレクトリ。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def _ensure_subdir(sub: str) -> Path:
    p = _app_sidecar_dir() / sub
    p.mkdir(parents=True, exist_ok=True)
    return p


def _compose_preview_zoom_viewport(
    base_rgb: Image.Image,
    nw: int,
    nh: int,
    zoom_pct: int,
    off_x: float | None = None,
    off_y: float | None = None,
) -> Image.Image:
    """
    プレビュー表示ズーム。表示ウィジェットのピクセルサイズは常に nw×nh とする。
    縮小: 画像を余白内で配置（off_* が None のとき中央）。
    拡大: off_* が None のとき中央クロップ、指定時はその原点で nw×nh を切り出し。
    """
    z = max(0.01, zoom_pct / 100.0)
    sw = max(1, int(round(nw * z)))
    sh = max(1, int(round(nh * z)))
    scaled = base_rgb.resize((sw, sh), _BILINEAR)
    out = Image.new("RGB", (nw, nh), _PREVIEW_BG_GREY)
    if sw <= nw and sh <= nh:
        px = float((nw - sw) // 2) if off_x is None else float(off_x)
        py = float((nh - sh) // 2) if off_y is None else float(off_y)
        px = max(0.0, min(float(nw - sw), px))
        py = max(0.0, min(float(nh - sh), py))
        out.paste(scaled, (int(round(px)), int(round(py))))
        return out
    left = (
        float(max(0, min((sw - nw) // 2, sw - nw)))
        if off_x is None
        else float(off_x)
    )
    top = (
        float(max(0, min((sh - nh) // 2, sh - nh)))
        if off_y is None
        else float(off_y)
    )
    left = max(0.0, min(float(sw - nw), left))
    top = max(0.0, min(float(sh - nh), top))
    cropped = scaled.crop(
        (int(round(left)), int(round(top)), int(round(left + nw)), int(round(top + nh)))
    )
    out.paste(cropped, (0, 0))
    return out


def _preview_zoom_scaled_dims(nw: int, nh: int, zoom_pct: int) -> tuple[int, int]:
    z = max(0.01, zoom_pct / 100.0)
    sw = max(1, int(round(nw * z)))
    sh = max(1, int(round(nh * z)))
    return sw, sh


def _preview_viewport_to_base(
    vx: float,
    vy: float,
    nw: int,
    nh: int,
    zoom_pct: int,
    off_x: float | None,
    off_y: float | None,
) -> tuple[float, float]:
    """ビューポート座標をベース画像（nw×nh）上の座標に変換。"""
    sw, sh = _preview_zoom_scaled_dims(nw, nh, zoom_pct)
    if sw <= nw and sh <= nh:
        px = float((nw - sw) // 2) if off_x is None else float(off_x)
        py = float((nh - sh) // 2) if off_y is None else float(off_y)
        px = max(0.0, min(float(nw - sw), px))
        py = max(0.0, min(float(nh - sh), py))
        sx = vx - px
        sy = vy - py
    else:
        left = (
            float(max(0, min((sw - nw) // 2, sw - nw)))
            if off_x is None
            else float(off_x)
        )
        top = (
            float(max(0, min((sh - nh) // 2, sh - nh)))
            if off_y is None
            else float(off_y)
        )
        left = max(0.0, min(float(sw - nw), left))
        top = max(0.0, min(float(sh - nh), top))
        sx = left + vx
        sy = top + vy
    bx = sx * nw / sw
    by = sy * nh / sh
    return bx, by


def _preview_anchor_offsets_for_zoom(
    vx: float,
    vy: float,
    nw: int,
    nh: int,
    zoom_pct: int,
    bx: float,
    by: float,
) -> tuple[float, float]:
    """ベース上の (bx,by) がビューポートの (vx,vy) に来るような off_x, off_y。"""
    sw, sh = _preview_zoom_scaled_dims(nw, nh, zoom_pct)
    sx = bx * sw / nw
    sy = by * sh / nh
    if sw <= nw and sh <= nh:
        px = vx - sx
        py = vy - sy
        px = max(0.0, min(float(nw - sw), px))
        py = max(0.0, min(float(nh - sh), py))
        return px, py
    left = sx - vx
    top = sy - vy
    left = max(0.0, min(float(sw - nw), left))
    top = max(0.0, min(float(sh - nh), top))
    return left, top


class ExportWorker(QObject):
    """書き出し処理をメインスレッドから分離し、完了時に結果だけ通知する。"""

    finished = Signal(object)

    def __init__(
        self,
        source_rgba: Image.Image,
        resolved: ResolvedOutput,
        manual_scale_pct: float,
        off_x: float,
        off_y: float,
        anchor_top_left: bool,
        out_path: str,
    ) -> None:
        super().__init__()
        self._source_rgba = source_rgba
        self._resolved = resolved
        self._manual_scale_pct = manual_scale_pct
        self._off_x = off_x
        self._off_y = off_y
        self._anchor_top_left = anchor_top_left
        self._out_path = out_path

    @Slot()
    def run(self) -> None:
        try:
            r = self._resolved
            # メインスレッドがプレビュー合成などで同画像を触れるので別バッファで処理する
            src = self._source_rgba.copy()
            layer, li, ti, oob, _ = build_layer_and_overflow(
                src,
                r.width,
                r.height,
                self._manual_scale_pct,
                self._off_x,
                self._off_y,
                anchor_top_left=self._anchor_top_left,
            )
            write_psd(
                layer,
                li,
                ti,
                r.width,
                r.height,
                self._out_path,
                r.dpi,
            )
            self.finished.emit(("ok", self._out_path, oob))
        except Exception as e:  # noqa: BLE001
            self.finished.emit(("err", e))


class PreviewLabel(QLabel):
    """プレビュー画像を左ドラッグで平行移動し、位置 X/Y スライダーと同期する。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scale_x = 1.0
        self._scale_y = 1.0
        self._drag_active = False
        self._last_mouse = None
        self._delta_cb = None
        self._begin_cb = None
        self._wheel_zoom_cb = None
        self.setMouseTracking(False)

    def set_wheel_zoom_callback(self, cb) -> None:
        self._wheel_zoom_cb = cb

    def set_drag_callbacks(self, begin_cb, delta_cb) -> None:
        self._begin_cb = begin_cb
        self._delta_cb = delta_cb

    def set_canvas_preview_scale(self, cw: int, ch: int, nw: int, nh: int) -> None:
        self._scale_x = cw / nw if nw > 0 else 1.0
        self._scale_y = ch / nh if nh > 0 else 1.0

    def mousePressEvent(self, event) -> None:
        from PySide6.QtGui import QMouseEvent

        if (
            isinstance(event, QMouseEvent)
            and event.button() == Qt.LeftButton
            and self._delta_cb is not None
            and self.pixmap() is not None
            and not self.pixmap().isNull()
        ):
            self._drag_active = True
            self._last_mouse = event.position()
            if self._begin_cb:
                self._begin_cb()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.grabMouse()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        from PySide6.QtGui import QMouseEvent

        if isinstance(event, QMouseEvent) and event.button() == Qt.LeftButton:
            if self._drag_active:
                self.releaseMouse()
                self._drag_active = False
                self._last_mouse = None
                self.setCursor(
                    Qt.CursorShape.OpenHandCursor
                    if self.pixmap() is not None and not self.pixmap().isNull()
                    else Qt.CursorShape.ArrowCursor
                )
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._drag_active
            and self._last_mouse is not None
            and self._delta_cb is not None
        ):
            pos = event.position()
            dx = pos.x() - self._last_mouse.x()
            dy = pos.y() - self._last_mouse.y()
            self._last_mouse = pos
            self._delta_cb(dx * self._scale_x, dy * self._scale_y)
        super().mouseMoveEvent(event)

    def wheelEvent(self, event) -> None:
        from PySide6.QtGui import QWheelEvent

        if (
            isinstance(event, QWheelEvent)
            and bool(event.modifiers() & Qt.ControlModifier)
            and self._wheel_zoom_cb is not None
            and self.pixmap() is not None
            and not self.pixmap().isNull()
        ):
            self._wheel_zoom_cb(float(event.angleDelta().y()), event.position())
            event.accept()
            return
        super().wheelEvent(event)

    def enterEvent(self, event) -> None:
        if self.pixmap() is not None and not self.pixmap().isNull():
            self.setCursor(
                Qt.CursorShape.ClosedHandCursor
                if self._drag_active
                else Qt.CursorShape.OpenHandCursor
            )
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._drag_active:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"PSDリサイズ出力ツール v{__version__}")
        self.setMinimumSize(900, 600)

        self._template_path: str | None = None
        self._image_path: str | None = None
        self._out_path: str | None = None
        self._template: TemplateInfo | None = None
        self._template_overlay_rgb: Image.Image | None = None
        self._source_rgba: Image.Image | None = None
        self._io_warn: str | None = None
        self._preview_pan_rx = 0.0
        self._preview_pan_ry = 0.0
        self._preview_layer_key: tuple | None = None
        self._preview_layer_rgba: Image.Image | None = None
        self._overlay_at_preview_key: tuple[int, int, int] | None = None
        self._overlay_at_preview_rgb: Image.Image | None = None
        self._preview_base_rgb: Image.Image | None = None
        self._preview_meta_cw: int = 0
        self._preview_meta_ch: int = 0
        self._preview_meta_oob: bool = False
        self._preview_zoom_off_x: float | None = None
        self._preview_zoom_off_y: float | None = None
        self._export_busy: bool = False
        self._export_progress: QProgressDialog | None = None
        self._export_prev_status: str = ""
        self._export_payload: object | None = None
        self._export_thread: QThread | None = None
        self._export_worker: ExportWorker | None = None

        w = self._build_ui()
        self.setCentralWidget(w)

    def closeEvent(self, event: QCloseEvent) -> None:
        t = self._export_thread
        if t is not None and t.isRunning():
            t.quit()
            t.wait(30_000)
        super().closeEvent(event)

    def _build_ui(self) -> QWidget:
        root = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setAlignment(Qt.AlignTop)

        # ファイル
        g_file = QGroupBox("ファイル")
        fl = QVBoxLayout(g_file)
        self._ed_template = self._row_file("テンプレートPSD", self._pick_template, fl)
        self._ed_image = self._row_file("入力画像", self._pick_image, fl)
        self._ed_out = self._row_file("出力先 (PSD)", self._pick_out, fl)
        left_l.addWidget(g_file)

        # 上書き
        g_ov = QGroupBox("印刷所マニュアル上書き")
        ovl = QVBoxLayout(g_ov)
        self._chk_override = QCheckBox("幅・高さ・解像度をマニュアルで上書きする")
        self._chk_override.setChecked(True)
        self._chk_override.toggled.connect(self._on_override_toggled)
        ovl.addWidget(self._chk_override)
        ovl.addWidget(
            QLabel(
                "オフのときは、画面上の数値は §8.2 の「1. 画面上指定」に使われず、テンプレ解像度を使います。"
            )
        )
        row = QHBoxLayout()
        self._sp_w = self._spin_px("幅 (px)", 1, 30000, row)
        self._sp_h = self._spin_px("高さ (px)", 1, 30000, row)
        self._sp_dpi = self._spin_dpi("解像度 (dpi)", 1, 3000, 300, row)
        ovl.addLayout(row)
        left_l.addWidget(g_ov)

        # テンプレ情報
        g_info = QGroupBox("テンプレート情報")
        self._lbl_twh = QLabel("—")
        self._lbl_tdpi = QLabel("—")
        self._lbl_out_res = QLabel("—")
        il = QVBoxLayout(g_info)
        il.addWidget(self._stat_row("PSD からの幅 / 高さ", self._lbl_twh))
        il.addWidget(self._stat_row("PSD からの解像度", self._lbl_tdpi))
        il.addWidget(self._stat_row("上書き後の出力", self._lbl_out_res))
        left_l.addWidget(g_info)

        # 調整
        g_adj = QGroupBox("配置・調整")
        al = QVBoxLayout(g_adj)
        self._sl_scale, self._sp_scale = self._slider_spin_row(
            "拡大率 (contain 後 %)", 10, 500, 100, al
        )
        self._chk_scale_anchor_tl = QCheckBox(
            "拡大縮小は画像の左上を固定する（オフ＝中心固定）"
        )
        self._chk_scale_anchor_tl.setToolTip(
            "オンにすると、contain 後の矩形の左上を固定して拡大・縮小します。"
            "オフのときは従来どおり、矩形の中心を固定して伸縮します。"
            "切り替え時は見た目の位置が変わらないよう位置を変換します。"
        )
        self._chk_scale_anchor_tl.toggled.connect(self._on_anchor_tl_toggled)
        al.addWidget(self._chk_scale_anchor_tl)
        self._sl_ox, self._sp_ox = self._slider_spin_row(
            "位置 X (px)", -2000, 2000, 0, al
        )
        self._sl_oy, self._sp_oy = self._slider_spin_row(
            "位置 Y (px)", -2000, 2000, 0, al
        )
        hb = QHBoxLayout()
        self._btn_contain = QPushButton("containで再配置")
        self._btn_contain.clicked.connect(self._on_contain)
        self._btn_center = QPushButton("中央に戻す (オフセットのみリセット)")
        self._btn_center.clicked.connect(self._on_center)
        hb.addWidget(self._btn_contain)
        hb.addWidget(self._btn_center)
        al.addLayout(hb)
        self._lbl_warn = QLabel("")
        self._lbl_warn.setStyleSheet("color: #7a6200; background: #fff4ce; padding:6px;")
        self._lbl_warn.setWordWrap(True)
        self._lbl_warn.hide()
        al.addWidget(self._lbl_warn)
        self._btn_export = QPushButton("PSDとして書き出し")
        self._btn_export.setObjectName("export")
        f = self._btn_export.font()
        f.setBold(True)
        self._btn_export.setFont(f)
        self._btn_export.clicked.connect(self._on_export)
        al.addWidget(self._btn_export)
        left_l.addWidget(g_adj)

        left_l.addStretch()
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(left)
        sa.setMinimumWidth(420)

        # プレビュー
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("プレビュー"))
        self._chk_tpl_overlay = QCheckBox(
            "テンプレート（点線・トンボ等）を重ねて表示（位置合わせ用）"
        )
        self._chk_tpl_overlay.setChecked(True)
        self._chk_tpl_overlay.setToolTip(
            "印刷所向けテンプレートを平坦化したものを前面に重ねます。"
            "書き出し PSD にはテンプレートは含まれません。"
        )
        self._chk_tpl_overlay.toggled.connect(self._refresh_preview)
        rl.addWidget(self._chk_tpl_overlay)
        self._sl_tpl_opacity, self._sp_tpl_opacity = self._slider_spin_row(
            "ガイドの強さ (%)", 10, 100, 90, rl
        )
        self._sl_preview_zoom, self._sp_preview_zoom = self._slider_spin_row(
            "プレビュー表示 (%)",
            25,
            400,
            100,
            rl,
            value_changed_slot=self._on_preview_zoom_changed,
        )
        lbl_zoom_hint = QLabel(
            "※ 書き出し解像度には影響しません。"
            "表示枠の大きさは一定で、縮小時は余白・拡大時はクロップ。"
            "Ctrl + ホイールはカーソル位置を中心に拡大・縮小します。"
            "スライダー／数値は中央基準に戻ります。"
        )
        lbl_zoom_hint.setStyleSheet("color:#666;font-size:11px;")
        lbl_zoom_hint.setWordWrap(True)
        rl.addWidget(lbl_zoom_hint)
        self._lbl_preview = PreviewLabel()
        self._lbl_preview.setText("テンプレートと画像を指定してください。")
        self._lbl_preview.setAlignment(Qt.AlignCenter)
        self._lbl_preview.setMinimumSize(400, 400)
        self._lbl_preview.setStyleSheet("background: #e8e8e8;")
        self._lbl_preview.setScaledContents(False)
        self._lbl_preview.setToolTip(
            "左ボタンドラッグで入力画像を移動します（位置 X / Y と連動）。"
            "Ctrl + ホイールでプレビュー表示をカーソル位置を中心に拡大・縮小できます。"
        )
        self._lbl_preview.set_wheel_zoom_callback(self._on_preview_wheel_zoom)
        self._lbl_preview.set_drag_callbacks(
            self._preview_drag_begin,
            self._on_preview_drag_delta,
        )
        rl.addWidget(self._lbl_preview, 1, Qt.AlignmentFlag.AlignCenter)
        self._status = QLabel("準備中")
        self._status.setStyleSheet("color: #555;")
        rl.addWidget(self._status)

        root.addWidget(sa)
        root.addWidget(right)
        root.setSizes([520, 480])

        # シグナル（拡大率・位置・ガイド強さはスライダ側でプレビュー更新済み）
        self._sp_w.valueChanged.connect(self._on_dim_changed)
        self._sp_h.valueChanged.connect(self._on_dim_changed)
        self._sp_dpi.valueChanged.connect(self._on_dim_changed)
        return root

    def _on_anchor_tl_toggled(self, checked: bool) -> None:
        """アンカー切替で貼り付け座標が変わらないよう位置 X/Y を変換する。"""
        new_tl = checked
        if not self._template or not self._source_rgba:
            self._refresh_preview()
            return
        try:
            r = self._resolved()
        except Exception:  # noqa: BLE001
            self._refresh_preview()
            return
        cw, ch = r.width, r.height
        c = compute_contain(cw, ch, self._source_rgba.width, self._source_rgba.height)
        scale_pct = float(self._sl_scale.value())
        ox = float(self._sl_ox.value())
        oy = float(self._sl_oy.value())
        old_tl = not new_tl
        l, t, _, _ = apply_manual(c, scale_pct, ox, oy, anchor_top_left=old_tl)
        if new_tl:
            ox_new = l - c.left
            oy_new = t - c.top
        else:
            m = scale_pct / 100.0
            rw_s = c.resized_w * m
            rh_s = c.resized_h * m
            ox_new = l - c.left - (c.resized_w - rw_s) / 2.0
            oy_new = t - c.top - (c.resized_h - rh_s) / 2.0
        ox_i = max(
            self._sl_ox.minimum(),
            min(self._sl_ox.maximum(), int(round(ox_new))),
        )
        oy_i = max(
            self._sl_oy.minimum(),
            min(self._sl_oy.maximum(), int(round(oy_new))),
        )
        self._sl_ox.blockSignals(True)
        self._sl_oy.blockSignals(True)
        self._sp_ox.blockSignals(True)
        self._sp_oy.blockSignals(True)
        self._sl_ox.setValue(ox_i)
        self._sl_oy.setValue(oy_i)
        self._sp_ox.setValue(ox_i)
        self._sp_oy.setValue(oy_i)
        self._sl_ox.blockSignals(False)
        self._sl_oy.blockSignals(False)
        self._sp_ox.blockSignals(False)
        self._sp_oy.blockSignals(False)
        self._preview_layer_key = None
        self._refresh_preview()

    def _preview_drag_begin(self) -> None:
        self._preview_pan_rx = 0.0
        self._preview_pan_ry = 0.0

    def _on_preview_drag_delta(self, dx_canvas: float, dy_canvas: float) -> None:
        if not self._template or not self._source_rgba:
            return
        self._preview_pan_rx += dx_canvas
        self._preview_pan_ry += dy_canvas
        ix = int(self._preview_pan_rx)
        iy = int(self._preview_pan_ry)
        self._preview_pan_rx -= ix
        self._preview_pan_ry -= iy
        if ix == 0 and iy == 0:
            return
        ox_lo, ox_hi = self._sl_ox.minimum(), self._sl_ox.maximum()
        oy_lo, oy_hi = self._sl_oy.minimum(), self._sl_oy.maximum()
        ox = max(ox_lo, min(ox_hi, self._sl_ox.value() + ix))
        oy = max(oy_lo, min(oy_hi, self._sl_oy.value() + iy))
        self._sl_ox.blockSignals(True)
        self._sl_oy.blockSignals(True)
        self._sp_ox.blockSignals(True)
        self._sp_oy.blockSignals(True)
        self._sl_ox.setValue(ox)
        self._sl_oy.setValue(oy)
        self._sp_ox.setValue(ox)
        self._sp_oy.setValue(oy)
        self._sl_ox.blockSignals(False)
        self._sl_oy.blockSignals(False)
        self._sp_ox.blockSignals(False)
        self._sp_oy.blockSignals(False)
        self._refresh_preview()

    def _row_file(
        self, label: str, slot, parent_l: QVBoxLayout
    ) -> QLabel:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        v = QLabel("")
        v.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.setWordWrap(True)
        b = QPushButton("参照…")
        b.clicked.connect(slot)
        row.addWidget(v, 1)
        row.addWidget(b)
        parent_l.addLayout(row)
        return v

    def _stat_row(self, name: str, value_lbl: QLabel) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        a = QLabel(name)
        a.setStyleSheet("color:#555;")
        h.addWidget(a, 0)
        h.addWidget(value_lbl, 1, Qt.AlignRight)
        return w

    def _spin_px(self, title: str, lo: int, hi: int, row: QHBoxLayout) -> QSpinBox:
        box = QGroupBox(title)
        l = QVBoxLayout(box)
        s = QSpinBox()
        s.setRange(lo, hi)
        l.addWidget(s)
        row.addWidget(box)
        return s

    def _spin_dpi(
        self, title: str, lo: int, hi: int, defv: int, row: QHBoxLayout
    ) -> QSpinBox:
        box = QGroupBox(title)
        l = QVBoxLayout(box)
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(defv)
        l.addWidget(s)
        row.addWidget(box)
        return s

    def _slider_spin_row(
        self,
        title: str,
        lo: int,
        hi: int,
        defv: int,
        parent: QVBoxLayout,
        *,
        value_changed_slot: Callable[..., None] | None = None,
    ) -> tuple[QSlider, QSpinBox]:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel(title))
        sl = QSlider(Qt.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(defv)
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setValue(defv)
        # PySide6 の QSpinBox には setMinimumContentsLength が無いため、フォントに応じた最小幅で桁を収める
        widest_txt = max(str(lo), str(hi), key=len)
        fm = QFontMetrics(sp.font())
        sp.setMinimumWidth(max(130, fm.horizontalAdvance(widest_txt) + 52))

        def sync_spin(v: int) -> None:
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)

        sl.valueChanged.connect(sync_spin)
        slot = (
            value_changed_slot
            if value_changed_slot is not None
            else self._refresh_preview
        )
        sl.valueChanged.connect(slot)
        sp.valueChanged.connect(sl.setValue)

        lay.addWidget(sl, 1)
        lay.addWidget(sp)
        parent.addWidget(w)
        return sl, sp

    def _on_override_toggled(self, on: bool) -> None:
        for w in (self._sp_w, self._sp_h, self._sp_dpi):
            w.setEnabled(on)
        self._update_template_labels()
        self._refresh_preview()

    def _on_dim_changed(self) -> None:
        self._update_template_labels()
        self._refresh_preview()

    def _pick_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "テンプレート PSD",
            str(_ensure_subdir("template")),
            "PSD (*.psd);;全て (*.*)",
        )
        if not path:
            return
        try:
            t = read_template_psd(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "エラー", f"PSD を読めませんでした:\n{e}")
            return
        self._template_path = path
        self._template = t
        self._template_overlay_rgb = None
        self._overlay_at_preview_key = None
        self._overlay_at_preview_rgb = None
        try:
            self._template_overlay_rgb = composite_template_overlay_rgb(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(
                self,
                "プレビュー",
                "テンプレートのガイド画像を作成できませんでした。"
                "プレビューは入力画像のみになります。\n\n"
                f"{e}",
            )
        self._ed_template.setText(path)
        self._sp_w.setValue(t.width)
        self._sp_h.setValue(t.height)
        d = int(round(t.dpi)) if t.dpi and t.dpi > 0 else 300
        self._sp_dpi.setValue(d)
        has_ov = self._template_overlay_rgb is not None
        self._chk_tpl_overlay.setEnabled(has_ov)
        self._sl_tpl_opacity.setEnabled(has_ov)
        self._sp_tpl_opacity.setEnabled(has_ov)
        self._update_template_labels()
        self._on_contain()
        self._status.setText(
            f"HEIC 対応: {'あり' if heif_available() else '未インストール (pillow-heif)'}"
        )

    def _pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "入力画像",
            str(_ensure_subdir("data")),
            "Images (*.png *.jpg *.jpeg *.webp *.gif *.tif *.tiff *.bmp"
            " *.heic *.heif);;全て (*.*)",
        )
        if not path:
            return
        try:
            im, warn = open_image_pil(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "エラー", f"画像を開けませんでした:\n{e}")
            return
        self._image_path = path
        self._source_rgba = im
        self._preview_layer_key = None
        self._preview_layer_rgba = None
        self._io_warn = warn
        self._ed_image.setText(path)
        wmsg = f"\n{warn}" if warn else ""
        if warn:
            QMessageBox.information(self, "注意", warn)
        self._on_contain()
        self._status.setText("画像を読み込みました" + wmsg)

    def _pick_out(self) -> None:
        default_psd = _ensure_subdir("output") / "out.psd"
        path, _ = QFileDialog.getSaveFileName(
            self, "PSD として保存", str(default_psd.resolve()), "PSD (*.psd)"
        )
        if not path:
            return
        if not path.lower().endswith(".psd"):
            path += ".psd"
        self._out_path = path
        self._ed_out.setText(path)

    def _update_template_labels(self) -> None:
        t = self._template
        if not t:
            self._lbl_twh.setText("—")
            self._lbl_tdpi.setText("—")
            self._lbl_out_res.setText("—")
            return
        td = f"{t.dpi:.0f} dpi" if t.dpi else "不明"
        self._lbl_twh.setText(f"{t.width} × {t.height} px")
        self._lbl_tdpi.setText(td)
        try:
            r = self._resolved()
        except Exception:  # noqa: BLE001
            self._lbl_out_res.setText("—")
            return
        self._lbl_out_res.setText(
            f"{r.width} × {r.height} px / {r.dpi:.0f} dpi（{r.source}）"
        )

    def _resolved(self) -> ResolvedOutput:
        if not self._template:
            raise ValueError("テンプレートが未指定です。")
        return resolve_dimensions(
            self._chk_override.isChecked(),
            int(self._sp_w.value()) if self._chk_override.isChecked() else None,
            int(self._sp_h.value()) if self._chk_override.isChecked() else None,
            float(self._sp_dpi.value()) if self._chk_override.isChecked() else None,
            self._template,
        )

    def _on_contain(self) -> None:
        self._sl_scale.setValue(100)
        self._sl_ox.setValue(0)
        self._sl_oy.setValue(0)
        self._refresh_preview()

    def _on_center(self) -> None:
        self._sl_ox.setValue(0)
        self._sl_oy.setValue(0)
        self._refresh_preview()

    def _on_preview_zoom_changed(self, _value: int | None = None) -> None:
        """プレビュー表示のみ。キャッシュ済みベース画像があるときは再合成しない。"""
        self._preview_zoom_off_x = None
        self._preview_zoom_off_y = None
        if self._preview_base_rgb is None:
            self._refresh_preview()
            return
        self._apply_preview_zoom_display()

    def _on_preview_wheel_zoom(self, angle_delta_y: float, pos: QPointF) -> None:
        if angle_delta_y == 0:
            return
        if self._preview_base_rgb is None:
            return
        prev = self._preview_base_rgb
        nw, nh = prev.size
        step = 10 if angle_delta_y > 0 else -10
        lo = self._sl_preview_zoom.minimum()
        hi = self._sl_preview_zoom.maximum()
        cur = self._sl_preview_zoom.value()
        v = max(lo, min(hi, cur + step))
        if v == cur:
            return
        vx = max(0.0, min(float(nw - 1), float(pos.x())))
        vy = max(0.0, min(float(nh - 1), float(pos.y())))
        z_old = int(cur)
        bx, by = _preview_viewport_to_base(
            vx,
            vy,
            nw,
            nh,
            z_old,
            self._preview_zoom_off_x,
            self._preview_zoom_off_y,
        )
        ox, oy = _preview_anchor_offsets_for_zoom(vx, vy, nw, nh, v, bx, by)
        self._preview_zoom_off_x = ox
        self._preview_zoom_off_y = oy
        self._sl_preview_zoom.blockSignals(True)
        self._sp_preview_zoom.blockSignals(True)
        self._sl_preview_zoom.setValue(v)
        self._sp_preview_zoom.setValue(v)
        self._sl_preview_zoom.blockSignals(False)
        self._sp_preview_zoom.blockSignals(False)
        self._apply_preview_zoom_display()

    def _apply_preview_zoom_display(self) -> None:
        """ベースプレビューをズームするが、ウィジェットのピクセルサイズは常に nw×nh に固定。"""
        if self._preview_base_rgb is None:
            return
        prev = self._preview_base_rgb
        nw, nh = prev.size
        cw, ch = self._preview_meta_cw, self._preview_meta_ch
        zpct = int(self._sl_preview_zoom.value())
        disp = _compose_preview_zoom_viewport(
            prev,
            nw,
            nh,
            zpct,
            self._preview_zoom_off_x,
            self._preview_zoom_off_y,
        )
        pix = QPixmap.fromImage(_qimage_from_pil(disp))
        self._lbl_preview.setMinimumSize(0, 0)
        self._lbl_preview.setFixedSize(nw, nh)
        self._lbl_preview.setPixmap(pix)
        self._lbl_preview.set_canvas_preview_scale(cw, ch, nw, nh)
        if self._preview_meta_oob:
            self._lbl_warn.setText(
                "警告: 手動調整の結果、画像がキャンバス外にかかっている可能性があります。入稿前にご確認ください。"
            )
            self._lbl_warn.show()
        else:
            self._lbl_warn.hide()

    def _refresh_preview(self) -> None:
        if not self._template or not self._source_rgba:
            self._lbl_preview.setText("テンプレートと画像を指定してください。")
            self._lbl_preview.setMinimumSize(400, 400)
            self._preview_base_rgb = None
            self._preview_zoom_off_x = None
            self._preview_zoom_off_y = None
            self._lbl_warn.hide()
            return
        try:
            r = self._resolved()
        except Exception as e:  # noqa: BLE001
            self._lbl_preview.setText(str(e))
            self._lbl_preview.setMinimumSize(400, 400)
            self._preview_base_rgb = None
            self._preview_zoom_off_x = None
            self._preview_zoom_off_y = None
            return
        cw, ch = r.width, r.height
        scale_pct = float(self._sl_scale.value())
        ox = float(self._sl_ox.value())
        oy = float(self._sl_oy.value())
        anchor_tl = self._chk_scale_anchor_tl.isChecked()
        max_w, max_h = 720, 720
        ratio = min(max_w / cw, max_h / ch, 1.0) if cw and ch else 1.0
        nw, nh = max(1, int(cw * ratio)), max(1, int(ch * ratio))
        prev_preview_size = (
            self._preview_base_rgb.size
            if self._preview_base_rgb is not None
            else None
        )
        try:
            c = compute_contain(cw, ch, self._source_rgba.width, self._source_rgba.height)
            l, t, rw, rh = apply_manual(
                c, scale_pct, ox, oy, anchor_top_left=anchor_tl
            )
            rw_i = max(1, int(round(rw)))
            rh_i = max(1, int(round(rh)))
            oob = is_overflow(
                l,
                t,
                float(rw_i),
                float(rh_i),
                cw,
                ch,
                visible_alpha_bbox_rgba(self._source_rgba),
            )

            preview_w = max(1, int(round(rw * ratio)))
            preview_h = max(1, int(round(rh * ratio)))
            layer_key = (
                id(self._source_rgba),
                self._source_rgba.size,
                cw,
                ch,
                scale_pct,
                preview_w,
                preview_h,
                anchor_tl,
            )
            if (
                layer_key != self._preview_layer_key
                or self._preview_layer_rgba is None
            ):
                layer = self._source_rgba.resize((preview_w, preview_h), _BILINEAR)
                if layer.mode != "RGBA":
                    layer = layer.convert("RGBA")
                self._preview_layer_rgba = layer
                self._preview_layer_key = layer_key

            prev = Image.new("RGB", (nw, nh), (255, 255, 255))
            prev.paste(
                self._preview_layer_rgba,
                (int(round(l * ratio)), int(round(t * ratio))),
                self._preview_layer_rgba,
            )
            if (
                self._chk_tpl_overlay.isChecked()
                and self._template_overlay_rgb is not None
            ):
                ov_key = (nw, nh, id(self._template_overlay_rgb))
                if (
                    self._overlay_at_preview_key != ov_key
                    or self._overlay_at_preview_rgb is None
                ):
                    self._overlay_at_preview_rgb = self._template_overlay_rgb.resize(
                        (nw, nh), _LANCZOS
                    )
                    self._overlay_at_preview_key = ov_key
                ov = self._overlay_at_preview_rgb
                prev = blend_template_guides_over_rgb(
                    prev,
                    ov,
                    int(self._sl_tpl_opacity.value()),
                )
            self._preview_base_rgb = prev
            self._preview_meta_cw = cw
            self._preview_meta_ch = ch
            self._preview_meta_oob = oob
        except Exception as e:  # noqa: BLE001
            self._preview_base_rgb = None
            self._preview_zoom_off_x = None
            self._preview_zoom_off_y = None
            self._lbl_preview.setMinimumSize(400, 400)
            self._lbl_preview.setText(f"プレビュー失敗: {e}")
            traceback.print_exc()
            return
        if prev_preview_size != (nw, nh):
            self._preview_zoom_off_x = None
            self._preview_zoom_off_y = None
        self._apply_preview_zoom_display()

    def _on_export(self) -> None:
        if self._export_busy:
            return
        if not self._template or not self._source_rgba:
            QMessageBox.warning(self, "未入力", "テンプレートPSDと入力画像を指定してください。")
            return
        if not self._out_path:
            self._pick_out()
        if not self._out_path:
            return
        try:
            r = self._resolved()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "エラー", str(e))
            return

        progress = QProgressDialog(self)
        progress.setWindowTitle("書き出し")
        progress.setLabelText("PSD を書き出しています…")
        progress.setRange(0, 0)
        progress.setMinimumDuration(0)
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._export_progress = progress
        progress.show()
        QApplication.processEvents()

        self._export_busy = True
        self._btn_export.setEnabled(False)
        self._export_prev_status = self._status.text()
        self._status.setText("書き出し中…")

        worker = ExportWorker(
            self._source_rgba,
            r,
            float(self._sl_scale.value()),
            float(self._sl_ox.value()),
            float(self._sl_oy.value()),
            self._chk_scale_anchor_tl.isChecked(),
            self._out_path,
        )
        thread = QThread(self)
        self._export_thread = thread
        self._export_worker = worker
        worker.moveToThread(thread)
        worker.finished.connect(self._on_worker_export_finished)
        thread.finished.connect(self._on_export_thread_finished)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        invoked = QMetaObject.invokeMethod(
            worker,
            "run",
            Qt.ConnectionType.QueuedConnection,
        )
        if not invoked:
            self._on_worker_export_finished(
                ("err", RuntimeError("書き出しスレッドの開始に失敗しました。"))
            )

    def _on_worker_export_finished(self, payload: object) -> None:
        """worker.finished を1スロットで処理し、payload 設定後に必ず thread.quit（順序競合を防ぐ）。"""
        self._export_payload = payload
        t = self._export_thread
        if t is not None:
            t.quit()

    def _on_export_thread_finished(self) -> None:
        self._export_thread = None
        self._export_worker = None
        self._export_busy = False
        self._btn_export.setEnabled(True)
        if self._export_progress is not None:
            self._export_progress.close()
            self._export_progress = None

        payload = self._export_payload
        self._export_payload = None

        if (
            payload is None
            or not isinstance(payload, tuple)
            or len(payload) < 2
            or payload[0] not in ("ok", "err")
        ):
            self._status.setText(self._export_prev_status)
            return

        kind = payload[0]
        if kind == "err":
            err = payload[1]
            self._status.setText(self._export_prev_status)
            QMessageBox.critical(self, "書き出し失敗", str(err))
            if isinstance(err, BaseException):
                traceback.print_exception(type(err), err, err.__traceback__)
            return

        if len(payload) < 3:
            self._status.setText(self._export_prev_status)
            return
        _, out_path, oob = payload
        self._status.setText("保存しました。")
        msg = f"保存しました:\n{out_path}"
        if oob:
            msg += "\n\n（上記のとおり、キャンバス外にはみ出しの恐れがあります）"
        QMessageBox.information(self, "完了", msg)


def run() -> int:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
