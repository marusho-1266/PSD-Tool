from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from psd_tools import PSDImage
from psd_tools.constants import Resource


@dataclass
class TemplateInfo:
    width: int
    height: int
    dpi: float | None
    path: str


def _resolution_info_to_dpi(horizontal: int) -> float:
    """
    ResoulutionInfo.horizontal は 16.16 固定小数点 (pixels / inch) のことが多い。
    """
    return horizontal / 65536.0


def read_template_psd(path: str | Path) -> TemplateInfo:
    path = Path(path)
    psd = PSDImage.open(str(path))
    w, h = int(psd.width), int(psd.height)
    dpi: float | None = None
    try:
        if Resource.RESOLUTION_INFO in psd.image_resources:
            data = psd.image_resources.get_data(Resource.RESOLUTION_INFO)
            if data is not None and hasattr(data, "horizontal"):
                horiz = int(getattr(data, "horizontal", 0) or 0)
                if horiz > 0:
                    dpi = _resolution_info_to_dpi(horiz)
    except Exception:
        dpi = None
    return TemplateInfo(width=w, height=h, dpi=dpi, path=str(path))


def composite_template_overlay_rgb(path: str | Path) -> Image.Image:
    """
    テンプレート PSD を可視レイヤで平坦化した RGB 画像。
    プレビューで塗り足し線・トンボ等と入力画像を重ねる用。書き出し PSD には含めない。
    """
    resolved = Path(path).resolve()
    psd = PSDImage.open(str(resolved))
    img = psd.composite()
    if img is None:
        raise RuntimeError(
            f"テンプレート PSD の合成に失敗しました（composite が None）。ファイル: {resolved}"
        )
    if img.mode == "CMYK":
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return img


def resolution_info_from_dpi(dpi: float):
    """
    psd-tools の ResoulutionInfo を返す。horizontal_unit: 1 = inch。
    """
    from psd_tools.psd.image_resources import ResoulutionInfo  # 公式表記

    h = int(round(dpi * 65536.0))  # 16.16 fixed, pixels / inch
    v = h
    return ResoulutionInfo(
        horizontal=h,
        horizontal_unit=1,
        width_unit=1,
        vertical=v,
        vertical_unit=1,
        height_unit=1,
    )
