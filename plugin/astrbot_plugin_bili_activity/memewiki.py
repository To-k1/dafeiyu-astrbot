"""Read-only lookup of the public dsh-memewiki dictionary, through FishHome."""

import argparse
import html
import json
import re
import shutil
import sqlite3
from contextlib import contextmanager, closing
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


FILENAME = "memewiki.sqlite3"
LIMIT = 120
CATALOG = "dsh-memewiki"
ENTRY_API = "https://cnmeme.wiki/api/v1/public/memes"
SNAPSHOT_URL = "https://cdn.jsdelivr.net/gh/thawline/dsh-memewiki@main/data/memes.db"
COLUMNS = "title, summary, usage, tags, origin, slug"
SELECT = f"SELECT {COLUMNS} FROM memes"


@contextmanager
def database(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        yield conn


def validate_query(query):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= LIMIT or "\0" in query:
        raise ValueError(f"请输入 1 至 {LIMIT} 字的梗名、别名或简短片段")
    return query.strip()


def search(conn, query):
    # Preserve dsh-memewiki's lookup precedence. Treat wildcard glyphs literally.
    stages = [
        ("title", "title = ?", "title", (query,)),
        ("alias", "instr(',' || lower(aliases) || ',', ',' || lower(?) || ',') > 0", "title", (query,)),
        ("prefix", "substr(lower(title), 1, length(?)) = lower(?)", "length(title), title", (query, query)),
        ("embedded_title", "length(title) >= 2 AND instr(?, title) > 0", "length(title) DESC, title", (query,)),
    ]
    if len(query) >= 3 or re.fullmatch(r"[A-Za-z0-9]{2,}", query):
        stages.append(("substring", "instr(title, ?) > 0", "length(title), title", (query,)))
    for method, condition, order, params in stages:
        rows = conn.execute(f"{SELECT} WHERE {condition} ORDER BY {order} LIMIT 4", params).fetchall()
        if rows:
            return method, rows
    return None, []


def compact(value, limit):
    value = " ".join(value.split()) if isinstance(value, str) else ""
    return value if len(value) <= limit else value[:limit].rstrip() + "…"


def entry(row):
    origin = compact(row["origin"], 500)
    slug = (row["slug"] or "").strip()
    return {
        "term": compact(row["title"], 180),
        "meaning": compact(row["summary"], 500),
        "usage": compact(row["usage"], 500),
        "context_tags": compact(row["tags"], 160),
        "origin": origin,
        "origin_status": "reported_unverified" if origin else "unknown",
        **({"entry_url": ENTRY_API + "/" + quote(slug, safe="")} if slug else {}),
    }


def lookup(path, query):
    query = validate_query(query)
    result = {"kind": "meme_dictionary", "source": CATALOG, "query": query}
    if not path.is_file():
        return result | {"status": "unavailable", "reason": "dictionary_missing"}
    with database(path) as conn:
        method, rows = search(conn, query)
    if not rows:
        return result | {"status": "not_found"}
    return result | {"status": "ambiguous" if len(rows) > 1 else "found", "match": method,
                     "entries": [entry(row) for row in rows[:3]], "more_candidates": len(rows) > 3}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get(url, *, timeout, accept="application/json"):
    request = Request(url, headers={"User-Agent": "FishHome-MemeLookup/1.0", "Accept": accept})
    return build_opener(NoRedirect()).open(request, timeout=timeout)


def get_json(url):
    with get(url, timeout=8) as response:
        raw = response.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError("CNMemeWiki 响应超过读取上限")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("CNMemeWiki 响应无法读取")
        return data


def plain_text(value):
    without_code = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", value, flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", without_code)).strip()


def online_entry(item):
    body = item.get("content_html") or ""
    parts = re.split(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", body, flags=re.I | re.S)
    sections = {plain_text(heading): plain_text(content) for heading, content in zip(parts[1::2], parts[2::2])}
    tags = item.get("tags") or []
    result = entry({"title": item["title"], "summary": item.get("summary"), "usage": sections.get("用法"),
                    "origin": sections.get("来源"), "tags": "、".join(tag for tag in tags if isinstance(tag, str)),
                    "slug": item["slug"]})
    if item.get("updated_at"):
        result["entry_updated_at"] = item["updated_at"]
    return result


def online_lookup(query):
    query = validate_query(query)
    result = {"kind": "meme_dictionary", "source": "cnmeme.wiki", "query": query}
    try:
        data = get_json(ENTRY_API + "?" + urlencode({"q": query, "limit": 4}))
        candidates = data["items"]
        if not candidates:
            return result | {"status": "not_found"}
        titles = [item for item in candidates if item["title"].casefold() == query.casefold()]
        aliases = [item for item in candidates if any(alias.casefold() == query.casefold()
                   for alias in (item.get("aliases") or []))] if not titles else []
        matches = titles or aliases or candidates
        entries = []
        for item in matches[:3]:
            url = ENTRY_API + "/" + quote(item["slug"], safe="")
            try:
                details = get_json(url)
                entries.append(online_entry(item | details.get("item", details)))
            except (URLError, TimeoutError, KeyError, TypeError, ValueError):
                entries.append(online_entry(item) | {"detail_status": "unavailable"})
    except HTTPError as exc:
        return result | {"status": "unavailable", "http_status": exc.code}
    except (URLError, TimeoutError, KeyError, TypeError, ValueError):
        return result | {"status": "unavailable", "reason": "online_lookup_failed"}
    return result | {"status": "found" if len(matches) == 1 else "ambiguous",
                     "match": "title" if titles else "alias" if aliases else "site_candidate",
                     "entries": entries, "more_candidates": len(matches) > 3 or bool(not (titles or aliases) and data.get("has_more"))}


def status(directory):
    path = directory / FILENAME
    if not path.is_file():
        return {"available": False}
    try:
        with database(path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0]
            meta = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
            built = conn.execute("SELECT value FROM meta WHERE key='built_at'").fetchone() if meta else None
        return {"available": count > 0, "entries": count, "built_at": built[0] if built else None}
    except (OSError, sqlite3.Error):
        return {"available": False, "reason": "dictionary_unreadable"}


def install(source, directory):
    """Stage a standalone upstream SQLite file before atomically replacing the dictionary."""
    directory.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=directory, suffix=".sqlite3", delete=False) as temp:
        staged = Path(temp.name)
    try:
        shutil.copyfile(source, staged)
        with database(staged) as conn:
            conn.execute(f"SELECT {COLUMNS}, aliases FROM memes LIMIT 1").fetchone()
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("梗百科 SQLite 校验失败")
            count = conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0]
            if not count:
                raise ValueError("梗百科没有词条")
        staged.replace(directory / FILENAME)
        return count
    finally:
        staged.unlink(missing_ok=True)


def update(directory):
    """Fetch the latest published upstream SQLite snapshot on manual request."""
    directory.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=directory, suffix=".download", delete=False) as temp:
        incoming = Path(temp.name)
    try:
        with get(SNAPSHOT_URL, timeout=15, accept="application/octet-stream") as response, incoming.open("wb") as output:
            size = 0
            while chunk := response.read(128 * 1024):
                size += len(chunk)
                if size > 64 * 1024 * 1024:
                    raise ValueError("梗百科词库下载超过 64 MiB")
                output.write(chunk)
        install(incoming, directory)
        return status(directory)
    except (OSError, sqlite3.Error) as exc:
        raise ValueError(f"词库更新失败，原有词库未替换（{type(exc).__name__}）") from exc
    finally:
        incoming.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="把 dsh-memewiki 的独立 SQLite 词库导入 FishHome。")
    parser.add_argument("source", type=Path, help="原版 data/memes.db")
    parser.add_argument("directory", type=Path, help="AstrBot 的 astrbot_plugin_bili_activity 数据目录")
    args = parser.parse_args()
    print(f"已导入 {install(args.source, args.directory)} 条词条至 {args.directory / FILENAME}")
