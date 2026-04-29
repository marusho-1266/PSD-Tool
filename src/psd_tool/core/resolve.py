from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from psd_tool.core.template import TemplateInfo


@dataclass
class ResolvedOutput:
    width: int
    height: int
    dpi: float
    source: str  # "screen" | "template"


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

    if override_on:
        if screen_w is not None and screen_h is not None and screen_w > 0 and screen_h > 0:
            dpi = float(screen_dpi) if screen_dpi and screen_dpi > 0 else t_dpi
            return ResolvedOutput(screen_w, screen_h, dpi, "screen")
        raise ValueError("マニュアル上書きがオンのときは、幅・高さに正の整数を入力してください。")

    return ResolvedOutput(int(template.width), int(template.height), t_dpi, "template")
