"""Korak 2: tehnička provera sajta.

Čiste funkcije (bez mreže) su na vrhu i pokrivene testovima. Ispod su mrežni delovi:
HTTP provera (httpx) i renderovanje u Chromium-u (Playwright).
"""
import asyncio
import re
import socket
import ssl
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import config

CURRENT_YEAR = datetime.now().year

UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 " + config.USER_AGENT
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1 " + config.USER_AGENT
)

# ================================================================ čiste funkcije

_MARK_RE = re.compile(r"©|&copy;|&#169;|\(c\)|copyright|sva prava", re.I)
_YEAR_RE = re.compile(r"\b(199\d|20\d{2})\b")


def find_footer_year(*texts: str | None) -> int | None:
    """Najnovija godina pored ©/copyright, u bilo kom od tekstova (HTML ili renderovan tekst)."""
    years = []
    for text in texts:
        if not text:
            continue
        for m in _MARK_RE.finditer(text):
            window = text[max(0, m.start() - 40): m.end() + 60]
            years += [int(y) for y in _YEAR_RE.findall(window)]
    years = [y for y in years if y <= CURRENT_YEAR]
    return max(years) if years else None


def has_viewport(html: str) -> bool:
    for tag in re.findall(r"<meta\b[^>]*>", html, re.I):
        if re.search(r"""name\s*=\s*["']?viewport""", tag, re.I):
            return True
    return False


def _generator(html: str) -> str:
    for tag in re.findall(r"<meta\b[^>]*>", html, re.I):
        if re.search(r"""name\s*=\s*["']?generator""", tag, re.I):
            m = re.search(r"""content\s*=\s*["']([^"']+)""", tag, re.I)
            if m:
                return m.group(1)
    return ""


def _major(version: str | None) -> int | None:
    m = re.match(r"(\d+)", version or "")
    return int(m.group(1)) if m else None


def detect_platform(html: str) -> tuple[str | None, list[str]]:
    """Vraća (platforma, spisak problema sa zastarelom tehnologijom)."""
    low = html.lower()
    gen = _generator(html)
    gen_low = gen.lower()
    platform, issues = None, []

    if "wordpress" in gen_low or "/wp-content/" in low or "/wp-includes/" in low:
        platform = "WordPress"
        m = re.search(r"wordpress\s*([\d.]+)", gen_low)
        if m and (_major(m.group(1)) or 99) < 6:
            issues.append(f"Stara verzija WordPress-a ({m.group(1)}), bez bezbednosnih zakrpa")
    elif "joomla" in gen_low or "/media/jui/" in low or "/components/com_" in low:
        platform = "Joomla"
        m = re.search(r"joomla!?\s*-?\s*(?:open source content management\s*-?\s*version\s*)?([\d.]+)", gen_low)
        if m and (_major(m.group(1)) or 99) < 4:
            issues.append(f"Stara verzija Joomle ({m.group(1)}), bez bezbednosnih zakrpa")
    elif "drupal" in gen_low or "/sites/default/files/" in low:
        platform = "Drupal"
        m = re.search(r"drupal\s*([\d.]+)", gen_low)
        if m and (_major(m.group(1)) or 99) < 9:
            issues.append(f"Stara verzija Drupala ({m.group(1)})")
    elif "wix.com" in low or "wixstatic.com" in low:
        platform = "Wix"
    elif "squarespace" in low:
        platform = "Squarespace"
    elif "cdn.shopify.com" in low:
        platform = "Shopify"
    elif "webflow" in gen_low or "webflow.com" in low:
        platform = "Webflow"
    elif gen:
        platform = gen.split()[0]

    jq = re.search(r"jquery[.-]?(\d+)\.(\d+)(?:\.\d+)?(?:\.min)?\.js", low) or re.search(
        r"jquery(?:\.min)?\.js\?ver=(\d+)\.(\d+)", low
    )
    if jq and int(jq.group(1)) == 1:
        issues.append(f"Zastarela jQuery biblioteka ({jq.group(1)}.{jq.group(2)}), poznati sigurnosni propusti")

    if ".swf" in low or "application/x-shockwave-flash" in low:
        issues.append("Koristi Flash, koji nijedan pregledač više ne prikazuje")

    return platform, issues


_PARKED = ["domain is for sale", "this domain may be for sale", "buy this domain", "domen je na prodaju",
           "domain parking", "parked free", "sedoparking", "parkingcrew", "this domain is parked"]
_SUSPENDED = ["account suspended", "account has been suspended", "this account has been suspended",
              "nalog je suspendovan", "hosting je istekao"]
_DEFAULT_PAGE = ["apache2 ubuntu default page", "apache2 debian default page", "welcome to nginx!",
                 "it works!", "index of /", "test page for the apache", "default web site page",
                 "web server's default page", "future home of something quite cool"]
_CONSTRUCTION = ["under construction", "coming soon", "sajt je u izradi", "stranica je u izradi",
                 "sajt u izradi", "website is under", "maintenance mode", "uskoro novi sajt"]


def page_kind(text: str | None) -> str | None:
    """Prepoznaje stranice koje nisu pravi sajt: parkiran domen, suspendovan hosting itd."""
    if not text:
        return None
    low = text.lower()
    if any(p in low for p in _PARKED):
        return "parkiran"
    if any(p in low for p in _SUSPENDED):
        return "suspendovan"
    if len(low) < 3000 and any(p in low for p in _DEFAULT_PAGE):
        return "podrazumevana"
    if len(low) < 2500 and any(p in low for p in _CONSTRUCTION):
        return "u_izradi"
    return None


CATEGORIES = ["sajt_ne_radi", "bez_sajta", "samo_drustvene", "nebezbedan", "nije_za_telefon",
              "zastareo", "spor", "dobar_sajt", "nepoznato"]


def categorize(website_type: str | None, r: dict | None) -> str:
    """Glavni razlog za kontakt, po prioritetu. Bot za slanje po njoj bira šablon mejla."""
    if website_type == "nema":
        return "bez_sajta"
    if website_type == "drustvene_mreze":
        return "samo_drustvene"
    if not r or r.get("error") == "provera":
        return "nepoznato"
    if r.get("error") or (r.get("http_status") or 0) >= 400 or r.get("page_kind") in (
        "parkiran", "suspendovan", "podrazumevana", "u_izradi"
    ):
        return "sajt_ne_radi"
    if r.get("ssl") in ("nema", "nevazeci"):
        return "nebezbedan"
    if r.get("viewport") is False or r.get("mobile_overflow"):
        return "nije_za_telefon"
    year = r.get("footer_year")
    ai = r.get("ai_score") if r.get("ai_usable") else None
    if (ai is not None and ai <= 5) or (year and year <= CURRENT_YEAR - 3) or r.get("platform_issues"):
        return "zastareo"
    psi, lcp = r.get("psi_score"), r.get("lcp_ms")
    if (psi is not None and psi < 50) or (lcp is not None and lcp > 10000):
        return "spor"
    return "dobar_sajt"


_ERROR_LABELS = {
    "dns": "domen ne postoji ili je istekao",
    "timeout": "server ne odgovara",
    "connect": "server odbija vezu",
}


def assess(website_type: str | None, r: dict | None) -> tuple[list[str], int]:
    """Iz rezultata provere pravi spisak nedostataka (srpski, za ljude) i ocenu prilike 0–100.

    Veća ocena = veća šansa da im trebaš.
    """
    # Ovde smo već probali da nađemo sajt po imenu firme i nismo ga potvrdili.
    # Ipak ne tvrdimo da sajta nema: možda postoji pod imenom koje nismo pogodili.
    if website_type == "nema":
        return ["Sajt nije pronađen (ni na mapi ni po imenu firme): proveriti pre kontakta"], 70
    if website_type == "drustvene_mreze":
        return ["Sajt nije pronađen, firma ima samo stranicu na društvenim mrežama: proveriti pre kontakta"], 75
    if not r:
        return [], 0

    if r.get("error") == "provera":
        return ["Automatska provera nije uspela, pogledati ručno"], 0
    if r.get("error"):
        return [f"Sajt ne radi ({_ERROR_LABELS.get(r['error'], r['error'])})"], 85
    status = r.get("http_status") or 0
    if status >= 400:
        return [f"Sajt ne radi (greška {status})"], 85

    kind = r.get("page_kind")
    if kind == "parkiran":
        return ["Domen je parkiran ili na prodaju, sajta nema"], 85
    if kind == "suspendovan":
        return ["Hosting je suspendovan, sajt ne radi"], 85
    if kind == "podrazumevana":
        return ["Na domenu je samo podrazumevana stranica servera, sajta nema"], 85

    issues, score = [], 0
    if kind == "u_izradi":
        issues.append("Sajt stoji „u izradi“")
        score += 60

    ssl_state = r.get("ssl")
    if ssl_state == "nema":
        issues.append("Nema SSL: Chrome sajt označava kao „Nije bezbedno“")
        score += 30
    elif ssl_state == "nevazeci":
        issues.append("SSL sertifikat je nevažeći: posetioci dobijaju upozorenje pre ulaska")
        score += 30
    else:
        days = r.get("ssl_days_left")
        if days is not None and days < 15:
            issues.append(f"SSL sertifikat ističe za {days} dana")
            score += 10
        if r.get("https_redirect") is False:
            issues.append("Adresa sa http:// ne preusmerava na bezbednu https:// verziju")
            score += 10

    if r.get("viewport") is False or r.get("mobile_overflow"):
        issues.append("Nije prilagođen telefonu: sadržaj se sitni ili izlazi van ekrana")
        score += 25

    year = r.get("footer_year")
    if year and year <= CURRENT_YEAR - 3:
        issues.append(f"U footeru piše © {year}: sajt deluje napušteno")
        score += 15
    elif year and year <= CURRENT_YEAR - 2:
        issues.append(f"U footeru piše © {year}")
        score += 8

    platform_issues = r.get("platform_issues") or []
    issues += platform_issues
    score += min(25, 12 * len(platform_issues))

    ai = r.get("ai_score")
    if r.get("ai_usable") and ai:
        era = r.get("ai_era")
        era_note = f" (deluje kao iz perioda {era})" if era and era.lower() != "savremen" else ""
        ai_issues = list(r.get("ai_issues") or [])
        if ai <= 3:
            issues.append(f"Dizajn deluje zastarelo{era_note}: AI ocena {ai}/10")
            issues += ai_issues[:2]
            score += 20
        elif ai <= 5:
            issues.append(f"Dizajn deluje staro ili amaterski{era_note}: AI ocena {ai}/10")
            issues += ai_issues[:2]
            score += 12
        elif ai == 6:
            issues += ai_issues[:1]
            score += 5

    psi, lcp = r.get("psi_score"), r.get("lcp_ms")
    # Google: glavni sadržaj (LCP) do 2,5 s je dobro, preko 4 s je loše
    slow_lcp = lcp is not None and lcp > 4000
    if psi is not None and (psi < 70 or slow_lcp):
        load = f", glavni sadržaj se učitava za {lcp / 1000:.1f} s" if lcp else ""
        if psi < 50 or (lcp or 0) > 10000:
            issues.append(f"Spor na telefonu: Google PageSpeed {psi}/100{load}")
            score += 20 if psi < 30 or (lcp or 0) > 15000 else 15
        elif slow_lcp:
            issues.append(f"Sporo učitavanje na telefonu{load} (Google preporučuje do 2,5 s)")
            score += 10
        else:
            issues.append(f"Prosečna brzina na telefonu: Google PageSpeed {psi}/100{load}")
            score += 5

    if r.get("missing_title"):
        issues.append("Stranica nema naslov (title), loše za Google")
        score += 5

    return issues, min(score, 100)


# ================================================================ mreža

def _host(url: str) -> str:
    return urlparse(url).netloc.split("@")[-1].split(":")[0]


def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443)
        return True
    except (socket.gaierror, UnicodeError):
        return False


def _cert_days_left(host: str) -> int | None:
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as s:
                not_after = s.getpeercert()["notAfter"]
        expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), tz=timezone.utc)
        return (expires - datetime.now(timezone.utc)).days
    except Exception:  # noqa: BLE001
        return None


def _is_cert_error(e: Exception) -> bool:
    text = str(e).lower()
    return "certificate" in text or "ssl" in text or "tls" in text


async def check_http(url: str) -> dict:
    """Dostupnost, SSL, preusmerenje i HTML početne strane."""
    import httpx

    res: dict = {"ssl": None, "https_redirect": None, "error": None}
    host = _host(url)
    if not host or not await asyncio.to_thread(_resolves, host):
        res["error"] = "dns"
        return res

    parsed = urlparse(url)
    path = parsed.path or "/"
    https_url, http_url = f"https://{parsed.netloc}{path}", f"http://{parsed.netloc}{path}"
    opts = dict(follow_redirects=True, timeout=20, headers={"User-Agent": UA_DESKTOP})

    response = None
    async with httpx.AsyncClient(**opts) as client:
        try:
            response = await client.get(https_url)
            res["ssl"] = "ok"
        except httpx.TimeoutException:
            res["error"] = "timeout"
        except httpx.HTTPError as e:
            if _is_cert_error(e):
                res["ssl"] = "nevazeci"
                try:
                    async with httpx.AsyncClient(verify=False, **opts) as insecure:
                        response = await insecure.get(https_url)
                except httpx.HTTPError:
                    response = None

        if response is None:
            try:
                response = await client.get(http_url)
                res["error"] = None
                if response.url.scheme == "http" and res["ssl"] != "nevazeci":
                    res["ssl"] = "nema"
            except httpx.TimeoutException:
                res["error"] = res["error"] or "timeout"
            except httpx.HTTPError:
                res["error"] = res["error"] or "connect"
        elif res["ssl"] == "ok":
            try:
                r2 = await client.get(http_url)
                res["https_redirect"] = r2.url.scheme == "https"
            except httpx.HTTPError:
                res["https_redirect"] = None

    if response is None:
        res["error"] = res["error"] or "connect"
        return res

    res["error"] = None
    res["http_status"] = response.status_code
    res["final_url"] = str(response.url)
    if response.url.scheme == "http" and res["ssl"] == "ok":
        res["ssl"] = "nema"
    res["html"] = response.text[:2_000_000]
    if res["ssl"] == "ok":
        res["ssl_days_left"] = await asyncio.to_thread(_cert_days_left, _host(res["final_url"]))
    return res


async def render(browser, url: str, slug: str) -> dict:
    """Otvara sajt u Chromium-u: tekst, provera na 390 px, screenshotovi."""
    shots = config.DATA_DIR / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    out: dict = {}

    ctx = await browser.new_context(viewport={"width": 1366, "height": 768},
                                    user_agent=UA_DESKTOP, ignore_https_errors=True)
    try:
        page = await ctx.new_page()
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2500)
        out["browser_status"] = response.status if response else None
        out["final_url"] = page.url
        out["html"] = (await page.content())[:2_000_000]
        out["text"] = (await page.inner_text("body"))[:300_000]
        path = shots / f"{slug}_desktop.png"
        await page.screenshot(path=str(path))
        out["screenshot_desktop"] = str(path.relative_to(config.BASE_DIR))
    finally:
        await ctx.close()

    ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True,
                                    has_touch=True, device_scale_factor=2,
                                    user_agent=UA_MOBILE, ignore_https_errors=True)
    try:
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2500)
        m = await page.evaluate(
            "() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth})"
        )
        out["layout_width"] = m["iw"]
        out["mobile_overflow"] = m["sw"] > m["iw"] + 8 or m["iw"] > 500
        path = shots / f"{slug}_mobile.png"
        await page.screenshot(path=str(path))
        out["screenshot_mobile"] = str(path.relative_to(config.BASE_DIR))
    finally:
        await ctx.close()
    return out


# Kodovi kojima sajtovi često odbijaju programe (zaštita od botova), a ne znače da sajt ne radi.
BLOCKED_STATUSES = {401, 403, 405, 406, 429, 503}
_CHALLENGE = ["just a moment", "checking your browser", "attention required", "access denied",
              "verify you are human", "ddos protection", "enable javascript and cookies"]


def is_challenge(text: str | None) -> bool:
    low = (text or "").lower()
    return len(low) < 4000 and any(p in low for p in _CHALLENGE)


def browser_verdict(http_result: dict, browser_status: int | None, text: str | None) -> str:
    """Šta zaključujemo kad je program odbijen, a pravi browser je probao isti sajt.

    'radi'     — browser je učitao pravu stranicu: sajt radi, samo blokira programe
    'zastita'  — i browser je dobio zaštitnu stranicu: ne znamo, proveriti ručno
    'ne_radi'  — browser potvrđuje grešku
    """
    if is_challenge(text):
        return "zastita"
    if browser_status is not None and browser_status >= 400:
        return "zastita" if browser_status in BLOCKED_STATUSES else "ne_radi"
    if text and len(text.strip()) >= 50:
        return "radi"
    return "ne_radi"


def needs_browser_retry(r: dict) -> bool:
    return r.get("error") in ("timeout", "connect") or r.get("http_status") in BLOCKED_STATUSES


async def analyze_site(url: str, slug: str, browser=None) -> dict:
    """Cela provera jednog sajta. Vraća rečnik spreman za bazu (bez sirovog HTML-a)."""
    r = await check_http(url)
    html = r.pop("html", "") or ""
    rendered = None

    if r.get("error") or (r.get("http_status") or 0) >= 400:
        if browser is None or not needs_browser_retry(r):
            return r
        # Program je odbijen ili nije dobio odgovor: probamo kao pravi posetilac.
        try:
            rendered = await render(browser, r.get("final_url") or url, slug)
        except Exception:  # noqa: BLE001 — ni browser ne može da otvori: ostaje prvobitni zaključak
            return r
        verdict = browser_verdict(r, rendered.get("browser_status"), rendered.get("text"))
        if verdict == "ne_radi":
            return r
        if verdict == "zastita":
            return {**r, "error": "provera",
                    "render_error": "sajt ima zaštitu od botova, proveriti ručno"}
        r.update(error=None, http_status=rendered.get("browser_status") or 200,
                 final_url=rendered.get("final_url") or url)
        if r["final_url"].startswith("http://") and r.get("ssl") != "nevazeci":
            r["ssl"] = "nema"
        html = rendered.get("html") or ""

    r["viewport"] = has_viewport(html)
    r["missing_title"] = not re.search(r"<title[^>]*>\s*\S", html, re.I)
    r["platform"], r["platform_issues"] = detect_platform(html)

    if browser is not None and rendered is None:
        try:
            rendered = await render(browser, r["final_url"], slug)
        except Exception as e:  # noqa: BLE001 — sajt koji ne može da se renderuje i dalje ima HTTP podatke
            r["render_error"] = str(e)[:200]

    text = None
    if rendered:
        text = rendered.pop("text", None)
        for key in ("html", "browser_status", "final_url"):
            rendered.pop(key, None)
        r.update(rendered)

    plain = re.sub(r"<[^>]+>", " ", html)
    r["page_kind"] = page_kind(text) or page_kind(plain)
    r["footer_year"] = find_footer_year(text, html)
    return r
