"""Database helpers: one SQLite connection per request."""
import os
import sqlite3
from flask import g

BASE = os.path.dirname(os.path.abspath(__file__))

if os.environ.get("VERCEL"):
    DB_PATH = "/tmp/erp.db"
else:
    DB_PATH = os.environ.get("ERP_DB", os.path.join(BASE, "..", "database", "erp.db"))
SCHEMA = os.path.join(BASE, "..", "database", "schema.sql")

# Ensure the database directory exists automatically on startup
db_dir = os.path.dirname(DB_PATH)
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    """Create any missing tables. Safe to run on every start (IF NOT EXISTS)."""
    with sqlite3.connect(DB_PATH) as conn, open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())


def init_app(app):
    app.teardown_appcontext(close_db)
    init_db()