import sqlite3
import os
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get('DB_PATH', 'mymoney.db')
BKK = timezone(timedelta(hours=7))  # Bangkok UTC+7


def _now():
    return datetime.now(BKK)


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with get_conn() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                amount REAL,
                sender TEXT,
                receiver TEXT,
                bank TEXT,
                category TEXT,
                image_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        for col in ('category', 'image_path'):
            try:
                conn.execute(f'ALTER TABLE transactions ADD COLUMN {col} TEXT')
            except sqlite3.OperationalError:
                pass
        conn.commit()


def add_transaction(data: dict, image_path: str = None) -> int:
    today = _now().strftime('%Y-%m-%d')
    with get_conn() as conn:
        cur = conn.execute(
            'INSERT INTO transactions (date, amount, sender, receiver, bank, image_path) VALUES (?, ?, ?, ?, ?, ?)',
            (
                data.get('date') or today,
                data.get('amount') or 0.0,
                data.get('sender'),
                data.get('receiver'),
                data.get('bank'),
                image_path,
            )
        )
        conn.commit()
        return cur.lastrowid


def update_category(tx_id: int, category: str):
    with get_conn() as conn:
        conn.execute('UPDATE transactions SET category = ? WHERE id = ?', (category, tx_id))
        conn.commit()


def get_transaction(tx_id: int):
    with get_conn() as conn:
        return conn.execute(
            'SELECT id, date, amount, sender, receiver, bank, category, image_path FROM transactions WHERE id = ?',
            (tx_id,)
        ).fetchone()


def get_all_transactions():
    with get_conn() as conn:
        return conn.execute(
            'SELECT id, date, amount, sender, bank, category, image_path FROM transactions ORDER BY created_at DESC'
        ).fetchall()


def _fetch_summary(start: str, end: str):
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT date, amount, sender, category FROM transactions WHERE date >= ? AND date <= ? ORDER BY date DESC',
            (start, end)
        ).fetchall()
        total = conn.execute(
            'SELECT SUM(amount) FROM transactions WHERE date >= ? AND date <= ?',
            (start, end)
        ).fetchone()[0] or 0.0
    return rows, total


def get_daily_summary():
    today = _now().strftime('%Y-%m-%d')
    rows, total = _fetch_summary(today, today)
    return rows, total, today, today


def get_weekly_summary():
    now = _now()
    start = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
    end = (now + timedelta(days=6 - now.weekday())).strftime('%Y-%m-%d')
    rows, total = _fetch_summary(start, end)
    return rows, total, start, end


def get_monthly_summary():
    now = _now()
    start = now.replace(day=1).strftime('%Y-%m-%d')
    if now.month == 12:
        end = now.replace(year=now.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        end = now.replace(month=now.month + 1, day=1) - timedelta(days=1)
    end = end.strftime('%Y-%m-%d')
    rows, total = _fetch_summary(start, end)
    return rows, total, start, end
