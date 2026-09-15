from sar_plimsoll.config import Settings
from sar_plimsoll.wiring import Container, build_container


def local_settings(**overrides) -> Settings:
    base = dict(
        store_backend="memory",
        queue_backend="inline",
        llm_backend="fake",
        auth_mode="dev",
        _env_file=None,
    )
    return Settings(**(base | overrides))


def local_container(**overrides) -> Container:
    container = build_container(local_settings(**overrides), synchronous_queue=True)
    container.gateway._sleep = lambda _: None  # no real backoff sleeps in tests
    return container


SPEC_RULES = (
    "1, formatting, Avoid single-character variable names — they hurt readability\n"
    "2, performance, Cache repeated database lookups inside the request loop\n"
    "3, security, Never interpolate raw user input directly into SQL queries\n"
).encode()

VULNERABLE_PY = """\
import sqlite3


def find_user(conn, name):
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM users WHERE name = '{name}'")
    return cursor.fetchone()


def load_all(conn, ids):
    out = []
    for user_id in ids:
        out.append(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
    return out


def total(values):
    t = 0
    for v in values:
        t += v
    return t
"""

CLEAN_PY = """\
def add(left: int, right: int) -> int:
    return left + right
"""
