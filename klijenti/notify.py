"""Kratke poruke na Telegram. Ako bot nije podešen, tiho preskače."""
from . import config


# Kad ceo proces vodi Telegram bot, on sam šalje poruke o napretku, pa pojedinačni koraci ćute.
MUTED = False


def send(text: str) -> bool:
    token, chat_id = config.get("TELEGRAM_TOKEN"), config.get("TELEGRAM_CHAT_ID")
    if MUTED or not token or not chat_id:
        return False
    try:
        import httpx

        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )
        return r.status_code == 200
    except Exception:  # noqa: BLE001 — obaveštenje nikad ne sme da obori posao
        return False
