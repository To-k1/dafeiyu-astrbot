"""Stdio MCP client for the Compute account's authenticated loopback worker."""

import http.client
import json
from pathlib import Path
import sys


def forward(port, token, payload):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=85)
    try:
        connection.request("POST", "/mcp", body=payload, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        })
        response = connection.getresponse()
        if response.status == 204:
            return None
        if response.status != 200:
            raise OSError(f"Compute worker returned HTTP {response.status}")
        return response.read(131072)
    finally:
        connection.close()


def dispatch(port, token, payload, request, *, disposable=False):
    if disposable and request["method"] == "tools/call" and (request.get("params") or {}).get("name") != "run_python":
        return json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {
            "code": -32602, "message": "This session supports disposable Python only",
        }}).encode("utf-8")
    result = forward(port, token, payload)
    if disposable and request["method"] == "tools/list" and result is not None:
        response = json.loads(result)
        if "result" in response:
            response["result"]["tools"] = [tool for tool in response["result"]["tools"] if tool["name"] == "run_python"]
            result = json.dumps(response).encode("utf-8")
    return result


def main(port, token_file, *, disposable=False):
    token = token_file.read_text(encoding="ascii").strip()
    for payload in sys.stdin.buffer:
        request = json.loads(payload)
        try:
            result = dispatch(port, token, payload, request, disposable=disposable)
        except (OSError, http.client.HTTPException):
            if "id" not in request:
                continue
            result = json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {
                "code": -32603, "message": "Compute worker unavailable",
            }}).encode("utf-8")
        if result is not None:
            sys.stdout.buffer.write(result + b"\n")
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    main(int(sys.argv[1]), Path(sys.argv[2]), disposable=sys.argv[3:] == ["disposable"])
