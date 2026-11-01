"""온톨로지 드라이런용 합성 커머스 DB를 만든다. 실제 데이터가 아니다."""

from __future__ import annotations

import sqlite3
from pathlib import Path

CUSTOMER_COUNT = 8
ORDER_COUNT = 20
PROMOTION_COUNT = 3


def build_commerce_db(path: Path) -> None:
    """customers/orders/promotions 세 테이블을 고정 행 수로 채운다."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT);
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER REFERENCES customers(id),
                total_cents INTEGER,
                status TEXT
            );
            CREATE TABLE promotions (id INTEGER PRIMARY KEY, code TEXT, active INTEGER);
            """
        )
        connection.executemany(
            "INSERT INTO customers (id, name, email) VALUES (?, ?, ?)",
            [(i, f"customer-{i}", f"customer{i}@example.com")
             for i in range(1, CUSTOMER_COUNT + 1)],
        )
        statuses = ("paid", "shipped", "cancelled", "pending")
        connection.executemany(
            "INSERT INTO orders (id, customer_id, total_cents, status) VALUES (?, ?, ?, ?)",
            [(i, (i - 1) % CUSTOMER_COUNT + 1, 1000 + i * 250, statuses[i % len(statuses)])
             for i in range(1, ORDER_COUNT + 1)],
        )
        connection.executemany(
            "INSERT INTO promotions (id, code, active) VALUES (?, ?, ?)",
            [(1, "WELCOME10", 1), (2, "SUMMER20", 0), (3, "VIP5", 1)],
        )
        connection.commit()
    finally:
        connection.close()
