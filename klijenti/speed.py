"""Korak 4: brzina sajta na telefonu preko Google PageSpeed Insights API-ja."""
import asyncio

from . import config

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def parse_psi(data: dict) -> dict:
    """Iz odgovora PageSpeed-a izvlači ocenu i glavne metrike."""
    lh = data.get("lighthouseResult") or {}
    score = (lh.get("categories", {}).get("performance") or {}).get("score")
    audits = lh.get("audits", {})

    def num(key):
        value = (audits.get(key) or {}).get("numericValue")
        return value if isinstance(value, (int, float)) else None

    lcp, tbt, cls = num("largest-contentful-paint"), num("total-blocking-time"), num("cumulative-layout-shift")
    if score is None:
        return {"psi_error": (lh.get("runtimeError") or {}).get("message", "PageSpeed nije vratio ocenu")[:200]}
    return {
        "psi_score": round(score * 100),
        "lcp_ms": round(lcp) if lcp is not None else None,
        "tbt_ms": round(tbt) if tbt is not None else None,
        "cls": round(cls, 3) if cls is not None else None,
        "psi_error": None,
    }


async def measure(client, url: str, attempts: int = 3) -> dict:
    """Jedno merenje (mobilni). PageSpeed zna da odbije zahtev pod opterećenjem, pa ponavljamo."""
    params = {"url": url, "strategy": "mobile", "category": "performance",
              "key": config.get("GOOGLE_API_KEY", required=True)}
    wait = 10
    last = "nepoznata greška"
    for _ in range(attempts):
        try:
            r = await client.get(PSI_URL, params=params, timeout=150)
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}"
        else:
            if r.status_code == 200:
                return parse_psi(r.json())
            try:
                last = r.json().get("error", {}).get("message", f"HTTP {r.status_code}")
            except ValueError:
                last = f"HTTP {r.status_code}"
            if r.status_code == 400:  # Lighthouse ne može da otvori stranicu: ponavljanje ne pomaže
                break
        await asyncio.sleep(wait)
        wait *= 2
    return {"psi_error": last[:200]}
