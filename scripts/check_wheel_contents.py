"""Fail when a built wheel omits the production dashboard."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.wheel) as archive:
        names = set(archive.namelist())
    required = {"tri_arb/frontend_dist/index.html"}
    missing = required - names
    asset_prefix = "tri_arb/frontend_dist/assets/"
    scripts = tuple(
        name for name in names if name.startswith(asset_prefix) and name.endswith(".js")
    )
    styles = tuple(
        name for name in names if name.startswith(asset_prefix) and name.endswith(".css")
    )
    if missing or not scripts or not styles:
        detail = ", ".join(sorted(missing)) or "hashed JS/CSS assets"
        raise SystemExit(f"wheel dashboard is incomplete: missing {detail}")
    print(f"wheel dashboard ok: {len(scripts)} script(s), {len(styles)} stylesheet(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
