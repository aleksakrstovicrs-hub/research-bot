"""Izveštaj za klijenta: lista potencijalnih klijenata u Excelu, u POSEBNOJ bazi.

Ne dira tvoju bazu, tvoj Sheet ni tvoj Telegram bot, i ne šalje nijedan mejl.
Predlozi ponuda se pišu u ime klijenta (npr. njegovog web studija), ne u tvoje.

    python izvestaj.py "gradjevinske firme" Beograd --klijent "Primer Studio" --sajt primer-studio.rs
    python izvestaj.py "gradjevinske firme" Beograd --klijent "..." --bez-ponuda     # samo lista
    python izvestaj.py ... --samo-excel        # ne radi ponovo research, samo napravi Excel iz postojeće baze
"""
import argparse
import asyncio
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import analyze
import design_review
import find_emails
import pagespeed
from klijenti import config, db, drafts, notify, osm, templates
from klijenti.botlogic import CATEGORY_LABELS
from klijenti.niche import NAME_HINTS, resolve_niche, simplify


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", simplify(text)).strip("-") or "klijent"


async def research(conn, niche_text: str, city: str, parallel: int, with_design: bool) -> None:
    niche, tags = resolve_niche(niche_text)
    print(f"\n🔎 1/5 Tražim: {niche}, {city}")
    area_id, area_name = await asyncio.to_thread(osm.find_city_area, city)
    elements = await asyncio.to_thread(osm.run_overpass, osm.build_query(area_id, tags, name_regex=NAME_HINTS.get(niche)))
    leads = [l for el in elements if (l := osm.parse_element(el, niche, city))]
    counts = {"new": 0, "updated": 0, "duplicate": 0}
    for lead in leads:
        counts[db.upsert_lead(conn, lead)] += 1
    conn.commit()
    print(f"   {area_name}: {len(leads)} firmi (novih {counts['new']}, duplikata {counts['duplicate']})")

    rows = db.leads_to_analyze(conn, niche, city)
    print(f"\n🔧 2/5 Proveravam sajtove: {len(rows)}")
    if rows:
        await analyze.analyze_all(rows, conn, parallel)
    rows = db.leads_for_email_scan(conn, niche, city)
    print(f"\n✉️ 3/5 Tražim javne mejlove: {len(rows)}")
    if rows:
        await find_emails.run_all(rows, conn, parallel)
    rows = db.leads_for_speed(conn, niche, city)
    print(f"\n⚡ 4/5 Merim brzinu: {len(rows)}")
    if rows:
        await pagespeed.run_all(rows, conn, parallel)
    rows = db.leads_for_design(conn, niche, city)
    if with_design and rows:
        print(f"\n🎨 5/5 AI ocena dizajna: {len(rows)}")
        await design_review.run_all(rows, conn, parallel)
    db.rescore_all(conn)
    conn.commit()


def report_rows(conn, niche: str, city: str) -> list[dict]:
    rows = conn.execute(
        """SELECT l.*, a.score, a.category, a.issues, a.ai_summary, a.psi_score, a.ai_score,
                  a.screenshot_desktop, a.screenshot_mobile
           FROM leads l LEFT JOIN analyses a ON a.lead_id = l.id
           WHERE l.niche = ? AND lower(l.city) = lower(?)
           ORDER BY (a.score IS NULL), a.score DESC, l.name""",
        (niche, city),
    ).fetchall()
    return [dict(r) for r in rows]


def wants_pitch(r: dict, min_score: int) -> bool:
    return bool(r.get("email")) and (r.get("score") or 0) >= min_score \
        and r.get("category") not in ("dobar_sajt", "nepoznato", None)


async def write_pitches(rows: list[dict], sender: dict, min_score: int, limit: int) -> None:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=config.get("OPENAI_API_KEY", required=True))
    targets = [r for r in rows if wants_pitch(r, min_score)][:limit]
    print(f"\n✍️  Pišem {len(targets)} predloga ponuda u ime: {sender['name']}")
    sem = asyncio.Semaphore(4)

    async def one(r):
        async with sem:
            try:
                d = await drafts.generate(client, r, sender=sender)
                r["pitch_subject"] = d["subject"]
                r["pitch_body"] = (f"{d['core']}\n\nSrdačan pozdrav,\n{sender['name']}"
                                   + (f"\n{sender['site']}" if sender.get("site") else "")
                                   + f"\n\n{templates.OPT_OUT}")
            except Exception as e:  # noqa: BLE001
                r["pitch_subject"], r["pitch_body"] = "", f"(predlog nije napravljen: {e})"[:200]

    await asyncio.gather(*(one(r) for r in targets))


THUMB_DESKTOP = (320, 180)   # piksela u Excelu
THUMB_MOBILE_H = 180


def thumbnail(rel_path: str | None, mobile: bool):
    """Umanjena slika sajta kao openpyxl Image, ili None ako slike nema."""
    if not rel_path:
        return None
    src = config.BASE_DIR / rel_path
    if not src.exists():
        return None
    import io

    from openpyxl.drawing.image import Image as XLImage
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        if mobile:
            w, h = im.size
            im = im.crop((0, 0, w, min(h, int(w * 2.0))))  # gornji deo ekrana telefona
            ratio = THUMB_MOBILE_H / im.height
            im = im.resize((max(1, int(im.width * ratio)), THUMB_MOBILE_H), Image.LANCZOS)
        else:
            im.thumbnail(THUMB_DESKTOP, Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80, optimize=True)
    buf.seek(0)
    return XLImage(buf)


def write_excel(rows: list[dict], path: Path, title: str, min_score: int) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    head_fill = PatternFill("solid", fgColor="16221A")
    hot, warm = PatternFill("solid", fgColor="FBD9D6"), PatternFill("solid", fgColor="FFF3CC")
    wb = Workbook()

    def sheet(ws, columns, data, images=False):
        ws.append([c[0] for c in columns])
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = head_fill
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for r in data:
            ws.append([c[1](r) for c in columns])
            score = r.get("score") or 0
            fill = hot if score >= 70 else warm if score >= min_score else None
            for cell in ws[ws.max_row]:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if fill:
                    cell.fill = fill
            if images:
                row = ws.max_row
                placed = False
                for offset, (key, mobile) in enumerate([("screenshot_desktop", False), ("screenshot_mobile", True)]):
                    try:
                        img = thumbnail(r.get(key), mobile)
                    except Exception:  # noqa: BLE001 — oštećena slika ne sme da obori izveštaj
                        img = None
                    if img:
                        ws.add_image(img, f"{get_column_letter(len(columns) + 1 + offset)}{row}")
                        placed = True
                if placed:
                    ws.row_dimensions[row].height = 142
        for i, c in enumerate(columns, 1):
            ws.column_dimensions[get_column_letter(i)].width = c[2]
        if images:
            n = len(columns)
            for title_, width, off in (("Izgled na računaru", 47, 1), ("Izgled na telefonu", 15, 2)):
                cell = ws.cell(row=1, column=n + off, value=title_)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = head_fill
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                ws.column_dimensions[get_column_letter(n + off)].width = width
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions

    def issues(r):
        items = json.loads(r["issues"]) if r.get("issues") else []
        return "\n".join(f"• {i}" for i in items) or "Bez većih problema"

    def site(r):
        if r.get("website_type") == "sajt":
            return r.get("website") or ""
        if r.get("website_type") == "drustvene_mreze":
            return f"samo mreže: {r.get('website') or ''}"
        return "nije pronađen"

    def email_src(r):
        src = r.get("email_source") or ""
        return "OpenStreetMap" if src == "openstreetmap" else src

    columns = [
        ("Firma", lambda r: r["name"], 30),
        ("Ocena prilike (0–100)", lambda r: r.get("score"), 12),
        ("Glavni razlog", lambda r: CATEGORY_LABELS.get(r.get("category"), r.get("category") or ""), 18),
        ("Sajt", site, 34),
        ("Uočeni nedostaci", issues, 60),
        ("Mejl (javno objavljen)", lambda r: r.get("email") or "", 30),
        ("Gde je mejl objavljen", email_src, 34),
        ("Telefon", lambda r: r.get("phone") or "", 18),
        ("Adresa", lambda r: r.get("address") or "", 30),
        ("Predlog naslova", lambda r: r.get("pitch_subject") or "", 34),
        ("Predlog ponude", lambda r: r.get("pitch_body") or "", 80),
    ]
    ws = wb.active
    ws.title = "Preporučeni"
    sheet(ws, columns, [r for r in rows if wants_pitch(r, min_score)], images=True)
    sheet(wb.create_sheet("Sve firme"), columns[:9], rows)

    info = wb.create_sheet("O izveštaju")
    for line in [
        title,
        f"Datum: {date.today().strftime('%d.%m.%Y.')}",
        "",
        f"„Preporučeni“: firme sa javno objavljenim poslovnim mejlom i ocenom prilike {min_score} ili više.",
        "„Sve firme“: sve pronađene firme, uključujući one bez mejla (za kontakt telefonom).",
        "Ocena prilike: veća ocena znači veću verovatnoću da firmi treba nov sajt ili redizajn.",
        "„Sajt nije pronađen“: sajt nije na mapi niti je pronađen po imenu firme. Proveriti pre kontakta.",
        "Mejlovi su preuzeti samo sa javnih stranica firmi; kolona „Gde je mejl objavljen“ navodi izvor.",
        "Predlozi ponuda su nacrti. Pročitajte ih i prilagodite pre slanja.",
        "",
        "Izvor podataka o firmama: © OpenStreetMap contributors (openstreetmap.org/copyright).",
    ]:
        info.append([line])
    info.column_dimensions["A"].width = 120
    info["A1"].font = Font(bold=True, size=14)
    wb.save(path)


async def make_report(niche_text: str, city: str, client: str, site: str = "", *, with_pitches: bool = True,
                      with_design: bool = True, only_excel: bool = False, min_score: int = 40,
                      max_pitches: int = 40, parallel: int = 3) -> dict:
    """Ceo izveštaj za klijenta. Vraća {path, total, recommended, minutes}. Baca ValueError ako nema firmi."""
    notify.MUTED = True
    niche, _ = resolve_niche(niche_text)
    slug = slugify(client)
    out_dir = config.DATA_DIR / "izvestaji" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    conn = db.connect(out_dir / "baza.db")  # posebna baza po klijentu
    try:
        # institucije ubačene ranijim verzijama izbacujemo i iz postojeće baze
        for row in conn.execute("SELECT id, name FROM leads").fetchall():
            if osm.is_public_institution(row["name"], {}):
                conn.execute("DELETE FROM analyses WHERE lead_id=?", (row["id"],))
                conn.execute("DELETE FROM emails WHERE lead_id=?", (row["id"],))
                conn.execute("DELETE FROM leads WHERE id=?", (row["id"],))
        conn.commit()
        if not only_excel:
            await research(conn, niche_text, city, max(1, parallel), with_design)
        db.rescore_all(conn)  # i za --samo-excel: ocena po najnovijim pravilima
        conn.commit()
        rows = report_rows(conn, niche, city)
        if not rows:
            raise ValueError(f"Za „{niche}, {city}“ nije pronađena nijedna firma.")
        sender = {"name": client, "site": site.replace("https://", "").replace("http://", "").strip("/")}
        if with_pitches:
            await write_pitches(rows, sender, min_score, max_pitches)
        path = out_dir / f"{slug}_{slugify(niche)}_{slugify(city)}_{date.today().isoformat()}.xlsx"
        title = f"Potencijalni klijenti: {niche}, {city}, pripremljeno za {client}"
        write_excel(rows, path, title, min_score)
    finally:
        conn.close()
    return {"path": path, "total": len(rows), "minutes": (time.time() - started) / 60,
            "recommended": sum(1 for r in rows if wants_pitch(r, min_score))}


def main() -> int:
    p = argparse.ArgumentParser(description="Excel izveštaj za klijenta (posebna baza).")
    p.add_argument("nisa")
    p.add_argument("grad")
    p.add_argument("--klijent", required=True, help='npr. "Primer Studio"')
    p.add_argument("--sajt", default="", help="sajt klijenta, npr. primer-studio.rs")
    p.add_argument("--bez-ponuda", action="store_true", help="samo lista, bez predloga ponuda")
    p.add_argument("--bez-ai-dizajna", action="store_true")
    p.add_argument("--samo-excel", action="store_true", help="ne radi research ponovo")
    p.add_argument("--min-ocena", type=int, default=40)
    p.add_argument("--max-ponuda", type=int, default=40)
    p.add_argument("--istovremeno", type=int, default=3)
    args = p.parse_args()

    try:
        result = asyncio.run(make_report(
            args.nisa, args.grad, args.klijent, args.sajt, with_pitches=not args.bez_ponuda,
            with_design=not args.bez_ai_dizajna, only_excel=args.samo_excel, min_score=args.min_ocena,
            max_pitches=args.max_ponuda, parallel=args.istovremeno))
    except ValueError as e:
        print(f"GREŠKA: {e}")
        return 1
    print(f"\n✅ Gotovo za {result['minutes']:.0f} min: {result['total']} firmi, "
          f"{result['recommended']} preporučenih.")
    print(f"📄 {result['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
