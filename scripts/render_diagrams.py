"""
Render every ```mermaid ...``` fenced block in README.md to a PNG image
in docs/diagrams/.

Run:
    uv run python scripts/render_diagrams.py

Uses kroki.io (POST the mermaid source, get back a PNG). No Node / no
headless Chrome / no extra system deps — just httpx, which is already a
project dependency.

Each PNG is named after the nearest ``##`` or ``###`` heading that sits
above the fenced block in the README (slug-cased). If two blocks resolve to
the same name, later ones get a ``_2``, ``_3`` … suffix. If no heading
precedes the block, the file is named ``diagram_<N>.png``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
OUTPUT_DIR = REPO_ROOT / "docs" / "diagrams"
KROKI_URL = "https://kroki.io/mermaid/png"

MERMAID_FENCE = re.compile(
    r"```mermaid\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)
HEADING = re.compile(r"^#{2,3}\s+(?P<title>.+?)\s*$", re.MULTILINE)
SLUG_SAFE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return SLUG_SAFE.sub("_", text.lower()).strip("_") or "diagram"


def nearest_heading_before(markdown: str, position: int) -> str | None:
    last: str | None = None
    for match in HEADING.finditer(markdown):
        if match.start() >= position:
            break
        last = match.group("title")
    return last


def render(mermaid_source: str) -> bytes:
    """POST the mermaid source to kroki.io and return the PNG bytes."""
    response = httpx.post(
        KROKI_URL,
        content=mermaid_source.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.content


def main() -> int:
    if not README_PATH.exists():
        print(f"error: {README_PATH} not found", file=sys.stderr)
        return 1

    markdown = README_PATH.read_text(encoding="utf-8")
    blocks = list(MERMAID_FENCE.finditer(markdown))

    if not blocks:
        print(f"no ```mermaid blocks found in {README_PATH}")
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"found {len(blocks)} mermaid block(s) in README.md")
    print(f"writing PNGs to {OUTPUT_DIR.relative_to(REPO_ROOT)}/\n")

    rc = 0
    seen: dict[str, int] = {}
    for index, block in enumerate(blocks, start=1):
        heading = nearest_heading_before(markdown, block.start())
        name = slugify(heading) if heading else f"diagram_{index}"
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name}_{seen[name]}"
        out_path = OUTPUT_DIR / f"{name}.png"

        print(f"  [{index}/{len(blocks)}] {name}.png", end=" ... ", flush=True)
        try:
            png = render(block.group("body"))
        except httpx.HTTPError as exc:
            print(f"FAILED ({exc})")
            rc = 1
            continue

        out_path.write_bytes(png)
        print(f"ok ({len(png):,} bytes)")

    print("\ndone.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
