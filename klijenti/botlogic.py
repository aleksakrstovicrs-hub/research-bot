"""Logika Telegram bota bez same Telegram biblioteke (da bi mogla da se testira)."""
import json
import sqlite3

from .niche import resolve_niche

CATEGORY_LABELS = {
    "sajt_ne_radi": "sajt ne radi", "bez_sajta": "sajt nije pronađen", "samo_drustvene": "samo društvene mreže",
    "nebezbedan": "nema SSL", "nije_za_telefon": "nije za telefon", "zastareo": "zastareo",
    "spor": "spor", "dobar_sajt": "dobar sajt", "nepoznato": "proveriti ručno",
}

HELP = """Komande:
/trazi stomatolozi Beograd: nađi i proveri firme (traje 20–60 min)
/trazi auto servisi, Novi Sad: zarez kad grad ima više reči
/status: pregled po kategorijama
/top 10: najveće prilike
/sajt Primer: screenshotovi, ocena i nedostaci jedne firme
/nise: spisak niša

Mejlovi:
/nacrti 5: napiši nacrte za firme sa statusom za_kontakt (ti odobravaš svaki)
/red: šta čeka slanje, šta je poslato danas
/mejltest: proveri prijavu na SMTP/IMAP (ništa ne šalje)
/probni tvoja@adresa.com: pošalji probni mejl sebi"""


def parse_search(args: list[str]) -> tuple[str, str]:
    """'/trazi auto servisi Novi Sad' → ('auto servisi', 'Novi Sad').

    Zarez ima prednost; bez zareza tražimo najduži početak koji je poznata niša.
    """
    text = " ".join(args).strip()
    if "," in text:
        niche, city = (p.strip() for p in text.split(",", 1))
        if niche and city:
            return niche, city
        raise ValueError("Napiši i nišu i grad, npr. /trazi stomatolozi, Beograd")
    words = text.split()
    for i in range(len(words) - 1, 0, -1):
        niche = " ".join(words[:i])
        try:
            resolve_niche(niche)
        except ValueError:
            continue
        return niche, " ".join(words[i:])
    raise ValueError("Nisam prepoznao nišu. Primer: /trazi stomatolozi Beograd (spisak: /nise)")


def top_leads(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT l.name, l.email, l.website, l.status, a.score, a.category, a.issues
           FROM leads l JOIN analyses a ON a.lead_id = l.id
           WHERE l.status IN ('analiziran', 'za_kontakt')
           ORDER BY (l.email IS NULL), a.score DESC, l.name LIMIT ?""",
        (limit,),
    ).fetchall()


def format_top(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "Još nema analiziranih firmi."
    lines = ["🏆 Najveće prilike (prvo one sa mejlom)", ""]
    for i, r in enumerate(rows, 1):
        issues = json.loads(r["issues"] or "[]")
        first = issues[0] if issues else "bez većih problema"
        mark = "✅" if r["status"] == "za_kontakt" else "•"
        lines.append(f"{i}. {mark} {r['name']}: {r['score']} ({CATEGORY_LABELS.get(r['category'], r['category'])})")
        lines.append(f"   {first}")
        lines.append(f"   ✉️ {r['email'] or 'nema mejla'}")
    return "\n".join(lines)


def find_lead(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    query = query.strip()
    if not query:
        return []
    return conn.execute(
        """SELECT l.*, a.score, a.category, a.issues, a.screenshot_desktop, a.screenshot_mobile,
                  a.ai_score, a.psi_score
           FROM leads l LEFT JOIN analyses a ON a.lead_id = l.id
           WHERE l.id = ? OR lower(l.name) LIKE lower(?)
           ORDER BY (lower(l.name) = lower(?)) DESC, a.score DESC LIMIT 5""",
        (query, f"%{query}%", query),
    ).fetchall()


def format_lead(r: sqlite3.Row, limit: int = 1000) -> str:
    issues = json.loads(r["issues"] or "[]") if r["issues"] else []
    lines = [f"🏢 {r['name']}  ({r['niche']}, {r['city']})"]
    if r["score"] is not None:
        lines.append(f"Ocena prilike: {r['score']} · {CATEGORY_LABELS.get(r['category'], r['category'])}")
    extra = []
    if r["psi_score"] is not None:
        extra.append(f"brzina {r['psi_score']}/100")
    if r["ai_score"] is not None:
        extra.append(f"dizajn {r['ai_score']}/10")
    if extra:
        lines.append(" · ".join(extra))
    lines.append(f"🌐 {r['website'] or '—'}")
    lines.append(f"✉️ {r['email'] or '—'}   📞 {r['phone'] or '—'}")
    lines.append(f"Status: {r['status']}")
    if issues:
        lines.append("")
        lines += [f"• {i}" for i in issues]
    text = "\n".join(lines)
    return text if len(text) <= limit else text[: limit - 1] + "…"
