from __future__ import annotations

import datetime
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
    password: Optional[str] = None


@dataclass
class LoginJob:
    id: int
    email: str
    password: str
    proxy: str
    ios_profile: str
    profile_path: str
    status: str
    started_at: str
    finished_at: Optional[str] = None
    checked_at: Optional[str] = None
    valid: Optional[int] = None
    account_id: Optional[int] = None


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
                created_at TEXT,
                password TEXT
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
                account_id INTEGER,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                email TEXT,
                password TEXT,
                proxy TEXT,
                ios_profile TEXT,
                profile_path TEXT,
                checked_at TEXT,
                valid INTEGER
            );
            """
        )

        # --- migrate accounts table if older DB exists ---
        account_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(accounts)").fetchall()
        }
        if "created_at" not in account_columns:
            connection.execute("ALTER TABLE accounts ADD COLUMN created_at TEXT")
        if "profile_path" not in account_columns:
            connection.execute("ALTER TABLE accounts ADD COLUMN profile_path TEXT")
        if "password" not in account_columns:
            connection.execute("ALTER TABLE accounts ADD COLUMN password TEXT")

        # --- migrate login_jobs table if older DB exists ---
        login_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(login_jobs)").fetchall()
        }
        for column_name, sql_type in {
            "email": "TEXT",
            "password": "TEXT",
            "proxy": "TEXT",
            "ios_profile": "TEXT",
            "profile_path": "TEXT",
            "checked_at": "TEXT",
            "valid": "INTEGER",
        }.items():
            if column_name not in login_columns:
                connection.execute(
                    f"ALTER TABLE login_jobs ADD COLUMN {column_name} {sql_type}"
                )

        # --- backfill derived columns for accounts ---
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


def create_login_job(email: str, password: str, proxy: str) -> int:
    started_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    ios_profile = "iPhone 13"
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO login_jobs
                (account_id, status, started_at, email, password, proxy, ios_profile, profile_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                0,
                "running",
                started_at,
                email,
                password,
                proxy,
                ios_profile,
                "",
            ),
        )
        job_id = int(cursor.lastrowid)
        profile_path = build_profile_path(job_id)
        connection.execute(
            "UPDATE login_jobs SET profile_path = ? WHERE id = ?",
            (profile_path, job_id),
        )
        connection.commit()
    return job_id


def fetch_login_job(job_id: int) -> Optional[LoginJob]:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM login_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if row is None:
        return None
    return LoginJob(**dict(row))


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


def update_login_job(
    job_id: int,
    status: str,
    finished_at: Optional[str] = None,
    checked_at: Optional[str] = None,
    valid: Optional[int] = None,
    account_id: Optional[int] = None,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE login_jobs
            SET status = ?,
                finished_at = COALESCE(?, finished_at),
                checked_at = COALESCE(?, checked_at),
                valid = COALESCE(?, valid),
                account_id = COALESCE(?, account_id)
            WHERE id = ?
            """,
            (status, finished_at, checked_at, valid, account_id, job_id),
        )
        connection.commit()


def check_login_valid(job: LoginJob) -> bool:
    profile_path = Path(job.profile_path)
    profile_path.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        device = playwright.devices.get(job.ios_profile) or playwright.devices["iPhone 13"]
        context = playwright.webkit.launch_persistent_context(
            user_data_dir=str(profile_path),
            proxy={"server": job.proxy} if job.proxy else None,
            locale="de-DE",
            headless=True,
            **device,
        )
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        current_url = page.url
        is_logged_in = current_url != LOGIN_URL and "anmeldung" not in current_url
        if not is_logged_in:
            storage = context.storage_state()
            cookie_names = {
                cookie.get("name", "").lower()
                for cookie in storage.get("cookies", [])
            }
            is_logged_in = any(
                token in name
                for name in cookie_names
                for token in ("session", "sid", "auth", "token")
            )
        context.close()
    return bool(is_logged_in)


def login_with_playwright(job_id: int) -> None:
    """
    Startet eine persistente WebKit Session (iOS Device Settings) mit Proxy.
    Human-in-the-loop: Fenster bleibt offen, damit der User manuell einloggen kann.
    """
    job = fetch_login_job(job_id)
    if job is None:
        return

    profile_path = Path(job.profile_path or build_profile_path(job.id))
    profile_path.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as playwright:
            device = playwright.devices.get(job.ios_profile) or playwright.devices["iPhone 13"]

            context = playwright.webkit.launch_persistent_context(
                user_data_dir=str(profile_path),
                proxy={"server": job.proxy} if job.proxy else None,
                locale="de-DE",
                headless=False,
                **device,
            )
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")

            # Markiere: wartet auf den User (Login im offenen Browser-Fenster)
            update_login_job(job.id, "waiting_for_user")

            # Blockiert bis Fenster/Context geschlossen wird
            context.wait_for_event("close")
    finally:
        update_login_job(
            job.id,
            "checking",
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        is_valid = check_login_valid(job)
        checked_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        if is_valid:
            created_at = datetime.datetime.now().isoformat(timespec="minutes")
            account_name = job.email.split("@")[0] if "@" in job.email else job.email
            with get_connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO accounts
                        (name, email, age_days, proxy, ios_profile, profile_path, notes, created_at, password)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_name,
                        job.email,
                        0,
                        job.proxy,
                        job.ios_profile,
                        job.profile_path,
                        "",
                        created_at,
                        job.password,
                    ),
                )
                account_id = int(cursor.lastrowid)
                connection.commit()
            update_login_job(
                job.id,
                "valid",
                finished_at=checked_at,
                checked_at=checked_at,
                valid=1,
                account_id=account_id,
            )
        else:
            update_login_job(
                job.id,
                "invalid",
                finished_at=checked_at,
                checked_at=checked_at,
                valid=0,
            )


def start_login_thread(job_id: int) -> None:
    Thread(target=login_with_playwright, args=(job_id,), daemon=True).start()


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
    email = (payload.get("email") or "").strip()
    password = (payload.get("password") or "").strip()
    proxy = (payload.get("proxy") or "").strip()

    if not email or not password:
        return jsonify({"error": "E-Mail und Passwort sind erforderlich."}), 400

    job_id = create_login_job(email=email, password=password, proxy=proxy)
    start_login_thread(job_id)
    return jsonify({"status": "started", "job_id": job_id})


@app.get("/api/login-jobs/<int:job_id>")
def get_login_job(job_id: int):
    job = fetch_login_job(job_id)
    if job is None:
        return jsonify({"error": "Kein Login-Job gefunden."}), 404

    return jsonify(
        {
            "status": job.status,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "checked_at": job.checked_at,
            "valid": job.valid,
            "account_id": job.account_id,
        }
    )


init_db()

if __name__ == "__main__":
    app.run(debug=True)