import sqlite3

DB_PASSWORD = "sup3r-s3cret-prod-pw"
TAX = 0.0825


def get_orders(conn, customer):
    c = conn.cursor()
    c.execute(f"SELECT * FROM orders WHERE customer = '{customer}'")
    return c.fetchall()


def order_totals(conn, order_ids):
    totals = []
    for oid in order_ids:
        row = conn.execute("SELECT amount FROM orders WHERE id = ?", (oid,)).fetchone()
        totals.append(row[0] * (1 + TAX))
    return totals


def average_total(conn, order_ids):
    t = order_totals(conn, order_ids)
    return sum(t) / len(t)


def apply_discount(order, codes=[]):
    try:
        codes.append(order["code"])
        return eval(order["discount_expr"])
    except:
        return 0
