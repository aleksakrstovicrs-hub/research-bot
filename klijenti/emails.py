"""Korak 3: javno objavljen poslovni mejl sa sajta firme.

Pravila (ZZPL/GDPR):
- uzimamo samo adrese koje su vidljivo objavljene na sajtu firme (ili na mapi),
- ne pogađamo adrese (info@domen) i ne koristimo servise trećih strana,
- za svaku adresu pamtimo stranicu na kojoj je nađena,
- poštujemo robots.txt i otvaramo najviše nekoliko stranica po sajtu.
"""
import asyncio
import html as htmllib
import json
import re
from urllib.parse import unquote, urljoin, urlparse

from .niche import simplify

EMAIL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}")

# "info [at] firma [dot] rs", "info(at)firma.rs", "info{at}firma.rs"
_AT = r"\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|\s+at\s+|\[@\])\s*"
_DOT = r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\s+dot\s+|\.)\s*"
OBFUSCATED_RE = re.compile(
    rf"([A-Za-z0-9][A-Za-z0-9._%+-]{{0,63}}){_AT}([A-Za-z0-9-]+(?:{_DOT}[A-Za-z0-9-]+)*{_DOT}[A-Za-z]{{2,24}})",
    re.I,
)

IGNORED_DOMAINS = {
    "example.com", "example.org", "domain.com", "email.com", "yourdomain.com", "mysite.com",
    "sentry.io", "sentry-next.wixpress.com", "wixpress.com", "wix.com", "godaddy.com",
    "wordpress.org", "wordpress.com", "w3.org", "schema.org", "jquery.com", "googlemail.com.png",
}
IGNORED_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".ico")
IGNORED_LOCAL = {"you", "your", "name", "ime", "email", "korisnik", "user", "test", "noreply", "no-reply"}

FREE_PROVIDERS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com",
                  "eunet.rs", "open.telekom.rs", "mts.rs", "sbb.rs", "ptt.rs", "yandex.com", "mail.ru"}

GENERIC_LOCAL = {
    "info", "kontakt", "contact", "office", "ordinacija", "recepcija", "prijem", "zakazivanje",
    "hello", "mail", "admin", "sales", "prodaja", "podrska", "support", "booking", "rezervacije",
    "rezervacija", "marketing", "klinika", "dental", "salon", "studio", "servis", "biro", "kancelarija",
    "pisarnica", "apoteka", "hotel", "restoran", "posta", "webmaster", "team", "tim", "upit", "upiti",
}

CONTACT_WORDS = ("kontakt", "contact", "o-nama", "onama", "about", "impressum", "lokacija", "zakazivanje")


# ================================================================ čiste funkcije

def _clean(email: str) -> str | None:
    email = unquote(email).strip().strip(".,;:()[]<>\"'").lower()
    if email.startswith("mailto:"):
        email = email[7:]
    email = email.split("?")[0]
    if not EMAIL_RE.fullmatch(email):
        return None
    local, domain = email.rsplit("@", 1)
    if domain in IGNORED_DOMAINS or domain.endswith(IGNORED_SUFFIXES) or email.endswith(IGNORED_SUFFIXES):
        return None
    if local in IGNORED_LOCAL or re.fullmatch(r"[0-9a-f]{16,}", local):  # heš adrese (Sentry i sl.)
        return None
    return email


def decode_cfemail(encoded: str) -> str | None:
    """Cloudflare „email protection“ zapis."""
    try:
        key = int(encoded[:2], 16)
        return "".join(chr(int(encoded[i:i + 2], 16) ^ key) for i in range(2, len(encoded), 2))
    except ValueError:
        return None


def extract_emails(page_html: str) -> list[str]:
    """Sve adrese sa stranice, redom kojim se pojavljuju, bez duplikata."""
    found: list[str] = []
    text = htmllib.unescape(page_html)

    candidates = []
    candidates += re.findall(r"""mailto:([^"'?\s>]+)""", text, re.I)
    candidates += [d for enc in re.findall(r"""data-cfemail=["']([0-9a-fA-F]+)""", text)
                   if (d := decode_cfemail(enc))]
    candidates += [d for enc in re.findall(r"/cdn-cgi/l/email-protection#([0-9a-fA-F]+)", text)
                   if (d := decode_cfemail(enc))]
    for block in re.findall(r"""<script[^>]+application/ld\+json[^>]*>(.*?)</script>""", text, re.I | re.S):
        candidates += re.findall(r'"email"\s*:\s*"([^"]+)"', block)
    plain = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    plain = re.sub(r"<[^>]+>", " ", plain)
    candidates += EMAIL_RE.findall(plain)
    for local, domain in OBFUSCATED_RE.findall(plain):
        domain = re.sub(_DOT, ".", domain, flags=re.I)
        candidates.append(f"{local}@{domain}")

    for c in candidates:
        email = _clean(c)
        if email and email not in found:
            found.append(email)
    return found


def classify(email: str, firm_name: str = "") -> str:
    """'genericki' (info@...), 'poslovni' (ime firme u adresi) ili 'licni' (verovatno osoba)."""
    local, domain = email.split("@", 1)
    parts = set(re.split(r"[._+-]", local))
    if local in GENERIC_LOCAL or parts & GENERIC_LOCAL:
        return "genericki"
    firm_words = [w for w in re.split(r"[^a-z0-9]+", simplify(firm_name)) if len(w) >= 4]
    if any(w in local for w in firm_words):
        return "poslovni"
    domain_root = domain.split(".")[0]
    if domain not in FREE_PROVIDERS and domain_root in local:
        return "poslovni"
    return "licni"


_RANK = {"genericki": 0, "poslovni": 1, "licni": 2}


def choose_best(emails: list[dict], site_domain: str | None) -> dict | None:
    """Najbolja adresa za kontakt: generička na domenu firme, pa ostale. Lične na kraju."""
    usable = [e for e in emails if e.get("mx_ok") is not False]
    if not usable:
        return None

    def key(e):
        same = 0 if site_domain and e["email"].endswith("@" + site_domain) else 1
        return (_RANK.get(e["kind"], 3), same)

    return sorted(usable, key=key)[0]


def contact_links(page_html: str, base_url: str, limit: int = 3) -> list[str]:
    """Linkovi ka kontakt stranicama na istom sajtu."""
    host = urlparse(base_url).netloc.lower().removeprefix("www.")
    links = []
    for href, label in re.findall(r"""<a\b[^>]*href=["']([^"'#]+)["'][^>]*>(.*?)</a>""", page_html, re.I | re.S):
        url = urljoin(base_url, href.strip())
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or parsed.netloc.lower().removeprefix("www.") != host:
            continue
        haystack = simplify(unquote(parsed.path) + " " + re.sub(r"<[^>]+>", " ", label))
        if any(w in haystack for w in CONTACT_WORDS):
            url = url.split("#")[0]
            if url not in links and url.rstrip("/") != base_url.rstrip("/"):
                links.append(url)
    links.sort(key=lambda u: 0 if ("kontakt" in u or "contact" in u) else 1)
    return links[:limit]


# ================================================================ mreža

def mx_ok(domain: str) -> bool | None:
    """True ako domen može da prima poštu (MX, ili bar A zapis). None ako ne možemo da proverimo."""
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        import socket
        try:
            socket.getaddrinfo(domain, 25)
            return True
        except socket.gaierror:
            return False
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 8
    for rtype in ("MX", "A"):
        try:
            if resolver.resolve(domain, rtype):
                return True
        except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            return False
        except (dns.resolver.NoAnswer, dns.exception.Timeout):
            continue
        except dns.exception.DNSException:
            return None
    return False


async def _robots(client, base_url: str, user_agent: str):
    from urllib.robotparser import RobotFileParser

    rp = RobotFileParser()
    try:
        r = await client.get(urljoin(base_url, "/robots.txt"))
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except Exception:  # noqa: BLE001 — bez robots.txt sve je dozvoljeno
        rp.parse([])
    return lambda url: rp.can_fetch(user_agent, url)


async def scan_site(url: str, firm_name: str, browser=None, max_pages: int = 4) -> dict:
    """Obilazi početnu i kontakt stranice. Vraća {'emails': [...], 'pages': n, 'blocked': n}."""
    import httpx

    from . import config

    found: dict[str, dict] = {}
    pages = blocked = 0
    ua = config.USER_AGENT.split("/")[0]
    headers = {"User-Agent": config.USER_AGENT}

    async with httpx.AsyncClient(follow_redirects=True, timeout=20, headers=headers, verify=False) as client:
        allowed = await _robots(client, url, ua)
        queue, seen = [url], set()
        while queue and pages < max_pages:
            page_url = queue.pop(0)
            if page_url in seen:
                continue
            seen.add(page_url)
            if not allowed(page_url):
                blocked += 1
                continue
            try:
                r = await client.get(page_url)
            except httpx.HTTPError:
                continue
            pages += 1
            if r.status_code >= 400 or "html" not in r.headers.get("content-type", "html"):
                continue
            body = r.text
            for email in extract_emails(body):
                found.setdefault(email, {"email": email, "source_url": str(r.url)})
            if page_url == url:
                queue += [l for l in contact_links(body, str(r.url)) if l not in seen]
            await asyncio.sleep(1)  # pristojna pauza između stranica

    # Sajtovi koji mejl ubacuju JavaScript-om (Wix i sl.): probamo renderovanu početnu i kontakt stranu.
    if not found and browser is not None:
        ctx = await browser.new_context(ignore_https_errors=True)
        try:
            page = await ctx.new_page()
            for page_url in list(seen)[:2]:
                if not allowed(page_url):
                    continue
                try:
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
                    await page.wait_for_timeout(2500)
                    for email in extract_emails(await page.content()):
                        found.setdefault(email, {"email": email, "source_url": page_url})
                except Exception:  # noqa: BLE001
                    continue
        finally:
            await ctx.close()

    emails = list(found.values())
    for e in emails:
        e["kind"] = classify(e["email"], firm_name)
        e["mx_ok"] = await asyncio.to_thread(mx_ok, e["email"].split("@", 1)[1])
    return {"emails": emails, "pages": pages, "blocked": blocked}
