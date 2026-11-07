"""Ceo research proces za jednu nišu i grad (koraci 1–6), jedan za drugim.

Koristi ga Telegram bot (/trazi), a može i ručno:
    python pipeline.py stomatolozi Beograd
    python pipeline.py "auto servisi" "Novi Sad"
"""
import asyncio
import sys
import time
from typing import Awaitable, Callable

import analyze
import design_review
import discover
import find_emails
import pagespeed
import status
from klijenti import config, db, notify, sheets
from klijenti.niche import resolve_niche

Progress = Callable[[str], Awaitable[None]]


async def _print(msg: str) -> None:
    print(msg)


async def run(niche_text: str, city: str, progress: Progress = _print, parallel: int = 3) -> str:
    """Vraća završni izveštaj (tekst). Čeka ako upravo radi izveštaj za nekog klijenta."""
    from klijenti.joblock import heavy_job

    async with heavy_job():
        return await _run(niche_text, city, progress, parallel)


async def _run(niche_text: str, city: str, progress: Progress = _print, parallel: int = 3) -> str:
    niche, _ = resolve_niche(niche_text)
    started = time.time()
    was_muted = notify.MUTED
    notify.MUTED = True  # koraci ne šalju svoje poruke; napredak javlja ova funkcija
    try:
        await progress(f"🔎 1/6 Tražim: {niche}, {city}...")
        code = await asyncio.to_thread(discover.run, niche_text, city, False, True)
        if code != 0:
            raise RuntimeError("pronalaženje firmi nije uspelo")

        conn = db.connect(config.DB_PATH)
        try:
            await asyncio.to_thread(sheets.sync, conn)  # tvoje izmene i odjave pre provere

            leads = db.leads_to_analyze(conn, niche, city)
            await progress(f"🔧 2/6 Proveravam sajtove: {len(leads)} firmi...")
            if leads:
                await analyze.analyze_all(leads, conn, parallel)

            leads = db.leads_for_email_scan(conn, niche, city)
            await progress(f"✉️ 3/6 Tražim mejlove: {len(leads)} firmi...")
            if leads:
                await find_emails.run_all(leads, conn, parallel)

            leads = db.leads_for_speed(conn, niche, city)
            await progress(f"⚡ 4/6 Merim brzinu: {len(leads)} sajtova...")
            if leads:
                await pagespeed.run_all(leads, conn, parallel)

            leads = db.leads_for_design(conn, niche, city)
            if config.get("OPENAI_API_KEY") and config.get("OPENAI_MODEL"):
                await progress(f"🎨 5/6 AI ocenjuje dizajn: {len(leads)} sajtova...")
                if leads:
                    await design_review.run_all(leads, conn, parallel)
            else:
                await progress("🎨 5/6 Preskačem AI ocenu (nema OPENAI ključa ili modela u .env)")

            await progress("📊 6/6 Kategorije i Sheet...")
            result = await asyncio.to_thread(sheets.sync, conn)
            report = status.build_report(db.category_report(conn), result.get("promoted", 0),
                                         db.min_score_for_contact())
        finally:
            conn.close()
    finally:
        notify.MUTED = was_muted

    minutes = (time.time() - started) / 60
    return f"✅ Gotovo: {niche}, {city} ({minutes:.0f} min)\n\n{report}"


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    try:
        print(asyncio.run(run(sys.argv[1], " ".join(sys.argv[2:]))))
    except (ValueError, RuntimeError) as e:
        print(f"GREŠKA: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
