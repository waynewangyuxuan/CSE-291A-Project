#!/usr/bin/env python3
"""Check maintained navigation links and the index of operational scripts."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
NAVIGATION = (
    "README.md",
    "docs/running.md",
    "docs/architecture.md",
    "scripts/README.md",
    "results/README.md",
    "results/phase1-recovery/README.md",
    "artifacts/README.md",
)


def main() -> int:
    errors = []
    links = 0
    for name in NAVIGATION:
        source = ROOT / name
        for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", source.read_text()):
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            destination = (source.parent / unquote(parsed.path)).resolve()
            links += 1
            if not destination.is_relative_to(ROOT) or not destination.exists():
                errors.append(f"{name}: broken repository link {target}")
    index = (ROOT / "scripts/README.md").read_text()
    scripts = sorted(path for path in (ROOT / "scripts").iterdir() if path.suffix in {".py", ".sh"})
    for script in scripts:
        if f"]({script.name})" not in index:
            errors.append(f"scripts/README.md: missing entry for {script.name}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Navigation passed: {links} local links, {len(scripts)} indexed scripts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
