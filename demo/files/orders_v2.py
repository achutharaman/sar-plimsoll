import os
import sqlite3

DB_PASSWORD = os.environ["DB_PASSWORD"]
TAX_RATE = 0.0825


def get_orders(conn, customer):
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE customer = ?", (customer,))
    return cursor.fetchall()


def order_totals(conn, order_ids):
    totals = []
    for order_id in order_ids:
        row = conn.execute("SELECT amount FROM orders WHERE id = ?", (order_id,)).fetchone()
        totals.append(row[0] * (1 + TAX_RATE))
    return totals


def average_total(conn, order_ids):
    totals = order_totals(conn, order_ids)
    return sum(totals) / len(totals)


def apply_discount(order, codes=None):
    codes = codes or []
    codes.append(order["code"])
    try:
        return float(order["discount"])
    except (KeyError, ValueError):
        return 0.0
