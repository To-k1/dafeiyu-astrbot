"""Owner conversation API for the browser, with no QQ bridge writes."""

import json
from contextlib import closing

from plugin.astrbot_plugin_bili_activity.core import Store
from plugin.astrbot_plugin_bili_activity.dsh_chats import DshChats

from .public_jobs import JOB_ID


def public_session(row, selected):
    return {key: row[key] for key in ("id", "title", "created", "status")} | {"selected": row["id"] == selected}


def public_turn(row):
    return {key: row[key] for key in ("id", "message", "status", "result", "created", "failure", "memory_saved", "memory_error")
            if key in row}


def access(activity, scope, *, query=None, body=None, ready=False):
    store = Store(activity, existing=True, read_only=body is None)
    with closing(store.db):
        jobs = DshChats(store, scope, owner_web=True)
        roster = jobs.roster(current_mode=True)
        if roster.mode is None:
            raise ValueError("请先选定 ThinkBook 的正式或测试人格")
        if body is None:
            response = {"group": roster.mode, "ready": ready}
            agent = query.get("agent")
            if not agent:
                return {**response, "agents": [{key: row[key] for key in ("id", "name", "status")}
                                                for row in roster.list()]}
            profile = roster.get(agent)
            current = profile.get("current_chat_session")
            selected = query.get("session") or current
            sessions = [public_session(row, current) for row in profile["chat_sessions"]]
            turns = []
            if selected:
                roster._session(profile, selected)
                rows = store.db.execute(
                    "SELECT value FROM settings WHERE substr(key,1,?)=? "
                    "AND json_extract(value,'$.agent_id')=? AND json_extract(value,'$.logical_session_id')=? "
                    "ORDER BY json_extract(value,'$.sequence')",
                    (len(jobs.prefix), jobs.prefix, agent, selected))
                turns = [public_turn(json.loads(row[0])) for row in rows]
            return {**response, "sessions": sessions, "session": selected, "turns": turns,
                    "agent": {key: profile[key] for key in ("id", "name", "status")}}
        if not isinstance(body, dict) or roster.mode != body.get("group"):
            raise ValueError("人格分组已变化，请刷新后重试")
        action = body.get("action")
        fields = {"new": {"title"}, "select": {"session"}, "archive": {"session"},
                  "send": {"session", "request_id", "text"}}
        if not isinstance(action, str) or action not in fields or body.keys() != fields[action] | {"action", "agent", "group"}:
            raise ValueError("无效的主人直聊操作")
        agent = body["agent"]
        profile = roster.get(agent)
        if action == "new":
            roster.snapshot(agent)
            created = roster.new_session(agent, body["title"])
            return public_session(created, created["id"])
        session = body["session"]
        if not isinstance(session, str) or not session:
            raise ValueError("请选择主人直聊中的会话")
        roster._session(profile, session)
        if action == "select":
            return public_session(roster.select_session(agent, session), session)
        if action == "archive":
            archived = roster.archive_session(agent, session)
            return public_session(archived, profile.get("current_chat_session"))
        ident = body["request_id"]
        if not isinstance(ident, str) or not JOB_ID.fullmatch(ident):
            raise ValueError("无效的消息编号")
        if not ready:
            raise ValueError("受托 DSH 的 Go 通道暂未就绪")
        return public_turn(jobs.enqueue(body["text"], agent, ident, session_id=session, report_to_owner=False))
