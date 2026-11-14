"""DSH stdio tool for one disposable Python job under FishHomeCompute."""

import json
import ctypes as ct
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import shutil
import stat
import sys
from tempfile import TemporaryDirectory

from probe_appcontainer import appcontainer, grant, launch


TOOL = {
    "name": "run_python",
    "description": "Run Python in a fresh offline Windows workspace. Write report.md to return a Markdown report.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "minLength": 1, "maxLength": 16000},
            "source_text": {"type": "string", "maxLength": 12000,
                            "description": "Optional text input, available to Python as source.txt"},
        },
        "required": ["code"],
        "additionalProperties": False,
    },
}


WRAPPER = """\
import contextlib
from pathlib import Path
import runpy

with open('stdout.txt', 'w', encoding='utf-8') as out, open('stderr.txt', 'w', encoding='utf-8') as err:
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            runpy.run_path(str(Path(__file__).with_name('task.py')), run_name='__main__')
        except Exception:
            import traceback
            traceback.print_exc(file=err)
            raise SystemExit(1)
"""


def read_result(path):
    attrs = path.lstat()
    if (not stat.S_ISREG(attrs.st_mode) or attrs.st_nlink != 1
            or attrs.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError("Unexpected output file type")
    with path.open("rb") as result:
        data = result.read(8193)
    return data[:8192].decode("utf-8", errors="replace"), len(data) > 8192


def run_python(archive, code, source_text=None):
    if not isinstance(code, str) or not 0 < len(code) <= 16000:
        raise ValueError("Code must contain 1–16000 characters")
    if source_text is not None and (not isinstance(source_text, str) or len(source_text) > 12000):
        raise ValueError("Source text must contain at most 12000 characters")
    with appcontainer() as (kernel, sid, ace), TemporaryDirectory(prefix="FishHomeCompute-", dir=Path.home()) as stage:
        root = Path(stage)
        shutil.unpack_archive(archive, root, filter="data")
        runtime, work = root / "python", root / "work"
        work.mkdir()
        if source_text is not None:
            (work / "source.txt").write_bytes(source_text.encode("utf-8"))
        (runtime / "task.py").write_text(code, encoding="utf-8")
        wrapper = runtime / "run_task.py"
        wrapper.write_text(WRAPPER, encoding="utf-8")
        for output in ("stdout.txt", "stderr.txt"):
            (work / output).touch()
        report_path = work / "report.md"
        grant(root, "/grant", f"{ace}:RX")
        grant(runtime, "/grant", f"{ace}:(OI)(CI)RX")
        grant(work, "/grant", f"{ace}:(OI)(CI)M")
        grant(work, "/setintegritylevel", "(OI)(CI)L", "/T")
        kernel.SetErrorMode(0x8003)
        status = launch(kernel, sid, runtime / "python.exe", ["-B", "-I", str(wrapper)], work, 25_000)
        stdout, stdout_truncated = read_result(work / "stdout.txt")
        stderr, stderr_truncated = read_result(work / "stderr.txt")
        report, report_truncated = read_result(report_path) if status == "0x00000000" and os.path.lexists(report_path) else (None, False)
        return {"exit": status, "stdout": stdout, "stderr": stderr,
                "report": report, "output_truncated": stdout_truncated or stderr_truncated or report_truncated}


def dispatch(archive, method, params):
    if method == "initialize":
        return {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                "serverInfo": {"name": "fishhome-compute", "version": "0.1.0"}}
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "resources/list":
        return {"resources": []}
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    if method == "ping":
        return {}
    if method != "tools/call" or params.get("name") != TOOL["name"]:
        raise ValueError("Unknown method or tool")
    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict) or "code" not in arguments or arguments.keys() - {"code", "source_text"}:
        raise ValueError("Expected code and optional source_text arguments")
    result = run_python(archive, arguments["code"], arguments.get("source_text"))
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "isError": result["exit"] != "0x00000000"}


def reply(archive, request):
    if "id" not in request:
        return None
    response = {"jsonrpc": "2.0", "id": request["id"]}
    try:
        response["result"] = dispatch(archive, request["method"], request.get("params", {}))
    except ValueError as exc:
        response["error"] = {"code": -32602, "message": str(exc)}
    except Exception as exc:
        print(f"Compute tool failed: {type(exc).__name__}", file=sys.stderr, flush=True)
        response["error"] = {"code": -32603, "message": "Compute job could not finish"}
    return response


def serve(archive, port):
    token = os.environ.pop("FISHHOME_COMPUTE_TOKEN")
    if len(token) < 32 or not token.isascii():
        raise ValueError("Invalid compute worker token")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.connection.settimeout(5)
            if self.path != "/mcp" or not compare_digest(
                    self.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()):
                self.send_error(401)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 200000:
                    raise ValueError("Invalid request size")
                request = json.loads(self.rfile.read(size))
                if not isinstance(request, dict):
                    raise ValueError("Expected an object")
            except (ValueError, OSError):
                self.send_error(400)
                return
            response = reply(archive, request)
            payload = json.dumps(response, ensure_ascii=False).encode("utf-8") if response is not None else b""
            self.connection.settimeout(30)
            self.send_response(200 if payload else 204)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    with HTTPServer(("127.0.0.1", port), Handler) as server:
        server.serve_forever()


def main(archive, port=None):
    account = ct.create_unicode_buffer(256)
    size = ct.c_ulong(len(account))
    if not ct.windll.advapi32.GetUserNameW(account, ct.byref(size)) or account.value.casefold() != "fishhomecompute":
        raise RuntimeError("Start the tool as FishHomeCompute with its profile loaded")
    archive = archive.resolve(strict=True)
    if port is not None:
        return serve(archive, port)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        request = json.loads(line)
        response = reply(archive, request)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else None)
