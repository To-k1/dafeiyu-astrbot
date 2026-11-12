"""Beijing billing days and peak windows shared by dispatch and accounting."""

from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
HOLIDAYS_2026 = (
    ("2026-01-01", "2026-01-03"), ("2026-02-15", "2026-02-23"),
    ("2026-04-04", "2026-04-06"), ("2026-05-01", "2026-05-05"),
    ("2026-06-19", "2026-06-21"), ("2026-09-25", "2026-09-27"),
    ("2026-10-01", "2026-10-07"),
)


def budget_day(now):
    return datetime.fromtimestamp(now, SHANGHAI).date().isoformat()


def peak_end(now, holidays=HOLIDAYS_2026):
    local = datetime.fromtimestamp(now, SHANGHAI)
    day = local.date().isoformat()
    if local.weekday() < 5 and not any(first <= day <= last for first, last in holidays):
        for start, end in ((9, 12), (14, 18)):
            if start <= local.hour < end:
                return local.replace(hour=end, minute=0, second=0, microsecond=0).timestamp()
    return None
