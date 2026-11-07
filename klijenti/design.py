"""Korak 5: AI ocena dizajna sa screenshotova (OpenAI, model sa podrškom za slike)."""
import base64
import json
from pathlib import Path

from . import config

SYSTEM_PROMPT = """Ti si iskusan web dizajner iz Srbije. Dobijaš dva screenshota početne strane sajta
jedne firme: desktop (1366 px) i telefon (390 px). Proceni samo vizuelni dizajn: koliko deluje savremeno,
pregledno i profesionalno za potencijalnog klijenta te firme.

Pravila:
- Oceni samo ono što se VIDI na slikama. Ne izmišljaj sadržaj koji nije prikazan.
- Ocena 1–10: 1–3 jako zastarelo ili neuredno, 4–5 primetno staro ili amatersko, 6 prosečno,
  7–8 dobro i savremeno, 9–10 odlično.
- "procena_godine": iz kog perioda dizajn deluje, npr. "oko 2012" ili "savremen".
- "nedostaci": najviše 3 konkretna, vidljiva problema, samo ako je ocena 6 ili niža, inače prazna lista.
  Svaki nedostatak je jedna kratka rečenica (do 120 znakova), konkretna i ljubazna, bez uvreda,
  jer se može citirati vlasniku firme. Primeri tona: "Tekst na početnoj je sitan i zbijen, teško se čita",
  "Nema jasnog dugmeta za zakazivanje ili poziv".
- NE ponavljaj tehničke probleme koji su već poznati (biće navedeni u poruci), npr. SSL, brzinu, footer.
- Ako je slika neupotrebljiva (prazna stranica, samo kolačići/pop-up preko celog ekrana, greška),
  postavi "slika_upotrebljiva" na false i ocenu na 0.
- Piši na srpskom, latinicom, ekavicom."""

RESPONSE_SCHEMA = {
    "name": "ocena_dizajna",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "slika_upotrebljiva": {"type": "boolean"},
            "ocena": {"type": "integer"},
            "procena_godine": {"type": "string"},
            "utisak": {"type": "string"},
            "nedostaci": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["slika_upotrebljiva", "ocena", "procena_godine", "utisak", "nedostaci"],
    },
}


def _data_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def build_messages(desktop: Path, mobile: Path | None, name: str, niche: str,
                   known_issues: list[str], detail: str = "low") -> list[dict]:
    known = "\n".join(f"- {i}" for i in known_issues) or "- (nema)"
    content = [
        {"type": "text", "text": f"Firma: {name}\nDelatnost: {niche}\n\nVeć poznati tehnički problemi "
                                 f"(ne ponavljaj ih):\n{known}\n\nPrva slika: desktop. Druga slika: telefon."},
        {"type": "image_url", "image_url": {"url": _data_url(desktop), "detail": detail}},
    ]
    if mobile and mobile.exists():
        content.append({"type": "image_url", "image_url": {"url": _data_url(mobile), "detail": detail}})
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]


def parse_reply(text: str) -> dict:
    """Proverava i čisti odgovor modela. Baca ValueError ako odgovor nije upotrebljiv."""
    data = json.loads(text)
    usable = bool(data.get("slika_upotrebljiva"))
    score = int(data.get("ocena") or 0)
    if usable and not 1 <= score <= 10:
        raise ValueError(f"ocena van opsega: {score}")
    issues = [str(i).strip().rstrip(".") for i in (data.get("nedostaci") or []) if str(i).strip()]
    issues = [i[:160] for i in issues][:3] if usable and score <= 6 else []
    return {
        "ai_usable": usable,
        "ai_score": score if usable else None,
        "ai_era": str(data.get("procena_godine") or "").strip()[:40] or None,
        "ai_summary": str(data.get("utisak") or "").strip()[:300] or None,
        "ai_issues": issues,
    }


async def review(client, desktop: Path, mobile: Path | None, name: str, niche: str,
                 known_issues: list[str]) -> dict:
    """Jedan poziv modela. Vraća polja za bazu (ili ai_error)."""
    model = config.get("OPENAI_MODEL", required=True)
    detail = config.get("OPENAI_IMAGE_DETAIL", "low")
    if not desktop.exists():
        return {"ai_error": "nema screenshota (pokreni analyze.py --ponovo)"}
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=build_messages(desktop, mobile, name, niche, known_issues, detail),
            response_format={"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
            temperature=0.2,
            max_tokens=500,
        )
        result = parse_reply(resp.choices[0].message.content or "")
        result["ai_error"] = None
        return result
    except Exception as e:  # noqa: BLE001
        return {"ai_error": f"{type(e).__name__}: {e}"[:200]}
