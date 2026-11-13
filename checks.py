"""出来上がった勤務表を確かめて、守れなかった条件の一覧と公平さの数字を出す。"""
from __future__ import annotations

from dataclasses import dataclass

from data import BUILDINGS, Facility, fmt_date, is_weekend
from demand import role_minimums

OFF = "休"


@dataclass
class Issue:
    level: str  # 必ず / なるべく
    kind: str  # 人数・役割・露天・連勤・休み・応援・週の目安・新人
    building: str
    day: int | None
    sid: str | None
    text: str


def daily_counts(fac: Facility, assign: dict, b: str, d: int) -> dict[str, int]:
    people = [s for s in fac.staff if assign.get((s.sid, d)) == b]
    return {
        "出勤": len(people),
        "リーダー": sum(s.role == "リーダー" for s in people),
        "チェッカー": sum(s.role == "チェッカー" for s in people),
        "露天": sum(s.roten in ("○", "△") for s in people),
        "新人": sum("新人" in s.groups for s in people),
        "応援": sum(s.home != b for s in people),
    }


def find_issues(fac: Facility, assign: dict, required: dict[str, list[int]]) -> list[Issue]:
    issues: list[Issue] = []
    dates = fac.dates
    for b in BUILDINGS:
        for d in range(fac.days):
            c = daily_counts(fac, assign, b, d)
            req = required[b][d]
            day = fmt_date(dates[d])
            if c["出勤"] < req:
                issues.append(Issue("必ず", "人数", b, d, None, f"{day} {b}：必要 {req}人に {req - c['出勤']}人足りません（出勤 {c['出勤']}人）"))
            elif c["出勤"] > req + 1:
                issues.append(Issue("なるべく", "人数", b, d, None, f"{day} {b}：必要 {req}人より {c['出勤'] - req}人多いです"))
            mins = role_minimums(req)
            for role in ("リーダー", "チェッカー"):
                if c[role] < mins[role]:
                    issues.append(Issue("必ず", "役割", b, d, None, f"{day} {b}：{role}が {mins[role] - c[role]}人足りません"))
            if fac.counts[b][d]["roten"] > 0 and c["露天"] == 0:
                issues.append(Issue("必ず", "露天", b, d, None, f"{day} {b}：露天風呂付き客室を担当できる人がいません"))
            if c["新人"] >= 3:
                issues.append(Issue("なるべく", "新人", b, d, None, f"{day} {b}：新人が {c['新人']}人います"))

    for s in fac.staff:
        row = [assign.get((s.sid, d), OFF) for d in range(fac.days)]
        # 連勤（前の期間の7日から続けて数える）
        streak = 0
        for v in fac.prev[s.sid]:
            streak = streak + 1 if v != OFF else 0
        for d, v in enumerate(row):
            streak = streak + 1 if v != OFF else 0
            if streak == s.max_consec:
                issues.append(Issue("なるべく", "連勤", s.home, d, s.sid, f"{s.name}：{fmt_date(dates[d])}で {streak}日続けての出勤です（上限 {s.max_consec}日）"))
            if streak > s.max_consec:
                issues.append(Issue("必ず", "連勤", s.home, d, s.sid, f"{s.name}：{fmt_date(dates[d])}で連勤の上限 {s.max_consec}日を超えています"))
        for d, v in enumerate(row):
            r = fac.requests.get((s.sid, d))
            if v != OFF and r == "必ず休み":
                issues.append(Issue("必ず", "休み", s.home, d, s.sid, f"{s.name}：{fmt_date(dates[d])}は「必ず休み」の希望ですが出勤です"))
            if v != OFF and r == "できれば休み":
                issues.append(Issue("なるべく", "休み", s.home, d, s.sid, f"{s.name}：{fmt_date(dates[d])}は「できれば休み」の希望ですが出勤です"))
            if v not in (OFF, s.home):
                issues.append(Issue("なるべく", "応援", v, d, s.sid, f"{s.name}：{fmt_date(dates[d])}は{s.home}から{v}へ応援です"))
        for w0 in range(0, fac.days, 7):
            ds = range(w0, min(w0 + 7, fac.days))
            worked = sum(row[d] != OFF for d in ds)
            target = round(s.week_days * len(ds) / 7)
            if abs(worked - target) >= 2:
                issues.append(Issue("なるべく", "週の目安", s.home, w0, s.sid, f"{s.name}：{fmt_date(dates[w0])}からの週は {worked}日（目安 {target}日）"))
    return issues


def fairness(fac: Facility, assign: dict) -> dict[str, str]:
    reg = [s for s in fac.staff if "社員" in s.groups]
    rests = [sum(assign.get((s.sid, d)) == OFF for d in range(fac.days)) for s in reg]
    wk = [d for d, date in enumerate(fac.dates) if is_weekend(date)]
    wrests = [sum(assign.get((s.sid, d)) == OFF for d in wk) for s in fac.staff if not s.off_weekdays]
    want = [(sid, d) for (sid, d), r in fac.requests.items() if r == "できれば休み"]
    got = sum(assign.get(k) == OFF for k in want)
    return {
        "社員の休み": f"{min(rests)}〜{max(rests)}日" if rests else "-",
        "土日祝の休み": f"{min(wrests)}〜{max(wrests)}日" if wrests else "-",
        "できれば休み": f"{got}/{len(want)}件" if want else "-",
    }
