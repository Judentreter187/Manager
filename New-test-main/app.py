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
    notes: str = ""


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
                notes TEXT
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
                connection.executemany(
                    """
                    INSERT INTO accounts
                        (name, email, age_days, proxy, ios_profile, notes)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    [
                        (
                            "Account A",
                            "account-a@firma.de",
                            320,
                            "http://user:pass@proxy-a:8080",
                            "iPhone 13",
                            "Hauptaccount",
                        ),
                        (
                            "Account B",
                            "account-b@firma.de",
                            180,
                            "http://user:pass@proxy-b:8080",
                            "iPhone 12",
                            "Ersatzaccount",
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


def fetch_accounts() -> List[Account]:
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    return [Account(**dict(row)) for row in rows]


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
                WHERE account_id = ? AND status = 'running'
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

    profile_path = PROFILE_DIR / f"account_{account.id}"
    profile_path.mkdir(parents=True, exist_ok=True)

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
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

        # Wichtig: nicht sofort schließen -> Human-in-the-loop.
        # Wenn du irgendwann auto-close willst, kannst du hier mit wait_for_timeout arbeiten.
        # context.close()


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
    account_id = int(payload["account_id"])

    with get_connection() as connection:
        row = connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()

    if row is None:
        return jsonify({"error": "Account nicht gefunden"}), 404

    account = Account(**dict(row))
    start_login_thread(account)
    return jsonify({"status": "started", "login_url": LOGIN_URL})


init_db()

if __name__ == "__main__":
    app.run(debug=True)
