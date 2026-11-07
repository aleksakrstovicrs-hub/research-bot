"""Korak 4: brzina na telefonu (Google PageSpeed).

Primeri:
    python pagespeed.py                    # svi sajtovi koji rade, a još nisu mereni
    python pagespeed.py --limit 5
    python pagespeed.py --ponovo
    python pagespeed.py --sajt https://primer.rs     # samo prikaži
"""
import argparse
import asyncio
import sys
import time

from klijenti import config, db, notify, sheets, speed


async def run_all(leads: list, conn, parallel: int) -> dict:
    import httpx

    stats = {"measured": 0, "slow": 0, "failed": 0}
    sem = asyncio.Semaphore(parallel)

    async with httpx.AsyncClient() as client:
        async def one(lead):
            async with sem:
                return lead, await speed.measure(client, lead["url"])

        tasks = [asyncio.create_task(one(lead)) for lead in leads]
        for i, task in enumerate(asyncio.as_completed(tasks), 1):
            lead, result = await task
            db.save_speed(conn, lead["id"], result)
            issues, score = db.rescore(conn, lead["id"])
            conn.commit()
            if result.get("psi_score") is not None:
                stats["measured"] += 1
                stats["slow"] += result["psi_score"] < 50
                lcp = result.get("lcp_ms")
                info = f"{result['psi_score']:3d}/100" + (f"  LCP {lcp / 1000:.1f} s" if lcp else "")
            else:
                stats["failed"] += 1
                info = f"greška: {result.get('psi_error')}"
            print(f"[{i}/{len(leads)}] {lead['name'][:40]:40}  {info}   → ocena prilike {score}")
    return stats


async def single(url: str) -> None:
    import httpx

    async with httpx.AsyncClient() as client:
        result = await speed.measure(client, url)
    for k, v in result.items():
        print(f"  {k:10} {v}")


def main() -> int:
    p = argparse.ArgumentParser(description="Brzina sajta na telefonu (korak 4).")
    p.add_argument("--nisa")
    p.add_argument("--grad")
    p.add_argument("--limit", type=int)
    p.add_argument("--ponovo", action="store_true")
    p.add_argument("--istovremeno", type=int, default=3)
    p.add_argument("--bez-sheeta", action="store_true")
    p.add_argument("--sajt")
    args = p.parse_args()

    if args.sajt:
        asyncio.run(single(args.sajt))
        return 0

    conn = db.connect(config.DB_PATH)
    if not args.bez_sheeta:
        sheets.sync(conn)
    leads = db.leads_for_speed(conn, args.nisa, args.grad, args.ponovo, args.limit)
    if not leads:
        print("Nema sajtova za merenje. (Za ponovno merenje dodaj --ponovo.)")
        return 0

    print(f"Merim brzinu {len(leads)} sajtova (10–40 s po sajtu)...\n")
    started = time.time()
    stats = asyncio.run(run_all(leads, conn, max(1, args.istovremeno)))
    minutes = (time.time() - started) / 60
    print(f"\nGotovo za {minutes:.1f} min: izmereno {stats['measured']}, sporih (ispod 50) {stats['slow']}, "
          f"neuspelo {stats['failed']}.")

    if not args.bez_sheeta:
        info = sheets.sync(conn)
        print(f"Sheet osvežen: {info['rows']} redova.")
        for w in info.get("warnings", []):
            print(f"NAPOMENA: {w}")
    conn.close()
    notify.send(f"⚡ Brzina: izmereno {stats['measured']} sajtova, sporih {stats['slow']}, za {minutes:.0f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
