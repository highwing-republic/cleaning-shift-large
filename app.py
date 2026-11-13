"""清掃勤務表アプリ 大規模版の試作（3館・50人の架空データ）。

起動: streamlit run app.py
データは保存しません（ブラウザを読み込み直すと最初に戻ります）。
"""
from __future__ import annotations

import datetime as dt
import io

import pandas as pd
import streamlit as st

from checks import daily_counts, fairness, find_issues
from data import BUILDING_SHORT, BUILDINGS, WEEKDAYS, fmt_date, is_weekend, make_facility
from demand import build_required, required_people, role_minimums
from solver import OFF, solve

st.set_page_config(page_title="清掃勤務表 大規模版（試作）", layout="wide")

COLORS = {"本館": "#dbeafe", "別館": "#dcfce7", "離れ": "#fef3c7", OFF: "#f3f4f6"}
STEPS = ["① 期間を選ぶ", "② 予約", "③ 休み・人数の確認", "④ 作成・手直し", "⑤ 確定・公開"]
USERS = {
    "客室係長（施設の管理者）": {"buildings": BUILDINGS, "edit": True},
    "本館リーダー（本館の作成者）": {"buildings": ["本館"], "edit": True},
    "フロント（全館を見るだけ）": {"buildings": BUILDINGS, "edit": False},
}


# ---------- 状態 ----------
def init():
    ss = st.session_state
    if "fac" in ss:
        return
    ss.fac = make_facility()
    ss.manual_req = {}  # (館, 日) -> 人数（手直し）
    ss.assign = None
    ss.edited = set()  # 手直ししたマス
    ss.step = 0
    ss.confirmed = []  # 確定した版
    ss.result = None
    ss.highlight = None


init()
ss = st.session_state
fac = ss.fac
dates = fac.dates
period_text = f"{fmt_date(dates[0])}〜{fmt_date(dates[-1])}（{fac.days}日間）"


def required():
    return build_required(fac.counts, ss.manual_req)


# ---------- 左メニュー ----------
if ss.get("_goto"):
    ss.page = ss.pop("_goto")

with st.sidebar:
    st.markdown("**清掃勤務表 大規模版（試作）**")
    user = st.selectbox("ログイン中（試作用の切り替え）", list(USERS), key="user")
    perm = USERS[user]
    choices = (["すべての館"] if len(perm["buildings"]) > 1 else []) + perm["buildings"]
    bsel = st.selectbox("館", choices, key="bsel")
    page = st.radio("メニュー", ["ホーム", "勤務表づくり（5ステップ）", "スタッフ管理", "スタッフのスマホ画面（見本）"], key="page")
    if ss.assign is not None and not ss.confirmed:
        st.caption(f"作成中：{period_text}　いま {STEPS[ss.step]}")
    if user.startswith("本館リーダー"):
        st.info("別館は 田中さん が編集中です（15:20から）。見るだけなら開けます。", icon="🔒")
    st.caption("データは保存しません。読み込み直すと最初に戻ります。")

shown_buildings = BUILDINGS if bsel == "すべての館" else [bsel]
can_edit = perm["edit"]


def step_bar():
    marks = []
    for i, name in enumerate(STEPS):
        if i < ss.step:
            marks.append(f"✓ {name}")
        elif i == ss.step:
            marks.append(f"**{name}**")
        else:
            marks.append(name)
    st.markdown(f"#### {period_text}　{'・'.join(shown_buildings) if bsel != 'すべての館' else '全館'}")
    st.markdown("　→　".join(marks))
    st.divider()


def next_button(label: str, disabled: bool = False, help: str | None = None):
    if st.button(label, type="primary", disabled=disabled or not can_edit, help=help, width="stretch"):
        ss.step += 1
        st.rerun()


# ---------- ホーム ----------
def page_home():
    st.title("清掃勤務表 大規模版（試作）")
    st.write("休みの希望を確かめて、全館の勤務表をつくりましょう。")
    rows = []
    for b in BUILDINGS:
        members = [s for s in fac.staff if s.home == b]
        sub = sum(fac.submitted[s.sid] is not None for s in members)
        rows.append(
            {
                "館": b,
                "スタッフ": f"{len(members)}人",
                "休みの希望": f"{sub}/{len(members)}人が提出" + (f"（あと{len(members) - sub}人）" if sub < len(members) else ""),
                "作成": "まだ" if ss.assign is None else STEPS[ss.step],
                "確定": "確定済み" if ss.confirmed else "まだ",
            }
        )
    st.subheader("いまの状況")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    label = "作成途中の勤務表を続ける" if ss.assign is not None else f"勤務表をつくる（{fmt_date(dates[0])}から）"
    if st.button(label, type="primary", disabled=not can_edit, width="stretch"):
        ss._goto = "勤務表づくり（5ステップ）"
        st.rerun()
    if not can_edit:
        st.caption("見るだけの権限です。勤務表は「勤務表づくり」の④⑤で見られます。")


# ---------- ① ----------
def step1():
    st.write("作る期間を選びます。全館で同じ期間です。")
    st.date_input("始まりの日", value=dates[0], disabled=True, format="YYYY/MM/DD")
    st.info(f"{period_text}の分を、本館・別館・離れまとめて作ります。前の期間は {fmt_date(dates[0] - dt.timedelta(days=1))} まで確定済みです。")
    next_button("次へ：② 予約")


# ---------- ② ----------
def step2():
    st.write("予約から出した、日ごとの清掃の数です。数を直すと必要人数も変わります。")
    for b in shown_buildings:
        st.markdown(f"**{b}**")
        df = pd.DataFrame(fac.counts[b])
        df.columns = ["アウト", "連泊", "アウトイン", "露天風呂付き"]
        df.insert(0, "日付", [fmt_date(d) for d in dates])
        df["必要人数（自動）"] = [required_people(b, c) for c in fac.counts[b]]
        edited = st.data_editor(
            df,
            hide_index=True,
            disabled=["日付", "必要人数（自動）"] if can_edit else True,
            key=f"counts_{b}",
            width="stretch",
        )
        for i, r in edited.iterrows():
            fac.counts[b][i] = {"out": int(r["アウト"]), "stay": int(r["連泊"]), "outin": int(r["アウトイン"]), "roten": int(r["露天風呂付き"])}
    st.caption("必要人数＝（アウト90分・連泊30分・アウトインは＋20分・露天風呂付きは＋40分）÷1人300分 ＋ 大浴場や廊下の人数。目安は館ごとに直せる想定です。")
    next_button("次へ：③ 休み・人数の確認")


# ---------- ③ ----------
def step3():
    st.markdown("##### 休みの希望")
    members = [s for s in fac.staff if s.home in shown_buildings]
    sub = [s for s in members if fac.submitted[s.sid] is not None]
    st.write(f"締切 11/25(水) 18:00　提出 **{len(sub)}/{len(members)}人**")
    missing = [s for s in members if fac.submitted[s.sid] is None]
    if missing:
        c1, c2 = st.columns([3, 1])
        c1.warning("まだ出していない人：" + "、".join(f"{s.name}（{s.home}）" for s in missing))
        if c2.button("まだの人に知らせる", disabled=not can_edit):
            st.toast(f"{len(missing)}人に知らせました（試作なので送られません）")
    no_phone = [s for s in members if not s.phone]
    if no_phone:
        st.caption("スマホを使わない人（代わりに入れる）：" + "、".join(s.name for s in no_phone))

    req = required()
    st.markdown("##### 希望が集まっている日")
    crowded = []
    for b in shown_buildings:
        for d, date in enumerate(dates):
            hard = [s for s in fac.staff if s.home == b and fac.requests.get((s.sid, d)) == "必ず休み"]
            avail = [s for s in fac.staff if s.home == b and fac.requests.get((s.sid, d)) != "必ず休み" and date.weekday() not in s.off_weekdays]
            if len(avail) - req[b][d] < 4 or len(hard) >= 5:
                crowded.append(f"{fmt_date(date)} {b}：必ず休み {len(hard)}人（出られる人 {len(avail)}人、必要 {req[b][d]}人）")
    st.write("\n".join(f"- {c}" for c in crowded) if crowded else "特にありません。")

    if fac.comments:
        with st.expander(f"スタッフのひとこと（{sum(1 for k in fac.comments if any(s.sid == k for s in members))}件）"):
            for s in members:
                if s.sid in fac.comments:
                    st.write(f"- {s.name}（{s.home}）：{fac.comments[s.sid]}")

    st.markdown("##### 必要人数")
    st.write("予約から自動で出した人数です。日ごとに直せます。直した日は自動で上書きしません。")
    for b in shown_buildings:
        auto = [required_people(b, c) for c in fac.counts[b]]
        df = pd.DataFrame(
            {fmt_date(d): [req[b][i]] for i, d in enumerate(dates)}, index=[f"{b} 必要人数"]
        )
        ed = st.data_editor(df, key=f"req_{b}", disabled=not can_edit, width="stretch")
        for i, d in enumerate(dates):
            v = int(ed.iloc[0][fmt_date(d)])
            if v != auto[i]:
                ss.manual_req[(b, i)] = v
            else:
                ss.manual_req.pop((b, i), None)
        marks = [fmt_date(dates[i]) for (bb, i) in ss.manual_req if bb == b]
        mins = role_minimums(max(req[b]))
        st.caption(
            f"{b}：各日の人数のうち、リーダー{mins['リーダー']}人・チェッカー1人以上（10人以上の日は2人）。"
            + (f"　手直し：{'、'.join(marks)}" if marks else "")
        )
    next_button("次へ：④ 作成・手直し")


# ---------- ④ ----------
def styled_grid(assign, staff, highlight):
    cols_prev = [f"前{7 - k}" for k in range(7)]
    cols = [fmt_date(d) for d in dates]
    data = []
    for s in staff:
        row = {"名前": s.name, "館": s.home, "役割": s.role}
        for k, c in enumerate(cols_prev):
            v = fac.prev[s.sid][k]
            row[c] = BUILDING_SHORT.get(v, v)
        for d, c in enumerate(cols):
            v = assign[(s.sid, d)]
            text = BUILDING_SHORT.get(v, v)
            r = fac.requests.get((s.sid, d))
            if r == "必ず休み":
                text += "◆"
            elif r == "できれば休み":
                text += "◇"
            if (s.sid, d) in ss.edited:
                text += "・"
            row[c] = text
        row["出勤"] = sum(assign[(s.sid, d)] != OFF for d in range(fac.days))
        data.append(row)
    df = pd.DataFrame(data)
    rev = {v: k for k, v in BUILDING_SHORT.items()}

    def color(v):
        if not isinstance(v, str) or not v:
            return ""
        b = rev.get(v[0], OFF if v.startswith(OFF) else None)
        return f"background-color: {COLORS[b]}" if b else ""

    sty = df.style.map(color, subset=cols_prev + cols)
    sty = sty.map(lambda v: "color: #9ca3af", subset=cols_prev)
    if highlight:
        sid, d = highlight
        if d is not None:
            sty = sty.map(lambda v: "background-color: #fb923c; font-weight: bold", subset=pd.IndexSlice[:, [cols[d]]] if sid is None else pd.IndexSlice[df.index[[s.sid == sid for s in staff]], [cols[d]]])
    return sty, df


def totals_table(assign, req):
    rows = {}
    for b in shown_buildings:
        out, need, diff = [], [], []
        for d in range(fac.days):
            c = daily_counts(fac, assign, b, d)
            out.append(c["出勤"])
            need.append(req[b][d])
            diff.append(c["出勤"] - req[b][d])
        rows[f"{b} 出勤"] = out
        rows[f"{b} 必要"] = need
        rows[f"{b} 過不足"] = diff
    df = pd.DataFrame(rows, index=[fmt_date(d) for d in dates]).T

    def col(v, name):
        if "過不足" not in name:
            return ""
        if v < 0:
            return "background-color: #fecaca; color: #991b1b; font-weight: bold"
        if v > 1:
            return "background-color: #bfdbfe"
        return "background-color: #dcfce7"

    return df.style.apply(lambda r: [col(v, r.name) for v in r], axis=1)


def step4():
    req = required()
    c1, c2, c3 = st.columns([2, 2, 2])
    scope = c1.radio("作る範囲", ["全館まとめて（応援も自動）", "表示中の館だけ"], horizontal=False, disabled=not can_edit)
    keep = c2.checkbox("手直ししたマスは残して作り直す", value=True, disabled=ss.assign is None or not can_edit)
    other = c3.button("別の案を作る", disabled=ss.assign is None or not can_edit)
    main_label = "自動でつくる" if ss.assign is None else "もう一度つくる"
    go = st.button(main_label, type="primary" if ss.assign is None else "secondary", disabled=not can_edit, width="stretch")
    if go or other:
        locked = {k: ss.assign[k] for k in ss.edited} if (keep and ss.assign) else {}
        only = shown_buildings if scope.startswith("表示中") and bsel != "すべての館" else None
        with st.spinner("作成中です（50人で数秒〜20秒ほど）…"):
            res = solve(fac, req, locked=locked, buildings=only, base=ss.assign, time_limit=20, seed=(ss.get("seed", 1) + (1 if other else 0)))
        ss.seed = ss.get("seed", 1) + (1 if other else 0)
        if not res.assign:
            st.error("案を作れませんでした。条件を見直してください。")
            return
        ss.assign = res.assign
        ss.result = res
        if not keep:
            ss.edited = set()
        ss.highlight = None
        st.rerun()

    if ss.assign is None:
        st.info("「自動でつくる」を押すと、全館の案を作ります。")
        return
    res = ss.result
    st.caption(f"作成にかかった時間 {res.seconds:.1f}秒（{res.status}）")

    issues = find_issues(fac, ss.assign, req)
    issues = [i for i in issues if i.building in shown_buildings]
    must = [i for i in issues if i.level == "必ず"]
    should = [i for i in issues if i.level == "なるべく"]

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("必ず（赤）", f"{len(must)}件")
    m2.metric("なるべく（黄）", f"{len(should)}件")
    fair = fairness(fac, ss.assign)
    helps = {"社員の休み": "14日のうちの休みの日数の、いちばん少ない人と多い人", "土日祝の休み": "期間中の土日祝のうち休めた日数（出ない曜日がない人）", "できれば休み": "「できれば休み」の希望がかなった件数"}
    for col, (k, v) in zip((m3, m4, m5), fair.items()):
        col.metric(k, v, help=helps[k])
    if must:
        st.error("赤を直せば確定できます。黄はできれば直す、くらいで大丈夫です。")
    else:
        st.success("「必ず」はすべて守れています。黄はできれば直す、くらいで確定できます。")

    st.markdown("##### 日ごとの人数")
    st.dataframe(totals_table(ss.assign, req), width="stretch")

    st.markdown("##### 守れなかった条件")
    f1, f2 = st.columns(2)
    lv = f1.multiselect("強さ", ["必ず", "なるべく"], default=["必ず", "なるべく"])
    kinds = sorted({i.kind for i in issues})
    kd = f2.multiselect("種類", kinds, default=kinds)
    shown = [i for i in issues if i.level in lv and i.kind in kd]
    shown.sort(key=lambda i: (i.level != "必ず", i.day or 0))
    idf = pd.DataFrame([{"強さ": "🔴 必ず" if i.level == "必ず" else "🟡 なるべく", "種類": i.kind, "内容": i.text} for i in shown])
    if len(idf):
        ev = st.dataframe(idf, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", height=min(400, 38 + 35 * len(idf)))
        sel = ev.selection.rows
        ss.highlight = (shown[sel[0]].sid, shown[sel[0]].day) if sel else None
        st.caption("行を選ぶと、下の勤務表のその日（その人）のマスがオレンジになります。")
    else:
        st.write("ありません。")

    st.markdown("##### 勤務表")
    g1, g2, g3 = st.columns(3)
    role_f = g1.multiselect("役割", ["リーダー", "チェッカー", "クリーナー"], default=[], placeholder="すべて")
    grp_f = g2.multiselect("グループ", ["社員", "パート", "派遣", "新人"], default=[], placeholder="すべて")
    name_f = g3.text_input("名前で探す")
    staff = [s for s in fac.staff if s.home in shown_buildings]
    if role_f:
        staff = [s for s in staff if s.role in role_f]
    if grp_f:
        staff = [s for s in staff if set(grp_f) & set(s.groups)]
    if name_f:
        staff = [s for s in staff if name_f in s.name]
    st.caption("本＝本館、別＝別館、離＝離れ、休＝休み。◆＝必ず休みの希望、◇＝できれば休みの希望、・＝手直し。「前」は前の期間の最後の7日（灰色）。")
    tab1, tab2 = st.tabs(["見る", "手直しする"])
    with tab1:
        sty, _ = styled_grid(ss.assign, staff, ss.highlight)
        st.dataframe(sty, hide_index=True, width="stretch", height=min(900, 38 + 35 * len(staff)), column_config={"名前": st.column_config.Column(pinned=True, width="medium")})
    with tab2:
        if not can_edit:
            st.write("見るだけの権限です。")
        else:
            cols = [fmt_date(d) for d in dates]
            edf = pd.DataFrame([{"名前": s.name, **{cols[d]: ss.assign[(s.sid, d)] for d in range(fac.days)}} for s in staff])
            cfg = {c: st.column_config.SelectboxColumn(c, options=BUILDINGS + [OFF], width="small") for c in cols}
            cfg["名前"] = st.column_config.Column(pinned=True, disabled=True, width="medium")
            out = st.data_editor(edf, hide_index=True, column_config=cfg, key="grid_edit", width="stretch", height=min(900, 38 + 35 * len(staff)))
            if st.button("手直しを反映する"):
                n = 0
                for i, s in enumerate(staff):
                    for d in range(fac.days):
                        v = out.iloc[i][cols[d]]
                        if v != ss.assign[(s.sid, d)]:
                            ss.assign[(s.sid, d)] = v
                            ss.edited.add((s.sid, d))
                            n += 1
                st.toast(f"{n}マスを手直ししました")
                st.rerun()
    next_button("次へ：⑤ 確定・公開", help=None)


# ---------- ⑤ ----------
def step5():
    if ss.assign is None:
        st.warning("まだ勤務表がありません。④で作ってください。")
        return
    req = required()
    must = [i for i in find_issues(fac, ss.assign, req) if i.level == "必ず"]
    if ss.confirmed:
        st.success(f"確定しました（{len(ss.confirmed)}版目）。スタッフのスマホで見られます（試作なので送られません）。")
    else:
        if must:
            st.warning(f"「必ず」が {len(must)}件 残っています。このまま確定もできます。")
        if st.button("確定して公開する", type="primary", disabled=not can_edit, width="stretch"):
            ss.confirmed.append(dict(ss.assign))
            st.rerun()
    st.markdown("##### 給与・勤怠向けの出力")
    st.write("月で締められるよう、期間とは別に日付の範囲を選べます。")
    rng = st.date_input("出力する範囲", value=(dates[0], dates[-1]), format="YYYY/MM/DD")
    rows = []
    for s in fac.staff:
        for d, date in enumerate(dates):
            if isinstance(rng, tuple) and len(rng) == 2 and rng[0] <= date <= rng[1]:
                v = ss.assign[(s.sid, d)]
                rows.append({"社員番号": s.sid, "氏名": s.name, "日付": date.isoformat(), "勤務": "休み" if v == OFF else "出勤", "館": "" if v == OFF else v, "役割": "" if v == OFF else s.role})
    csv = pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig")
    st.download_button("CSVで出す", csv, file_name="kinmu.csv", mime="text/csv", disabled=not ss.confirmed)
    if not ss.confirmed:
        st.caption("出力は確定した勤務表だけです。")


# ---------- スタッフ管理 ----------
def page_staff():
    st.title("スタッフ管理")
    f1, f2, f3 = st.columns(3)
    bf = f1.multiselect("館", BUILDINGS, default=shown_buildings)
    rf = f2.multiselect("役割", ["リーダー", "チェッカー", "クリーナー"], placeholder="すべて")
    nf = f3.text_input("名前で探す", key="staff_name")
    staff = [s for s in fac.staff if s.home in bf and (not rf or s.role in rf) and (not nf or nf in s.name)]
    df = pd.DataFrame(
        [
            {
                "社員番号": s.sid,
                "名前": s.name,
                "主に働く館": s.home,
                "応援に行ける館": "・".join(s.can_help),
                "役割": s.role,
                "グループ": "・".join(s.groups),
                "露天風呂付き": s.roten,
                "出ない曜日": "".join(WEEKDAYS[i] for i in sorted(s.off_weekdays)),
                "週の目安": f"{s.week_days}日",
                "連勤の上限": f"{s.max_consec}日",
                "月の上限": f"{s.monthly_cap_days}日" if s.monthly_cap_days else "",
                "スマホ": "使う" if s.phone else "使わない",
            }
            for s in staff
        ]
    )
    st.write(f"{len(staff)}人")
    st.dataframe(df, hide_index=True, width="stretch", height=min(900, 38 + 35 * len(df)), column_config={"名前": st.column_config.Column(pinned=True, width="medium")})

    st.markdown("##### 設定のコピー")
    names = {s.name: s for s in fac.staff}
    c1, c2 = st.columns(2)
    src = c1.selectbox("この人の設定を", list(names))
    dst = c2.multiselect("この人たちにコピー", [n for n in names if n != src], placeholder="選んでください")
    if st.button("コピーする（応援に行ける館・露天風呂付き・出ない曜日・週の目安・連勤の上限）", disabled=not dst or not can_edit):
        a = names[src]
        for n in dst:
            b = names[n]
            b.can_help = [x for x in a.can_help if x != b.home]
            b.roten, b.off_weekdays, b.week_days, b.max_consec = a.roten, list(a.off_weekdays), a.week_days, a.max_consec
        st.toast(f"{len(dst)}人にコピーしました")
        st.rerun()

    st.markdown("##### CSVでまとめて登録（確かめるところまで）")
    up = st.file_uploader("CSV（UTF-8 か Shift_JIS）", type="csv")
    if up is not None:
        raw = up.getvalue()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp932")
        new = pd.read_csv(io.StringIO(text), dtype=str).fillna("")
        known = {s.sid for s in fac.staff}
        errs = []
        for i, r in new.iterrows():
            if r.get("主に働く館", "") and r["主に働く館"] not in BUILDINGS:
                errs.append(f"{i + 2}行目：主に働く館「{r['主に働く館']}」がありません")
        add = sum(1 for x in new.get("社員番号", []) if x not in known)
        st.info(f"新しく入る {add}人／変わる {len(new) - add}人" + ("" if not errs else f"／エラー {len(errs)}行"))
        for e in errs:
            st.write(f"- {e}")
        st.caption("試作なので、ここでは取り込みません。")


# ---------- スマホ見本 ----------
def page_phone():
    st.title("スタッフのスマホ画面（見本）")
    names = {s.name: s for s in fac.staff}
    who = st.selectbox("スタッフ", list(names), index=0)
    s = names[who]
    left, _ = st.columns([1, 2])
    with left:
        with st.container(border=True):
            if not ss.confirmed:
                st.markdown(f"**{period_text}の休みの希望**")
                st.caption("締切 11/25(水) 18:00")
                hard = sum(1 for d in range(fac.days) if fac.requests.get((s.sid, d)) == "必ず休み")
                st.write(f"「必ず休み」はあと {max(0, 3 - hard)}日 入れられます")
                for d, date in enumerate(dates):
                    cur = fac.requests.get((s.sid, d), "どちらでもよい")
                    opts = ["どちらでもよい", "必ず休み", "できれば休み"]
                    st.selectbox(fmt_date(date), opts, index=opts.index(cur), key=f"ph_{s.sid}_{d}", disabled=True)
                sub = fac.submitted[s.sid]
                st.caption(f"出しました（{sub.month}/{sub.day} {sub:%H:%M}）" if sub else "まだ出していません")
            else:
                st.markdown(f"**{period_text}の勤務**")
                a = ss.confirmed[-1]
                for d, date in enumerate(dates):
                    v = a[(s.sid, d)]
                    st.write(f"{fmt_date(date)}　{'休み' if v == OFF else v + '（' + s.role + '）'}")
    st.caption("試作では見るだけです。")


# ---------- 振り分け ----------
if page == "ホーム":
    page_home()
elif page == "勤務表づくり（5ステップ）":
    step_bar()
    if not can_edit and ss.step < 3:
        st.caption("見るだけの権限です。")
    [step1, step2, step3, step4, step5][ss.step]()
    if ss.step > 0 and st.button("前のステップへ戻る"):
        ss.step -= 1
        st.rerun()
elif page == "スタッフ管理":
    page_staff()
else:
    page_phone()
