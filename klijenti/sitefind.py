"""Pronalaženje sajta za firme kojima OpenStreetMap ne zna sajt.

Pogađamo domen iz imena firme (primer-ordinacija.rs, dental-plaza.co.rs...) i sajt prihvatamo
SAMO ako ga potvrdimo: isti broj telefona kao na mapi, ili ime firme + grad/ulica na stranici.
Bolje da propustimo sajt nego da firmi pripišemo tuđi.
"""
import asyncio
import re

from .niche import simplify

TLDS = ["rs", "co.rs", "com"]

# Reči koje opisuju delatnost ili pravni oblik, a obično nisu deo domena.
GENERIC = {
    "stomatoloska", "stomatoloski", "stomatolog", "ordinacija", "specijalisticka", "privatna",
    "poliklinika", "klinika", "ambulanta", "veterinarska", "apoteka", "salon", "frizerski",
    "kozmeticki", "restoran", "kafic", "pekara", "advokatska", "kancelarija", "agencija",
    "doo", "d.o.o", "d.o.o.", "sztr", "str", "szr", "pr", "ad", "od", "i", "za", "the",
}


def _words(name: str) -> list[str]:
    text = simplify(name).replace("&", " i ").replace("+", " plus ")
    # pravni oblici sa tačkama: d.o.o., a.d., s.z.r. ...
    text = re.sub(r"\b(?:d\.?\s*o\.?\s*o|a\.\s*d|s\.\s*z\.\s*t?\.?\s*r|s\.\s*t\.\s*r)\b\.?", " ", text)
    return [w for w in re.split(r"[^a-z0-9]+", text) if len(w) > 1 or w.isdigit()]


def slug_candidates(name: str) -> list[str]:
    """Moguća imena domena, od najverovatnijeg."""
    words = _words(name)
    core = [w for w in words if w not in GENERIC] or words
    slugs = []
    for parts in (core, words, [w for w in core if w != "dr"]):
        if not parts:
            continue
        for slug in ("".join(parts), "-".join(parts)):
            if 3 <= len(slug) <= 40 and slug not in slugs:
                slugs.append(slug)
    return slugs[:4]


def domain_candidates(name: str) -> list[str]:
    return [f"{slug}.{tld}" for slug in slug_candidates(name) for tld in TLDS]


_PHONE_RE = re.compile(r"(?:\+|00)?(?:381|0)[\s\-/().]*\d(?:[\s\-/().]*\d){5,10}")


def _phone_key(phone: str | None) -> str | None:
    """Poslednjih 8 cifara: isto za +381 11 123 4567 i 011/123-4567."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return digits[-8:] if len(digits) >= 8 else None


def phone_matches(phone: str | None, text: str) -> bool:
    key = _phone_key(phone)
    if not key or not text:
        return False
    return any(_phone_key(m) == key for m in _PHONE_RE.findall(text))


def name_matches(name: str, text: str) -> bool:
    """Sve bitne reči iz imena firme postoje na stranici."""
    page = " " + " ".join(_words(text)) + " "
    compact = page.replace(" ", "")
    significant = [w for w in _words(name) if w not in GENERIC and len(w) >= 3]
    return bool(significant) and all(
        f" {w} " in page or (len(w) >= 5 and w in compact) for w in significant
    )


def place_matches(city: str | None, address: str | None, text: str) -> bool:
    page = " ".join(_words(text))
    hints = []
    if city:
        hints.append(" ".join(_words(city)))
    if address:
        street = address.split(",")[0]
        street_words = [w for w in _words(street) if not w.isdigit()]
        if street_words:
            hints.append(" ".join(street_words))
    return any(h and h in page for h in hints)


def confirm(lead: dict, text: str) -> str | None:
    """Vraća razlog potvrde ('telefon' / 'ime_i_mesto') ili None."""
    if phone_matches(lead.get("phone"), text):
        return "telefon"
    if name_matches(lead["name"], text) and place_matches(lead.get("city"), lead.get("address"), text):
        return "ime_i_mesto"
    return None


async def find_site(lead: dict) -> tuple[str, str] | None:
    """Vraća (url, razlog potvrde) ili None."""
    from . import checks

    domains = domain_candidates(lead["name"])
    resolves = await asyncio.gather(*(asyncio.to_thread(checks._resolves, d) for d in domains))
    for domain, ok in zip(domains, resolves):
        if not ok:
            continue
        try:
            r = await asyncio.wait_for(checks.check_http(f"https://{domain}/"), timeout=45)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            continue
        html = r.get("html") or ""
        if r.get("error") or (r.get("http_status") or 0) >= 400 or not html:
            continue
        plain = re.sub(r"<[^>]+>", " ", html)
        if checks.page_kind(plain) in ("parkiran", "suspendovan", "podrazumevana"):
            continue
        reason = confirm(lead, html + " " + plain)
        if reason:
            return r.get("final_url") or f"https://{domain}/", reason
    return None
