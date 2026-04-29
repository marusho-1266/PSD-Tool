from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from psd_tool.core.template import TemplateInfo


@dataclass
class ResolvedOutput:
    width: int
    height: int
    dpi: float
    source: str  # "screen" | "template"


def _positive_dimension_or_raise(dim_name: str, value: object) -> int:
    """
    テンプレート由来のキャンバス寸法を検証して int で返す。
    None・非数・0 以下を拒否する。
    """
    if value is None:
        raise ValueError(
            f"テンプレートPSDの{dim_name}が取得できませんでした（未設定）。"
            "有効な PSD を選び直してください。"
        )
    if isinstance(value, bool):
        raise ValueError(f"テンプレートPSDの{dim_name}が無効です（論理値）。")
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"テンプレートPSDの{dim_name}が無効です（非数）。")
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError) as e:
        raise ValueError(
            f"テンプレートPSDの{dim_name}が正の整数に変換できません: {value!r}"
        ) from e
    if n <= 0:
        raise ValueError(
            f"テンプレートPSDの{dim_name}は正の値である必要があります（取得値: {value!r}）。"
        )
    return n


def resolve_dimensions(
    override_on: bool,
    screen_w: Optional[int],
    screen_h: Optional[int],
    screen_dpi: Optional[float],
    template: TemplateInfo,
) -> ResolvedOutput:
    """
    §8.2 / §8.2.1: 上書きオンのとき 1. 画面、オフのとき 2→3（v1 では保存2は未実装のため 3. テンプレのみ）。
    """
    t_dpi = float(template.dpi) if template.dpi and template.dpi > 0 else 300.0
    if isinstance(t_dpi, float) and (math.isnan(t_dpi) or math.isinf(t_dpi)):
        t_dpi = 300.0

    if override_on:
        if screen_w is not None and screen_h is not None and screen_w > 0 and screen_h > 0:
            dpi = float(screen_dpi) if screen_dpi and screen_dpi > 0 else t_dpi
            if isinstance(dpi, float) and (math.isnan(dpi) or math.isinf(dpi)):
                dpi = t_dpi
            return ResolvedOutput(screen_w, screen_h, dpi, "screen")
        raise ValueError("マニュアル上書きがオンのときは、幅・高さに正の整数を入力してください。")

    tw = _positive_dimension_or_raise("幅", template.width)
    th = _positive_dimension_or_raise("高さ", template.height)

    return ResolvedOutput(tw, th, t_dpi, "template")
