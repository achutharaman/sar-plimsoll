import os
import sqlite3
from decimal import Decimal

TAX_RATE = Decimal("0.0825")


def connect() -> sqlite3.Connection:
    return sqlite3.connect(os.environ["ORDERS_DB_PATH"])


def get_orders(conn: sqlite3.Connection, customer: str) -> list[tuple]:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE customer = ?", (customer,))
    return cursor.fetchall()


def order_totals(conn: sqlite3.Connection, order_ids: list[int]) -> dict[int, Decimal]:
    if not order_ids:
        return {}
    placeholders = ",".join("?" for _ in order_ids)
    rows = conn.execute(
        f"SELECT id, amount FROM orders WHERE id IN ({placeholders})", order_ids
    ).fetchall()
    return {order_id: Decimal(str(amount)) * (1 + TAX_RATE) for order_id, amount in rows}


def average_total(conn: sqlite3.Connection, order_ids: list[int]) -> Decimal | None:
    totals = order_totals(conn, order_ids)
    if not totals:
        return None
    return sum(totals.values()) / len(totals)


def apply_discount(order: dict) -> Decimal:
    try:
        return Decimal(str(order["discount"]))
    except (KeyError, ArithmeticError):
        return Decimal("0")
