"""架空の旅館データ（3館・50人）を作る。毎回同じ内容になるよう乱数の種を固定する。"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

BUILDINGS = ["本館", "別館", "離れ"]
BUILDING_SHORT = {"本館": "本", "別館": "別", "離れ": "離"}
ROLES = ["リーダー", "チェッカー", "クリーナー"]
WEEKDAYS = "月火水木金土日"

# 館ごとの部屋数（うち露天風呂付き）
ROOMS = {"本館": (50, 6), "別館": (35, 4), "離れ": (15, 15)}

# 祝日（期間の近くだけ）
HOLIDAYS = {dt.date(2026, 11, 23), dt.date(2026, 12, 23)}


@dataclass
class Staff:
    sid: str
    name: str
    home: str  # 主に働く館
    role: str
    groups: list[str]  # 雇用形態・新人など
    can_help: list[str]  # 応援に行ける館
    roten: str  # 露天風呂付き客室の清掃: ○ / △ / ""
    off_weekdays: list[int]  # 出ない曜日（0=月）
    max_consec: int  # 連勤の上限（必ず）
    week_days: int  # 週に働く日数の目安
    monthly_cap_days: int | None = None  # 扶養の範囲などの月の上限（日数）
    phone: bool = True  # スマホで希望を出すか


@dataclass
class Facility:
    start: dt.date
    days: int
    staff: list[Staff]
    prev: dict[str, list[str]]  # 前の期間の最後の7日: sid -> ["本館" or "休", ...]
    counts: dict[str, list[dict]]  # 館 -> 日ごとの {"out","stay","outin","roten"}
    requests: dict[tuple[str, int], str]  # (sid, day) -> "必ず休み" / "できれば休み"
    submitted: dict[str, dt.datetime | None]
    comments: dict[str, str] = field(default_factory=dict)

    @property
    def dates(self) -> list[dt.date]:
        return [self.start + dt.timedelta(days=i) for i in range(self.days)]


SURNAMES = [
    "佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤",
    "吉田", "山田", "佐々木", "山口", "松本", "井上", "木村", "林", "斎藤", "清水",
    "山崎", "森", "池田", "橋本", "阿部", "石川", "山下", "中島", "石井", "小川",
    "前田", "岡田", "長谷川", "藤田", "後藤", "近藤", "村上", "遠藤", "青木", "坂本",
    "斉藤", "福田", "太田", "西村", "藤井", "金子", "岡本", "藤原", "中野", "三浦",
]
GIVEN = ["花子", "幸子", "恵子", "美香", "由美", "陽子", "和子", "直美", "智子", "裕子"]


def fmt_date(d: dt.date) -> str:
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


def is_weekend(d: dt.date) -> bool:
    return d.weekday() >= 5 or d in HOLIDAYS


def make_facility(start: dt.date = dt.date(2026, 12, 4), days: int = 14, seed: int = 7) -> Facility:
    rnd = random.Random(seed)
    staff: list[Staff] = []
    plan = {"本館": 24, "別館": 16, "離れ": 10}
    n = 0
    for b, size in plan.items():
        for i in range(size):
            n += 1
            if i < 3:
                role = "リーダー"
            elif i < 3 + max(2, size // 6):
                role = "チェッカー"
            else:
                role = "クリーナー"
            emp = "社員" if role != "クリーナー" else rnd.choices(["社員", "パート", "派遣"], [3, 6, 1])[0]
            groups = [emp]
            if role == "クリーナー" and rnd.random() < 0.15:
                groups.append("新人")
            others = [x for x in BUILDINGS if x != b]
            can_help = rnd.sample(others, k=rnd.choice([0, 1, 1, 2])) if role != "リーダー" else []
            roten = "○" if (b == "離れ" or rnd.random() < 0.35) else ("△" if rnd.random() < 0.4 else "")
            if "新人" in groups:
                roten = ""
            off = rnd.sample(range(7), k=rnd.choice([0, 0, 1, 1, 2])) if emp == "パート" else []
            week = 5 if emp == "社員" else rnd.choice([3, 4, 4, 5])
            cap = 16 if emp == "パート" and rnd.random() < 0.4 else None
            staff.append(
                Staff(
                    sid=f"S{n:03d}",
                    name=f"{SURNAMES[n - 1]} {GIVEN[n % len(GIVEN)]}",
                    home=b,
                    role=role,
                    groups=groups,
                    can_help=can_help,
                    roten=roten,
                    off_weekdays=off,
                    max_consec=6 if emp == "社員" else 5,
                    week_days=week,
                    monthly_cap_days=cap,
                    phone=rnd.random() > 0.1,
                )
            )

    # 前の期間の最後の7日（連勤の判断に使う）
    prev: dict[str, list[str]] = {}
    for s in staff:
        row = []
        streak = rnd.randint(0, 4)
        for k in range(7):
            if streak >= 4 or rnd.random() < 0.3:
                row.append("休")
                streak = 0
            else:
                row.append(s.home)
                streak += 1
        prev[s.sid] = row

    fac = Facility(start=start, days=days, staff=staff, prev=prev, counts={}, requests={}, submitted={})

    # 予約から出した日ごとの清掃数
    for b, (rooms, roten_rooms) in ROOMS.items():
        rows = []
        for d in fac.dates:
            occ = 0.55 + (0.3 if d.weekday() in (4, 5) or d in HOLIDAYS else 0.0) + rnd.uniform(-0.1, 0.1)
            if d.month == 12 and d.day >= 12 and d.weekday() == 5:
                occ += 0.1  # 忙しい土曜
            occ = min(occ, 0.98)
            occupied = round(rooms * occ)
            out = round(occupied * rnd.uniform(0.45, 0.7))
            stay = occupied - out
            outin = round(out * rnd.uniform(0.3, 0.6))
            roten = min(roten_rooms, round(roten_rooms * occ))
            rows.append({"out": out, "stay": stay, "outin": outin, "roten": roten})
        fac.counts[b] = rows

    # 休みの希望（スマホ）
    busy_day = next(i for i, d in enumerate(fac.dates) if d.weekday() == 5 and d.day >= 12)
    for s in staff:
        if rnd.random() < 0.08:
            fac.submitted[s.sid] = None  # まだ出していない
            continue
        fac.submitted[s.sid] = dt.datetime(2026, 11, 20, 18, 0) + dt.timedelta(hours=rnd.randint(0, 100))
        k = rnd.choice([0, 1, 1, 2, 2, 3])
        for day in rnd.sample(range(days), k=k):
            fac.requests[(s.sid, day)] = "必ず休み"
        for day in rnd.sample(range(days), k=rnd.choice([0, 1, 2])):
            fac.requests.setdefault((s.sid, day), "できれば休み")
        if s.home == "本館" and rnd.random() < 0.3:
            fac.requests[(s.sid, busy_day)] = "必ず休み"  # 希望が集まる日を作る
        if rnd.random() < 0.15:
            fac.comments[s.sid] = rnd.choice(
                ["12/10は午前だけなら出られます", "年末は多めに入れます", "子どもの行事で土曜は休みたいです"]
            )
    return fac
