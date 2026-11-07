"""Faza 2: nacrti → tvoje odobrenje → slanje. Bez Telegram biblioteke (da bi se testiralo)."""
import asyncio
import json
import sqlite3
from datetime import datetime

from . import db, drafts, mailer, templates
from .botlogic import CATEGORY_LABELS

MAX_OPEN_DRAFTS = 15  # dok ne odobriš/preskočiš ove, novi se ne prave


def format_draft(msg: sqlite3.Row, lead: sqlite3.Row) -> str:
    issues = json.loads(lead["issues"] or "[]") if lead["issues"] else []
    head = (f"🏢 {lead['name']} · ocena {lead['score']} · "
            f"{CATEGORY_LABELS.get(lead['category'], lead['category'])}"
            + ("  (podsetnik)" if msg["kind"] == "followup" else ""))
    info = f"🌐 {lead['website'] or '—'}"
    if issues:
        info += "\n🔎 " + " · ".join(issues[:3])
    body = templates.compose(msg["core"])
    return f"{head}\n{info}\n\nZa: {msg['to_email']}\nNaslov: {msg['subject']}\n\n{body}"[:4000]


async def create_drafts(conn: sqlite3.Connection, client, limit: int) -> tuple[list[int], str | None]:
    """Pravi nove nacrte. Vraća (id-jevi, napomena)."""
    open_now = db.open_drafts_count(conn)
    if open_now >= MAX_OPEN_DRAFTS:
        return [], f"Imaš {open_now} neodobrenih nacrta. Prvo njih odobri ili preskoči."
    limit = min(limit, MAX_OPEN_DRAFTS - open_now)
    leads = db.leads_for_drafts(conn, limit)
    if not leads:
        return [], "Nema novih firmi za kontakt (status za_kontakt sa mejlom)."

    async def one(lead):
        try:
            return lead, await drafts.generate(client, dict(lead)), None
        except Exception as e:  # noqa: BLE001
            return lead, None, f"{type(e).__name__}: {e}"

    ids, errors = [], []
    for lead, draft, err in await asyncio.gather(*(one(l) for l in leads)):
        if err:
            errors.append(f"{lead['name']}: {err}"[:150])
            continue
        ids.append(db.create_message(conn, lead, "prvi", draft["subject"], draft["core"]))
    conn.commit()
    return ids, ("Greške: " + "; ".join(errors)) if errors else None


async def apply_edit(conn: sqlite3.Connection, client, msg_id: int, reply: str) -> None:
    msg = db.get_message(conn, msg_id)
    if drafts.looks_like_full_text(reply):
        lead = db.lead_for_message(conn, msg["lead_id"])
        core = drafts.clean_core(reply, (lead["domain"] or "",))
        db.update_message(conn, msg_id, core=core)
    else:
        lead = db.lead_for_message(conn, msg["lead_id"])
        new = await drafts.generate(client, dict(lead), msg["kind"], instruction=reply,
                                    previous={"subject": msg["subject"], "core": msg["core"]})
        db.update_message(conn, msg_id, subject=new["subject"], core=new["core"])
    conn.commit()


def approve(conn: sqlite3.Connection, msg_id: int) -> str:
    msg = db.get_message(conn, msg_id)
    if not msg or msg["status"] != "nacrt":
        return "Ova poruka je već obrađena."
    db.update_message(conn, msg_id, status="odobren", approved_at=db.now())
    conn.commit()
    s = mailer.settings()
    if not s["enabled"]:
        return "✅ Odobreno. Slanje je još isključeno, poslaće se kad ga uključiš (SENDING_ENABLED=1)."
    queued = conn.execute("SELECT COUNT(*) FROM messages WHERE status='odobren'").fetchone()[0]
    return f"✅ Odobreno, u redu za slanje ({queued} čeka; limit {s['daily_limit']} dnevno, radnim danima)."


def skip(conn: sqlite3.Connection, msg_id: int, permanent: bool) -> str:
    msg = db.get_message(conn, msg_id)
    if not msg or msg["status"] not in ("nacrt", "odobren"):
        return "Ova poruka je već obrađena."
    db.update_message(conn, msg_id, status="preskocen")
    if permanent:
        conn.execute("UPDATE leads SET status='ne_kontaktirati', status_locked=1 WHERE id=?", (msg["lead_id"],))
        text = "🚫 Preskočeno. Firma je trajno izbačena (ne_kontaktirati)."
    else:
        text = "⏳ Preskočeno. Firma može ponovo da dobije nacrt za 30 dana."
    conn.commit()
    return text


def auto_settings() -> dict:
    from . import config

    def num(name, default):
        try:
            return int(config.get(name, str(default)))
        except ValueError:
            return default

    hh, _, mm = (config.get("MORNING_DRAFTS_TIME", "08:30") or "08:30").partition(":")
    return {
        "after_search": num("DRAFTS_AFTER_SEARCH", 5),        # 0 = isključeno
        "morning": num("MORNING_DRAFTS", mailer.settings()["daily_limit"]),  # 0 = isključeno
        "morning_hour": int(hh or 8),
        "morning_minute": int(mm or 30),
    }


def morning_due(now: datetime, last_run_date: str | None, s: dict) -> bool:
    """Jutarnja tura nacrta: radnim danima, jednom dnevno, od podešenog vremena do podneva."""
    if s["morning"] <= 0 or now.weekday() >= 5:
        return False
    if last_run_date == now.date().isoformat():
        return False
    start = now.replace(hour=s["morning_hour"], minute=s["morning_minute"], second=0, microsecond=0)
    return start <= now < now.replace(hour=12, minute=0, second=0, microsecond=0)


def morning_count(conn: sqlite3.Connection, s: dict) -> int:
    """Koliko novih nacrta jutros: do jutarnjeg broja, umanjeno za one koji već čekaju."""
    waiting = db.open_drafts_count(conn) + conn.execute(
        "SELECT COUNT(*) FROM messages WHERE status='odobren'").fetchone()[0]
    return max(0, s["morning"] - waiting)


SAMPLE_CORE = ("Poštovani,\n\novo je probni mejl iz sistema za ponude, da proverim kako izgleda kod primaoca "
               "i da li stiže u Inbox.\n\nAko želite, pošaljem vam kratku besplatnu analizu sajta.")


def build_test_email(conn: sqlite3.Connection, to: str):
    """Probni mejl na TVOJU adresu: poslednji nacrt (ili primer teksta). Ne menja ništa u bazi."""
    row = conn.execute("SELECT subject, core FROM messages WHERE status IN ('nacrt', 'odobren') "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    subject, core = (row["subject"], row["core"]) if row else ("Kratko zapažanje o vašem sajtu", SAMPLE_CORE)
    return mailer.build_email(to, f"[PROBA] {subject}", core), bool(row)


def send_next(conn: sqlite3.Connection, now: datetime | None = None, sender=mailer.send) -> tuple[sqlite3.Row | None, str]:
    """Šalje jednu odobrenu poruku ako pravila dozvoljavaju. Vraća (poruka ili None, razlog)."""
    now = now or datetime.now()
    ok, reason = mailer.can_send_now(now, db.sent_today(conn), db.last_sent_at(conn), mailer.settings())
    if not ok:
        return None, reason
    msg = db.next_to_send(conn)
    if not msg:
        return None, "nema odobrenih poruka"
    in_reply_to = None
    if msg["kind"] == "followup":
        row = conn.execute("""SELECT message_id FROM messages WHERE lead_id=? AND kind='prvi'
                              AND status='poslat' ORDER BY sent_at DESC LIMIT 1""", (msg["lead_id"],)).fetchone()
        in_reply_to = row["message_id"] if row else None
    email = mailer.build_email(msg["to_email"], msg["subject"], msg["core"], in_reply_to)
    try:
        sender(email)
    except Exception as e:  # noqa: BLE001
        db.update_message(conn, msg["id"], status="greska", error=f"{type(e).__name__}: {e}"[:300])
        conn.commit()
        return msg, f"greška pri slanju: {e}"
    db.mark_sent(conn, msg["id"], email["Message-ID"])
    conn.commit()
    return msg, "poslato"
