from __future__ import annotations

import datetime
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from threading import Thread
from typing import List, Optional

from flask import Flask, jsonify, render_template, request
from playwright.sync_api import sync_playwright

app = Flask(__name__)

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
PROFILE_DIR = DATA_DIR / "profiles"
DB_PATH = DATA_DIR / "accounts.db"
LOGIN_URL = "https://www.kleinanzeigen.de/m-benutzer-anmeldung-inapp.html?appType=MWEB"

DATA_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Account:
    id: int
    name: str
    email: str
    age_days: int
    proxy: str
    ios_profile: str
    profile_path: str
    notes: str = ""
    created_at: Optional[str] = None


@dataclass
class Message:
    account_id: int
    listing_title: str
    sender: str
    text: str
    timestamp: str = field(
        default_factory=lambda: datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    )


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def build_profile_path(account_id: int) -> str:
    return str(PROFILE_DIR / f"account_{account_id}")


def init_db() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                age_days INTEGER NOT NULL,
                proxy TEXT NOT NULL,
                ios_profile TEXT NOT NULL,
                profile_path TEXT,
                notes TEXT,
                created_at TEXT
            );
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                listing_title TEXT NOT NULL,
                sender TEXT NOT NULL,
                text TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS login_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT
            );
            """
        )

        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(accounts)").fetchall()
        }
        if "created_at" not in columns:
            connection.execute("ALTER TABLE accounts ADD COLUMN created_at TEXT")
            connection.commit()
        if "profile_path" not in columns:
            connection.execute("ALTER TABLE accounts ADD COLUMN profile_path TEXT")
            connection.commit()

        rows = connection.execute(
            "SELECT id, age_days, created_at, profile_path FROM accounts"
        ).fetchall()
        now = datetime.datetime.now()
        for row in rows:
            updates = {}
            if not row["created_at"]:
                created_at = now - datetime.timedelta(days=int(row["age_days"] or 0))
                updates["created_at"] = created_at.isoformat(timespec="minutes")
            if not row["profile_path"]:
                updates["profile_path"] = build_profile_path(int(row["id"]))
            if updates:
                connection.execute(
                    """
                    UPDATE accounts
                    SET created_at = COALESCE(?, created_at),
                        profile_path = COALESCE(?, profile_path)
                    WHERE id = ?
                    """,
                    (
                        updates.get("created_at"),
                        updates.get("profile_path"),
                        row["id"],
                    ),
                )
        connection.commit()

        seed_demo_data = os.getenv("SEED_DEMO_DATA", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }

        if seed_demo_data:
            account_count = connection.execute(
                "SELECT COUNT(*) AS count FROM accounts"
            ).fetchone()["count"]
            if account_count == 0:
                now = datetime.datetime.now()
                connection.executemany(
                    """
                    INSERT INTO accounts
                        (name, email, age_days, proxy, ios_profile, profile_path, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    [
                        (
                            "Account A",
                            "account-a@firma.de",
                            320,
                            "http://user:pass@proxy-a:8080",
                            "iPhone 13",
                            build_profile_path(1),
                            "Hauptaccount",
                            (now - datetime.timedelta(days=320)).isoformat(timespec="minutes"),
                        ),
                        (
                            "Account B",
                            "account-b@firma.de",
                            180,
                            "http://user:pass@proxy-b:8080",
                            "iPhone 12",
                            build_profile_path(2),
                            "Ersatzaccount",
                            (now - datetime.timedelta(days=180)).isoformat(timespec="minutes"),
                        ),
                    ],
                )

            message_count = connection.execute(
                "SELECT COUNT(*) AS count FROM messages"
            ).fetchone()["count"]
            if message_count == 0:
                now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                connection.executemany(
                    """
                    INSERT INTO messages
                        (account_id, listing_title, sender, text, timestamp)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    [
                        (
                            1,
                            "iPhone 13 Pro 128GB",
                            "Kunde",
                            "Ist das Gerät noch verfügbar?",
                            now,
                        ),
                        (
                            2,
                            "MacBook Air M1",
                            "Kunde",
                            "Ist der Preis verhandelbar?",
                            now,
                        ),
                    ],
                )

        connection.commit()


def row_to_account(row: sqlite3.Row) -> Account:
    data = dict(row)
    created_at = data.get("created_at")
    if created_at:
        try:
            created = datetime.datetime.fromisoformat(created_at)
            data["age_days"] = max((datetime.datetime.now() - created).days, 0)
        except ValueError:
            try:
                created = datetime.datetime.strptime(created_at, "%Y-%m-%d %H:%M")
                data["age_days"] = max((datetime.datetime.now() - created).days, 0)
            except ValueError:
                data["age_days"] = int(data.get("age_days") or 0)
    else:
        data["age_days"] = int(data.get("age_days") or 0)

    if not data.get("profile_path"):
        data["profile_path"] = build_profile_path(int(data["id"]))
    return Account(**data)


def fetch_accounts() -> List[Account]:
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    return [row_to_account(row) for row in rows]


def fetch_messages() -> List[Message]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT account_id, listing_title, sender, text, timestamp FROM messages"
        ).fetchall()
    return [Message(**dict(row)) for row in rows]


def record_login_job(account_id: int, status: str, finished_at: Optional[str] = None) -> None:
    """
    status:
      - "running": create a new job entry
      - anything else: update the latest running job for that account
    """
    with get_connection() as connection:
        if status == "running":
            started_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            connection.execute(
                "INSERT INTO login_jobs (account_id, status, started_at) VALUES (?, ?, ?)",
                (account_id, status, started_at),
            )
        else:
            connection.execute(
                """
                UPDATE login_jobs
                SET status = ?, finished_at = ?
                WHERE id = (
                    SELECT id FROM login_jobs
                    WHERE account_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                )
                """,
                (status, finished_at, account_id),
            )
        connection.commit()


def login_with_playwright(account: Account) -> None:
    """
    Startet eine persistente WebKit Session (iOS Device Settings) mit Proxy.
    Human-in-the-loop: Fenster bleibt offen, damit der User manuell einloggen kann.
    """
    record_login_job(account.id, "running")

    if not account.profile_path:
        account.profile_path = build_profile_path(account.id)
        with get_connection() as connection:
            connection.execute(
                "UPDATE accounts SET profile_path = ? WHERE id = ?",
                (account.profile_path, account.id),
            )
            connection.commit()

    profile_path = Path(account.profile_path or build_profile_path(account.id))
    profile_path.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as playwright:
            device = playwright.devices.get(account.ios_profile) or playwright.devices["iPhone 13"]

            context = playwright.webkit.launch_persistent_context(
                user_data_dir=str(profile_path),
                proxy={"server": account.proxy} if account.proxy else None,
                locale="de-DE",
                headless=False,
                **device,
            )
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")

            # Markiere: wartet auf den User (Login im offenen Browser-Fenster)
            record_login_job(
                account.id,
                "waiting_for_user",
            )

            context.wait_for_event("close")
    finally:
        record_login_job(
            account.id,
            "completed",
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


def start_login_thread(account: Account) -> None:
    Thread(target=login_with_playwright, args=(account,), daemon=True).start()


@app.get("/")
def index() -> str:
    accounts = fetch_accounts()
    messages = fetch_messages()
    return render_template(
        "index.html",
        accounts=accounts,
        messages=messages,
        login_url=LOGIN_URL,
    )


@app.get("/api/messages")
def get_messages():
    return jsonify([message.__dict__ for message in fetch_messages()])


@app.post("/api/messages")
def post_message():
    payload = request.get_json(force=True)
    message = Message(
        account_id=int(payload["account_id"]),
        listing_title=payload["listing_title"],
        sender="Firma",
        text=payload["text"],
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO messages (account_id, listing_title, sender, text, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                message.account_id,
                message.listing_title,
                message.sender,
                message.text,
                message.timestamp,
            ),
        )
        connection.commit()

    return jsonify(message.__dict__), 201


@app.post("/api/login")
def login_account():
    payload = request.get_json(force=True)
    proxy = (payload.get("proxy") or "").strip()
    ios_profile = (payload.get("ios_profile") or "").strip()
    label = (payload.get("label") or "").strip()

    if not proxy or not ios_profile:
        return jsonify({"error": "Proxy und iOS-Profil sind erforderlich."}), 400

    created_at = datetime.datetime.now().isoformat(timespec="minutes")
    account_name = label or "Neuer Account"

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO accounts (name, email, age_days, proxy, ios_profile, profile_path, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_name, "", 0, proxy, ios_profile, "", label, created_at),
        )
        account_id = cursor.lastrowid
        profile_path = build_profile_path(account_id)
        connection.execute(
            "UPDATE accounts SET profile_path = ? WHERE id = ?",
            (profile_path, account_id),
        )
        connection.commit()

        row = connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()

    account = row_to_account(row)
    start_login_thread(account)
    return jsonify({"status": "started", "account_id": account.id})


@app.get("/api/login-jobs/<int:account_id>")
def get_login_job(account_id: int):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT status, started_at, finished_at
            FROM login_jobs
            WHERE account_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (account_id,),
        ).fetchone()

    if row is None:
        return jsonify({"error": "Kein Login-Job gefunden."}), 404

    return jsonify(dict(row))


init_db()

if __name__ == "__main__":
    app.run(debug=True)
