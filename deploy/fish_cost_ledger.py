"""Persistent RMB reservations for FishHome's two budget buckets."""

import sqlite3
import time
from contextlib import closing, contextmanager
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from plugin.astrbot_plugin_bili_activity.cost_window import budget_day


BUCKETS = ("interaction", "autonomous")
YUAN = 1_000_000_000
DEFAULT_LIMIT = 5 * YUAN


class CostLimit(Exception):
    pass


def amount(value):
    try:
        units = Decimal(value) * YUAN
        if units.is_finite() and 0 <= units <= 100_000 * YUAN and units == int(units):
            return int(units)
    except (InvalidOperation, TypeError):
        pass
    raise ValueError("额度应为 0 到 100000 之间的人民币金额")


def yuan(units):
    return str(Decimal(units) / YUAN)


class Ledger:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS fish_cost_limits (
                    bucket TEXT NOT NULL, day TEXT NOT NULL, amount INTEGER NOT NULL,
                    PRIMARY KEY(bucket, day));
                CREATE TABLE IF NOT EXISTS fish_cost_calls (
                    id TEXT PRIMARY KEY, bucket TEXT NOT NULL, day TEXT NOT NULL,
                    kind TEXT NOT NULL, reserved INTEGER NOT NULL, actual INTEGER);
                CREATE INDEX IF NOT EXISTS fish_cost_day ON fish_cost_calls(bucket, day);
            """)

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    @staticmethod
    def totals(db, bucket, day):
        row = db.execute("SELECT amount FROM fish_cost_limits WHERE bucket=? AND day IN (?, '') "
                         "ORDER BY day DESC LIMIT 1", (bucket, day)).fetchone()
        cap = row[0] if row else DEFAULT_LIMIT
        settled, reserved = db.execute(
            "SELECT COALESCE(SUM(actual), 0), COALESCE(SUM(CASE WHEN actual IS NULL THEN reserved END), 0) "
            "FROM fish_cost_calls WHERE bucket=? AND day=?", (bucket, day)).fetchone()
        return cap, settled, reserved

    def adjust(self, bucket, value, *, temporary):
        if bucket not in BUCKETS:
            raise ValueError("unknown budget bucket")
        with self.transaction() as db:
            day = budget_day(self.clock())
            if not temporary:
                db.execute("DELETE FROM fish_cost_limits WHERE bucket=? AND day=?", (bucket, day))
            db.execute("INSERT OR REPLACE INTO fish_cost_limits VALUES (?, ?, ?)",
                       (bucket, day if temporary else "", amount(value)))

    def reserve(self, bucket, kind, maximum, now):
        if bucket not in BUCKETS or type(maximum) is not int or maximum <= 0:
            raise ValueError("invalid reservation")
        day, ident = budget_day(now), uuid4().hex
        with self.transaction() as db:
            cap, settled, reserved = self.totals(db, bucket, day)
            if settled + reserved + maximum > cap:
                raise CostLimit("剩余额度不足以预留本次请求。预算日于北京时间 00:00 切换。")
            db.execute("INSERT INTO fish_cost_calls VALUES (?, ?, ?, ?, ?, NULL)",
                       (ident, bucket, day, kind, maximum))
        return ident

    def settle(self, ident, actual):
        if type(actual) is not int or actual < 0:
            raise ValueError("invalid settled usage")
        with self.transaction() as db:
            db.execute("UPDATE fish_cost_calls SET actual=? WHERE id=? AND actual IS NULL", (actual, ident))

    def snapshot(self):
        day = budget_day(self.clock())
        with self.transaction() as db:
            ledgers = {}
            for bucket in BUCKETS:
                cap, settled, reserved = self.totals(db, bucket, day)
                ledgers[bucket] = {"limit": yuan(cap), "used": yuan(settled), "reserved": yuan(reserved),
                                   "remaining": yuan(max(0, cap - settled - reserved))}
        return {"day": day, "ledgers": ledgers}
