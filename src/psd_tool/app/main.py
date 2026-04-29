from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PIL import Image, ImageQt

try:
    from PIL import ImageResampling

    _BILINEAR = ImageResampling.BILINEAR
except ImportError:
    _BILINEAR = Image.BILINEAR  # type: ignore[attr-defined]
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
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
from psd_tool.core.export import build_layer_and_overflow, composite_preview_rgb, write_psd
from psd_tool.core.images import heif_available, open_image_pil
from psd_tool.core.resolve import resolve_dimensions, ResolvedOutput
from psd_tool.core.template import read_template_psd, TemplateInfo


def _qimage_from_pil(im: Image.Image):
    if im.mode != "RGB":
        im = im.convert("RGB")
    return ImageQt.ImageQt(im)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"PSDリサイズ出力ツール v{__version__}")
        self.setMinimumSize(900, 600)

        self._template_path: str | None = None
        self._image_path: str | None = None
        self._out_path: str | None = None
        self._template: TemplateInfo | None = None
        self._source_rgba: Image.Image | None = None
        self._io_warn: str | None = None

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
        rl.addWidget(QLabel("プレビュー (白背景 / contain)"))
        self._lbl_preview = QLabel("テンプレートと画像を指定してください。")
        self._lbl_preview.setAlignment(Qt.AlignCenter)
        self._lbl_preview.setMinimumSize(400, 400)
        self._lbl_preview.setStyleSheet("background: #e8e8e8;")
        self._lbl_preview.setScaledContents(False)
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
        self._ed_template.setText(path)
        self._sp_w.setValue(t.width)
        self._sp_h.setValue(t.height)
        d = int(round(t.dpi)) if t.dpi and t.dpi > 0 else 300
        self._sp_dpi.setValue(d)
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
        try:
            layer, li, ti, oob, _ = build_layer_and_overflow(
                self._source_rgba,
                cw,
                ch,
                float(self._sl_scale.value()),
                float(self._sl_ox.value()),
                float(self._sl_oy.value()),
            )
            prev = composite_preview_rgb(layer, li, ti, cw, ch)
        except Exception as e:  # noqa: BLE001
            self._lbl_preview.setText(f"プレビュー失敗: {e}")
            traceback.print_exc()
            return
        # 枠内に縮小表示
        max_w, max_h = 720, 720
        ratio = min(max_w / cw, max_h / ch, 1.0) if cw and ch else 1.0
        nw, nh = max(1, int(cw * ratio)), max(1, int(ch * ratio))
        small = prev.resize((nw, nh), _BILINEAR)
        from PySide6.QtGui import QPixmap

        pix = QPixmap.fromImage(_qimage_from_pil(small))
        self._lbl_preview.setFixedSize(nw, nh)
        self._lbl_preview.setPixmap(pix)
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
