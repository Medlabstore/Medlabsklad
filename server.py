#!/usr/bin/env python3
import hashlib
import hmac
import html
import json
import mimetypes
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "warehouse.db"))
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
SESSION_COOKIE = "warehouse_session"
SESSION_TTL_DAYS = 14


def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


def make_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def make_join_code():
    return secrets.token_hex(3).upper()


def generate_unique_join_code(conn):
    while True:
        code = make_join_code()
        exists = conn.execute("SELECT id FROM organizations WHERE join_code = ?", (code,)).fetchone()
        if not exists:
            return code


def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return f"{salt}${digest.hex()}"


def verify_password(password, stored):
    try:
        salt, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    recalculated = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000).hex()
    return hmac.compare_digest(recalculated, digest_hex)


@contextmanager
def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS organizations (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              join_code TEXT UNIQUE,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              email TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memberships (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              org_id TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('owner','manager','viewer')),
              created_at TEXT NOT NULL,
              UNIQUE(user_id, org_id),
              FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
              FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              org_id TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
              FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS org_products (
              id TEXT PRIMARY KEY,
              org_id TEXT NOT NULL,
              name TEXT NOT NULL,
              sku TEXT NOT NULL,
              unit TEXT NOT NULL DEFAULT 'шт',
              price REAL NOT NULL DEFAULT 0,
              stock INTEGER NOT NULL DEFAULT 0,
              purchase_price REAL NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS org_receipts (
              id TEXT PRIMARY KEY,
              org_id TEXT NOT NULL,
              product_id TEXT NOT NULL,
              quantity INTEGER NOT NULL,
              cost REAL NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE,
              FOREIGN KEY (product_id) REFERENCES org_products(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS org_shipments (
              id TEXT PRIMARY KEY,
              org_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS org_shipment_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              shipment_id TEXT NOT NULL,
              org_id TEXT NOT NULL,
              product_id TEXT NOT NULL,
              quantity INTEGER NOT NULL,
              price REAL NOT NULL,
              amount REAL NOT NULL,
              FOREIGN KEY (shipment_id) REFERENCES org_shipments(id) ON DELETE CASCADE,
              FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE,
              FOREIGN KEY (product_id) REFERENCES org_products(id) ON DELETE CASCADE
            );
            """
        )

        cols = [r["name"] for r in conn.execute("PRAGMA table_info(organizations)").fetchall()]
        if "join_code" not in cols:
            conn.execute("ALTER TABLE organizations ADD COLUMN join_code TEXT")
        orgs_without_code = conn.execute("SELECT id FROM organizations WHERE join_code IS NULL OR join_code = ''").fetchall()
        for org in orgs_without_code:
            conn.execute("UPDATE organizations SET join_code = ? WHERE id = ?", (generate_unique_join_code(conn), org["id"]))

        admin = conn.execute("SELECT id FROM users WHERE name = 'admin'").fetchone()
        if not admin:
            org = conn.execute("SELECT id, name FROM organizations ORDER BY created_at ASC LIMIT 1").fetchone()
            if org:
                org_id = org["id"]
            else:
                org_id = make_id("org")
                conn.execute(
                    "INSERT INTO organizations (id, name, join_code, created_at) VALUES (?, ?, ?, ?)",
                    (org_id, "Основная организация", generate_unique_join_code(conn), now_iso()),
                )
                seed_org(conn, org_id)

            user_id = make_id("u")
            conn.execute(
                "INSERT INTO users (id, name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, "admin", "admin@local", hash_password("admin123"), now_iso()),
            )
            conn.execute(
                "INSERT INTO memberships (id, user_id, org_id, role, created_at) VALUES (?, ?, ?, 'owner', ?)",
                (make_id("m"), user_id, org_id, now_iso()),
            )


def seed_org(conn, org_id):
    p1 = make_id("p")
    p2 = make_id("p")
    p3 = make_id("p")

    conn.executemany(
        "INSERT INTO org_products (id, org_id, name, sku, unit, price, stock, purchase_price, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (p1, org_id, "Роликовый массажер", "00044", "шт", 65000, 6, 55000, now_iso()),
            (p2, org_id, "Сыворотка SkinLab", "00047", "шт", 2000, 35, 1200, now_iso()),
            (p3, org_id, "Игла 27g", "00030", "шт", 600, 190, 300, now_iso()),
        ],
    )

    conn.executemany(
        "INSERT INTO org_receipts (id, org_id, product_id, quantity, cost, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (make_id("r"), org_id, p2, 20, 1500, now_iso()),
            (make_id("r"), org_id, p1, 10, 55000, now_iso()),
        ],
    )


def row_to_product(r):
    return {
        "id": r["id"],
        "name": r["name"],
        "sku": r["sku"],
        "unit": r["unit"],
        "price": float(r["price"]),
        "stock": int(r["stock"]),
        "purchasePrice": float(r["purchase_price"]),
    }


def fetch_state(conn, org_id):
    products = [
        row_to_product(r)
        for r in conn.execute(
            "SELECT * FROM org_products WHERE org_id = ? ORDER BY datetime(created_at) DESC", (org_id,)
        ).fetchall()
    ]

    receipts = [
        {
            "id": r["id"],
            "productId": r["product_id"],
            "quantity": int(r["quantity"]),
            "cost": float(r["cost"]),
            "createdAt": r["created_at"],
        }
        for r in conn.execute(
            "SELECT * FROM org_receipts WHERE org_id = ? ORDER BY datetime(created_at) DESC", (org_id,)
        ).fetchall()
    ]

    shipments = []
    shipment_rows = conn.execute(
        "SELECT * FROM org_shipments WHERE org_id = ? ORDER BY datetime(created_at) DESC", (org_id,)
    ).fetchall()
    for sh in shipment_rows:
        items = [
            {
                "productId": i["product_id"],
                "quantity": int(i["quantity"]),
                "price": float(i["price"]),
                "amount": float(i["amount"]),
            }
            for i in conn.execute(
                "SELECT * FROM org_shipment_items WHERE shipment_id = ? ORDER BY id ASC", (sh["id"],)
            ).fetchall()
        ]
        shipments.append({"id": sh["id"], "createdAt": sh["created_at"], "items": items})

    return {"products": products, "receipts": receipts, "shipments": shipments}


def render_shipment_print_html(conn, org_id, org_name, shipment_id):
    shipment = conn.execute(
        "SELECT id, created_at FROM org_shipments WHERE id = ? AND org_id = ?",
        (shipment_id, org_id),
    ).fetchone()
    if not shipment:
        return None

    items = conn.execute(
        """
        SELECT i.quantity, i.price, i.amount, p.name AS product_name
        FROM org_shipment_items i
        LEFT JOIN org_products p ON p.id = i.product_id
        WHERE i.shipment_id = ? AND i.org_id = ?
        ORDER BY i.id ASC
        """,
        (shipment_id, org_id),
    ).fetchall()

    rows = []
    total = 0.0
    for index, item in enumerate(items, start=1):
        total += float(item["amount"])
        rows.append(
            f"""
            <tr>
              <td>{index}</td>
              <td>{html.escape(item["product_name"] or "Удаленный товар")}</td>
              <td>{int(item["quantity"])}</td>
              <td>{float(item["price"]):,.2f}</td>
              <td>{float(item["amount"]):,.2f}</td>
            </tr>
            """
        )

    rows_html = "".join(rows) if rows else '<tr><td colspan="5">Нет позиций</td></tr>'
    created = html.escape(shipment["created_at"])
    org_title = html.escape(org_name)
    doc_no = html.escape(shipment["id"])

    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <title>Товарный чек {doc_no}</title>
    <style>
      @page {{ size: A4; margin: 14mm; }}
      body {{ font-family: Arial, sans-serif; color: #1c2430; margin: 0; }}
      .doc {{ width: 100%; }}
      .head {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 14px; }}
      .title {{ font-size: 28px; font-weight: 700; margin: 0 0 4px; }}
      .meta {{ font-size: 13px; margin: 2px 0; color: #354656; }}
      .line {{ border-top: 2px solid #0b75b4; margin: 10px 0 14px; }}
      table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
      th, td {{ border: 1px solid #cfdce8; padding: 8px; text-align: left; }}
      th {{ background: #f3f8fc; }}
      .num {{ width: 42px; text-align: center; }}
      .qty {{ width: 80px; }}
      .money {{ width: 140px; white-space: nowrap; }}
      .total {{ margin-top: 14px; text-align: right; font-size: 22px; font-weight: 700; }}
      .signs {{ margin-top: 36px; display: grid; grid-template-columns: 1fr 1fr; gap: 22px; font-size: 12px; color: #3b4e61; }}
      .sign-line {{ margin-top: 34px; border-top: 1px solid #6f8397; padding-top: 5px; }}
      @media print {{ .print-note {{ display: none; }} }}
      .print-note {{ margin-top: 12px; color: #5d6c79; font-size: 12px; }}
    </style>
  </head>
  <body>
    <div class="doc">
      <div class="head">
        <div>
          <h1 class="title">Товарный чек</h1>
          <p class="meta">Организация: {org_title}</p>
          <p class="meta">Документ: Отгрузка № {doc_no}</p>
          <p class="meta">Дата: {created}</p>
        </div>
      </div>
      <div class="line"></div>
      <table>
        <thead>
          <tr>
            <th class="num">№</th>
            <th>Наименование</th>
            <th class="qty">Кол-во</th>
            <th class="money">Цена</th>
            <th class="money">Сумма</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
      <div class="total">Итого: {total:,.2f}</div>
      <div class="signs">
        <div><div class="sign-line">Отпустил(а)</div></div>
        <div><div class="sign-line">Получил(а)</div></div>
      </div>
      <div class="print-note">Окно печати откроется автоматически. В нем можно выбрать «Сохранить как PDF».</div>
    </div>
    <script>
      window.onload = function () {{
        setTimeout(function () {{ window.print(); }}, 120);
      }};
    </script>
  </body>
</html>"""


def parse_cookie(header_value):
    cookies = {}
    if not header_value:
        return cookies
    parts = header_value.split(";")
    for part in parts:
        chunk = part.strip()
        if "=" not in chunk:
            continue
        key, val = chunk.split("=", 1)
        cookies[key] = val
    return cookies


def iso_to_dt(value):
    return datetime.fromisoformat(value)


class AppHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status=HTTPStatus.OK, set_cookie=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path):
        if not os.path.isfile(path):
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        ctype, _ = mimetypes.guess_type(path)
        ctype = ctype or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, html_text, status=HTTPStatus.OK):
        data = html_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _make_session_cookie(self, token):
        max_age = SESSION_TTL_DAYS * 24 * 3600
        return f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"

    def _clear_session_cookie(self):
        return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"

    def _create_session(self, conn, user_id, org_id):
        token = secrets.token_urlsafe(32)
        expires = now_utc() + timedelta(days=SESSION_TTL_DAYS)
        conn.execute(
            "INSERT INTO sessions (token, user_id, org_id, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (token, user_id, org_id, expires.isoformat(), now_iso()),
        )
        return token

    def _get_current_auth(self, conn):
        cookies = parse_cookie(self.headers.get("Cookie"))
        token = cookies.get(SESSION_COOKIE)
        if not token:
            return None

        row = conn.execute(
            """
            SELECT s.token, s.user_id, s.org_id, s.expires_at,
                   u.name AS user_name, u.email AS user_email,
                   o.name AS org_name, o.join_code AS org_join_code,
                   m.role AS role
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            JOIN organizations o ON o.id = s.org_id
            JOIN memberships m ON m.user_id = s.user_id AND m.org_id = s.org_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()

        if not row:
            return None

        if iso_to_dt(row["expires_at"]) <= now_utc():
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return None

        return {
            "token": row["token"],
            "userId": row["user_id"],
            "orgId": row["org_id"],
            "name": row["user_name"],
            "email": row["user_email"],
            "orgName": row["org_name"],
            "orgJoinCode": row["org_join_code"] or "",
            "role": row["role"],
        }

    def _require_auth(self, conn):
        auth = self._get_current_auth(conn)
        if not auth:
            self._send_json({"error": "Требуется авторизация"}, status=HTTPStatus.UNAUTHORIZED)
            return None
        return auth

    def _require_role(self, auth, allowed):
        return auth["role"] in allowed

    def _route_auth(self):
        path = urlparse(self.path).path

        if self.command == "POST" and path == "/api/auth/register":
            self._send_json({"error": "Регистрация отключена. Используйте аккаунт администратора."}, status=HTTPStatus.BAD_REQUEST)
            return True

        if self.command == "POST" and path == "/api/auth/login":
            payload = self._read_json()
            username = str(payload.get("username", "")).strip()
            password = str(payload.get("password", ""))

            with db_conn() as conn:
                user = conn.execute("SELECT * FROM users WHERE name = ?", (username,)).fetchone()
                if not user or not verify_password(password, user["password_hash"]):
                    self._send_json({"error": "Неверное имя пользователя или пароль"}, status=HTTPStatus.UNAUTHORIZED)
                    return True

                membership = conn.execute(
                    """
                    SELECT m.org_id, m.role, o.name AS org_name, o.join_code AS org_join_code
                    FROM memberships m
                    JOIN organizations o ON o.id = m.org_id
                    WHERE m.user_id = ?
                    ORDER BY m.created_at ASC
                    LIMIT 1
                    """,
                    (user["id"],),
                ).fetchone()

                if not membership:
                    self._send_json({"error": "У пользователя нет организации"}, status=HTTPStatus.FORBIDDEN)
                    return True

                token = self._create_session(conn, user["id"], membership["org_id"])
                self._send_json(
                    {
                        "ok": True,
                        "me": {
                            "name": user["name"],
                            "email": user["email"],
                            "orgName": membership["org_name"],
                            "orgJoinCode": membership["org_join_code"] or "",
                            "role": membership["role"],
                        },
                    },
                    set_cookie=self._make_session_cookie(token),
                )
            return True

        if self.command == "POST" and path == "/api/auth/logout":
            with db_conn() as conn:
                auth = self._get_current_auth(conn)
                if auth:
                    conn.execute("DELETE FROM sessions WHERE token = ?", (auth["token"],))
            self._send_json({"ok": True}, set_cookie=self._clear_session_cookie())
            return True

        if self.command == "GET" and path == "/api/auth/me":
            with db_conn() as conn:
                auth = self._get_current_auth(conn)
                if not auth:
                    self._send_json({"error": "Не авторизован"}, status=HTTPStatus.UNAUTHORIZED)
                    return True
                self._send_json(
                    {
                        "name": auth["name"],
                        "email": auth["email"],
                        "orgName": auth["orgName"],
                        "orgJoinCode": auth["orgJoinCode"],
                        "role": auth["role"],
                    }
                )
            return True

        return False

    def _route_api(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path.startswith("/api/auth/"):
                return self._route_auth()

            with db_conn() as conn:
                auth = self._require_auth(conn)
                if not auth:
                    return True

                org_id = auth["orgId"]

                if self.command == "GET" and path == "/api/state":
                    state = fetch_state(conn, org_id)
                    state["me"] = {
                        "name": auth["name"],
                        "email": auth["email"],
                        "orgName": auth["orgName"],
                        "orgJoinCode": auth["orgJoinCode"],
                        "role": auth["role"],
                    }
                    self._send_json(state)
                    return True

                if self.command == "POST" and path == "/api/memberships/role":
                    if not self._require_role(auth, {"owner"}):
                        self._send_json({"error": "Только владелец может менять роли"}, status=HTTPStatus.FORBIDDEN)
                        return True
                    payload = self._read_json()
                    email = str(payload.get("email", "")).strip().lower()
                    role = str(payload.get("role", "")).strip()
                    if role not in {"owner", "manager", "viewer"} or not email:
                        self._send_json({"error": "Укажите корректные email и role"}, status=HTTPStatus.BAD_REQUEST)
                        return True

                    user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
                    if not user:
                        self._send_json({"error": "Пользователь не найден"}, status=HTTPStatus.NOT_FOUND)
                        return True

                    membership = conn.execute(
                        "SELECT id FROM memberships WHERE user_id = ? AND org_id = ?",
                        (user["id"], org_id),
                    ).fetchone()
                    if not membership:
                        self._send_json({"error": "Пользователь не состоит в вашей организации"}, status=HTTPStatus.NOT_FOUND)
                        return True

                    conn.execute(
                        "UPDATE memberships SET role = ? WHERE user_id = ? AND org_id = ?",
                        (role, user["id"], org_id),
                    )
                    self._send_json({"ok": True})
                    return True

                if self.command == "POST" and path == "/api/products":
                    if not self._require_role(auth, {"owner", "manager"}):
                        self._send_json({"error": "Недостаточно прав"}, status=HTTPStatus.FORBIDDEN)
                        return True

                    payload = self._read_json()
                    product_id = make_id("p")
                    name = str(payload.get("name", "")).strip()
                    if not name:
                        self._send_json({"error": "Название обязательно"}, status=HTTPStatus.BAD_REQUEST)
                        return True
                    sku = str(payload.get("sku", "")).strip() or f"AUTO-{uuid.uuid4().hex[:4].upper()}"
                    unit = str(payload.get("unit", "шт")).strip() or "шт"
                    price = max(0, float(payload.get("price", 0) or 0))
                    stock = max(0, int(float(payload.get("stock", 0) or 0)))
                    purchase = max(0, float(payload.get("purchasePrice", 0) or 0))

                    conn.execute(
                        "INSERT INTO org_products (id, org_id, name, sku, unit, price, stock, purchase_price, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (product_id, org_id, name, sku, unit, price, stock, purchase, now_iso()),
                    )
                    if stock > 0:
                        conn.execute(
                            "INSERT INTO org_receipts (id, org_id, product_id, quantity, cost, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                            (make_id("r"), org_id, product_id, stock, purchase, now_iso()),
                        )
                    self._send_json(fetch_state(conn, org_id), status=HTTPStatus.CREATED)
                    return True

                if self.command == "PATCH" and path.startswith("/api/products/") and path.endswith("/price"):
                    if not self._require_role(auth, {"owner", "manager"}):
                        self._send_json({"error": "Недостаточно прав"}, status=HTTPStatus.FORBIDDEN)
                        return True
                    product_id = path.split("/")[3]
                    payload = self._read_json()
                    price = max(0, float(payload.get("price", 0) or 0))
                    cur = conn.execute(
                        "UPDATE org_products SET price = ? WHERE id = ? AND org_id = ?",
                        (price, product_id, org_id),
                    )
                    if cur.rowcount == 0:
                        self._send_json({"error": "Товар не найден"}, status=HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json(fetch_state(conn, org_id))
                    return True

                if self.command == "PATCH" and path.startswith("/api/products/") and not path.endswith("/price"):
                    if not self._require_role(auth, {"owner", "manager"}):
                        self._send_json({"error": "Недостаточно прав"}, status=HTTPStatus.FORBIDDEN)
                        return True
                    product_id = path.split("/")[3]
                    payload = self._read_json()
                    name = str(payload.get("name", "")).strip()
                    price = max(0, float(payload.get("price", 0) or 0))
                    if not name:
                        self._send_json({"error": "Название товара обязательно"}, status=HTTPStatus.BAD_REQUEST)
                        return True

                    cur = conn.execute(
                        "UPDATE org_products SET name = ?, price = ? WHERE id = ? AND org_id = ?",
                        (name, price, product_id, org_id),
                    )
                    if cur.rowcount == 0:
                        self._send_json({"error": "Товар не найден"}, status=HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json(fetch_state(conn, org_id))
                    return True

                if self.command == "DELETE" and path.startswith("/api/products/"):
                    if not self._require_role(auth, {"owner", "manager"}):
                        self._send_json({"error": "Недостаточно прав"}, status=HTTPStatus.FORBIDDEN)
                        return True
                    product_id = path.split("/")[3]
                    cur = conn.execute(
                        "DELETE FROM org_products WHERE id = ? AND org_id = ?",
                        (product_id, org_id),
                    )
                    if cur.rowcount == 0:
                        self._send_json({"error": "Товар не найден"}, status=HTTPStatus.NOT_FOUND)
                        return True
                    conn.execute(
                        "DELETE FROM org_shipments WHERE id IN (SELECT s.id FROM org_shipments s LEFT JOIN org_shipment_items i ON i.shipment_id=s.id WHERE s.org_id=? GROUP BY s.id HAVING COUNT(i.id)=0)",
                        (org_id,),
                    )
                    self._send_json(fetch_state(conn, org_id))
                    return True

                if self.command == "POST" and path == "/api/receipts":
                    if not self._require_role(auth, {"owner", "manager"}):
                        self._send_json({"error": "Недостаточно прав"}, status=HTTPStatus.FORBIDDEN)
                        return True
                    payload = self._read_json()
                    product_id = str(payload.get("productId", "")).strip()
                    quantity = int(float(payload.get("quantity", 0) or 0))
                    cost = max(0, float(payload.get("cost", 0) or 0))
                    if not product_id or quantity <= 0:
                        self._send_json({"error": "Неверные данные приемки"}, status=HTTPStatus.BAD_REQUEST)
                        return True

                    product = conn.execute(
                        "SELECT id FROM org_products WHERE id = ? AND org_id = ?", (product_id, org_id)
                    ).fetchone()
                    if not product:
                        self._send_json({"error": "Товар не найден"}, status=HTTPStatus.NOT_FOUND)
                        return True

                    conn.execute(
                        "UPDATE org_products SET stock = stock + ?, purchase_price = CASE WHEN ? > 0 THEN ? ELSE purchase_price END WHERE id = ? AND org_id = ?",
                        (quantity, cost, cost, product_id, org_id),
                    )
                    conn.execute(
                        "INSERT INTO org_receipts (id, org_id, product_id, quantity, cost, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (make_id("r"), org_id, product_id, quantity, cost, now_iso()),
                    )
                    self._send_json(fetch_state(conn, org_id), status=HTTPStatus.CREATED)
                    return True

                if self.command == "DELETE" and path.startswith("/api/receipts/"):
                    if not self._require_role(auth, {"owner", "manager"}):
                        self._send_json({"error": "Недостаточно прав"}, status=HTTPStatus.FORBIDDEN)
                        return True
                    receipt_id = path.split("/")[3]
                    receipt = conn.execute(
                        "SELECT * FROM org_receipts WHERE id = ? AND org_id = ?", (receipt_id, org_id)
                    ).fetchone()
                    if not receipt:
                        self._send_json({"error": "Приемка не найдена"}, status=HTTPStatus.NOT_FOUND)
                        return True

                    conn.execute(
                        "UPDATE org_products SET stock = CASE WHEN stock - ? < 0 THEN 0 ELSE stock - ? END WHERE id = ? AND org_id = ?",
                        (int(receipt["quantity"]), int(receipt["quantity"]), receipt["product_id"], org_id),
                    )
                    conn.execute("DELETE FROM org_receipts WHERE id = ? AND org_id = ?", (receipt_id, org_id))
                    self._send_json(fetch_state(conn, org_id))
                    return True

                if self.command == "POST" and path == "/api/shipments":
                    if not self._require_role(auth, {"owner", "manager"}):
                        self._send_json({"error": "Недостаточно прав"}, status=HTTPStatus.FORBIDDEN)
                        return True
                    payload = self._read_json()
                    items = payload.get("items", [])
                    if not isinstance(items, list) or len(items) == 0:
                        self._send_json({"error": "Добавьте хотя бы одну позицию"}, status=HTTPStatus.BAD_REQUEST)
                        return True

                    prepared = []
                    for item in items:
                        product_id = str(item.get("productId", "")).strip()
                        qty = int(float(item.get("quantity", 0) or 0))
                        custom_price = item.get("price", None)
                        if custom_price is not None:
                            custom_price = max(0.0, float(custom_price))
                        if not product_id or qty <= 0:
                            self._send_json({"error": "Некорректная позиция отгрузки"}, status=HTTPStatus.BAD_REQUEST)
                            return True
                        prepared.append({"productId": product_id, "qty": qty, "price": custom_price})

                    for line in prepared:
                        product_id = line["productId"]
                        qty = line["qty"]
                        product = conn.execute(
                            "SELECT id, stock, price FROM org_products WHERE id = ? AND org_id = ?",
                            (product_id, org_id),
                        ).fetchone()
                        if not product:
                            self._send_json({"error": "Товар не найден"}, status=HTTPStatus.NOT_FOUND)
                            return True
                        if int(product["stock"]) < qty:
                            self._send_json({"error": "Недостаточно товара на складе"}, status=HTTPStatus.BAD_REQUEST)
                            return True

                    shipment_id = make_id("s")
                    conn.execute(
                        "INSERT INTO org_shipments (id, org_id, created_at) VALUES (?, ?, ?)",
                        (shipment_id, org_id, now_iso()),
                    )

                    for line in prepared:
                        product_id = line["productId"]
                        qty = line["qty"]
                        product = conn.execute(
                            "SELECT price FROM org_products WHERE id = ? AND org_id = ?",
                            (product_id, org_id),
                        ).fetchone()
                        price = float(line["price"]) if line["price"] is not None else float(product["price"])
                        amount = price * qty
                        conn.execute(
                            "UPDATE org_products SET stock = stock - ? WHERE id = ? AND org_id = ?",
                            (qty, product_id, org_id),
                        )
                        conn.execute(
                            "INSERT INTO org_shipment_items (shipment_id, org_id, product_id, quantity, price, amount) VALUES (?, ?, ?, ?, ?, ?)",
                            (shipment_id, org_id, product_id, qty, price, amount),
                        )

                    self._send_json(fetch_state(conn, org_id), status=HTTPStatus.CREATED)
                    return True

                if self.command == "DELETE" and path.startswith("/api/shipments/"):
                    if not self._require_role(auth, {"owner", "manager"}):
                        self._send_json({"error": "Недостаточно прав"}, status=HTTPStatus.FORBIDDEN)
                        return True
                    shipment_id = path.split("/")[3]
                    shipment = conn.execute(
                        "SELECT id FROM org_shipments WHERE id = ? AND org_id = ?", (shipment_id, org_id)
                    ).fetchone()
                    if not shipment:
                        self._send_json({"error": "Отгрузка не найдена"}, status=HTTPStatus.NOT_FOUND)
                        return True

                    items = conn.execute(
                        "SELECT product_id, quantity FROM org_shipment_items WHERE shipment_id = ? AND org_id = ?",
                        (shipment_id, org_id),
                    ).fetchall()
                    for item in items:
                        conn.execute(
                            "UPDATE org_products SET stock = stock + ? WHERE id = ? AND org_id = ?",
                            (int(item["quantity"]), item["product_id"], org_id),
                        )

                    conn.execute("DELETE FROM org_shipments WHERE id = ? AND org_id = ?", (shipment_id, org_id))
                    self._send_json(fetch_state(conn, org_id))
                    return True

        except ValueError:
            self._send_json({"error": "Некорректный формат данных"}, status=HTTPStatus.BAD_REQUEST)
            return True
        except Exception as exc:
            self._send_json({"error": f"Внутренняя ошибка: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return True

        return False

    def do_GET(self):
        if self.path.startswith("/api/"):
            if not self._route_api():
                self._send_json({"error": "Маршрут не найден"}, status=HTTPStatus.NOT_FOUND)
            return

        path = urlparse(self.path).path
        if path.startswith("/print/shipment/"):
            shipment_id = path.split("/")[-1]
            with db_conn() as conn:
                auth = self._require_auth(conn)
                if not auth:
                    return
                html_doc = render_shipment_print_html(conn, auth["orgId"], auth["orgName"], shipment_id)
                if not html_doc:
                    self._send_json({"error": "Отгрузка не найдена"}, status=HTTPStatus.NOT_FOUND)
                    return
                self._send_html(html_doc)
            return

        if path == "/":
            self._send_file(os.path.join(BASE_DIR, "index.html"))
            return

        rel = path.lstrip("/")
        full_path = os.path.join(BASE_DIR, rel)
        if os.path.abspath(full_path).startswith(BASE_DIR):
            self._send_file(full_path)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self.path.startswith("/api/") and self._route_api():
            return
        self._send_json({"error": "Маршрут не найден"}, status=HTTPStatus.NOT_FOUND)

    def do_PATCH(self):
        if self.path.startswith("/api/") and self._route_api():
            return
        self._send_json({"error": "Маршрут не найден"}, status=HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        if self.path.startswith("/api/") and self._route_api():
            return
        self._send_json({"error": "Маршрут не найден"}, status=HTTPStatus.NOT_FOUND)


if __name__ == "__main__":
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Warehouse server started on http://{HOST}:{PORT}")
    server.serve_forever()
