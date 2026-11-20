"""Portable DSH role cards and separate authored background files."""

import json
import re
from io import BytesIO
from pathlib import Path, PureWindowsPath
from tempfile import NamedTemporaryFile
from time import time
from uuid import uuid4
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from .team_mode import store_mode, visible

FORMAT = "fishhome-dsh-role/v1"
MAX_PACKET = 256 * 1024
MAX_BACKGROUND = 12_000
MAX_ATTACHMENTS = 8
ID_PATTERN = re.compile(r"[0-9a-f]{16}")
PERSONA_FILES = {"persona.md", "persona.txt"}
STORY_FILES = {"story.md", "story.txt"}
IMPRESSION_FILES = {"impressions.md", "impressions.txt"}
PERSONAL_FILES = STORY_FILES | IMPRESSION_FILES
SHARED_DOC = re.compile(r"(?:stories|shared)/[\w -][\w .-]{0,59}\.(?:md|txt)", re.IGNORECASE)
STORY_PREFIX = "dsh-story:v1:"
FISH_PARTICIPANT = "fish"


def decode_text(body):
    try:
        return body.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("请选择 UTF-8 编码的 .md 或 .txt 文件") from exc


def read_card_upload(body, filename, name=""):
    if not body or len(body) > MAX_PACKET:
        raise ValueError("角色卡文件超过大小限制或为空")
    if body.startswith(b"PK\x03\x04"):
        return unpack(body)
    path = PureWindowsPath(filename)
    if path.suffix.lower() not in {".md", ".txt"}:
        raise ValueError("请选择 UTF-8 的 .md 或 .txt 角色卡")
    named, persona, background = _validate(name.strip() or path.stem, decode_text(body), {})
    persona_file = "persona.txt" if path.suffix.lower() == ".txt" else "persona.md"
    return {"name": named, "persona": persona, "persona_file": persona_file, "background": background,
            "stories": {}, "fish_stories": [], "plain": True}


def validate_background(files):
    if (not isinstance(files, dict) or len(files) > MAX_ATTACHMENTS
            or len(STORY_FILES & files.keys()) > 1 or len(IMPRESSION_FILES & files.keys()) > 1):
        raise ValueError("角色包最多包含八份背景文件，个人故事和他人印象各只能放一份")
    seen = set()
    total = 0
    for name, text in files.items():
        if (not isinstance(name, str) or (name not in PERSONAL_FILES and not SHARED_DOC.fullmatch(name))
                or name.casefold() in seen or PureWindowsPath(name).is_reserved()):
            raise ValueError("请使用 story.md、impressions.md（也可用 .txt）或 stories/ 存放角色背景")
        if not isinstance(text, str) or not text.strip() or "\x00" in text:
            raise ValueError("角色背景需要非空 UTF-8 正文")
        seen.add(name.casefold())
        if name in PERSONAL_FILES:
            total += len(text)
    if total > MAX_BACKGROUND:
        raise ValueError("单人故事与他人印象正文合计不能超过 12000 字")
    return files


def _validate(name, persona, background):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise ValueError("请填写 1–60 字的角色名")
    if not isinstance(persona, str) or not persona.strip() or len(persona) > 16000 or "\x00" in persona:
        raise ValueError("角色人格需要 1–16000 字正文")
    return name.strip(), persona, validate_background(background)


def pack(name, persona, background, *, persona_file="persona.md", links=None, fish_stories=()):
    name, persona, background = _validate(name, persona, background)
    if persona_file not in PERSONA_FILES:
        raise ValueError("请使用 persona.md 或 persona.txt")
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as card:
        metadata = {"format": FORMAT, "name": name}
        if links:
            metadata["stories"] = links
        if fish_stories:
            metadata["fish_stories"] = list(fish_stories)
        card.writestr("card.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        card.writestr(persona_file, persona)
        for path, text in background.items():
            card.writestr(path, text)
    return output.getvalue()


def unpack(body, *, max_size=MAX_PACKET):
    if not body or len(body) > max_size:
        raise ValueError("角色卡文件超过大小限制或为空")
    try:
        with ZipFile(BytesIO(body)) as card:
            entries = card.infolist()
            names = [entry.filename for entry in entries]
            persona_names = PERSONA_FILES & set(names)
            if (len(entries) > MAX_ATTACHMENTS + 5 or len({name.casefold() for name in names}) != len(entries)
                    or "card.json" not in names or len(persona_names) != 1
                    or sum(entry.file_size for entry in entries) > max_size):
                raise ValueError("角色卡清单或文件大小无效")
            persona_file = next(iter(persona_names))
            if any(name not in {"card.json", persona_file, "shared/", "stories/"} | PERSONAL_FILES
                   and not SHARED_DOC.fullmatch(name) for name in names):
                raise ValueError("角色卡含有未知文件")
            metadata = json.loads(card.read("card.json").decode("utf-8-sig"))
            if not isinstance(metadata, dict) or metadata.get("format") != FORMAT:
                raise ValueError("角色卡格式不受支持")
            persona = card.read(persona_file).decode("utf-8-sig")
            background = {key: card.read(key).decode("utf-8-sig") for key in names
                          if key not in {"card.json", persona_file, "shared/", "stories/"}}
    except (BadZipFile, UnicodeError, json.JSONDecodeError, NotImplementedError, RuntimeError) as exc:
        raise ValueError("无法解析角色卡 ZIP 或 UTF-8 文本") from exc
    name, persona, background = _validate(metadata.get("name"), persona, background)
    links = metadata.get("stories", {})
    if (not isinstance(links, dict) or any(not SHARED_DOC.fullmatch(path) or path not in background
            or not isinstance(ident, str) or not ID_PATTERN.fullmatch(ident) for path, ident in links.items())):
        raise ValueError("多人故事的关联清单无效")
    fish_stories = metadata.get("fish_stories", [])
    if (not isinstance(fish_stories, list) or len(fish_stories) > MAX_ATTACHMENTS
            or any(not isinstance(path, str) or not SHARED_DOC.fullmatch(path) or path not in background
                   for path in fish_stories)):
        raise ValueError("大肥鱼参与的故事清单无效")
    return {"name": name, "persona": persona, "persona_file": persona_file, "background": background,
            "stories": links, "fish_stories": fish_stories}


def card_path(store, agent_id):
    if not isinstance(agent_id, str) or not ID_PATTERN.fullmatch(agent_id):
        raise ValueError("请选择有效的小弟")
    return Path(store.folder) / "role_cards" / (agent_id + ".zip")


def load_card(store, agent_id):
    path = card_path(store, agent_id)
    try:
        with path.open("rb") as source:
            body = source.read(MAX_PACKET + 1)
    except FileNotFoundError:
        return {"persona_file": "persona.md", "background": {}}
    return unpack(body)


def _write_file(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        stage = Path(temp.name)
        try:
            temp.write(body)
            temp.close()
            stage.replace(path)
        finally:
            stage.unlink(missing_ok=True)


def save_background(store, agent_id, name, persona, background, *, persona_file="persona.md"):
    path = card_path(store, agent_id)
    _write_file(path, pack(name, persona, background, persona_file=persona_file))


def fish_impressions_path(store):
    return Path(store.folder) / "fish_impressions.txt"


def save_fish_impressions(store, text):
    validate_background({"impressions.txt": text})
    _write_file(fish_impressions_path(store), text.encode("utf-8"))


def load_fish_impressions(store):
    try:
        return fish_impressions_path(store).read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None


def read_fish_impressions(store, offset=0):
    body = load_fish_impressions(store)
    if body is None:
        return {"status": "empty"}
    return {"status": "available", "name": "大肥鱼的他人印象", **_text_page(body, offset)}


def _text_page(body, offset):
    if type(offset) is not int or not 0 <= offset <= len(body):
        raise ValueError("请使用上一页返回的读取位置")
    end = min(len(body), offset + 5000)
    return {"text": body[offset:end], "next_offset": end if end < len(body) else None}


def _story_key(scope, ident=""):
    return f"{STORY_PREFIX}{scope}:{ident}"


def _story_path(store, ident):
    if not isinstance(ident, str) or not ID_PATTERN.fullmatch(ident):
        raise ValueError("无效的多人故事 ID")
    return Path(store.folder) / "role_stories" / (ident + ".txt")


def stories(store, scope):
    prefix = _story_key(scope)
    return [json.loads(row[0]) for row in store.db.execute(
        "SELECT value FROM settings WHERE substr(key,1,?)=? ORDER BY json_extract(value,'$.created') DESC",
        (len(prefix), prefix))]


def get_story(store, scope, ident):
    path = _story_path(store, ident)
    row = store.get_setting(_story_key(scope, ident))
    if row is None:
        raise ValueError("找不到多人故事")
    return {**row, "text": decode_text(path.read_bytes())}


def discover_stories(store, scope, query):
    mode = store_mode(store)
    found = [{"id": row["id"], "name": row["path"].split("/", 1)[-1],
              "fish_participates": FISH_PARTICIPANT in row["participants"], "participants": row["participants"]}
             for row in stories(store, scope)
             if row["status"] == "active" and visible(row, mode) and query.casefold() in row["path"].casefold()]
    return {"stories": found[:20], "total": len(found)}


def read_story(store, scope, ident, offset):
    _story_path(store, ident)
    row = store.get_setting(_story_key(scope, ident))
    if not visible(row, store_mode(store)) or row["status"] != "active":
        raise ValueError("这篇多人故事在当前分组不可用")
    story = get_story(store, scope, ident)
    return {"id": ident, "name": story["path"].split("/", 1)[-1], **_text_page(story["text"], offset),
            "fish_participates": FISH_PARTICIPANT in story["participants"], "participants": story["participants"]}


def _linked(store, agent_id):
    for value, in store.db.execute(
        "SELECT value FROM settings WHERE substr(key,1,?)=? "
        "AND json_extract(value,'$.status')='active' AND EXISTS("
        "SELECT 1 FROM json_each(settings.value,'$.participants') WHERE json_each.value=?)",
        (len(STORY_PREFIX), STORY_PREFIX, agent_id)):
        row = json.loads(value)
        yield row, decode_text(_story_path(store, row["id"]).read_bytes())


def load_background(store, agent_id):
    authored = load_card(store, agent_id)["background"]
    return {**authored, **{row["path"]: text for row, text in _linked(store, agent_id)}}


def export_card(store, agent_id, name, persona):
    card = load_card(store, agent_id)
    linked = list(_linked(store, agent_id))
    links = {row["path"]: row.get("origin_id") or row["id"] for row, _ in linked}
    fish_stories = [row["path"] for row, _ in linked if FISH_PARTICIPANT in row["participants"]]
    content = {**card["background"], **{row["path"]: text for row, text in linked}}
    return pack(name, persona, content, persona_file=card["persona_file"], links=links, fish_stories=fish_stories)


def save_personal_text(store, agent_id, profile, kind, filename, text):
    if kind not in {"story", "impressions"} or not isinstance(filename, str):
        raise ValueError("请选择单人故事或他人印象文件")
    extension = PureWindowsPath(filename).suffix.lower()
    if extension not in {".md", ".txt"}:
        raise ValueError("请选择 UTF-8 的 .md 或 .txt 文件")
    card = load_card(store, agent_id)
    personal = STORY_FILES if kind == "story" else IMPRESSION_FILES
    background = {path: value for path, value in card["background"].items() if path not in personal}
    background[kind + extension] = text
    validate_background({**background, **{row["path"]: value for row, value in _linked(store, agent_id)}})
    save_background(store, agent_id, profile["name"], profile["persona"], background,
                    persona_file=card["persona_file"])


def set_story(store, scope, *, ident="", filename=None, text=None, participants=None, status=None, origin_id=None):
    old = get_story(store, scope, ident) if ident else None
    if not old:
        ident = uuid4().hex[:16]
    if text is None:
        text = old["text"] if old else None
    path = "stories/" + filename.strip() if isinstance(filename, str) else old["path"] if old else None
    validate_background({path: text})
    participants = participants if participants is not None else old["participants"] if old else []
    status = status if status else old["status"] if old else "active"
    if status not in {"active", "archived"} or not isinstance(participants, list) or not participants:
        raise ValueError("请选择多人故事的参与角色")
    if any(agent_id != FISH_PARTICIPANT and (not isinstance(agent_id, str) or not ID_PATTERN.fullmatch(agent_id)
           or not store.get_setting(f"dsh-roster:v1:{scope}:{agent_id}")) for agent_id in participants):
        raise ValueError("所选参与角色不存在")
    participants = list(dict.fromkeys(participants))
    if status == "active":
        for agent_id in participants:
            if agent_id == FISH_PARTICIPANT:
                continue
            background = dict(load_card(store, agent_id)["background"])
            background.update({row["path"]: body for row, body in _linked(store, agent_id) if row["id"] != ident})
            if any(name.casefold() == path.casefold() for name in background):
                raise ValueError("所选角色已有同名故事文件，请改个文件名")
            validate_background({**background, path: text})
    row = {"id": ident, "path": path, "participants": participants, "status": status,
           "created": old["created"] if old else time(), "persona_mode": old.get("persona_mode") if old else store_mode(store)}
    if source_id := (old or {}).get("origin_id") or origin_id:
        row["origin_id"] = source_id
    if text != (old or {}).get("text"):
        _write_file(_story_path(store, ident), text.encode("utf-8"))
    store.setting(_story_key(scope, ident), row)
    return row


def import_card_files(store, scope, agent_id, name, card):
    background = card["background"]
    personal = {p: t for p, t in background.items() if p in PERSONAL_FILES}
    known = stories(store, scope)
    validate_background({**personal, **{r["path"]: t for r, t in _linked(store, agent_id)},
                         **{"stories/" + p.split("/", 1)[1]: t for p, t in background.items() if p not in PERSONAL_FILES}})
    for path, text in background.items():
        if path in PERSONAL_FILES:
            continue
        ident = card["stories"].get(path)
        participants = [agent_id]
        if path in card.get("fish_stories", ()):
            participants.append(FISH_PARTICIPANT)
        linked = next((row for row in known if ident and ident in {row["id"], row.get("origin_id")}), None)
        if linked is None:
            set_story(store, scope, filename=path.split("/", 1)[1], text=text, participants=participants, origin_id=ident)
        else:
            current = get_story(store, scope, linked["id"])
            if current["text"] != text or current["path"].split("/", 1)[1] != path.split("/", 1)[1]:
                raise ValueError("同一多人故事内容有差异，请在多人故事入口统一更新")
            additions = [member for member in participants if member not in current["participants"]]
            if additions:
                set_story(store, scope, ident=linked["id"], participants=[*current["participants"], *additions])
    save_background(store, agent_id, name, card["persona"], personal, persona_file=card["persona_file"])


def background_prompt(files):
    if not files:
        return ""
    return (
        "\n这些是角色出厂故事、他人印象和多人故事。个人回忆与他人印象是角色最初的视角。"
        "后续相处可以改变看法。实际的经历、关系变化和任务结果按各自的真实记录。"
        "把出厂设定留在角色资料中，不将它写成实时发生的长期记忆。\n"
        + "\n".join(f"[{path}]\n{text}" for path, text in files.items()) + "\n"
    )
