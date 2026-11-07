"""Slanje mejlova: pravila kada sme da se pošalje + SMTP.

Slanje je ISKLJUČENO dok u .env ne postaviš SENDING_ENABLED=1.
"""
import imaplib
import smtplib
import ssl
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from . import config, templates


def settings() -> dict:
    def num(name, default):
        try:
            return int(config.get(name, str(default)))
        except ValueError:
            return default

    hours = (config.get("SEND_HOURS", "9-16") or "9-16").split("-")
    return {
        "enabled": config.get("SENDING_ENABLED", "0") == "1",
        "daily_limit": num("DAILY_LIMIT", 8),
        "start_hour": int(hours[0]),
        "end_hour": int(hours[1]) if len(hours) > 1 else 16,
        "min_gap": num("MIN_GAP_MINUTES", 7),
    }


def can_send_now(now: datetime, sent_today: int, last_sent: str | None, s: dict) -> tuple[bool, str]:
    """Da li sme sledeći mejl da ode sada, i ako ne, zašto."""
    if not s["enabled"]:
        return False, "slanje je isključeno (SENDING_ENABLED=0)"
    if now.weekday() >= 5:
        return False, "vikend"
    if not s["start_hour"] <= now.hour < s["end_hour"]:
        return False, f"van radnog vremena ({s['start_hour']}–{s['end_hour']}h)"
    if sent_today >= s["daily_limit"]:
        return False, f"dnevni limit ({s['daily_limit']}) je dostignut"
    if last_sent:
        last = datetime.fromisoformat(last_sent)
        if now - last < timedelta(minutes=s["min_gap"]):
            return False, "razmak između mejlova"
    return True, "ok"


def build_email(to: str, subject: str, core: str, in_reply_to: str | None = None) -> EmailMessage:
    from_email = config.get("FROM_EMAIL", required=True)
    msg = EmailMessage()
    msg["From"] = formataddr((config.get("FROM_NAME", "Aleksa Krstović"), from_email))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=from_email.split("@", 1)[1])
    # Gmail/Outlook iz ovoga prave dugme „Odjavi se“ pored pošiljaoca
    msg["List-Unsubscribe"] = f"<mailto:{from_email}?subject=odjava>"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(templates.compose(core))
    return msg


def send(msg: EmailMessage) -> None:
    """Šalje preko SMTP-a (AdriaHost) i stavlja kopiju u Poslate, da je vidiš u mejl programu."""
    host = config.get("SMTP_HOST", required=True)
    port = int(config.get("SMTP_PORT", "465"))
    user = config.get("SMTP_USER") or config.get("FROM_EMAIL", required=True)
    password = config.get("SMTP_PASSWORD", required=True)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=ctx)
            s.login(user, password)
            s.send_message(msg)
    save_to_sent(msg, user, password)


def save_to_sent(msg: EmailMessage, user: str, password: str) -> None:
    host = config.get("IMAP_HOST") or config.get("SMTP_HOST")
    folder = config.get("IMAP_SENT_FOLDER", "INBOX.Sent")
    try:
        with imaplib.IMAP4_SSL(host, int(config.get("IMAP_PORT", "993")), timeout=30) as imap:
            imap.login(user, password)
            imap.append(folder, "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    except Exception:  # noqa: BLE001 — kopija u Poslatim je pogodnost, ne uslov
        pass


def test_connection() -> str:
    """Proverava SMTP i IMAP prijavu bez slanja ičega."""
    host = config.get("SMTP_HOST", required=True)
    port = int(config.get("SMTP_PORT", "465"))
    user = config.get("SMTP_USER") or config.get("FROM_EMAIL", required=True)
    password = config.get("SMTP_PASSWORD", required=True)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(user, password)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, password)
    imap_host = config.get("IMAP_HOST") or host
    with imaplib.IMAP4_SSL(imap_host, int(config.get("IMAP_PORT", "993")), timeout=30) as imap:
        imap.login(user, password)
        _, folders = imap.list()
    names = [f.decode(errors="replace").split(' "')[-1].strip('" ') for f in folders or []]
    return f"SMTP i IMAP prijava uspela. Folderi: {', '.join(names[:10])}"
