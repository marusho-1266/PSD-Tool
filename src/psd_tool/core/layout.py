from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

@dataclass
class ContainResult:
    scale: float
    """contain の min(cw/iw, ch/ih)。"""
    resized_w: float
    resized_h: float
    left: float
    top: float
    """中央配置直後（手動拡大率・オフセット前）。"""


def compute_contain(
    canvas_w: int, canvas_h: int, image_w: int, image_h: int
) -> ContainResult:
    if image_w <= 0 or image_h <= 0 or canvas_w <= 0 or canvas_h <= 0:
        raise ValueError("幅・高さは正の整数である必要があります。")
    s = min(canvas_w / image_w, canvas_h / image_h)
    rw = image_w * s
    rh = image_h * s
    left = (canvas_w - rw) / 2.0
    top = (canvas_h - rh) / 2.0
    return ContainResult(scale=s, resized_w=rw, resized_h=rh, left=left, top=top)


def apply_manual(
    c: ContainResult,
    manual_scale_pct: float,
    off_x: float,
    off_y: float,
) -> tuple[float, float, float, float]:
    """
    手動拡大率 (%)・オフセットを適用した最終的な枠
    (left, top, width, height)。画像の中心基準で拡大する。
    """
    m = manual_scale_pct / 100.0
    if m <= 0:
        raise ValueError("拡大率は正である必要があります。")
    rw, rh = c.resized_w * m, c.resized_h * m
    left = c.left + (c.resized_w - rw) / 2.0
    top = c.top + (c.resized_h - rh) / 2.0
    left += off_x
    top += off_y
    return left, top, rw, rh


def visible_alpha_bbox_rgba(rgba: Image.Image) -> tuple[int, int, int, int] | None:
    """透過以外がない場合は None。"""
    if rgba.mode != "RGBA":
        return rgba.getbbox()
    alpha = rgba.split()[3]
    return alpha.getbbox()


def is_overflow(
    left: float,
    top: float,
    w: float,
    h: float,
    canvas_w: int,
    canvas_h: int,
    visible_bbox: tuple[int, int, int, int] | None,
) -> bool:
    """
    幾何は画像全体の枠。全面透過などで visible_bbox が None の場合は
    仕様 §10.2 に従いはみ出し警告対象外。
    """
    if visible_bbox is None:
        wpx = int(round(w))
        hpx = int(round(h))
        if wpx <= 0 or hpx <= 0:
            return False
    l, t, r, b = left, top, left + w, top + h
    return l < 0 or t < 0 or r > canvas_w or b > canvas_h
