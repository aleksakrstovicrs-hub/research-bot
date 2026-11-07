"""Korak 0: provera da su svi ključevi i servisi ispravno podešeni.

Pokretanje:  python provera.py
Svaka provera je nezavisna, pa vidiš tačno šta radi, a šta ne.
"""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"nedostaje {name} u .env")
    return value


def test_osm() -> str:
    # OpenStreetMap Overpass: stomatolozi u širem centru Beograda (bez ključa i billinga).
    query = """
    [out:json][timeout:25];
    nwr["amenity"="dentist"](44.75,20.35,44.86,20.55);
    out tags center;
    """
    r = httpx.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query},
        headers={"User-Agent": os.getenv("BOT_USER_AGENT", "ResearchBot/0.1")},
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    elements = r.json().get("elements", [])
    with_site = [e for e in elements if e.get("tags", {}).get("website")]
    sample = ", ".join(e["tags"].get("name", "?") for e in elements[:3])
    return f"{len(elements)} ordinacija, {len(with_site)} sa sajtom (npr. {sample})"


def test_pagespeed() -> str:
    # PageSpeed je spor (10–40 s po sajtu), to je normalno.
    r = httpx.get(
        "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
        params={
            "url": "https://aleksakrstovic.rs",
            "strategy": "mobile",
            "category": "performance",
            "key": env("GOOGLE_API_KEY"),
        },
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    score = r.json()["lighthouseResult"]["categories"]["performance"]["score"]
    return f"aleksakrstovic.rs, mobilni performance = {round(score * 100)}"


def test_sheets() -> str:
    import gspread

    gc = gspread.service_account(filename=env("GOOGLE_SA_FILE"))
    sh = gc.open_by_key(env("SHEET_ID"))
    tabs = [ws.title for ws in sh.worksheets()]
    return f"tabela '{sh.title}', tabovi: {tabs}"


def test_telegram() -> str:
    token = env("TELEGRAM_TOKEN")
    base = f"https://api.telegram.org/bot{token}"
    me = httpx.get(f"{base}/getMe", timeout=15).json()
    if not me.get("ok"):
        raise RuntimeError(me)
    msg = f"bot @{me['result']['username']}"

    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if chat_id:
        sent = httpx.post(
            f"{base}/sendMessage",
            json={"chat_id": chat_id, "text": "Provera: bot radi ✅"},
            timeout=15,
        ).json()
        if not sent.get("ok"):
            raise RuntimeError(sent)
        return msg + ", probna poruka poslata"

    updates = httpx.get(f"{base}/getUpdates", timeout=15).json().get("result", [])
    ids = {u["message"]["chat"]["id"] for u in updates if "message" in u}
    if ids:
        return msg + f" | upiši TELEGRAM_CHAT_ID u .env, nađeni id-jevi: {sorted(ids)}"
    return msg + " | pošalji botu /start pa pokreni proveru ponovo da nađemo chat id"


def test_openai() -> str:
    from openai import OpenAI

    client = OpenAI(api_key=env("OPENAI_API_KEY"))
    # Lista modela proverava ključ bez trošenja tokena.
    available = {m.id for m in client.models.list()}
    model = os.getenv("OPENAI_MODEL", "").strip()
    if not model:
        vision = sorted(m for m in available if m.startswith(("gpt-4", "gpt-5", "o")))[:8]
        return f"ključ radi | upiši OPENAI_MODEL u .env, npr. jedan od: {vision}"
    if model not in available:
        raise RuntimeError(f"ključ radi, ali model '{model}' nije dostupan na tvom nalogu")
    return f"ključ radi, model {model} dostupan"


def test_playwright() -> str:
    from playwright.sync_api import sync_playwright

    out = Path("data/screenshots")
    out.mkdir(parents=True, exist_ok=True)
    path = out / "provera_mobilni.png"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto("https://aleksakrstovic.rs", wait_until="load", timeout=45_000)
        page.screenshot(path=str(path))
        browser.close()
    return f"screenshot sačuvan: {path}"


TESTS = [
    ("OpenStreetMap (Overpass)", test_osm),
    ("PageSpeed Insights", test_pagespeed),
    ("Google Sheets", test_sheets),
    ("Telegram", test_telegram),
    ("OpenAI API", test_openai),
    ("Playwright/Chromium", test_playwright),
]


def main() -> int:
    failed = 0
    for name, fn in TESTS:
        try:
            print(f"[ OK ] {name}: {fn()}")
        except Exception as e:  # noqa: BLE001 — hoćemo da vidimo svaku grešku
            failed += 1
            print(f"[GREŠKA] {name}: {e}")
    print()
    print("Sve radi, spremni za korak 1." if not failed else f"{failed} provera nije prošlo.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
