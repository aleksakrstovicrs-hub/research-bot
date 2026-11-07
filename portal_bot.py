"""Portal: Telegram bot preko kog KLIJENTI sami traže izveštaje.

Poseban bot (poseban token od @BotFather), poseban servis. Klijent se povezuje pozivnim kodom,
ima mesečni limit i dobija Excel direktno u chat. Ti (admin) dobijaš obaveštenje i kopiju.
"""
import asyncio
import logging
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

import izvestaj
from klijenti import botlogic, config, portal
from klijenti.joblock import heavy_job
from klijenti.niche import NICHES, resolve_niche

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("portal")

QUEUE: asyncio.Queue = asyncio.Queue()

CLIENT_HELP = """Komande:
/izvestaj stomatolozi Beograd: nova lista potencijalnih klijenata (Excel)
/izvestaj auto servisi, Novi Sad: zarez kad grad ima više reči
/stanje: koliko izveštaja Vam je ostalo ovog meseca
/nise: delatnosti koje možete da tražite

Izveštaj se pravi 30–60 minuta; javiću Vam kad bude gotov."""

ADMIN_HELP = """Admin:
/novi 4 primer-studio.rs Primer Studio: novi klijent (limit, sajt, ime) → pozivni kod
/klijenti: spisak, potrošnja ovog meseca, kodovi
/limit 3 8: klijentu #3 postavi limit 8 mesečno
/iskljuci 3, /ukljuci 3
/kod 3: novi pozivni kod za klijenta #3 (ako je izgubio stari ili menja telefon)"""


def admin_id() -> int:
    return int(config.get("TELEGRAM_CHAT_ID", required=True))


def is_admin(update: Update) -> bool:
    return update.effective_chat.id == admin_id()


# ---------------------------------------------------------------- klijent

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = portal.connect()
    try:
        if is_admin(update):
            await update.message.reply_text("Ti si admin.\n\n" + ADMIN_HELP)
            return
        if not context.args:
            client = portal.client_by_chat(conn, update.effective_chat.id)
            if client:
                await update.message.reply_text(f"Dobrodošli nazad, {client['name']}.\n\n{CLIENT_HELP}")
            else:
                await update.message.reply_text("Za pristup unesite pozivni kod: /start KOD")
            return
        client, status = portal.redeem(conn, context.args[0], update.effective_chat.id)
    finally:
        conn.close()
    if status == "nevazeci_kod":
        await update.message.reply_text("Kod nije važeći ili je već iskorišćen. Javite se Aleksi za novi.")
        return
    await update.message.reply_text(
        f"✅ Povezano: {client['name']}\nMesečni limit: {client['monthly_limit']} izveštaja.\n\n{CLIENT_HELP}")
    if status == "ok":
        await context.bot.send_message(admin_id(), f"🔗 {client['name']} je povezao/la portal.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(ADMIN_HELP if is_admin(update) else CLIENT_HELP)


async def cmd_nise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Delatnosti:\n" + "\n".join(f"• {n}" for n in sorted(NICHES)))


async def cmd_stanje(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = portal.connect()
    try:
        client = portal.client_by_chat(conn, update.effective_chat.id)
        if not client:
            await update.message.reply_text("Niste povezani. Unesite pozivni kod: /start KOD")
            return
        left = portal.remaining(conn, client)
        last = conn.execute("""SELECT niche, city, status, requested_at FROM reports WHERE client_id=?
                               ORDER BY id DESC LIMIT 5""", (client["id"],)).fetchall()
    finally:
        conn.close()
    lines = [f"Ovog meseca: preostalo {left} od {client['monthly_limit']} izveštaja."]
    if last:
        labels = {"gotovo": "✅", "u_redu": "⏳", "radi": "⚙️", "greska": "❌"}
        lines += ["", "Poslednji zahtevi:"] + [
            f"{labels.get(r['status'], '•')} {r['niche']}, {r['city']} ({r['requested_at'][:10]})" for r in last]
    await update.message.reply_text("\n".join(lines))


async def cmd_izvestaj(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = portal.connect()
    try:
        client = portal.client_by_chat(conn, update.effective_chat.id)
        if not client:
            await update.message.reply_text("Niste povezani. Unesite pozivni kod: /start KOD")
            return
        try:
            niche_text, city = botlogic.parse_search(context.args)
            niche, _ = resolve_niche(niche_text)
        except ValueError as e:
            await update.message.reply_text(str(e).replace("/trazi", "/izvestaj"))
            return
        report_id, reason = portal.request_report(conn, client, niche, city)
        if reason == "limit":
            await update.message.reply_text(
                f"Iskoristili ste svih {client['monthly_limit']} izveštaja za ovaj mesec. "
                "Limit se obnavlja 1. u mesecu; za dodatni izveštaj javite se Aleksi.")
            return
        if reason == "vec_radi":
            await update.message.reply_text("Već pravim jedan Vaš izveštaj. Pošaljite novi zahtev kad stigne.")
            return
        ahead = portal.queue_position(conn, report_id)
        left = portal.remaining(conn, client)
    finally:
        conn.close()
    wait = "Krećem odmah." if ahead == 0 else f"Ispred Vas u redu: {ahead}."
    await update.message.reply_text(
        f"📋 Primljeno: {niche}, {city}. {wait}\nIzveštaj stiže za 30–60 min. "
        f"Posle ovog ostaje Vam {left} ovog meseca.")
    await context.bot.send_message(admin_id(), f"📥 {client['name']} traži: {niche}, {city} (ostalo {left})")
    await QUEUE.put(report_id)


# ---------------------------------------------------------------- admin

async def cmd_novi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 3 or not context.args[0].isdigit():
        await update.message.reply_text("Primer: /novi 4 primer-studio.rs Primer Studio\n"
                                        "(sajt može biti - ako ga nema)")
        return
    limit, site, name = int(context.args[0]), context.args[1], " ".join(context.args[2:])
    conn = portal.connect()
    try:
        c = portal.add_client(conn, name, "" if site == "-" else site, limit)
    finally:
        conn.close()
    username = (await context.bot.get_me()).username
    await update.message.reply_text(
        f"✅ Klijent #{c['id']}: {c['name']}, limit {limit}/mesec\nPozivni kod: {c['invite_code']}\n\n"
        f"Pošalji klijentu:\n1. Otvori t.me/{username}\n2. Pošalji: /start {c['invite_code']}")


async def cmd_klijenti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = portal.connect()
    try:
        rows = portal.overview(conn)
    finally:
        conn.close()
    if not rows:
        await update.message.reply_text("Još nema klijenata. /novi ...")
        return
    lines = ["Klijenti (potrošnja ovog meseca):", ""]
    for r in rows:
        state = "" if r["active"] else " [isključen]"
        link = "povezan" if r["linked"] else f"kod {r['code']}"
        lines.append(f"#{r['id']} {r['name']}: {r['used']}/{r['limit']}, {link}{state}")
    await update.message.reply_text("\n".join(lines))


async def _set(update: Update, context: ContextTypes.DEFAULT_TYPE, sql: str, params_from_args, msg: str) -> None:
    try:
        params = params_from_args(context.args)
    except (IndexError, ValueError):
        await update.message.reply_text("Proveri broj klijenta. /pomoc")
        return
    conn = portal.connect()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()
    await update.message.reply_text(msg)


async def cmd_limit(update, context):
    await _set(update, context, "UPDATE clients SET monthly_limit=? WHERE id=?",
               lambda a: (int(a[1]), int(a[0])), "✅ Limit promenjen.")


async def cmd_iskljuci(update, context):
    await _set(update, context, "UPDATE clients SET active=0 WHERE id=?", lambda a: (int(a[0]),),
               "⛔ Klijent isključen (portal mu više ne odgovara).")


async def cmd_ukljuci(update, context):
    await _set(update, context, "UPDATE clients SET active=1 WHERE id=?", lambda a: (int(a[0]),),
               "✅ Klijent ponovo uključen.")


async def cmd_kod(update, context):
    code = portal.new_code()
    await _set(update, context, "UPDATE clients SET invite_code=?, chat_id=NULL WHERE id=?",
               lambda a: (code, int(a[0])), f"Novi pozivni kod: {code}\n(stari telefon je odvezan)")


# ---------------------------------------------------------------- red čekanja

async def worker(app: Application) -> None:
    while True:
        report_id = await QUEUE.get()
        conn = portal.connect()
        try:
            r = conn.execute("""SELECT r.*, c.name, c.site, c.chat_id FROM reports r
                                JOIN clients c ON c.id = r.client_id WHERE r.id=?""", (report_id,)).fetchone()
            if not r or r["status"] != "u_redu":
                continue
            async with heavy_job():
                portal.set_status(conn, report_id, "radi")
                await app.bot.send_message(r["chat_id"], f"⚙️ Počinjem: {r['niche']}, {r['city']}")
                try:
                    res = await izvestaj.make_report(r["niche"], r["city"], r["name"], r["site"] or "")
                except Exception as e:  # noqa: BLE001
                    log.exception("izveštaj pukao")
                    portal.set_status(conn, report_id, "greska", error=f"{type(e).__name__}: {e}"[:300])
                    await app.bot.send_message(
                        r["chat_id"], "❌ Izveštaj nije uspeo, i ne računa se u Vaš limit. "
                        "Aleksa je obavešten; možete probati ponovo kasnije ili sa drugim gradom.")
                    await app.bot.send_message(admin_id(), f"❌ {r['name']}: {r['niche']}, {r['city']}\n{e}"[:1000])
                    continue
            portal.set_status(conn, report_id, "gotovo", file=str(res["path"]), total=res["total"],
                              recommended=res["recommended"])
            caption = (f"✅ {r['niche']}, {r['city']}: {res['total']} firmi, "
                       f"{res['recommended']} preporučenih za kontakt.\n"
                       "Tab „Preporučeni“ ima i predloge ponuda; pročitajte ih pre slanja.")
            path = Path(res["path"])
            await app.bot.send_document(r["chat_id"], path.read_bytes(), filename=path.name, caption=caption)
            await app.bot.send_document(admin_id(), path.read_bytes(), filename=path.name,
                                        caption=f"📤 Poslato klijentu {r['name']}.\n{caption}")
        except Exception:  # noqa: BLE001
            log.exception("worker")
        finally:
            conn.close()
            QUEUE.task_done()


def main() -> None:
    token = config.get("PORTAL_BOT_TOKEN", required=True)
    admin = filters.Chat(chat_id=admin_id())

    async def post_init(app: Application) -> None:
        conn = portal.connect()
        try:
            for report_id in portal.requeue_interrupted(conn):
                QUEUE.put_nowait(report_id)
        finally:
            conn.close()
        app.bot_data["worker"] = asyncio.get_running_loop().create_task(worker(app))

    app = Application.builder().token(token).post_init(post_init).build()
    for name, handler in [("start", cmd_start), ("pomoc", cmd_help), ("help", cmd_help), ("nise", cmd_nise),
                          ("stanje", cmd_stanje), ("izvestaj", cmd_izvestaj)]:
        app.add_handler(CommandHandler(name, handler))
    for name, handler in [("novi", cmd_novi), ("klijenti", cmd_klijenti), ("limit", cmd_limit),
                          ("iskljuci", cmd_iskljuci), ("ukljuci", cmd_ukljuci), ("kod", cmd_kod)]:
        app.add_handler(CommandHandler(name, handler, filters=admin))
    log.info("Portal pokrenut")
    app.run_polling()


if __name__ == "__main__":
    main()
