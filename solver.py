"""自動作成（OR-Tools CP-SAT）。

「必ず」は守るか、守れないときに大きな重みで数える。「なるべく」は小さな重みで数える。
休みにする方向の条件（休みの希望・出ない曜日・連勤の上限・月の上限）は必ず守る。
これらはどれも「休みにすれば満たせる」ので、案は必ず作れる。
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass

from ortools.sat.python import cp_model

from data import BUILDINGS, Facility, is_weekend
from demand import role_minimums

OFF = "休"

W = {
    "shortage": 1000,  # 必要人数の不足（必ず）
    "leader": 800,  # リーダーの不足（必ず）
    "checker": 400,  # チェッカーの不足（必ず）
    "roten": 600,  # 露天風呂付き客室を担当できる人がいない（必ず）
    "over": 10,  # 必要人数＋1人を超える（なるべく）
    "help": 20,  # 応援（なるべく少なく）
    "wish": 10,  # できれば休みに出勤（なるべく）
    "consec_soft": 8,  # 連勤の上限の1日前（なるべく）
    "week": 40,  # 週に働く日数の目安とのずれ（なるべく）
    "stone": 3,  # 飛び石の休み（なるべく）
    "fair_rest": 15,  # 社員の休みの日数の差（公平さ）
    "fair_weekend": 10,  # 土日祝の休みの差（公平さ）
    "rookie": 20,  # 新人3人以上が同じ館（なるべく）
    "tri": 5,  # △の人に露天風呂付き客室を入れる（なるべく少なく）
}


@dataclass
class Result:
    assign: dict[tuple[str, int], str]
    status: str
    seconds: float
    objective: float


def solve(
    fac: Facility,
    required: dict[str, list[int]],
    locked: dict[tuple[str, int], str] | None = None,
    buildings: list[str] | None = None,
    base: dict[tuple[str, int], str] | None = None,
    time_limit: float = 20.0,
    seed: int = 1,
) -> Result:
    """buildings を指定すると、その館の人だけ作り直す（ほかの館の人と応援は base のまま）。"""
    locked = dict(locked or {})
    target_b = set(buildings or BUILDINGS)
    if base and buildings:
        for s in fac.staff:
            if s.home not in target_b:
                for d in range(fac.days):
                    locked[(s.sid, d)] = base.get((s.sid, d), OFF)

    m = cp_model.CpModel()
    D = range(fac.days)
    x: dict[tuple[str, int, str], cp_model.IntVar] = {}
    work: dict[tuple[str, int], cp_model.LinearExpr] = {}
    by_id = {s.sid: s for s in fac.staff}

    for s in fac.staff:
        allowed = [s.home] + s.can_help
        for d in D:
            vs = []
            for b in allowed:
                v = m.NewBoolVar(f"x_{s.sid}_{d}_{b}")
                x[(s.sid, d, b)] = v
                vs.append(v)
            m.Add(sum(vs) <= 1)
            work[(s.sid, d)] = sum(vs)

    penalties = []

    def pen(weight, expr):
        penalties.append(weight * expr)

    # 固定（鍵・ほかの館）
    for (sid, d), val in locked.items():
        s = by_id[sid]
        for b in [s.home] + s.can_help:
            m.Add(x[(sid, d, b)] == (1 if val == b else 0))

    for s in fac.staff:
        # 必ず休み・出ない曜日
        for d, date in enumerate(fac.dates):
            if fac.requests.get((s.sid, d)) == "必ず休み" or date.weekday() in s.off_weekdays:
                if (s.sid, d) not in locked:
                    m.Add(work[(s.sid, d)] == 0)
        # 連勤の上限（前の期間の7日も含める）
        prev = [0 if v == OFF else 1 for v in fac.prev[s.sid]]
        seq = [("p", v) for v in prev] + [("x", d) for d in D]

        def val(item):
            return item[1] if item[0] == "p" else work[(s.sid, item[1])]

        for L, weight in ((s.max_consec, None), (s.max_consec - 1, W["consec_soft"])):
            for i in range(len(seq) - L):
                win = seq[i : i + L + 1]
                if all(it[0] == "p" for it in win):
                    continue
                total = sum(val(it) for it in win)
                if weight is None:
                    m.Add(total <= L)
                else:
                    o = m.NewIntVar(0, 1, "")
                    m.Add(total - L <= o)
                    pen(weight, o)
        # 法定の休み：14日で2日以上（4週4休の半分）
        m.Add(sum(work[(s.sid, d)] for d in D) <= fac.days - 2)
        # 月の上限（扶養の範囲など）。前の期間のうち同じ月の分も数える
        if s.monthly_cap_days:
            months = {}
            for d, date in enumerate(fac.dates):
                months.setdefault((date.year, date.month), []).append(d)
            for (y, mo), ds in months.items():
                prev_same = sum(
                    1
                    for k, v in enumerate(fac.prev[s.sid])
                    if v != OFF and (fac.start.toordinal() - 7 + k) >= dt.date(y, mo, 1).toordinal()
                )
                m.Add(sum(work[(s.sid, d)] for d in ds) + prev_same <= s.monthly_cap_days)
        # できれば休み
        for d in D:
            if fac.requests.get((s.sid, d)) == "できれば休み":
                pen(W["wish"], work[(s.sid, d)])
        # 応援
        for d in D:
            for b in s.can_help:
                pen(W["help"], x[(s.sid, d, b)])
        # 週の目安
        for w0 in range(0, fac.days, 7):
            ds = [d for d in D if w0 <= d < w0 + 7]
            dev = m.NewIntVar(0, 7, "")
            tot = sum(work[(s.sid, d)] for d in ds)
            target = round(s.week_days * len(ds) / 7)
            m.Add(tot - target <= dev)
            m.Add(target - tot <= dev)
            pen(W["week"], dev)
        # 飛び石（休→出→休）
        for d in range(1, fac.days - 1):
            st = m.NewBoolVar("")
            m.Add(st >= work[(s.sid, d)] - work[(s.sid, d - 1)] - work[(s.sid, d + 1)])
            pen(W["stone"], st)

    # 館ごと・日ごとの人数
    for b in BUILDINGS:
        for d in D:
            people = [(s, x[(s.sid, d, b)]) for s in fac.staff if (s.sid, d, b) in x]
            tot = sum(v for _, v in people)
            req = required[b][d]
            short = m.NewIntVar(0, req, "")
            m.Add(tot + short >= req)
            pen(W["shortage"], short)
            over = m.NewIntVar(0, 60, "")
            m.Add(tot - req - 1 <= over)
            pen(W["over"], over)
            mins = role_minimums(req)
            for role, key in (("リーダー", "leader"), ("チェッカー", "checker")):
                r = sum(v for s, v in people if s.role == role)
                sh = m.NewIntVar(0, mins[role], "")
                m.Add(r + sh >= mins[role])
                pen(W[key], sh)
            if fac.counts[b][d]["roten"] > 0:
                ok = sum(v for s, v in people if s.roten in ("○", "△"))
                sh = m.NewIntVar(0, 1, "")
                m.Add(ok + sh >= 1)
                pen(W["roten"], sh)
                for s, v in people:
                    if s.roten == "△":
                        pen(W["tri"], v)
            rookies = sum(v for s, v in people if "新人" in s.groups)
            ro = m.NewIntVar(0, 20, "")
            m.Add(rookies - 2 <= ro)
            pen(W["rookie"], ro)

    # 公平さ
    regulars = [s for s in fac.staff if "社員" in s.groups]
    if regulars:
        rests = [fac.days - sum(work[(s.sid, d)] for d in D) for s in regulars]
        hi, lo = m.NewIntVar(0, fac.days, ""), m.NewIntVar(0, fac.days, "")
        for r in rests:
            m.Add(hi >= r)
            m.Add(lo <= r)
        pen(W["fair_rest"], hi - lo)
    wk = [d for d, date in enumerate(fac.dates) if is_weekend(date)]
    if wk:
        wrests = [len(wk) - sum(work[(s.sid, d)] for d in wk) for s in fac.staff if not s.off_weekdays]
        hi, lo = m.NewIntVar(0, len(wk), ""), m.NewIntVar(0, len(wk), "")
        for r in wrests:
            m.Add(hi >= r)
            m.Add(lo <= r)
        pen(W["fair_weekend"], hi - lo)

    m.Minimize(sum(penalties))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = 8
    solver.parameters.random_seed = seed
    t0 = time.time()
    st = solver.Solve(m)
    secs = time.time() - t0
    assign = {}
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for s in fac.staff:
            for d in D:
                assign[(s.sid, d)] = OFF
                for b in [s.home] + s.can_help:
                    if solver.Value(x[(s.sid, d, b)]):
                        assign[(s.sid, d)] = b
    return Result(
        assign=assign,
        status={cp_model.OPTIMAL: "最適", cp_model.FEASIBLE: "時間内のいちばんよい案"}.get(st, "作れませんでした"),
        seconds=secs,
        objective=solver.ObjectiveValue() if assign else 0,
    )
