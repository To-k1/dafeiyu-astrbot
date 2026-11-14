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


def main(port, token_file):
    token = token_file.read_text(encoding="ascii").strip()
    for payload in sys.stdin.buffer:
        request = json.loads(payload)
        try:
            result = forward(port, token, payload)
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
    main(int(sys.argv[1]), Path(sys.argv[2]))
