"""SQLite baza: izvor istine za sve leadove. Sheet je samo prikaz."""
import sqlite3
from datetime import datetime
from pathlib import Path

STATUSES = ["nov", "analiziran", "za_kontakt", "ne_kontaktirati", "kontaktiran", "odgovorio", "odjavljen"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id            TEXT PRIMARY KEY,
    source        TEXT,
    name          TEXT NOT NULL,
    niche         TEXT,
    city          TEXT,
    address       TEXT,
    phone         TEXT,
    website       TEXT,
    website_type  TEXT,
    domain        TEXT,
    email         TEXT,
    email_source  TEXT,
    lat           REAL,
    lon           REAL,
    status        TEXT NOT NULL DEFAULT 'nov',
    note          TEXT,
    first_seen    TEXT,
    last_seen     TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(domain);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);

CREATE TABLE IF NOT EXISTS suppression (
    value  TEXT PRIMARY KEY,   -- mejl ili domen, mala slova
    kind   TEXT,               -- 'email' ili 'domain'
    added  TEXT,
    note   TEXT
);

CREATE TABLE IF NOT EXISTS analyses (
    lead_id            TEXT PRIMARY KEY REFERENCES leads(id),
    analyzed_at        TEXT,
    error              TEXT,      -- dns / timeout / connect
    http_status        INTEGER,
    final_url          TEXT,
    ssl                TEXT,      -- ok / nema / nevazeci
    ssl_days_left      INTEGER,
    https_redirect     INTEGER,
    viewport           INTEGER,
    mobile_overflow    INTEGER,
    layout_width       INTEGER,
    footer_year        INTEGER,
    platform           TEXT,
    platform_issues    TEXT,      -- JSON lista
    page_kind          TEXT,      -- parkiran / suspendovan / podrazumevana / u_izradi
    missing_title      INTEGER,
    screenshot_desktop TEXT,
    screenshot_mobile  TEXT,
    render_error       TEXT,
    issues             TEXT,      -- JSON lista nedostataka (srpski)
    score              INTEGER    -- ocena prilike 0–100
);

CREATE TABLE IF NOT EXISTS emails (
    lead_id     TEXT REFERENCES leads(id),
    email       TEXT,
    kind        TEXT,     -- genericki / poslovni / licni
    source_url  TEXT,     -- stranica na kojoj je adresa objavljena (ili 'openstreetmap')
    mx_ok       INTEGER,  -- 1 = domen prima poštu, 0 = ne prima, NULL = nije provereno
    found_at    TEXT,
    PRIMARY KEY (lead_id, email)
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id      TEXT REFERENCES leads(id),
    kind         TEXT,      -- prvi / followup
    to_email     TEXT,
    subject      TEXT,
    core         TEXT,      -- tekst bez potpisa i odjave
    category     TEXT,      -- kategorija leada u trenutku pisanja (za statistiku)
    status       TEXT,      -- nacrt / odobren / poslat / preskocen / otkazan / greska
    created_at   TEXT,
    approved_at  TEXT,
    sent_at      TEXT,
    message_id   TEXT,      -- Message-ID zaglavlje (za praćenje odgovora)
    tg_message_id INTEGER,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_lead ON messages(lead_id);

CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    niche     TEXT,
    city      TEXT,
    started   TEXT,
    finished  TEXT,
    found     INTEGER,
    new       INTEGER,
    updated   INTEGER,
    duplicate INTEGER
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # timeout: bot i ručne komande mogu istovremeno da koriste bazu
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Dodaje kolone koje su nastale posle prvog pokretanja, bez gubljenja podataka."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(leads)")}
    if "website_source" not in cols:
        conn.execute("ALTER TABLE leads ADD COLUMN website_source TEXT")
        conn.execute("UPDATE leads SET website_source='openstreetmap' WHERE website IS NOT NULL")
    if "email_type" not in cols:
        conn.execute("ALTER TABLE leads ADD COLUMN email_type TEXT")
    if "emails_checked_at" not in cols:
        conn.execute("ALTER TABLE leads ADD COLUMN emails_checked_at TEXT")
    a_cols = {row["name"] for row in conn.execute("PRAGMA table_info(analyses)")}
    if a_cols:
        for col, typ in [("psi_score", "INTEGER"), ("lcp_ms", "INTEGER"), ("cls", "REAL"),
                         ("tbt_ms", "INTEGER"), ("psi_error", "TEXT"), ("psi_checked_at", "TEXT"),
                         ("ai_usable", "INTEGER"), ("ai_score", "INTEGER"), ("ai_era", "TEXT"),
                         ("ai_summary", "TEXT"), ("ai_issues", "TEXT"), ("ai_error", "TEXT"),
                         ("ai_checked_at", "TEXT"), ("category", "TEXT")]:
            if col not in a_cols:
                conn.execute(f"ALTER TABLE analyses ADD COLUMN {col} {typ}")
    if "status_locked" not in cols:
        # 1 = status si ručno postavio u Sheet-u; program ga posle toga ne menja sam
        conn.execute("ALTER TABLE leads ADD COLUMN status_locked INTEGER DEFAULT 0")
    if "synced_status" not in cols:
        # Šta je program poslednji put upisao u Sheet; služi da razlikujemo tvoje izmene od starih vrednosti.
        conn.execute("ALTER TABLE leads ADD COLUMN synced_status TEXT")
        conn.execute("ALTER TABLE leads ADD COLUMN synced_note TEXT")
        # Popravka: ranija verzija je vraćala 'analiziran' na 'nov' pri svakom sync-u.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='analyses'").fetchone():
            conn.execute("""UPDATE leads SET status='analiziran'
                            WHERE status='nov' AND id IN (SELECT lead_id FROM analyses)""")
    conn.commit()


def mark_synced(conn: sqlite3.Connection) -> None:
    """Posle upisa u Sheet: pamti šta smo upisali."""
    conn.execute("UPDATE leads SET synced_status=status, synced_note=note")


def leads_for_email_scan(conn: sqlite3.Connection, niche: str | None = None, city: str | None = None,
                         again: bool = False, limit: int | None = None) -> list[sqlite3.Row]:
    sql = """SELECT * FROM leads WHERE status NOT IN ('ne_kontaktirati', 'odjavljen')
             AND (website_type = 'sajt' OR email IS NOT NULL)"""
    params: list = []
    if not again:
        sql += " AND emails_checked_at IS NULL"
    if niche:
        sql += " AND niche = ?"
        params.append(niche)
    if city:
        sql += " AND lower(city) = lower(?)"
        params.append(city)
    sql += " ORDER BY first_seen"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def save_email_scan(conn: sqlite3.Connection, lead_id: str, emails: list[dict], best: dict | None) -> None:
    """Pamti sve nađene adrese i upisuje najbolju u lead."""
    ts = now()
    for e in emails:
        conn.execute(
            """INSERT OR REPLACE INTO emails (lead_id, email, kind, source_url, mx_ok, found_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (lead_id, e["email"], e["kind"], e["source_url"],
             None if e.get("mx_ok") is None else int(bool(e["mx_ok"])), ts),
        )
    if best:
        conn.execute(
            "UPDATE leads SET email=?, email_source=?, email_type=?, emails_checked_at=? WHERE id=?",
            (best["email"], best["source_url"], best["kind"], ts, lead_id),
        )
        domain = best["email"].split("@", 1)[1]
        if is_suppressed(conn, best["email"], domain):
            conn.execute("UPDATE leads SET status='ne_kontaktirati' WHERE id=?", (lead_id,))
    else:
        conn.execute(
            "UPDATE leads SET email=NULL, email_source=NULL, email_type=NULL, emails_checked_at=? WHERE id=?",
            (ts, lead_id),
        )


def set_found_website(conn: sqlite3.Connection, lead_id: str, url: str, domain: str, reason: str) -> None:
    conn.execute(
        """UPDATE leads SET website=?, domain=?, website_type='sajt', website_source=?
           WHERE id=?""",
        (url, domain, f"pronađen po imenu ({reason})", lead_id),
    )


def is_suppressed(conn: sqlite3.Connection, email: str | None, domain: str | None) -> bool:
    values = [v.lower() for v in (email, domain) if v]
    if email and "@" in email:
        values.append(email.split("@", 1)[1].lower())
    if not values:
        return False
    marks = ",".join("?" * len(values))
    return conn.execute(f"SELECT 1 FROM suppression WHERE value IN ({marks})", values).fetchone() is not None


def upsert_lead(conn: sqlite3.Connection, lead: dict) -> str:
    """Upisuje lead. Vraća 'new', 'updated' ili 'duplicate'."""
    ts = now()
    existing = conn.execute("SELECT * FROM leads WHERE id = ?", (lead["id"],)).fetchone()
    if existing:
        conn.execute(
            """UPDATE leads SET name=?, address=COALESCE(?, address), phone=COALESCE(?, phone),
                   website=COALESCE(?, website), website_type=?, domain=COALESCE(?, domain),
                   email=COALESCE(email, ?), email_source=COALESCE(email_source, ?),
                   lat=?, lon=?, last_seen=?
               WHERE id=?""",
            (lead["name"], lead["address"], lead["phone"], lead["website"],
             lead["website_type"] if lead["website"] else existing["website_type"],
             lead["domain"], lead["email"], lead["email_source"],
             lead["lat"], lead["lon"], ts, lead["id"]),
        )
        return "updated"

    # Ista firma sa više lokacija (isti domen) ulazi samo jednom.
    if lead["domain"] and conn.execute(
        "SELECT 1 FROM leads WHERE domain = ?", (lead["domain"],)
    ).fetchone():
        return "duplicate"

    status = "ne_kontaktirati" if is_suppressed(conn, lead["email"], lead["domain"]) else "nov"
    conn.execute(
        """INSERT INTO leads (id, source, name, niche, city, address, phone, website, website_type,
                              website_source, domain, email, email_source, lat, lon, status,
                              first_seen, last_seen)
           VALUES (:id, :source, :name, :niche, :city, :address, :phone, :website, :website_type,
                   :website_source, :domain, :email, :email_source, :lat, :lon, :status, :ts, :ts)""",
        {"website_source": "openstreetmap" if lead.get("website") else None,
         **lead, "status": status, "ts": ts},
    )
    return "new"


def set_suppression(conn: sqlite3.Connection, values: list[tuple[str, str]]) -> int:
    """Zamenjuje listu odjava sadržajem iz Sheet-a i označava pogođene leadove."""
    conn.execute("DELETE FROM suppression")
    ts = now()
    for value, note in values:
        value = value.strip().lower()
        if not value:
            continue
        kind = "email" if "@" in value else "domain"
        conn.execute(
            "INSERT OR REPLACE INTO suppression (value, kind, added, note) VALUES (?, ?, ?, ?)",
            (value, kind, ts, note),
        )
    changed = 0
    for row in conn.execute(
        "SELECT id, email, domain FROM leads WHERE status NOT IN ('ne_kontaktirati', 'odjavljen')"
    ).fetchall():
        if is_suppressed(conn, row["email"], row["domain"]):
            conn.execute("UPDATE leads SET status='ne_kontaktirati' WHERE id=?", (row["id"],))
            changed += 1
    return changed


def apply_sheet_edits(conn: sqlite3.Connection, edits: dict[str, dict]) -> int:
    """Upisuje status i napomenu koje si ručno menjao u Sheet-u.

    Vrednost iz Sheet-a se preuzima samo ako se razlikuje od onoga što je program poslednji
    put tamo upisao. Tako stara vrednost u Sheet-u ne poništava promenu koju je program
    u međuvremenu napravio (npr. nov → analiziran).
    """
    changed = 0
    for lead_id, edit in edits.items():
        row = conn.execute(
            "SELECT status, note, synced_status, synced_note FROM leads WHERE id=?", (lead_id,)
        ).fetchone()
        if not row:
            continue
        sheet_status, sheet_note = edit.get("status"), edit.get("note") or None
        status, note = row["status"], row["note"]
        user_set_status = False

        if sheet_status in STATUSES:
            if row["synced_status"] is None:
                # još nemamo zapis o sinhronizaciji: 'nov' je podrazumevana vrednost, ne tvoja izmena
                user_set_status = sheet_status != "nov"
            else:
                user_set_status = sheet_status != row["synced_status"]
            if user_set_status:
                status = sheet_status
        if row["synced_status"] is None:
            note = sheet_note or note
        elif sheet_note != row["synced_note"]:
            note = sheet_note

        if user_set_status:
            conn.execute("UPDATE leads SET status_locked=1 WHERE id=?", (lead_id,))
        if status != row["status"] or note != row["note"]:
            conn.execute("UPDATE leads SET status=?, note=? WHERE id=?", (status, note, lead_id))
            changed += 1
    return changed


ANALYSIS_FIELDS = [
    "error", "http_status", "final_url", "ssl", "ssl_days_left", "https_redirect", "viewport",
    "mobile_overflow", "layout_width", "footer_year", "platform", "platform_issues", "page_kind",
    "missing_title", "screenshot_desktop", "screenshot_mobile", "render_error", "issues", "score",
]


def leads_to_analyze(conn: sqlite3.Connection, niche: str | None = None, city: str | None = None,
                     again: bool = False, limit: int | None = None,
                     only_without_site: bool = False) -> list[sqlite3.Row]:
    sql = """SELECT l.* FROM leads l LEFT JOIN analyses a ON a.lead_id = l.id
             WHERE l.status NOT IN ('ne_kontaktirati', 'odjavljen')"""
    params: list = []
    if only_without_site:
        sql += " AND l.website_type != 'sajt'"
    elif not again:
        sql += " AND a.lead_id IS NULL"
    if niche:
        sql += " AND l.niche = ?"
        params.append(niche)
    if city:
        sql += " AND lower(l.city) = lower(?)"
        params.append(city)
    sql += " ORDER BY l.first_seen"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def save_analysis(conn: sqlite3.Connection, lead_id: str, result: dict) -> None:
    import json

    row = {f: result.get(f) for f in ANALYSIS_FIELDS}
    for f in ("platform_issues", "issues"):
        if row[f] is not None:
            row[f] = json.dumps(row[f], ensure_ascii=False)
    for f in ("https_redirect", "viewport", "mobile_overflow", "missing_title"):
        if row[f] is not None:
            row[f] = int(bool(row[f]))
    cols = ", ".join(ANALYSIS_FIELDS)
    marks = ", ".join(f":{f}" for f in ANALYSIS_FIELDS)
    updates = ", ".join(f"{f}=excluded.{f}" for f in ["analyzed_at", *ANALYSIS_FIELDS])
    # upsert: ponovna tehnička provera ne briše brzinu i AI ocenu iz kasnijih koraka
    conn.execute(
        f"""INSERT INTO analyses (lead_id, analyzed_at, {cols}) VALUES (:lead_id, :ts, {marks})
            ON CONFLICT(lead_id) DO UPDATE SET {updates}""",
        {**row, "lead_id": lead_id, "ts": now()},
    )
    conn.execute("UPDATE leads SET status='analiziran' WHERE id=? AND status='nov'", (lead_id,))


# ---------------------------------------------------------------- brzina (korak 4)

SPEED_FIELDS = ["psi_score", "lcp_ms", "cls", "tbt_ms", "psi_error"]


def leads_for_speed(conn: sqlite3.Connection, niche: str | None = None, city: str | None = None,
                    again: bool = False, limit: int | None = None) -> list[sqlite3.Row]:
    """Samo sajtovi koji rade (nema smisla meriti brzinu parkiranog domena)."""
    sql = """SELECT l.id, l.name, l.niche, l.city, COALESCE(a.final_url, l.website) AS url
             FROM leads l JOIN analyses a ON a.lead_id = l.id
             WHERE l.status NOT IN ('ne_kontaktirati', 'odjavljen') AND l.website_type = 'sajt'
               AND a.error IS NULL AND COALESCE(a.http_status, 0) < 400
               AND COALESCE(a.page_kind, '') NOT IN ('parkiran', 'suspendovan', 'podrazumevana')"""
    params: list = []
    if not again:
        sql += " AND a.psi_checked_at IS NULL"
    if niche:
        sql += " AND l.niche = ?"
        params.append(niche)
    if city:
        sql += " AND lower(l.city) = lower(?)"
        params.append(city)
    sql += " ORDER BY l.first_seen"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def save_speed(conn: sqlite3.Connection, lead_id: str, result: dict) -> None:
    sets = ", ".join(f"{f}=:{f}" for f in SPEED_FIELDS)
    conn.execute(
        f"UPDATE analyses SET {sets}, psi_checked_at=:ts WHERE lead_id=:lead_id",
        {**{f: result.get(f) for f in SPEED_FIELDS}, "ts": now(), "lead_id": lead_id},
    )


# ---------------------------------------------------------------- AI ocena dizajna (korak 5)

DESIGN_FIELDS = ["ai_usable", "ai_score", "ai_era", "ai_summary", "ai_issues", "ai_error"]


def leads_for_design(conn: sqlite3.Connection, niche: str | None = None, city: str | None = None,
                     again: bool = False, limit: int | None = None) -> list[sqlite3.Row]:
    """Sajtovi koji rade i imaju screenshot."""
    sql = """SELECT l.id, l.name, l.niche, a.screenshot_desktop, a.screenshot_mobile, a.issues
             FROM leads l JOIN analyses a ON a.lead_id = l.id
             WHERE l.status NOT IN ('ne_kontaktirati', 'odjavljen') AND l.website_type = 'sajt'
               AND a.error IS NULL AND a.screenshot_desktop IS NOT NULL
               AND COALESCE(a.page_kind, '') NOT IN ('parkiran', 'suspendovan', 'podrazumevana')"""
    params: list = []
    if not again:
        sql += " AND a.ai_checked_at IS NULL"
    if niche:
        sql += " AND l.niche = ?"
        params.append(niche)
    if city:
        sql += " AND lower(l.city) = lower(?)"
        params.append(city)
    sql += " ORDER BY l.first_seen"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def save_design(conn: sqlite3.Connection, lead_id: str, result: dict) -> None:
    import json

    row = {f: result.get(f) for f in DESIGN_FIELDS}
    if row["ai_issues"] is not None:
        row["ai_issues"] = json.dumps(row["ai_issues"], ensure_ascii=False)
    if row["ai_usable"] is not None:
        row["ai_usable"] = int(bool(row["ai_usable"]))
    sets = ", ".join(f"{f}=:{f}" for f in DESIGN_FIELDS)
    conn.execute(f"UPDATE analyses SET {sets}, ai_checked_at=:ts WHERE lead_id=:lead_id",
                 {**row, "ts": now(), "lead_id": lead_id})


# ---------------------------------------------------------------- ocena iz svih koraka

_BOOL_FIELDS = ("https_redirect", "viewport", "mobile_overflow", "missing_title")


def analysis_inputs(row: sqlite3.Row) -> dict:
    """Red iz tabele analyses -> rečnik kakav očekuje checks.assess."""
    import json

    r = {k: row[k] for k in row.keys()}
    for f in _BOOL_FIELDS:
        if r.get(f) is not None:
            r[f] = bool(r[f])
    for f in ("platform_issues", "ai_issues"):
        try:
            r[f] = json.loads(r.get(f) or "[]")
        except ValueError:
            r[f] = []
    if r.get("ai_usable") is not None:
        r["ai_usable"] = bool(r["ai_usable"])
    return r


def rescore(conn: sqlite3.Connection, lead_id: str) -> tuple[list[str], int]:
    """Ponovo računa nedostatke i ocenu prilike iz svega što o firmi znamo."""
    import json

    from .checks import assess, categorize

    lead = conn.execute("SELECT website_type FROM leads WHERE id=?", (lead_id,)).fetchone()
    row = conn.execute("SELECT * FROM analyses WHERE lead_id=?", (lead_id,)).fetchone()
    if not lead or not row:
        return [], 0
    r = analysis_inputs(row) if lead["website_type"] == "sajt" else None
    issues, score = assess(lead["website_type"], r)
    category = categorize(lead["website_type"], r)
    conn.execute("UPDATE analyses SET issues=?, score=?, category=? WHERE lead_id=?",
                 (json.dumps(issues, ensure_ascii=False), score, category, lead_id))
    return issues, score


def rescore_all(conn: sqlite3.Connection) -> int:
    ids = [r[0] for r in conn.execute("SELECT lead_id FROM analyses")]
    for lead_id in ids:
        rescore(conn, lead_id)
    return len(ids)


def min_score_for_contact() -> int:
    from . import config

    try:
        return int(config.get("MIN_SCORE_FOR_CONTACT", "40"))
    except ValueError:
        return 40


def promote_ready(conn: sqlite3.Connection, threshold: int | None = None) -> int:
    """analiziran → za_kontakt kad: ima proveren mejl, ocena ≥ prag, nije dobar sajt.

    Ne dira statuse koje si ručno menjao, odjavljene ni one bez mejla.
    """
    threshold = min_score_for_contact() if threshold is None else threshold
    rows = conn.execute(
        """SELECT l.id, l.email, l.domain FROM leads l JOIN analyses a ON a.lead_id = l.id
           WHERE l.status = 'analiziran' AND COALESCE(l.status_locked, 0) = 0
             AND l.email IS NOT NULL AND a.score >= ?
             AND a.category NOT IN ('dobar_sajt', 'nepoznato')""",
        (threshold,),
    ).fetchall()
    promoted = 0
    for row in rows:
        if is_suppressed(conn, row["email"], row["domain"]):
            continue
        conn.execute("UPDATE leads SET status='za_kontakt' WHERE id=?", (row["id"],))
        promoted += 1
    return promoted


def category_report(conn: sqlite3.Connection) -> dict:
    """Broj leadova po kategoriji, sa i bez mejla, i koliko ih je spremno za kontakt."""
    rows = conn.execute(
        """SELECT COALESCE(a.category, 'neanaliziran') AS cat, COUNT(*) AS n,
                  SUM(l.email IS NOT NULL) AS with_email, SUM(l.status = 'za_kontakt') AS ready
           FROM leads l LEFT JOIN analyses a ON a.lead_id = l.id
           WHERE l.status NOT IN ('ne_kontaktirati', 'odjavljen')
           GROUP BY cat ORDER BY n DESC"""
    ).fetchall()
    return {r["cat"]: {"n": r["n"], "with_email": r["with_email"] or 0, "ready": r["ready"] or 0} for r in rows}


# ---------------------------------------------------------------- poruke (Faza 2)

def leads_for_drafts(conn: sqlite3.Connection, limit: int = 5) -> list[sqlite3.Row]:
    """za_kontakt sa mejlom, bez otvorenog ili poslatog mejla; preskočeni čekaju 30 dana."""
    return conn.execute(
        """SELECT l.*, a.score, a.category, a.issues, a.ai_summary
           FROM leads l JOIN analyses a ON a.lead_id = l.id
           WHERE l.status = 'za_kontakt' AND l.email IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.lead_id = l.id
                             AND (m.status IN ('nacrt', 'odobren', 'poslat')
                                  OR (m.status = 'preskocen'
                                      AND datetime(m.created_at) > datetime('now', 'localtime', '-30 days'))))
           ORDER BY a.score DESC, l.name LIMIT ?""",
        (limit,),
    ).fetchall()


def lead_for_message(conn: sqlite3.Connection, lead_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT l.*, a.score, a.category, a.issues, a.ai_summary
           FROM leads l LEFT JOIN analyses a ON a.lead_id = l.id WHERE l.id = ?""",
        (lead_id,),
    ).fetchone()


def create_message(conn: sqlite3.Connection, lead: sqlite3.Row | dict, kind: str, subject: str, core: str) -> int:
    cur = conn.execute(
        """INSERT INTO messages (lead_id, kind, to_email, subject, core, category, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'nacrt', ?)""",
        (lead["id"], kind, lead["email"], subject, core, lead["category"], now()),
    )
    return cur.lastrowid


def get_message(conn: sqlite3.Connection, msg_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()


def update_message(conn: sqlite3.Connection, msg_id: int, **fields) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE messages SET {sets} WHERE id=?", (*fields.values(), msg_id))


def open_drafts_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM messages WHERE status='nacrt'").fetchone()[0]


def next_to_send(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Najstarija odobrena poruka čiji lead u međuvremenu nije odjavljen ni odgovorio."""
    return conn.execute(
        """SELECT m.* FROM messages m JOIN leads l ON l.id = m.lead_id
           WHERE m.status = 'odobren' AND l.status NOT IN ('ne_kontaktirati', 'odjavljen', 'odgovorio')
           ORDER BY m.approved_at LIMIT 1"""
    ).fetchone()


def sent_today(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM messages WHERE status='poslat' AND date(sent_at) = date('now', 'localtime')"
    ).fetchone()[0]


def last_sent_at(conn: sqlite3.Connection) -> str | None:
    return conn.execute("SELECT MAX(sent_at) FROM messages WHERE status='poslat'").fetchone()[0]


def mark_sent(conn: sqlite3.Connection, msg_id: int, message_id: str) -> None:
    msg = get_message(conn, msg_id)
    update_message(conn, msg_id, status="poslat", sent_at=now(), message_id=message_id, error=None)
    conn.execute("UPDATE leads SET status='kontaktiran' WHERE id=? AND status NOT IN "
                 "('ne_kontaktirati', 'odjavljen', 'odgovorio')", (msg["lead_id"],))


def start_run(conn: sqlite3.Connection, niche: str, city: str) -> int:
    cur = conn.execute("INSERT INTO runs (niche, city, started) VALUES (?, ?, ?)", (niche, city, now()))
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, counts: dict) -> None:
    conn.execute(
        "UPDATE runs SET finished=?, found=?, new=?, updated=?, duplicate=? WHERE id=?",
        (now(), counts.get("found", 0), counts.get("new", 0), counts.get("updated", 0),
         counts.get("duplicate", 0), run_id),
    )
