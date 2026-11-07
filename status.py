"""Korak 6: pregled po kategorijama + automatski za_kontakt.

    python status.py              # osveži Sheet, prikaži i pošalji izveštaj na Telegram
    python status.py --bez-telegrama
"""
import argparse
import sys

from klijenti import config, db, notify, sheets

LABELS = {
    "sajt_ne_radi": "Sajt ne radi",
    "bez_sajta": "Sajt nije pronađen",
    "samo_drustvene": "Samo društvene mreže",
    "nebezbedan": "Nema/nevažeći SSL",
    "nije_za_telefon": "Nije za telefon",
    "zastareo": "Zastareo",
    "spor": "Spor",
    "dobar_sajt": "Dobar sajt",
    "nepoznato": "Provera nije uspela",
    "neanaliziran": "Još nije provereno",
}


def build_report(report: dict, promoted: int, threshold: int) -> str:
    lines = ["📊 Leadovi po kategorijama", ""]
    total = ready = 0
    for cat, v in report.items():
        total += v["n"]
        ready += v["ready"]
        lines.append(f"{LABELS.get(cat, cat)}: {v['n']} (sa mejlom {v['with_email']}, za kontakt {v['ready']})")
    lines += ["", f"Ukupno: {total}", f"✅ Spremno za kontakt: {ready}"]
    if promoted:
        lines.append(f"(upravo dodato u za_kontakt: {promoted}, prag ocene {threshold})")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Pregled leadova po kategorijama (korak 6).")
    p.add_argument("--bez-telegrama", action="store_true")
    args = p.parse_args()

    conn = db.connect(config.DB_PATH)
    result = sheets.sync(conn)  # preračunava kategorije i postavlja za_kontakt
    text = build_report(db.category_report(conn), result.get("promoted", 0), db.min_score_for_contact())
    conn.close()

    print(text)
    for w in result.get("warnings", []):
        print(f"NAPOMENA: {w}")
    if not args.bez_telegrama:
        notify.send(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
