"""Nacrti mejlova: AI piše po šablonu kategorije, mi proveravamo i čistimo rezultat."""
import json
import re

from . import config, templates

ALLOWED_LINK = "aleksakrstovic.rs"
URL_RE = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:rs|com|net|org|co\.rs)\b\S*", re.I)

SCHEMA = {
    "name": "mejl",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"naslov": {"type": "string"}, "tekst": {"type": "string"}},
        "required": ["naslov", "tekst"],
    },
}


def lead_brief(lead: dict) -> str:
    issues = lead.get("issues") or []
    if isinstance(issues, str):
        issues = json.loads(issues or "[]")
    lines = [
        f"Firma: {lead['name']}",
        f"Delatnost: {lead.get('niche') or '-'}, grad: {lead.get('city') or '-'}",
        f"Sajt: {lead.get('website') or 'nije pronađen'}",
        "Nedostaci koje smo utvrdili (koristi najviše 2):",
        *[f"- {i}" for i in issues[:5]],
    ]
    if lead.get("ai_summary"):
        lines.append(f"Utisak o dizajnu: {lead['ai_summary']}")
    return "\n".join(lines)


def build_messages(lead: dict, kind: str = "prvi", instruction: str | None = None,
                   previous: dict | None = None, first_subject: str | None = None,
                   sender: dict | None = None) -> list[dict]:
    """sender: {"name": "Primer Studio", "site": "primer-studio.rs"} kad se piše u tuđe ime."""
    angle, offer = templates.ANGLES.get(lead.get("category") or "", templates.DEFAULT_ANGLE)
    who = (f"Pišeš u ime web studija „{sender['name']}“ iz Srbije, koji izrađuje i redizajnira sajtove."
           if sender else "Ti si Aleksa Krstović iz Srbije: izrađuješ sajtove i AI automatizacije.")
    rules = templates.RULES
    if sender:
        rules = rules.replace("aleksakrstovic.rs", sender.get("site") or "sajta studija")
        rules = rules.replace("Ti si Aleksa, pišeš lično jednoj firmi.", "Pišeš lično jednoj firmi.")
    if kind == "followup":
        system = who + " " + templates.FOLLOWUP_RULES
        user = f"{lead_brief(lead)}\n\nNaslov prvog mejla: {first_subject or ''}"
    else:
        system = (who + " Pišeš prvi, kratak i ličan mejl firmi koju ne poznaješ.\n\n" + rules)
        user = f"{lead_brief(lead)}\n\nUgao mejla: {angle}\nŠta nudiš: {offer}"
    ex_subject, ex_core = templates.example_for(lead.get("category"), kind)
    system += ("\n\nOdobren primer, za DRUGU (izmišljenu) firmu. Prati ton, dužinu i "
               "strukturu, ali NE kopiraj rečenice doslovno i ne preuzimaj činjenice iz primera "
               f"(ime, grad, brojeve):\nNaslov: {ex_subject}\n\n{ex_core}")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if previous and instruction:
        messages.append({"role": "assistant", "content": json.dumps(
            {"naslov": previous["subject"], "tekst": previous["core"]}, ensure_ascii=False)})
        messages.append({"role": "user", "content": f"Prepravi mejl ovako: {instruction}. "
                                                    "Pravila i dalje važe."})
    return messages


def clean_core(text: str, allowed: tuple[str, ...] = ()) -> str:
    """Uklanja linkove ka tuđim sajtovima, potpis i odjavu ako ih je model ipak napisao.

    Dozvoljeni su samo aleksakrstovic.rs i domen same firme (npr. „pregledao sam primer-ordinacija.rs“).
    """
    ok = (ALLOWED_LINK, *[a.lower() for a in allowed if a])

    def keep(m):
        return m.group(0) if any(a in m.group(0).lower() for a in ok) else ""
    text = URL_RE.sub(keep, text)
    text = re.split(r"\n\s*(?:srdačan pozdrav|srdačno|pozdrav|s poštovanjem)\b", text, flags=re.I)[0]
    text = text.replace(templates.OPT_OUT, "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse(content: str, kind: str = "prvi", first_subject: str | None = None,
          allowed: tuple[str, ...] = ()) -> dict:
    data = json.loads(content)
    subject = " ".join(str(data.get("naslov") or "").split())[:80]
    core = clean_core(str(data.get("tekst") or ""), allowed)
    if not core:
        raise ValueError("prazan tekst mejla")
    if kind == "followup" and first_subject:
        subject = f"Re: {first_subject}"
    if not subject:
        raise ValueError("prazan naslov")
    return {"subject": subject, "core": core}


async def generate(client, lead: dict, kind: str = "prvi", instruction: str | None = None,
                   previous: dict | None = None, first_subject: str | None = None,
                   sender: dict | None = None) -> dict:
    resp = await client.chat.completions.create(
        model=config.get("OPENAI_MODEL", required=True),
        messages=build_messages(lead, kind, instruction, previous, first_subject, sender),
        response_format={"type": "json_schema", "json_schema": SCHEMA},
        temperature=0.6,
        max_tokens=700,
    )
    allowed = (lead.get("domain") or "", (sender or {}).get("site") or "")
    return parse(resp.choices[0].message.content or "", kind, first_subject, allowed)


def looks_like_full_text(reply: str) -> bool:
    """Kod izmene: da li si poslao ceo novi tekst ili samo uputstvo („kraće“, „pomeni SSL“)."""
    reply = reply.strip()
    return reply.lower().startswith(("poštovani", "postovani", "dobar dan", "zdravo", "pozdrav")) \
        or len(reply) > 300 or reply.count("\n") >= 3
