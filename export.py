"""確定した勤務表を、印刷用のExcel（A4横・館ごとのシート）にする。"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from checks import daily_counts
from data import BUILDING_SHORT, HOLIDAYS, WEEKDAYS, Facility, fmt_date

OFF = "休"
FILL = {"本館": "DBEAFE", "別館": "DCFCE7", "離れ": "FEF3C7", OFF: "F3F4F6"}
THIN = Side(style="thin", color="9CA3AF")
GRID = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MARK = {
    "必ず休み": Side(style="thick", color="111827"),
    "できれば休み": Side(style="mediumDashed", color="111827"),
    "固定": Side(style="double", color="111827"),
}
CENTER = Alignment(horizontal="center", vertical="center")


def _fill(hex_):
    return PatternFill("solid", start_color=hex_, end_color=hex_)


def build_excel(fac: Facility, assign: dict, required: dict, buildings: list[str], version: str, rng) -> bytes:
    days = [d for d, date in enumerate(fac.dates) if rng[0] <= date <= rng[1]]
    wb = Workbook()
    wb.remove(wb.active)
    for b in buildings:
        ws = wb.create_sheet(b)
        staff = [s for s in fac.staff if s.home == b]
        first_day_col = 4
        last_col = first_day_col + len(days) + 1
        # 表題と承認欄
        ws.cell(1, 1, f"清掃勤務表　{b}　{fmt_date(fac.dates[days[0]])}〜{fmt_date(fac.dates[days[-1]])}").font = Font(size=14, bold=True)
        ws.cell(2, 1, version).font = Font(size=9, color="6B7280")
        for i, label in enumerate(["作成", "確認", "承認"]):
            c = last_col - 2 + i
            ws.cell(1, c, label).alignment = CENTER
            ws.cell(1, c).border = GRID
            ws.cell(2, c).border = GRID
        ws.row_dimensions[2].height = 30
        ws.cell(3, 1, "本＝本館、別＝別館、離＝離れ、休＝休み。太線＝必ず休みの希望、破線＝できれば休みの希望、二重線＝出ない曜日（固定）。").font = Font(size=8, color="6B7280")
        # 見出し
        hr = 4
        for c, t in enumerate(["名前", "役割", "グループ"], start=1):
            ws.cell(hr, c, t)
        for k, d in enumerate(days):
            date = fac.dates[d]
            cell = ws.cell(hr, first_day_col + k, f"{date.day}\n{WEEKDAYS[date.weekday()]}")
            if date.weekday() == 6 or date in HOLIDAYS:
                cell.fill, cell.font = _fill("FECDD3"), Font(color="9F1239", bold=True)
            elif date.weekday() == 5:
                cell.fill, cell.font = _fill("BFDBFE"), Font(color="1E3A8A", bold=True)
        ws.cell(hr, last_col - 1, "出勤")
        ws.cell(hr, last_col, "休み")
        for c in range(1, last_col + 1):
            ws.cell(hr, c).border = GRID
            ws.cell(hr, c).alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            if not ws.cell(hr, c).fill.fgColor.rgb or ws.cell(hr, c).fill.fgColor.rgb == "00000000":
                ws.cell(hr, c).fill = _fill("F3F4F6")
        ws.row_dimensions[hr].height = 28
        # 本体
        r = hr
        for s in staff:
            r += 1
            ws.cell(r, 1, s.name)
            ws.cell(r, 2, s.role)
            ws.cell(r, 3, "・".join(s.groups))
            worked = 0
            for k, d in enumerate(days):
                v = assign[(s.sid, d)]
                worked += v != OFF
                cell = ws.cell(r, first_day_col + k, BUILDING_SHORT.get(v, v))
                cell.fill = _fill(FILL.get(v, "FFFFFF"))
                cell.alignment = CENTER
                req = fac.requests.get((s.sid, d))
                side = None
                if fac.dates[d].weekday() in s.off_weekdays:
                    side = MARK["固定"]
                elif req in MARK:
                    side = MARK[req]
                cell.border = Border(left=side, right=side, top=side, bottom=side) if side else GRID
            ws.cell(r, last_col - 1, worked)
            ws.cell(r, last_col, len(days) - worked)
            for c in (1, 2, 3, last_col - 1, last_col):
                ws.cell(r, c).border = GRID
            for c in (last_col - 1, last_col):
                ws.cell(r, c).alignment = CENTER
        # 日ごとの人数
        for label in ("出勤（応援を含む）", "必要", "過不足"):
            r += 1
            ws.cell(r, 1, label).font = Font(bold=True)
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
            for k, d in enumerate(days):
                c = daily_counts(fac, assign, b, d)["出勤"]
                need = required[b][d]
                val = {"出勤（応援を含む）": c, "必要": need, "過不足": c - need}[label]
                cell = ws.cell(r, first_day_col + k, val)
                cell.alignment = CENTER
                cell.border = GRID
                if label == "過不足":
                    cell.fill = _fill("FECACA" if val < 0 else ("BFDBFE" if val > 1 else "DCFCE7"))
            ws.cell(r, 1).border = GRID
        # 幅・印刷
        ws.column_dimensions["A"].width = 13
        ws.column_dimensions["B"].width = 11
        ws.column_dimensions["C"].width = 13
        for k in range(len(days)):
            ws.column_dimensions[get_column_letter(first_day_col + k)].width = 5
        ws.column_dimensions[get_column_letter(last_col - 1)].width = 6
        ws.column_dimensions[get_column_letter(last_col)].width = 6
        ws.freeze_panes = ws.cell(hr + 1, first_day_col)
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_title_rows = f"{hr}:{hr}"
        ws.page_margins.left = ws.page_margins.right = 0.4
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
