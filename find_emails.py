"""Korak 3: javni poslovni mejl sa sajta.

Primeri:
    python find_emails.py                       # svi leadovi kojima mejl još nije tražen
    python find_emails.py --limit 5             # proba
    python find_emails.py --ponovo              # ponovo za sve
    python find_emails.py --sajt https://primer.rs --firma "Primer"   # samo prikaži, ništa ne upisuje
"""
import argparse
import asyncio
import sys
import time

from klijenti import config, db, emails, notify, sheets


async def scan_lead(lead: dict, browser) -> tuple[dict, list[dict], dict | None]:
    found: list[dict] = []
    if lead["website_type"] == "sajt" and lead["website"]:
        try:
            result = await asyncio.wait_for(emails.scan_site(lead["website"], lead["name"], browser), timeout=150)
            found = result["emails"]
        except Exception:  # noqa: BLE001 — jedan sajt ne sme da obori ceo prolaz
            found = []
    # Mejl sa mape (OSM) zadržavamo kao rezervu, ali i njega proveravamo.
    if lead["email"] and lead["email_source"] == "openstreetmap" and lead["email"] not in {e["email"] for e in found}:
        found.append({
            "email": lead["email"], "source_url": "openstreetmap",
            "kind": emails.classify(lead["email"], lead["name"]),
            "mx_ok": await asyncio.to_thread(emails.mx_ok, lead["email"].split("@", 1)[1]),
        })
    best = emails.choose_best(found, lead["domain"])
    return lead, found, best


async def run_all(leads: list, conn, parallel: int) -> dict:
    from playwright.async_api import async_playwright

    stats = {"with_email": 0, "without": 0, "rejected": 0}
    sem = asyncio.Semaphore(parallel)

    async def guarded(lead, browser):
        async with sem:
            return await scan_lead(dict(lead), browser)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            tasks = [asyncio.create_task(guarded(lead, browser)) for lead in leads]
            for i, task in enumerate(asyncio.as_completed(tasks), 1):
                lead, found, best = await task
                db.save_email_scan(conn, lead["id"], found, best)
                conn.commit()
                stats["rejected"] += sum(1 for e in found if e.get("mx_ok") is False)
                if best:
                    stats["with_email"] += 1
                    info = f"{best['email']}  ({best['kind']})"
                else:
                    stats["without"] += 1
                    info = "—"
                print(f"[{i}/{len(leads)}] {lead['name'][:40]:40}  {info}")
        finally:
            await browser.close()
    return stats


async def single(url: str, name: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            result = await emails.scan_site(url, name, browser)
        finally:
            await browser.close()
    print(f"Pregledano stranica: {result['pages']} (zabranjeno robots.txt-om: {result['blocked']})")
    for e in result["emails"]:
        mx = {True: "prima poštu", False: "NE prima poštu", None: "nije provereno"}[e["mx_ok"]]
        print(f"  {e['email']:40} {e['kind']:10} {mx:15} {e['source_url']}")
    best = emails.choose_best(result["emails"], None)
    print(f"\nNajbolji: {best['email'] if best else '—'}")


def main() -> int:
    p = argparse.ArgumentParser(description="Javni poslovni mejl sa sajta (korak 3).")
    p.add_argument("--nisa")
    p.add_argument("--grad")
    p.add_argument("--limit", type=int)
    p.add_argument("--ponovo", action="store_true")
    p.add_argument("--istovremeno", type=int, default=3)
    p.add_argument("--bez-sheeta", action="store_true")
    p.add_argument("--sajt", help="samo prikaži mejlove sa ovog sajta")
    p.add_argument("--firma", default="", help="ime firme uz --sajt (za tip mejla)")
    args = p.parse_args()

    if args.sajt:
        asyncio.run(single(args.sajt, args.firma))
        return 0

    conn = db.connect(config.DB_PATH)
    if not args.bez_sheeta:
        sheets.sync(conn)
    leads = db.leads_for_email_scan(conn, args.nisa, args.grad, args.ponovo, args.limit)
    if not leads:
        print("Nema leadova za proveru mejla. (Za ponovnu proveru dodaj --ponovo.)")
        return 0

    print(f"Tražim mejl za {len(leads)} firmi...\n")
    started = time.time()
    stats = asyncio.run(run_all(leads, conn, max(1, args.istovremeno)))
    minutes = (time.time() - started) / 60
    print(f"\nGotovo za {minutes:.1f} min: sa mejlom {stats['with_email']}, bez mejla {stats['without']}, "
          f"odbačeno adresa koje ne primaju poštu {stats['rejected']}.")

    if not args.bez_sheeta:
        info = sheets.sync(conn)
        print(f"Sheet osvežen: {info['rows']} redova.")
        for w in info.get("warnings", []):
            print(f"NAPOMENA: {w}")
    conn.close()

    notify.send(f"✉️ Mejlovi: {stats['with_email']} firmi sa mejlom, {stats['without']} bez "
                f"(od {len(leads)}), za {minutes:.0f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
