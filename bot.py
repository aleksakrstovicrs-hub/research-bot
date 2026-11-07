"""Telegram bot za research (Faza 1). Radi kao stalni servis na VPS-u.

Odgovara SAMO na poruke iz tvog chata (TELEGRAM_CHAT_ID iz .env).
"""
import asyncio
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, LinkPreviewOptions, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                          filters)

import pipeline
import status as status_report
from klijenti import botlogic, config, db, mailer, outreach, sheets
from klijenti.niche import NICHES

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

JOB_LOCK = asyncio.Lock()
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(botlogic.HELP)


async def cmd_nise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Niše:\n" + "\n".join(f"• {n}" for n in sorted(NICHES))
                                    + "\n\nMože i OSM tag, npr. /trazi shop=bicycle Beograd")


async def cmd_trazi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        niche, city = botlogic.parse_search(context.args)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return
    if JOB_LOCK.locked():
        await update.message.reply_text("⏳ Već radim jednu pretragu. Javiću kad završim, pa pošalji ponovo.")
        return

    chat_id = update.effective_chat.id

    async def progress(msg: str) -> None:
        log.info(msg)
        await context.bot.send_message(chat_id, msg)

    async def job() -> None:
        async with JOB_LOCK:
            try:
                report = await pipeline.run(niche, city, progress)
                await context.bot.send_message(chat_id, report)
                n = outreach.auto_settings()["after_search"]
                if n > 0:
                    await send_new_drafts(context.bot, chat_id, n, "✍️ Nacrti za najbolje nove leadove:")
            except Exception as e:  # noqa: BLE001
                log.exception("pretraga pukla")
                await context.bot.send_message(chat_id, f"❌ Greška: {type(e).__name__}: {e}"[:500])

    await update.message.reply_text(f"Krećem: {niche}, {city}. Javljam napredak.")
    context.application.create_task(job())


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    def build() -> str:
        conn = db.connect(config.DB_PATH)
        try:
            result = sheets.sync(conn)
            return status_report.build_report(db.category_report(conn), result.get("promoted", 0),
                                              db.min_score_for_contact())
        finally:
            conn.close()

    await update.message.reply_text(await asyncio.to_thread(build))


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        limit = max(1, min(30, int(context.args[0]))) if context.args else 10
    except ValueError:
        limit = 10
    conn = db.connect(config.DB_PATH)
    try:
        text = botlogic.format_top(botlogic.top_leads(conn, limit))
    finally:
        conn.close()
    await update.message.reply_text(text, link_preview_options=NO_PREVIEW)


async def cmd_sajt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Napiši ime firme, npr. /sajt Primer")
        return
    conn = db.connect(config.DB_PATH)
    try:
        rows = botlogic.find_lead(conn, query)
    finally:
        conn.close()
    if not rows:
        await update.message.reply_text("Nisam našao tu firmu.")
        return
    lead = rows[0]
    caption = botlogic.format_lead(lead)
    photos = [config.BASE_DIR / p for p in (lead["screenshot_desktop"], lead["screenshot_mobile"]) if p]
    photos = [p for p in photos if Path(p).exists()]
    if photos:
        media = [InputMediaPhoto(p.read_bytes(), caption=caption if i == 0 else None)
                 for i, p in enumerate(photos)]
        await context.bot.send_media_group(update.effective_chat.id, media)
    else:
        await update.message.reply_text(caption, link_preview_options=NO_PREVIEW)
    if len(rows) > 1:
        others = ", ".join(r["name"] for r in rows[1:])
        await update.message.reply_text(f"Slične firme: {others}")


# ================================================================ Faza 2: nacrti i slanje

def draft_buttons(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Pošalji", callback_data=f"ok:{msg_id}"),
        InlineKeyboardButton("✏️ Izmeni", callback_data=f"ed:{msg_id}"),
        InlineKeyboardButton("❌ Preskoči", callback_data=f"sk:{msg_id}"),
    ]])


def openai_client():
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=config.get("OPENAI_API_KEY", required=True))


async def show_draft(bot, chat_id: int, conn, msg_id: int) -> None:
    msg = db.get_message(conn, msg_id)
    lead = db.lead_for_message(conn, msg["lead_id"])
    sent = await bot.send_message(chat_id, outreach.format_draft(msg, lead),
                                  reply_markup=draft_buttons(msg_id), link_preview_options=NO_PREVIEW)
    db.update_message(conn, msg_id, tg_message_id=sent.message_id)
    conn.commit()


async def send_new_drafts(bot, chat_id: int, limit: int, intro: str | None = None) -> int:
    """Napiše do `limit` nacrta i pošalje ih na odobrenje. Vraća broj nacrta."""
    conn = db.connect(config.DB_PATH)
    try:
        ids, note = await outreach.create_drafts(conn, openai_client(), limit)
        if ids and intro:
            await bot.send_message(chat_id, intro)
        for msg_id in ids:
            await show_draft(bot, chat_id, conn, msg_id)
    finally:
        conn.close()
    if note and (ids or intro is None):
        await bot.send_message(chat_id, note)
    return len(ids)


async def cmd_nacrti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        limit = max(1, min(10, int(context.args[0]))) if context.args else 5
    except ValueError:
        limit = 5
    await update.message.reply_text(f"✍️ Pišem do {limit} nacrta...")
    await send_new_drafts(context.bot, update.effective_chat.id, limit)


async def cmd_red(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = db.connect(config.DB_PATH)
    try:
        counts = dict(conn.execute("SELECT status, COUNT(*) FROM messages GROUP BY status").fetchall())
        today = db.sent_today(conn)
    finally:
        conn.close()
    s = mailer.settings()
    state = "uključeno" if s["enabled"] else "ISKLJUČENO"
    await update.message.reply_text(
        f"📬 Slanje: {state}\nDanas poslato: {today}/{s['daily_limit']} "
        f"(radnim danima {s['start_hour']}–{s['end_hour']}h)\n\n"
        f"Nacrti: {counts.get('nacrt', 0)}\nOdobreno, čeka slanje: {counts.get('odobren', 0)}\n"
        f"Poslato ukupno: {counts.get('poslat', 0)}\nPreskočeno: {counts.get('preskocen', 0)}\n"
        f"Greške: {counts.get('greska', 0)}")


async def cmd_mejl_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        text = await asyncio.to_thread(mailer.test_connection)
    except Exception as e:  # noqa: BLE001
        text = f"❌ {type(e).__name__}: {e}"
    await update.message.reply_text(text[:1000])


async def cmd_probni(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    to = (context.args[0] if context.args else "").strip()
    if "@" not in to:
        await update.message.reply_text("Napiši svoju adresu, npr. /probni tvoja.adresa@gmail.com")
        return
    conn = db.connect(config.DB_PATH)
    try:
        email, from_draft = outreach.build_test_email(conn, to)
    finally:
        conn.close()
    try:
        await asyncio.to_thread(mailer.send, email)
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"❌ Slanje nije uspelo: {type(e).__name__}: {e}"[:500])
        return
    source = "poslednji nacrt" if from_draft else "primer teksta (još nemaš nacrta)"
    await update.message.reply_text(
        f"📨 Probni mejl poslat na {to} ({source}).\nProveri Inbox, ali i Spam/Promocije. "
        "U Gmail-u: ⋮ → Show original → SPF, DKIM i DMARC treba da budu PASS.")


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if update.effective_chat.id != int(config.get("TELEGRAM_CHAT_ID", required=True)):
        return
    await query.answer()
    action, msg_id = query.data.split(":", 1)
    msg_id = int(msg_id)
    conn = db.connect(config.DB_PATH)
    try:
        if action == "ok":
            text = outreach.approve(conn, msg_id)
            await query.edit_message_reply_markup(None)
            await query.message.reply_text(text)
        elif action == "ed":
            context.chat_data["editing"] = msg_id
            await query.message.reply_text(
                "✏️ Pošalji uputstvo (npr. „kraće“, „pomeni i SSL“, „opušteniji ton“) "
                "ili ceo novi tekst koji počinje sa „Poštovani“.")
        elif action == "sk":
            await query.message.reply_text("Trajno izbaciti firmu ili samo odložiti?", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🚫 Trajno", callback_data=f"skp:{msg_id}"),
                InlineKeyboardButton("⏳ Za 30 dana", callback_data=f"skl:{msg_id}"),
            ]]))
        elif action in ("skp", "skl"):
            text = outreach.skip(conn, msg_id, permanent=action == "skp")
            await query.edit_message_text(text)
            msg = db.get_message(conn, msg_id)
            if msg and msg["tg_message_id"]:
                try:
                    await context.bot.edit_message_reply_markup(query.message.chat_id, msg["tg_message_id"], None)
                except Exception:  # noqa: BLE001
                    pass
    finally:
        conn.close()


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg_id = context.chat_data.pop("editing", None)
    if not msg_id:
        await update.message.reply_text("Ne razumem. Komande: /pomoc")
        return
    await update.message.reply_text("✍️ Prepravljam...")
    conn = db.connect(config.DB_PATH)
    try:
        await outreach.apply_edit(conn, openai_client(), msg_id, update.message.text)
        await show_draft(context.bot, update.effective_chat.id, conn, msg_id)
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"❌ Izmena nije uspela: {e}"[:500])
    finally:
        conn.close()


async def sender_loop(app: Application, chat_id: int) -> None:
    """Na svaki minut: ako pravila dozvoljavaju, pošalje jednu odobrenu poruku."""
    while True:
        await asyncio.sleep(60)
        conn = db.connect(config.DB_PATH)
        try:
            msg, reason = await asyncio.to_thread(outreach.send_next, conn)
            if msg is not None:
                await app.bot.send_message(chat_id, f"📤 {reason}: {msg['to_email']} · {msg['subject']}")
        except Exception:  # noqa: BLE001
            log.exception("sender_loop")
        finally:
            conn.close()


async def morning_loop(app: Application, chat_id: int) -> None:
    """Radnim danima ujutru: nova tura nacrta iz leadova sa statusom za_kontakt."""
    from datetime import datetime

    last_run = None
    while True:
        await asyncio.sleep(60)
        try:
            s = outreach.auto_settings()
            now = datetime.now()
            if not outreach.morning_due(now, last_run, s):
                continue
            last_run = now.date().isoformat()
            conn = db.connect(config.DB_PATH)
            try:
                count = outreach.morning_count(conn, s)
            finally:
                conn.close()
            if count > 0:
                sent = await send_new_drafts(app.bot, chat_id, count, "☀️ Dobro jutro! Današnji nacrti:")
                if not sent:
                    log.info("jutarnja tura: nema novih leadova za nacrte")
        except Exception:  # noqa: BLE001
            log.exception("morning_loop")


def main() -> None:
    token = config.get("TELEGRAM_TOKEN", required=True)
    chat_id = int(config.get("TELEGRAM_CHAT_ID", required=True))
    only_me = filters.Chat(chat_id=chat_id)

    async def post_init(app: Application) -> None:
        # zadržavamo referencu da zadatak ne bi bio počišćen
        loop = asyncio.get_running_loop()
        app.bot_data["sender"] = loop.create_task(sender_loop(app, chat_id))
        app.bot_data["morning"] = loop.create_task(morning_loop(app, chat_id))

    app = Application.builder().token(token).post_init(post_init).build()
    for name, handler in [("start", cmd_help), ("pomoc", cmd_help), ("nise", cmd_nise), ("trazi", cmd_trazi),
                          ("status", cmd_status), ("top", cmd_top), ("sajt", cmd_sajt),
                          ("nacrti", cmd_nacrti), ("red", cmd_red), ("mejltest", cmd_mejl_test),
                          ("probni", cmd_probni)]:
        app.add_handler(CommandHandler(name, handler, filters=only_me))
    app.add_handler(CallbackQueryHandler(on_button))  # dugmad postoje samo u tvom chatu
    app.add_handler(MessageHandler(only_me & filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Bot pokrenut, slušam samo chat %s", chat_id)
    app.run_polling()


if __name__ == "__main__":
    main()
