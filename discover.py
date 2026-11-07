"""Korak 1: pronalaženje firmi za „nišu + grad“.

Primeri:
    python discover.py stomatolozi Beograd
    python discover.py "auto servisi" "Novi Sad"
    python discover.py stomatolozi Beograd --proba     # samo prikaže, ništa ne upisuje
    python discover.py --nise                          # spisak poznatih niša
    python discover.py --sync                          # samo osveži Sheet
"""
import argparse
import sys

from klijenti import config, db, notify, osm, sheets
from klijenti.niche import NAME_HINTS, NICHES, resolve_niche


def run(niche_text: str, city: str, dry_run: bool, skip_sheet: bool) -> int:
    niche, tags = resolve_niche(niche_text)
    print(f"Niša: {niche}  (OSM: {', '.join(f'{k}={v}' for k, v in tags)})")

    area_id, area_name = osm.find_city_area(city)
    print(f"Područje: {area_name}")

    elements = osm.run_overpass(osm.build_query(area_id, tags, name_regex=NAME_HINTS.get(niche)))
    leads = [lead for el in elements if (lead := osm.parse_element(el, niche, city))]
    with_site = sum(1 for l in leads if l["website_type"] == "sajt")
    social = sum(1 for l in leads if l["website_type"] == "drustvene_mreze")
    with_email = sum(1 for l in leads if l["email"])
    print(f"Nađeno: {len(leads)} firmi | sa sajtom {with_site} | samo društvene mreže {social} "
          f"| bez sajta {len(leads) - with_site - social} | mejl na OSM-u {with_email}")

    if dry_run:
        for l in leads[:15]:
            print(f"  - {l['name']} | {l['website'] or '—'} | {l['phone'] or '—'} | {l['address'] or '—'}")
        print("(proba: ništa nije upisano)")
        return 0

    conn = db.connect(config.DB_PATH)
    run_id = db.start_run(conn, niche, city)
    counts = {"found": len(leads), "new": 0, "updated": 0, "duplicate": 0}
    for lead in leads:
        counts[db.upsert_lead(conn, lead)] += 1
    db.finish_run(conn, run_id, counts)
    conn.commit()
    print(f"Baza: novih {counts['new']}, osveženih {counts['updated']}, duplikata {counts['duplicate']}")

    sheet_note = ""
    if not skip_sheet:
        result = sheets.sync(conn)
        print(f"Sheet: {result['rows']} redova | tvojih izmena preuzeto {result['edited']} "
              f"| označeno zbog odjave {result['suppressed']}")
        for w in result.get("warnings", []):
            print(f"NAPOMENA: {w}")
        sheet_note = f"\nU Sheet-u ukupno: {result['rows']}"
    conn.close()

    notify.send(
        f"🔎 {niche}, {city}\n"
        f"Nađeno: {len(leads)} (sa sajtom {with_site}, bez sajta {len(leads) - with_site - social})\n"
        f"Novih: {counts['new']}{sheet_note}"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Pronalaženje firmi po niši i gradu (OpenStreetMap).")
    p.add_argument("nisa", nargs="?", help='npr. stomatolozi, "auto servisi" ili OSM tag shop=bicycle')
    p.add_argument("grad", nargs="?", help="npr. Beograd, Novi Sad, Niš")
    p.add_argument("--proba", action="store_true", help="samo prikaži rezultate, ne upisuj")
    p.add_argument("--bez-sheeta", action="store_true", help="upiši samo u bazu")
    p.add_argument("--sync", action="store_true", help="samo sinhronizuj bazu i Sheet")
    p.add_argument("--nise", action="store_true", help="prikaži poznate niše")
    args = p.parse_args()

    if args.nise:
        for name, (tags, aliases) in sorted(NICHES.items()):
            print(f"{name:22} {', '.join(aliases)}")
        return 0
    if args.sync:
        conn = db.connect(config.DB_PATH)
        result = sheets.sync(conn)
        conn.close()
        print(f"Sheet: {result['rows']} redova | izmena preuzeto {result['edited']} "
              f"| označeno zbog odjave {result['suppressed']}")
        for w in result.get("warnings", []):
            print(f"NAPOMENA: {w}")
        return 0
    if not args.nisa or not args.grad:
        p.print_help()
        return 2
    try:
        return run(args.nisa, args.grad, args.proba, args.bez_sheeta)
    except (ValueError, RuntimeError) as e:
        print(f"GREŠKA: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
