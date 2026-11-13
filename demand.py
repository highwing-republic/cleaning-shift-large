"""清掃数から必要人数を出す。"""
from __future__ import annotations

import math

# 1室あたりの清掃時間（分）の目安と、1人が1日に清掃できる時間
MINUTES = {"out": 90, "stay": 30, "outin_extra": 20, "roten_extra": 40}
MINUTES_PER_PERSON = 300  # 1人が1日に客室の清掃に使える時間
# 客室のほかに毎日いる人数（大浴場・廊下・リネンなど）
COMMON = {"本館": 3, "別館": 2, "離れ": 1}
MIN_STAFF = {"本館": 4, "別館": 3, "離れ": 2}


def required_people(b: str, c: dict) -> int:
    minutes = (
        c["out"] * MINUTES["out"]
        + c["stay"] * MINUTES["stay"]
        + c["outin"] * MINUTES["outin_extra"]
        + c["roten"] * MINUTES["roten_extra"]
    )
    return max(MIN_STAFF[b], math.ceil(minutes / MINUTES_PER_PERSON) + COMMON[b])


def role_minimums(total: int) -> dict[str, int]:
    """全員の人数のうちの役割の人数（内数）。"""
    return {"リーダー": 1, "チェッカー": 1 if total < 10 else 2}


def build_required(counts: dict[str, list[dict]], manual: dict[tuple[str, int], int] | None = None):
    """館 -> 日ごとの必要人数。manual に手直しがあればそれを使う（自動で上書きしない）。"""
    manual = manual or {}
    out = {}
    for b, rows in counts.items():
        out[b] = [manual.get((b, i), required_people(b, c)) for i, c in enumerate(rows)]
    return out
