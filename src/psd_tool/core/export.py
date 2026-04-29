from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

try:
    from PIL import ImageResampling  # Pillow >= 9.1

    _LANCZOS = ImageResampling.LANCZOS
except ImportError:
    _LANCZOS = Image.LANCZOS  # type: ignore[attr-defined]
from psd_tools import PSDImage
from psd_tools.constants import Resource
from psd_tools.psd.image_resources import ImageResource

from psd_tool.core.layout import ContainResult, apply_manual, compute_contain, visible_alpha_bbox_rgba
from psd_tool.core.resolve import ResolvedOutput
from psd_tool.core.template import resolution_info_from_dpi


def build_layer_and_overflow(
    source_rgba: Image.Image,
    canvas_w: int,
    canvas_h: int,
    manual_scale_pct: float,
    off_x: float,
    off_y: float,
) -> tuple[Image.Image, int, int, bool, ContainResult]:
    """
    リサイズ後の RGBA レイヤ画像と整数座標、はみ出し、contain 情報。
    """
    iw, ih = source_rgba.size
    c = compute_contain(canvas_w, canvas_h, iw, ih)
    l, t, rw, rh = apply_manual(c, manual_scale_pct, off_x, off_y)
    vis = visible_alpha_bbox_rgba(source_rgba)

    rw_i = max(1, int(round(rw)))
    rh_i = max(1, int(round(rh)))
    scaled = source_rgba.resize((rw_i, rh_i), _LANCZOS)
    if scaled.mode != "RGBA":
        scaled = scaled.convert("RGBA")

    li = int(round(l))
    ti = int(round(t))

    from psd_tool.core.layout import is_overflow  # 循環避け

    oob = is_overflow(
        l, t, float(rw_i), float(rh_i), canvas_w, canvas_h, vis
    )
    return scaled, li, ti, oob, c


def composite_preview_rgb(
    layer_rgba: Image.Image,
    left: int,
    top: int,
    canvas_w: int,
    canvas_h: int,
) -> Image.Image:
    """プレビュー用の白背景合成（描画は write_psd と同趣旨）。"""
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
    canvas.paste(layer_rgba, (left, top), layer_rgba)
    return canvas


def blend_template_guides_over_rgb(
    base_rgb: Image.Image,
    template_rgb: Image.Image,
    opacity_pct: int,
) -> Image.Image:
    """
    テンプレート平坦画像を前面に重ねる。白に近い画素はベースをそのまま見せ、線や文字のみ目立たせる。
    opacity_pct はガイドの最大不透過（1〜100）。
    """
    base_rgb = base_rgb.convert("RGB")
    template_rgb = template_rgb.convert("RGB")
    if base_rgb.size != template_rgb.size:
        template_rgb = template_rgb.resize(base_rgb.size, _LANCZOS)
    op = max(1, min(100, opacity_pct))
    gray = ImageOps.grayscale(template_rgb)
    # テンプレが白い領域はマスク 0（ベース）、線や赤トンボ等はマスク大
    mask = gray.point(lambda x: (255 - x) * op // 100)
    return Image.composite(template_rgb, base_rgb, mask)


def write_psd(
    layer_rgba: Image.Image,
    left: int,
    top: int,
    canvas_w: int,
    canvas_h: int,
    out_path: str | Path,
    dpi: float,
) -> None:
    """白背景ドキュメント + 写真レイヤー（仕様 §5.1 の 背景 + 画像）。"""
    psd = PSDImage.new("RGB", (canvas_w, canvas_h), color=(255, 255, 255))
    psd.create_pixel_layer(layer_rgba, name="Image", left=left, top=top)
    psd.image_resources[Resource.RESOLUTION_INFO] = ImageResource(
        key=Resource.RESOLUTION_INFO,
        data=resolution_info_from_dpi(dpi),
    )
    psd.save(str(out_path))
