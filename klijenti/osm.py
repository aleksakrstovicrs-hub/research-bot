"""Pronalaženje firmi na OpenStreetMap-u: Nominatim za grad, Overpass za firme."""
import re
import time
from urllib.parse import urlparse

from . import config
from .niche import to_latin

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Javni Overpass serveri; ako je jedan preopterećen, probamo sledeći.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

SOCIAL_DOMAINS = {
    "facebook.com", "m.facebook.com", "fb.com", "instagram.com", "tiktok.com",
    "linkedin.com", "twitter.com", "x.com", "youtube.com", "linktr.ee",
}
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


# ---------------------------------------------------------------- mreža

def find_city_area(city: str) -> tuple[int, str]:
    """Vraća (Overpass area id, pun naziv) za grad u Srbiji."""
    import httpx

    r = httpx.get(
        NOMINATIM_URL,
        params={"q": city, "countrycodes": "rs", "format": "jsonv2", "limit": 10},
        headers={"User-Agent": config.USER_AGENT, "Accept-Language": "sr-Latn,sr,en"},
        timeout=30,
    )
    r.raise_for_status()
    for item in r.json():
        if item.get("osm_type") == "relation":
            return 3_600_000_000 + int(item["osm_id"]), item.get("display_name", city)
    raise ValueError(f"Nisam našao granice grada '{city}' na OpenStreetMap-u.")


def build_query(area_id: int, tags: list[tuple[str, str]], timeout: int = 120,
                name_regex: str | None = None) -> str:
    lines = [f'  nwr["{k}"="{v}"](area.a);' for k, v in tags]
    if name_regex:
        rx = name_regex.replace('"', "")
        lines += [f'  nwr["{key}"]["name"~"{rx}",i](area.a);' for key in ("office", "craft", "shop")]
    parts = "\n".join(lines)
    return f"[out:json][timeout:{timeout}];\narea({area_id})->.a;\n(\n{parts}\n);\nout tags center;"


def run_overpass(query: str, rounds: int = 3) -> list[dict]:
    """Šalje upit, uz ponovne pokušaje i rezervne servere (ukupno do ~15 min čekanja)."""
    import httpx

    last_error = "nepoznata greška"
    wait = 5
    for _ in range(rounds):
        for url in OVERPASS_URLS:
            try:
                r = httpx.post(
                    url, data={"data": query},
                    headers={"User-Agent": config.USER_AGENT}, timeout=180,
                )
                if r.status_code == 200:
                    return r.json().get("elements", [])
                last_error = f"{url} -> HTTP {r.status_code}"
            except (httpx.HTTPError, ValueError) as e:
                last_error = f"{url} -> {e}"
            time.sleep(wait)
            wait = min(wait * 2, 120)
    raise RuntimeError(f"Overpass nije dostupan ({last_error}). Probaj ponovo za nekoliko minuta.")


# ---------------------------------------------------------------- obrada

def first_value(tags: dict, *keys: str) -> str | None:
    for key in keys:
        value = tags.get(key)
        if value:
            return value.split(";")[0].strip() or None
    return None


def normalize_url(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.split(";")[0].strip()
    if not raw or " " in raw:
        return None
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    return raw


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host or None


def is_social(domain: str | None) -> bool:
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in SOCIAL_DOMAINS)


# Državne i javne institucije nisu potencijalni klijenti.
PUBLIC_RE = re.compile(  # poredi se sa imenom bez kvačica (simplify)
    r"\b(ministarstv|direkcij|zavod|uprav[ae]\b|sekretarijat|agencij[ae] za|javn[oa] preduze|"
    r"jp\b|jkp\b|pjp\b|fakultet|univerzitet|institut|opstin|skupstin|vlad[ae]\b|inspekcij|"
    r"komor[ae]\b|fond\b|republick|dom zdravlja|bolnic|klinicki centar)",
    re.I,
)
PUBLIC_TAGS = {("office", "government"), ("office", "administrative"), ("amenity", "townhall"),
               ("office", "diplomatic"), ("amenity", "university"), ("amenity", "college"),
               ("amenity", "hospital")}


def is_public_institution(name: str, tags: dict) -> bool:
    if any(tags.get(k) == v for k, v in PUBLIC_TAGS) or tags.get("government"):
        return True
    from .niche import simplify
    return bool(PUBLIC_RE.search(simplify(name)))


def parse_element(el: dict, niche: str, city: str) -> dict | None:
    """OSM element -> lead. Vraća None ako firma nema ime ili je državna/javna institucija."""
    tags = el.get("tags", {})
    name = tags.get("name:sr-Latn") or to_latin(tags.get("name") or tags.get("brand"))
    if not name or is_public_institution(name, tags):
        return None

    website = normalize_url(first_value(tags, "website", "contact:website", "url"))
    domain = domain_of(website)
    social = normalize_url(first_value(tags, "contact:facebook", "facebook", "contact:instagram"))
    if website and is_social(domain):
        website_type, domain = "drustvene_mreze", None
    elif website:
        website_type = "sajt"
    elif social:
        website, website_type = social, "drustvene_mreze"
    else:
        website_type = "nema"

    email = first_value(tags, "email", "contact:email")
    if email and not EMAIL_RE.match(email):
        email = None

    street = to_latin(first_value(tags, "addr:street"))
    number = first_value(tags, "addr:housenumber")
    place = to_latin(first_value(tags, "addr:city", "addr:place", "addr:suburb"))
    address = ", ".join(p for p in [" ".join(p for p in [street, number] if p), place] if p) or None

    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")

    return {
        "id": f"osm:{el['type']}/{el['id']}",
        "source": "osm",
        "name": name.strip(),
        "niche": niche,
        "city": city,
        "address": address,
        "phone": first_value(tags, "phone", "contact:phone", "contact:mobile"),
        "website": website,
        "website_type": website_type,
        "domain": domain,
        "email": email.lower() if email else None,
        "email_source": "openstreetmap" if email else None,
        "lat": lat,
        "lon": lon,
    }
