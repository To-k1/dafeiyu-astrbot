"""Compose the workspace around the existing, independently served controls."""

from pathlib import Path


HERE = Path(__file__).resolve().parent


def page(classic_html: str, target: str) -> str:
    shell = (HERE / "workspace.html").read_text(encoding="utf-8").replace("__TARGET__", target)
    css = (HERE / "workspace.css").read_text(encoding="utf-8")
    script = (HERE / "workspace.js").read_text(encoding="utf-8")
    return classic_html.replace("<main>", f"<style>{css}</style>\n{shell}\n<main>", 1) + (
        f"\n<script>\n{script}\n</script>\n"
    )
