"""Provera zdravlja celog sistema. Ništa ne šalje i ništa ne menja.

Pokretanje (kao korisnik klijenti, u ~/app):
    python zdravlje.py            # brza provera (~30 s)
    python zdravlje.py --sve      # + PageSpeed merenje (još ~30 s)
"""
import asyncio
import subprocess
import sys
import time

from klijenti import config

RESULTS: list[tuple[str, str, str]] = []


def check(name: str):
    def deco(fn):
        def run():
            started = time.time()
            try:
                status, msg = fn()
            except Exception as e:  # noqa: BLE001
                status, msg = "GREŠKA", f"{type(e).__name__}: {e}"
            RESULTS.append((status, name, f"{msg}  ({time.time() - started:.1f} s)"))
        run.__name__ = fn.__name__
        return run
    return deco


REQUIRED = ["GOOGLE_API_KEY", "GOOGLE_SA_FILE", "SHEET_ID", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID",
            "OPENAI_API_KEY", "OPENAI_MODEL", "FROM_EMAIL", "SMTP_HOST", "SMTP_PASSWORD"]


@check("Podešavanja (.env)")
def env():
    missing = [k for k in REQUIRED if not config.get(k)]
    if missing:
        return "GREŠKA", "nedostaje: " + ", ".join(missing)
    return "OK", f"svih {len(REQUIRED)} obaveznih vrednosti je popunjeno"


@check("Baza")
def database():
    from klijenti import db
    conn = db.connect(config.DB_PATH)
    leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    statuses = dict(conn.execute("SELECT status, COUNT(*) FROM leads GROUP BY status").fetchall())
    msgs = dict(conn.execute("SELECT status, COUNT(*) FROM messages GROUP BY status").fetchall())
    conn.close()
    st = ", ".join(f"{k} {v}" for k, v in sorted(statuses.items())) or "prazno"
    ms = ", ".join(f"{k} {v}" for k, v in sorted(msgs.items())) or "nema poruka"
    return "OK", f"{leads} firmi ({st}) | mejlovi: {ms}"


@check("Niše")
def niches():
    from klijenti.botlogic import parse_search
    for text in ["it firme Novi Sad", "stomatolozi Beograd", "auto servisi, Niš"]:
        parse_search(text.split())
    return "OK", "„it firme Novi Sad“, „stomatolozi Beograd“ i „auto servisi, Niš“ se prepoznaju"


@check("Google Sheets")
def google_sheets():
    import gspread
    gc = gspread.service_account(filename=config.get("GOOGLE_SA_FILE"))
    sh = gc.open_by_key(config.get("SHEET_ID"))
    tabs = [ws.title for ws in sh.worksheets()]
    missing = [t for t in ("Leadovi", "Odjave") if t not in tabs]
    if missing:
        return "UPOZORENJE", f"tabela „{sh.title}“ nema tabove {missing} (napraviće ih prvi /status)"
    return "OK", f"tabela „{sh.title}“, tabovi: {', '.join(tabs)}"


@check("OpenStreetMap (Overpass)")
def overpass():
    from klijenti import osm
    q = '[out:json][timeout:25];nwr["amenity"="dentist"](44.80,20.45,44.82,20.48);out tags 3;'
    elements = osm.run_overpass(q, rounds=1)
    return "OK", f"odgovara ({len(elements)} rezultata u probnom upitu)"


@check("OpenAI")
def openai_check():
    from openai import OpenAI
    model = config.get("OPENAI_MODEL")
    ids = {m.id for m in OpenAI(api_key=config.get("OPENAI_API_KEY")).models.list()}
    if model not in ids:
        return "GREŠKA", f"ključ radi, ali model „{model}“ nije dostupan"
    return "OK", f"ključ radi, model {model} dostupan"


@check("Mejl (SMTP + IMAP)")
def mail():
    from klijenti import mailer
    return "OK", mailer.test_connection()


@check("Chromium (Playwright)")
def chromium():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.set_content("<title>ok</title>")
        title = page.title()
        b.close()
    return ("OK", "pokreće se i renderuje") if title == "ok" else ("GREŠKA", "ne renderuje stranicu")


@check("Telegram")
def telegram():
    import httpx
    r = httpx.get(f"https://api.telegram.org/bot{config.get('TELEGRAM_TOKEN')}/getMe", timeout=15).json()
    if not r.get("ok"):
        return "GREŠKA", str(r)[:150]
    return "OK", f"bot @{r['result']['username']}, šalje samo u chat {config.get('TELEGRAM_CHAT_ID')}"


@check("Servis bota (systemd)")
def service():
    state = subprocess.run(["systemctl", "is-active", "klijenti-bot"], capture_output=True, text=True).stdout.strip()
    if state != "active":
        return "GREŠKA", f"stanje: {state or 'nepoznato'} (kao root: systemctl restart klijenti-bot)"
    return "OK", "radi"


@check("Slanje mejlova")
def sending():
    from klijenti import db, mailer
    s = mailer.settings()
    conn = db.connect(config.DB_PATH)
    today, waiting = db.sent_today(conn), conn.execute(
        "SELECT COUNT(*) FROM messages WHERE status='odobren'").fetchone()[0]
    conn.close()
    info = (f"limit {s['daily_limit']} dnevno, {s['start_hour']}–{s['end_hour']}h radnim danima, "
            f"danas poslato {today}, čeka u redu {waiting}")
    if not s["enabled"]:
        return "UPOZORENJE", "ISKLJUČENO (SENDING_ENABLED=0) | " + info
    return "OK", "uključeno | " + info


@check("PageSpeed")
def pagespeed():
    import httpx
    from klijenti import speed

    async def run():
        async with httpx.AsyncClient() as c:
            return await speed.measure(c, "https://" + (config.get("SIGNATURE_WEB") or "aleksakrstovic.rs"), attempts=1)
    r = asyncio.run(run())
    if r.get("psi_score") is None:
        return "GREŠKA", r.get("psi_error", "nema ocene")
    return "OK", f"radi (tvoj sajt na telefonu: {r['psi_score']}/100)"


def main() -> int:
    checks = [env, database, niches, google_sheets, overpass, openai_check, mail, chromium, telegram, service, sending]
    if "--sve" in sys.argv:
        checks.append(pagespeed)
    print("Provera sistema...\n")
    for c in checks:
        c()
        status, name, msg = RESULTS[-1]
        mark = {"OK": "✅", "UPOZORENJE": "⚠️ ", "GREŠKA": "❌"}[status]
        print(f"{mark} {name:26} {msg}", flush=True)
    errors = sum(1 for r in RESULTS if r[0] == "GREŠKA")
    warnings = sum(1 for r in RESULTS if r[0] == "UPOZORENJE")
    print(f"\n{'Sve radi.' if not errors else f'{errors} provera ima grešku.'}"
          + (f" Upozorenja: {warnings}." if warnings else ""))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
