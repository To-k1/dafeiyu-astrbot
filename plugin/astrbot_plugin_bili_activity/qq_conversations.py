"""Reconnect the owner's test and formal personas to their native QQ conversations."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo


CHAT_SLOT = "fishhome_selected_conversation:"


async def restore_persona_chat(manager, preferences, scope, current, desired):
    chats = await manager.get_conversations(scope)
    previous = await manager.get_curr_conversation_id(scope)
    if any(row.cid == previous and row.persona_id == current for row in chats):
        await preferences.session_put(scope, CHAT_SLOT + current, previous)
    candidates = [row for row in chats if row.persona_id == desired]
    chosen = await preferences.session_get(scope, CHAT_SLOT + desired, None)
    if not any(row.cid == chosen for row in candidates):
        latest = max(candidates, key=lambda row: (
            row.history not in (None, "", "[]"), row.updated_at, row.created_at), default=None)
        chosen = latest.cid if latest else None
    if chosen:
        await manager.switch_conversation(scope, chosen)
    else:
        await manager.new_conversation(scope, content=[], persona_id=desired)
    return previous


async def conversation_command(manager, scope, mode, text):
    usage = "用法 /对话，/对话 列表 2，/对话 切换 对话ID。用 /new 新开对话。"
    parts = text.split()
    if parts[1:] in ([], ["列表"]):
        page = 1
    elif len(parts) == 3 and parts[1] == "列表" and re.fullmatch(r"[1-9][0-9]{0,5}", parts[2]):
        page = int(parts[2])
    elif len(parts) == 3 and parts[1] == "切换" and re.fullmatch(r"[0-9a-fA-F-]{4,36}", parts[2]):
        page = None
    else:
        return usage
    prefix = {"formal": "feiyu-", "test": "fish-home-"}.get(mode)
    if prefix is None:
        return "请先在控制台选定 FishHome 正式或测试人格。"
    chats = await manager.get_conversations(scope)
    visible = [row for row in chats if (row.persona_id or "").startswith(prefix)]
    if page is None:
        matches = [row for row in visible if row.cid.lower().startswith(parts[2].lower())]
        if len(matches) != 1:
            return "当前分组没有匹配的对话，或 ID 前缀重复。可用 /对话 查看，再输入更长的 ID。"
        await manager.switch_conversation(scope, matches[0].cid)
        return f"已切换到 {matches[0].cid[:8]}。下一条消息会继续这段对话。"
    selected = await manager.get_curr_conversation_id(scope)
    visible.sort(key=lambda row: (row.cid == selected, row.updated_at, row.created_at), reverse=True)
    label = "正式" if mode == "formal" else "测试"
    lines = [f"{label}对话 · 共 {len(visible)} 段 · 第 {page} 页"]
    for row in visible[(page - 1) * 8:page * 8]:
        when = datetime.fromtimestamp(row.updated_at or row.created_at, ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M")
        title = " ".join((row.title or "").split())[:40] or "未命名"
        is_selected = "（当前）" if row.cid == selected else ""
        lines.append(f"{row.cid[:8]}{is_selected}  {when}  {title}")
    unknown = sum(not (row.persona_id or "").startswith(("feiyu-", "fish-home-")) for row in chats)
    if unknown:
        lines.append(f"另有 {unknown} 段旧对话的归属待确认。")
    return "\n".join([*lines, usage])
