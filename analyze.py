"""Korak 2: provera sajtova i nedostaci.

Primeri:
    python analyze.py                          # svi još neanalizirani leadovi
    python analyze.py --nisa stomatolozi --grad Beograd
    python analyze.py --limit 5                # proba na 5 sajtova
    python analyze.py --ponovo                 # ponovo i one već analizirane
    python analyze.py --sajt https://primer.rs # provera jednog sajta, ništa se ne upisuje
"""
import argparse
import asyncio
import json
import sys
import time

from klijenti import checks, config, db, notify, osm, sheets, sitefind


def slug(lead_id: str) -> str:
    return lead_id.replace(":", "_").replace("/", "_")


async def analyze_all(leads: list, conn, parallel: int) -> list[tuple[str, int, list[str]]]:
    from playwright.async_api import async_playwright

    results = []
    sem = asyncio.Semaphore(parallel)

    async def one(lead, browser):
        lead = dict(lead)
        async with sem:
            found = None
            if lead["website_type"] != "sajt":
                try:
                    found = await asyncio.wait_for(sitefind.find_site(lead), timeout=120)
                except Exception:  # noqa: BLE001 — ako pretraga zapne, ostaje „nije pronađen“
                    found = None
                if not found:
                    return lead, None, None
                lead.update(website=found[0], website_type="sajt")
            try:
                r = await asyncio.wait_for(
                    checks.analyze_site(lead["website"], slug(lead["id"]), browser), timeout=150
                )
            except asyncio.TimeoutError:
                # HTTP deo ima svoje kraće rokove; ovde zapinje renderovanje, ne znači da sajt ne radi
                r = {"error": "provera", "render_error": "provera je trajala predugo"}
            except Exception as e:  # noqa: BLE001 — jedan čudan sajt ne sme da obori ceo prolaz
                r = {"error": "provera", "render_error": f"{type(e).__name__}: {e}"[:200]}
            return lead, r, found

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            tasks = [asyncio.create_task(one(lead, browser)) for lead in leads]
            for i, task in enumerate(asyncio.as_completed(tasks), 1):
                lead, r, found = await task
                if found:
                    url, reason = found
                    db.set_found_website(conn, lead["id"], url, osm.domain_of(url), reason)
                db.save_analysis(conn, lead["id"], dict(r or {}))
                issues, score = db.rescore(conn, lead["id"])  # uključuje i brzinu ako je već merena
                conn.commit()
                results.append((lead["name"], score, issues))
                short = " · ".join(issues[:2]) if issues else "bez većih problema"
                if found:
                    short = f"[sajt nađen: {osm.domain_of(found[0])}] {short}"
                print(f"[{i}/{len(leads)}] {score:3d}  {lead['name'][:40]:40}  {short}")
        finally:
            await browser.close()
    return results


async def single_site(url: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            r = await checks.analyze_site(url, "proba", browser)
        finally:
            await browser.close()
    issues, score = checks.assess("sajt", r)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    print(f"\nOcena prilike: {score}")
    for issue in issues or ["Bez većih problema"]:
        print(f"  - {issue}")


def main() -> int:
    p = argparse.ArgumentParser(description="Provera sajtova i nedostaci (korak 2).")
    p.add_argument("--nisa")
    p.add_argument("--grad")
    p.add_argument("--limit", type=int)
    p.add_argument("--ponovo", action="store_true", help="ponovo proveri i već analizirane")
    p.add_argument("--samo-bez-sajta", action="store_true",
                   help="ponovo traži sajt samo za firme kojima sajt nije pronađen")
    p.add_argument("--istovremeno", type=int, default=3, help="koliko sajtova paralelno (podrazumevano 3)")
    p.add_argument("--bez-sheeta", action="store_true")
    p.add_argument("--sajt", help="proveri samo ovaj sajt i ispiši rezultat")
    args = p.parse_args()

    if args.sajt:
        asyncio.run(single_site(args.sajt))
        return 0

    conn = db.connect(config.DB_PATH)
    if not args.bez_sheeta:
        sheets.sync(conn)  # prvo pokupi tvoje izmene i odjave, da ne proveravamo nekog ko je odjavljen

    leads = db.leads_to_analyze(conn, args.nisa, args.grad, args.ponovo, args.limit,
                                only_without_site=args.samo_bez_sajta)
    if not leads:
        print("Nema leadova za proveru. (Za ponovnu proveru dodaj --ponovo.)")
        return 0
    with_site = sum(1 for l in leads if l["website_type"] == "sajt")
    print(f"Proveravam {len(leads)} leadova ({with_site} sa sajtom, za ostale tražim sajt po imenu)...\n")

    started = time.time()
    results = asyncio.run(analyze_all(leads, conn, max(1, args.istovremeno)))
    minutes = (time.time() - started) / 60
    print(f"\nGotovo za {minutes:.1f} min.")

    if not args.bez_sheeta:
        info = sheets.sync(conn)
        print(f"Sheet osvežen: {info['rows']} redova.")
        for w in info.get("warnings", []):
            print(f"NAPOMENA: {w}")
    conn.close()

    top = sorted(results, key=lambda x: x[1], reverse=True)[:5]
    lines = [f"{score} · {name}: {issues[0] if issues else '—'}" for name, score, issues in top]
    notify.send(f"🔧 Provereno {len(results)} leadova za {minutes:.0f} min\n\nNajveće prilike:\n" + "\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
