"""Verify a disposable DSH execution workspace without model or network calls."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile


def denied(operation):
    try:
        operation()
    except (PermissionError, FileNotFoundError):
        return True
    return False


def protected_access(path, mode):
    try:
        with path.open(mode):
            return "allowed"
    except PermissionError:
        return "denied"
    except FileNotFoundError:
        return "missing"


def verify(workspace, outside_fixture):
    if not outside_fixture.is_absolute() or outside_fixture.is_relative_to(workspace):
        raise ValueError("Use an absolute path to a disposable sample outside the workspace.")
    # The host must create the synthetic fixture before launching this program.
    # Opening without a write leaves it intact even if permissions are too broad.
    results = {
        "outside_read_denied": denied(lambda: outside_fixture.read_bytes()),
        "outside_write_denied": denied(lambda: outside_fixture.open("r+b").close()),
        "synthetic_token_absent": "FISHHOME_DSH_PROBE_TOKEN" not in os.environ,
        "gateway_environment_absent": not any(key in os.environ for key in (
            "FISHHOME_DSH_GATEWAY_TOKEN", "DEEPSEEK_API_KEY",
            "FISHHOME_AUTONOMOUS_TOKEN", "FISHHOME_INTERACTION_TOKEN", "FISH_MANAGER_TOKEN",
        )),
    }
    with TemporaryDirectory(prefix="dsh-python-check-", dir=workspace) as folder:
        root = Path(folder)
        env = root / "venv"
        interpreter = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        wheel = root / "fishhome_probe-0.0.1-py3-none-any.whl"
        with ZipFile(wheel, "w") as package:
            package.writestr("fishhome_probe.py", "VALUE = 17\n")
            metadata = "fishhome_probe-0.0.1.dist-info/"
            package.writestr(metadata + "METADATA", "Metadata-Version: 2.1\nName: fishhome-probe\nVersion: 0.0.1\n")
            package.writestr(metadata + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            package.writestr(metadata + "RECORD", "")
        commands = (
            [sys.executable, "-m", "venv", str(env)],
            [str(interpreter), "-m", "pip", "--isolated", "install", "--no-index", "--no-deps", str(wheel)],
            [str(interpreter), "-c", "from fishhome_probe import VALUE; assert VALUE == 17"],
        )
        for command in commands:
            subprocess.run(command, cwd=root, check=True, capture_output=True, timeout=90)
    return {"python_workspace": True, **results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("outside_fixture", type=Path)
    parser.add_argument("--probe-file", nargs=2, metavar=("LABEL", "PATH"), action="append", default=[])
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    results = verify(args.workspace.resolve(strict=True), args.outside_fixture)
    if args.probe_file:
        protected = {
            label: {"read": protected_access(Path(path), "rb"), "write": protected_access(Path(path), "r+b")}
            for label, path in args.probe_file
        }
        results["protected_reads_denied"] = all(access["read"] == "denied" for access in protected.values())
        results["protected_writes_denied"] = all(access["write"] == "denied" for access in protected.values())
    ok = all(results.values())
    if args.probe_file:
        results["protected_files"] = protected
    payload = json.dumps(results)
    if args.receipt:
        args.receipt.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
