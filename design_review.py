"""Korak 5: AI ocena dizajna sa screenshotova.

Primeri:
    python design_review.py --limit 3      # proba
    python design_review.py                # svi sajtovi koji još nisu ocenjeni
    python design_review.py --ponovo
"""
import argparse
import asyncio
import sys
import time

from klijenti import checks, config, db, design, notify, sheets

AI_FIELDS = ("ai_usable", "ai_score", "ai_era", "ai_summary", "ai_issues")


def technical_issues(conn, lead_id: str) -> list[str]:
    """Nedostaci bez AI dela, da ih model ne bi ponavljao."""
    lead = conn.execute("SELECT website_type FROM leads WHERE id=?", (lead_id,)).fetchone()
    row = conn.execute("SELECT * FROM analyses WHERE lead_id=?", (lead_id,)).fetchone()
    r = {k: v for k, v in db.analysis_inputs(row).items() if k not in AI_FIELDS}
    return checks.assess(lead["website_type"], r)[0]


async def run_all(leads: list, conn, parallel: int) -> dict:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=config.get("OPENAI_API_KEY", required=True))
    stats = {"rated": 0, "outdated": 0, "failed": 0}
    sem = asyncio.Semaphore(parallel)

    async def one(lead):
        async with sem:
            desktop = config.BASE_DIR / lead["screenshot_desktop"]
            mobile = config.BASE_DIR / lead["screenshot_mobile"] if lead["screenshot_mobile"] else None
            known = technical_issues(conn, lead["id"])
            return lead, await design.review(client, desktop, mobile, lead["name"], lead["niche"] or "", known)

    tasks = [asyncio.create_task(one(lead)) for lead in leads]
    for i, task in enumerate(asyncio.as_completed(tasks), 1):
        lead, result = await task
        db.save_design(conn, lead["id"], result)
        _, score = db.rescore(conn, lead["id"])
        conn.commit()
        if result.get("ai_error"):
            stats["failed"] += 1
            info = f"greška: {result['ai_error']}"
        elif not result.get("ai_usable"):
            stats["failed"] += 1
            info = "screenshot neupotrebljiv"
        else:
            stats["rated"] += 1
            stats["outdated"] += result["ai_score"] <= 5
            info = f"{result['ai_score']}/10 ({result.get('ai_era') or '?'})  {result.get('ai_summary') or ''}"[:110]
        print(f"[{i}/{len(leads)}] {lead['name'][:35]:35}  {info}   → ocena prilike {score}")
    return stats


def main() -> int:
    p = argparse.ArgumentParser(description="AI ocena dizajna (korak 5).")
    p.add_argument("--nisa")
    p.add_argument("--grad")
    p.add_argument("--limit", type=int)
    p.add_argument("--ponovo", action="store_true")
    p.add_argument("--istovremeno", type=int, default=3)
    p.add_argument("--bez-sheeta", action="store_true")
    args = p.parse_args()

    conn = db.connect(config.DB_PATH)
    if not args.bez_sheeta:
        sheets.sync(conn)
    leads = db.leads_for_design(conn, args.nisa, args.grad, args.ponovo, args.limit)
    if not leads:
        print("Nema sajtova za AI ocenu. (Za ponovnu ocenu dodaj --ponovo.)")
        return 0

    print(f"AI ocenjuje dizajn {len(leads)} sajtova (model: {config.get('OPENAI_MODEL')})...\n")
    started = time.time()
    stats = asyncio.run(run_all(leads, conn, max(1, args.istovremeno)))
    minutes = (time.time() - started) / 60
    print(f"\nGotovo za {minutes:.1f} min: ocenjeno {stats['rated']}, zastarelih (5 ili manje) "
          f"{stats['outdated']}, neuspelo {stats['failed']}.")

    if not args.bez_sheeta:
        info = sheets.sync(conn)
        print(f"Sheet osvežen: {info['rows']} redova.")
        for w in info.get("warnings", []):
            print(f"NAPOMENA: {w}")
    conn.close()
    notify.send(f"🎨 AI dizajn: ocenjeno {stats['rated']} sajtova, zastarelih {stats['outdated']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
