"""Sinhronizacija sa Google Sheet-om.

Pravilo: baza je izvor istine, ali kolone `status` i `napomena` menjaš ti u Sheet-u.
Zato sync prvo pročita te dve kolone i tab „Odjave“, upiše ih u bazu, pa tek onda
ponovo ispiše ceo tab „Leadovi“.
"""
import json
import sqlite3

from . import config
from .db import STATUSES, apply_sheet_edits, mark_synced, promote_ready, rescore_all, set_suppression

LEADS_TAB = "Leadovi"
OPTOUT_TAB = "Odjave"

# (naslov kolone u Sheet-u, izraz u SQL-u; l = leads, a = analyses)
COLUMNS = [
    ("id", "l.id"),
    ("firma", "l.name"),
    ("ocena_prilike", "a.score"),
    ("kategorija", "a.category"),
    ("nedostaci", "a.issues"),
    ("sajt", "l.website"),
    ("mejl", "l.email"),
    ("tip_mejla", "l.email_type"),
    ("telefon", "l.phone"),
    ("status", "l.status"),
    ("napomena", "l.note"),
    ("niša", "l.niche"),
    ("grad", "l.city"),
    ("adresa", "l.address"),
    ("tip_sajta", "l.website_type"),
    ("izvor_sajta", "l.website_source"),
    ("platforma", "a.platform"),
    ("brzina_mobilni", "a.psi_score"),
    ("dizajn_ai", "a.ai_score"),
    ("utisak_ai", "a.ai_summary"),
    ("izvor_mejla", "l.email_source"),
    ("prvi_put", "l.first_seen"),
    ("analizirano", "a.analyzed_at"),
]
HEADER = [title for title, _ in COLUMNS]
OPTOUT_HEADER = ["mejl_ili_domen", "datum", "napomena"]


def parse_sheet_edits(values: list[list[str]]) -> dict[str, dict]:
    """Iz sirovih redova Sheet-a izvlači {id: {status, note}}."""
    if not values:
        return {}
    header = [h.strip() for h in values[0]]
    try:
        i_id, i_status, i_note = header.index("id"), header.index("status"), header.index("napomena")
    except ValueError:
        return {}
    edits = {}
    for row in values[1:]:
        row = row + [""] * (len(header) - len(row))
        lead_id = row[i_id].strip()
        if lead_id:
            edits[lead_id] = {"status": row[i_status].strip(), "note": row[i_note].strip()}
    return edits


def parse_optouts(values: list[list[str]]) -> list[tuple[str, str]]:
    out = []
    for row in values[1:]:
        if row and row[0].strip():
            out.append((row[0].strip(), row[2].strip() if len(row) > 2 else ""))
    return out


def _cell(title: str, value) -> str:
    if value is None:
        return ""
    if title == "nedostaci":
        try:
            items = json.loads(value)
        except (TypeError, ValueError):
            return str(value)
        return " · ".join(items) if items else "Bez većih problema"
    return str(value)


def lead_rows(conn: sqlite3.Connection) -> list[list[str]]:
    cols = ", ".join(expr for _, expr in COLUMNS)
    rows = conn.execute(
        f"""SELECT {cols} FROM leads l LEFT JOIN analyses a ON a.lead_id = l.id
            ORDER BY (a.score IS NULL), a.score DESC, l.niche, l.city, l.name"""
    ).fetchall()
    return [[_cell(title, v) for (title, _), v in zip(COLUMNS, row)] for row in rows]


def _worksheet(sh, title: str, header: list[str]):
    import gspread

    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        pass
    # Prazan podrazumevani tab ("Sheet1") samo preimenujemo.
    first = sh.sheet1
    if title == LEADS_TAB and len(sh.worksheets()) == 1 and not first.get_all_values():
        first.update_title(title)
        ws = first
    else:
        ws = sh.add_worksheet(title=title, rows=100, cols=len(header))
    ws.update(range_name="A1", values=[header])
    ws.freeze(rows=1)
    ws.format("1:1", {"textFormat": {"bold": True}})
    return ws


def sync(conn: sqlite3.Connection) -> dict:
    """Čita tvoje izmene iz Sheet-a, upisuje ih u bazu i osvežava tab sa leadovima."""
    import gspread

    gc = gspread.service_account(filename=config.get("GOOGLE_SA_FILE", required=True))
    sh = gc.open_by_key(config.get("SHEET_ID", required=True))

    leads_ws = _worksheet(sh, LEADS_TAB, HEADER)
    optout_ws = _worksheet(sh, OPTOUT_TAB, OPTOUT_HEADER)

    edited = apply_sheet_edits(conn, parse_sheet_edits(leads_ws.get_all_values()))
    suppressed = set_suppression(conn, parse_optouts(optout_ws.get_all_values()))
    rescore_all(conn)
    promoted = promote_ready(conn)
    conn.commit()

    data = [HEADER] + lead_rows(conn)
    leads_ws.resize(rows=max(len(data) + 50, 100), cols=len(HEADER))
    leads_ws.batch_clear([f"A2:{_col_letter(len(HEADER))}"])
    leads_ws.update(range_name="A1", values=data)
    mark_synced(conn)
    conn.commit()

    existing_rules = 0
    try:
        for s in sh.fetch_sheet_metadata().get("sheets", []):
            if s.get("properties", {}).get("sheetId") == leads_ws.id:
                existing_rules = len(s.get("conditionalFormats", []))
    except Exception:  # noqa: BLE001 — bez boja, ali podaci su upisani
        pass
    warnings = apply_layout(sh, layout_requests(leads_ws.id, len(data), existing_rules))
    return {"rows": len(data) - 1, "edited": edited, "suppressed": suppressed,
            "promoted": promoted, "warnings": warnings}


def apply_layout(sh, requests: list[dict]) -> list[str]:
    """Izgled tabele šalje zahtev po zahtev. Ako neki ne uspe, podaci su već upisani,
    pa grešku samo prijavimo umesto da prekinemo ceo posao."""
    from gspread.exceptions import APIError

    warnings = []
    for req in requests:
        try:
            sh.batch_update({"requests": [req]})
        except APIError as e:
            msg = str(e)
            if "typed columns" in msg:
                hint = ("Tab „Leadovi“ je pretvoren u Google tabelu (Table), pa padajuća lista za status "
                        "ne može da se postavi. Desni klik na tabelu → „Convert to range“ to rešava.")
            else:
                hint = f"Izgled tabele nije podešen: {msg[:150]}"
            if hint not in warnings:
                warnings.append(hint)
    return warnings


def color_rules(sheet_id: int, n_rows: int) -> list[dict]:
    """Boja celog reda po oceni prilike: crveno ≥ 70, žuto ≥ 40."""
    col = _col_letter(HEADER.index("ocena_prilike") + 1)
    rng = {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": max(n_rows, 2),
           "startColumnIndex": 0, "endColumnIndex": len(HEADER)}
    rules = []
    for i, (formula, rgb) in enumerate([
        (f'=AND(ISNUMBER(${col}2), ${col}2>=70)', {"red": 0.98, "green": 0.85, "blue": 0.84}),
        (f'=AND(ISNUMBER(${col}2), ${col}2>=40)', {"red": 1.0, "green": 0.95, "blue": 0.80}),
    ]):
        rules.append({"addConditionalFormatRule": {"index": i, "rule": {
            "ranges": [rng],
            "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]},
                            "format": {"backgroundColor": rgb}},
        }}})
    return rules


def layout_requests(sheet_id: int, n_rows: int, existing_rules: int = 0) -> list[dict]:
    """Padajuća lista za status, širina kolone sa nedostacima i boje redova."""
    status_col = HEADER.index("status")
    issues_col = HEADER.index("nedostaci")
    # stara pravila za boje brišemo da se ne bi gomilala pri svakom sync-u
    deletes = [{"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": 0}}
               for _ in range(existing_rules)]
    header_style = {"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": len(HEADER)},
        "cell": {"userEnteredFormat": {
            "backgroundColor": {"red": 0.16, "green": 0.38, "blue": 0.29},
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)",
    }}
    return deletes + color_rules(sheet_id, n_rows) + [header_style] + [
        # stara padajuća lista može da ostane na koloni koja se pomerila, pa je prvo brišemo
        {"setDataValidation": {"range": {"sheetId": sheet_id}}},
        {"setDataValidation": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": max(n_rows, 2),
                      "startColumnIndex": status_col, "endColumnIndex": status_col + 1},
            "rule": {"condition": {"type": "ONE_OF_LIST",
                                   "values": [{"userEnteredValue": s} for s in STATUSES]},
                     "strict": True, "showCustomUi": True},
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": issues_col, "endIndex": issues_col + 1},
            "properties": {"pixelSize": 520}, "fields": "pixelSize",
        }},
    ]


def _col_letter(n: int) -> str:
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
