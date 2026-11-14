"""Write a machine-local DSH compute overlay without embedding the worker token."""

import argparse
import json
from pathlib import Path


def render(python, proxy, port, token_file):
    if not 1 <= port <= 65535:
        raise ValueError("Invalid compute port")
    values = {"PYTHON": python.resolve(strict=True), "PROXY": proxy.resolve(strict=True),
              "PORT": port, "TOKEN_FILE": token_file.resolve(strict=True)}
    template = Path(__file__).with_name("compute-preset.yml").read_text(encoding="utf-8")
    for name, value in values.items():
        template = template.replace(f"__COMPUTE_{name}__", json.dumps(str(value)))
    return template


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("python", type=Path)
    parser.add_argument("proxy", type=Path)
    parser.add_argument("port", type=int)
    parser.add_argument("token_file", type=Path)
    args = parser.parse_args()
    args.output.write_text(render(args.python, args.proxy, args.port, args.token_file), encoding="utf-8")
