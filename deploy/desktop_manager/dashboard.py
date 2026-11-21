"""Read the current activity ledger for the console overview."""

import sqlite3
from contextlib import closing

from plugin.astrbot_plugin_bili_activity.team_mode import GROUP_SQL


def read_activity(ledger, scope, group):
    if not scope or not ledger.is_file():
        return {"running": [], "recent": []}
    with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True, timeout=4)) as db:
        prefix = f"commissioned-file:v1:{scope}:"
        columns = (
            "coalesce(json_extract(value,'$.job_kind'),'file')",
            "coalesce(json_extract(value,'$.source'), json_extract(value,'$.spec.query'), json_extract(value,'$.id'))",
            "coalesce(json_extract(value,'$.agent.name'),json_extract(value,'$.spec.agent.name'))",
            "json_extract(value,'$.state')",
            "json_extract(value,'$.worker_phase')",
            "json_extract(value,'$.created')",
        )
        selector = ("SELECT " + ", ".join(columns) + " FROM settings WHERE substr(key,1,?)=? " + GROUP_SQL)

        def jobs(states, limit):
            return [
                {"kind": kind, "title": str(title or "DSH 任务")[:160],
                 "actor": ("DSH 代看 · " + agent if agent else "DSH 代看") if kind == "video" else agent,
                 "state": state, "phase": phase if state in {"running", "dispatching", "go_wait"} else None,
                 "at": at, "time_label": "发起"}
                for kind, title, agent, state, phase, at in db.execute(
                    selector + f"AND {columns[3]} IN ({','.join('?' * len(states))}) "
                    f"ORDER BY {columns[5]} DESC LIMIT ?", (len(prefix), prefix, group, group, *states, limit))
            ]

        running = jobs(("prepared", "dispatching", "queued", "submitting", "submitted", "running",
                        "continuing", "go_wait", "cancelling", "paused", "stopping"), -1)
        recent = jobs(("completed", "failed", "needs_review"), 6)
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='videos'").fetchone():
            # The phone/life feed is shared by personas. The cache stores direct
            # tool evidence, unlike DSH delegation, which lives in task records.
            observed = db.execute(
                """SELECT bvid, updated, json_extract(content,'$.title'),
                      CASE WHEN EXISTS (
                        SELECT 1 FROM json_each(content,'$.observations')
                        WHERE json_extract(json_each.value,'$.kind')='frames'
                      ) THEN '已取画面' ELSE '已读字幕' END
                   FROM videos WHERE scope=? AND (
                     json_array_length(content,'$.subtitles') > 0 OR EXISTS (
                       SELECT 1 FROM json_each(content,'$.observations')
                       WHERE json_extract(json_each.value,'$.kind')='frames'
                   )) ORDER BY updated DESC LIMIT 1""", (scope,)
            ).fetchone()
            if observed:
                bvid, at, title, stage = observed
                actor = "肥鱼亲看" if stage == "已取画面" else "肥鱼读字幕"
                recent.insert(0, {"title": str(title or bvid)[:160], "actor": actor + " · 共享活动",
                                  "state": stage, "at": at, "time_label": "取证"})
        return {"running": running, "recent": recent}
