from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PIL import Image, ImageQt

try:
    from PIL import ImageResampling

    _BILINEAR = ImageResampling.BILINEAR
    _LANCZOS = ImageResampling.LANCZOS
except ImportError:
    _BILINEAR = Image.BILINEAR  # type: ignore[attr-defined]
    _LANCZOS = Image.LANCZOS  # type: ignore[attr-defined]
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
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
        self.setMouseTracking(False)

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

        w = self._build_ui()
        self.setCentralWidget(w)

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
        self._sl_scale = self._slider_row("拡大率 (contain 後 %)", 10, 500, 100, al)
        self._sl_ox = self._slider_row("位置 X (px)", -2000, 2000, 0, al)
        self._sl_oy = self._slider_row("位置 Y (px)", -2000, 2000, 0, al)
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
        self._sl_tpl_opacity = self._slider_row("ガイドの強さ (%)", 10, 100, 90, rl)
        self._sl_tpl_opacity.valueChanged.connect(self._refresh_preview)
        self._lbl_preview = PreviewLabel()
        self._lbl_preview.setText("テンプレートと画像を指定してください。")
        self._lbl_preview.setAlignment(Qt.AlignCenter)
        self._lbl_preview.setMinimumSize(400, 400)
        self._lbl_preview.setStyleSheet("background: #e8e8e8;")
        self._lbl_preview.setScaledContents(False)
        self._lbl_preview.setToolTip(
            "左ボタンドラッグで入力画像を移動します（位置 X / Y と連動）。"
        )
        self._lbl_preview.set_drag_callbacks(
            self._preview_drag_begin,
            self._on_preview_drag_delta,
        )
        rl.addWidget(self._lbl_preview, 1)
        self._status = QLabel("準備中")
        self._status.setStyleSheet("color: #555;")
        rl.addWidget(self._status)

        root.addWidget(sa)
        root.addWidget(right)
        root.setSizes([520, 480])

        # シグナル
        for s in (self._sl_scale, self._sl_ox, self._sl_oy):
            s.valueChanged.connect(self._refresh_preview)
        self._sp_w.valueChanged.connect(self._on_dim_changed)
        self._sp_h.valueChanged.connect(self._on_dim_changed)
        self._sp_dpi.valueChanged.connect(self._on_dim_changed)
        return root

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
        self._sl_ox.setValue(ox)
        self._sl_oy.setValue(oy)
        self._sl_ox.blockSignals(False)
        self._sl_oy.blockSignals(False)
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

    def _slider_row(
        self, title: str, lo: int, hi: int, defv: int, parent: QVBoxLayout
    ) -> QSlider:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel(title))
        sl = QSlider(Qt.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(defv)
        val = QLabel(str(defv))
        val.setFixedWidth(56)
        val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sl.valueChanged.connect(lambda v: val.setText(str(v)))
        lay.addWidget(sl, 1)
        lay.addWidget(val)
        parent.addWidget(w)
        return sl

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
            self, "テンプレート PSD", str(Path.home()), "PSD (*.psd);;全て (*.*)"
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
        self._update_template_labels()
        self._on_contain()
        self._status.setText(
            f"HEIC 対応: {'あり' if heif_available() else '未インストール (pillow-heif)'}"
        )

    def _pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "入力画像",
            str(Path.home()),
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
        path, _ = QFileDialog.getSaveFileName(
            self, "PSD として保存", str(Path.home() / "out.psd"), "PSD (*.psd)"
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

    def _refresh_preview(self) -> None:
        if not self._template or not self._source_rgba:
            self._lbl_preview.setText("テンプレートと画像を指定してください。")
            self._lbl_warn.hide()
            return
        try:
            r = self._resolved()
        except Exception as e:  # noqa: BLE001
            self._lbl_preview.setText(str(e))
            return
        cw, ch = r.width, r.height
        scale_pct = float(self._sl_scale.value())
        ox = float(self._sl_ox.value())
        oy = float(self._sl_oy.value())
        max_w, max_h = 720, 720
        ratio = min(max_w / cw, max_h / ch, 1.0) if cw and ch else 1.0
        nw, nh = max(1, int(cw * ratio)), max(1, int(ch * ratio))
        try:
            c = compute_contain(cw, ch, self._source_rgba.width, self._source_rgba.height)
            l, t, rw, rh = apply_manual(c, scale_pct, ox, oy)
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
        except Exception as e:  # noqa: BLE001
            self._lbl_preview.setText(f"プレビュー失敗: {e}")
            traceback.print_exc()
            return
        pix = QPixmap.fromImage(_qimage_from_pil(prev))
        self._lbl_preview.setFixedSize(nw, nh)
        self._lbl_preview.setPixmap(pix)
        self._lbl_preview.set_canvas_preview_scale(cw, ch, nw, nh)
        if oob:
            self._lbl_warn.setText(
                "警告: 手動調整の結果、画像がキャンバス外にかかっている可能性があります。入稿前にご確認ください。"
            )
            self._lbl_warn.show()
        else:
            self._lbl_warn.hide()

    def _on_export(self) -> None:
        if not self._template or not self._source_rgba:
            QMessageBox.warning(self, "未入力", "テンプレートPSDと入力画像を指定してください。")
            return
        if not self._out_path:
            self._pick_out()
        if not self._out_path:
            return
        try:
            r = self._resolved()
            layer, li, ti, oob, _ = build_layer_and_overflow(
                self._source_rgba,
                r.width,
                r.height,
                float(self._sl_scale.value()),
                float(self._sl_ox.value()),
                float(self._sl_oy.value()),
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
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "書き出し失敗", str(e))
            traceback.print_exc()
            return
        msg = f"保存しました:\n{self._out_path}"
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
