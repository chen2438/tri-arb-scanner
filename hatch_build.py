"""Build the production dashboard before creating a distributable wheel."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version != "standard":
            return
        pnpm = shutil.which("pnpm")
        if pnpm is None:
            raise RuntimeError("building the wheel requires pnpm 11.13.1 on PATH")
        root = Path(self.root)
        frontend = root / "frontend"
        subprocess.run(
            [pnpm, "--dir", str(frontend), "install", "--frozen-lockfile"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            [pnpm, "--dir", str(frontend), "build"],
            cwd=root,
            check=True,
        )
        build_data["force_include"][str(frontend / "dist")] = "tri_arb/frontend_dist"
