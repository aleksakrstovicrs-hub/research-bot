"""Klijenti portala: pozivni kodovi, mesečni limiti i evidencija izveštaja.

Posebna baza (data/portal.db), odvojena od tvojih leadova.
"""
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    site          TEXT,
    monthly_limit INTEGER NOT NULL DEFAULT 4,
    invite_code   TEXT UNIQUE,
    chat_id       INTEGER UNIQUE,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER REFERENCES clients(id),
    niche       TEXT,
    city        TEXT,
    status      TEXT,      -- u_redu / radi / gotovo / greska
    requested_at TEXT,
    finished_at TEXT,
    file        TEXT,
    total       INTEGER,
    recommended INTEGER,
    error       TEXT
);
"""

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # bez 0/O i 1/I, da se ne pobrkaju


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = path or (config.DATA_DIR / "portal.db")
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def new_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def add_client(conn: sqlite3.Connection, name: str, site: str = "", limit: int = 4) -> sqlite3.Row:
    code = new_code()
    cur = conn.execute(
        "INSERT INTO clients (name, site, monthly_limit, invite_code, created_at) VALUES (?, ?, ?, ?, ?)",
        (name.strip(), site.strip().replace("https://", "").replace("http://", "").strip("/"), limit, code, now()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM clients WHERE id=?", (cur.lastrowid,)).fetchone()


def redeem(conn: sqlite3.Connection, code: str, chat_id: int) -> tuple[sqlite3.Row | None, str]:
    """Povezuje Telegram chat sa klijentom preko pozivnog koda (kod važi jednom)."""
    existing = client_by_chat(conn, chat_id)
    if existing:
        return existing, "vec_povezan"
    row = conn.execute("SELECT * FROM clients WHERE invite_code=? AND active=1",
                       (code.strip().upper(),)).fetchone()
    if not row or row["chat_id"]:
        return None, "nevazeci_kod"
    conn.execute("UPDATE clients SET chat_id=?, invite_code=NULL WHERE id=?", (chat_id, row["id"]))
    conn.commit()
    return conn.execute("SELECT * FROM clients WHERE id=?", (row["id"],)).fetchone(), "ok"


def client_by_chat(conn: sqlite3.Connection, chat_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM clients WHERE chat_id=? AND active=1", (chat_id,)).fetchone()


def month_key(when: datetime | None = None) -> str:
    return (when or datetime.now()).strftime("%Y-%m")


def used_this_month(conn: sqlite3.Connection, client_id: int, when: datetime | None = None) -> int:
    """Gotovi izveštaji ovog meseca + oni koji su u redu ili rade (da se limit ne prekorači čekanjem)."""
    return conn.execute(
        """SELECT COUNT(*) FROM reports WHERE client_id=? AND status IN ('gotovo', 'u_redu', 'radi')
           AND substr(requested_at, 1, 7) = ?""",
        (client_id, month_key(when)),
    ).fetchone()[0]


def remaining(conn: sqlite3.Connection, client: sqlite3.Row, when: datetime | None = None) -> int:
    return max(0, client["monthly_limit"] - used_this_month(conn, client["id"], when))


def request_report(conn: sqlite3.Connection, client: sqlite3.Row, niche: str, city: str,
                   when: datetime | None = None) -> tuple[int | None, str]:
    """Upisuje zahtev ako ima mesta u limitu. Vraća (id zahteva ili None, razlog)."""
    if remaining(conn, client, when) <= 0:
        return None, "limit"
    busy = conn.execute("SELECT 1 FROM reports WHERE client_id=? AND status IN ('u_redu', 'radi')",
                        (client["id"],)).fetchone()
    if busy:
        return None, "vec_radi"
    cur = conn.execute(
        "INSERT INTO reports (client_id, niche, city, status, requested_at) VALUES (?, ?, ?, 'u_redu', ?)",
        (client["id"], niche, city, (when or datetime.now()).isoformat(timespec="seconds")),
    )
    conn.commit()
    return cur.lastrowid, "ok"


def set_status(conn: sqlite3.Connection, report_id: int, status: str, **fields) -> None:
    fields["status"] = status
    if status in ("gotovo", "greska"):
        fields["finished_at"] = now()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE reports SET {sets} WHERE id=?", (*fields.values(), report_id))
    conn.commit()


def queue_position(conn: sqlite3.Connection, report_id: int) -> int:
    """Koliko izveštaja je ispred ovog (0 = sledeći je on)."""
    return conn.execute(
        "SELECT COUNT(*) FROM reports WHERE status IN ('u_redu', 'radi') AND id < ?", (report_id,)
    ).fetchone()[0]


def requeue_interrupted(conn: sqlite3.Connection) -> list[int]:
    """Posle restarta: izveštaji koji su „radili“ vraćaju se u red. Vraća sve iz reda, po redosledu."""
    conn.execute("UPDATE reports SET status='u_redu' WHERE status='radi'")
    conn.commit()
    return [r[0] for r in conn.execute("SELECT id FROM reports WHERE status='u_redu' ORDER BY id")]


def overview(conn: sqlite3.Connection, when: datetime | None = None) -> list[dict]:
    out = []
    for c in conn.execute("SELECT * FROM clients ORDER BY id"):
        out.append({"id": c["id"], "name": c["name"], "limit": c["monthly_limit"],
                    "used": used_this_month(conn, c["id"], when), "active": bool(c["active"]),
                    "linked": c["chat_id"] is not None, "code": c["invite_code"]})
    return out
