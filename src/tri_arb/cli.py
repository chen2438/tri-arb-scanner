"""Tri-Arb Scanner command-line interface."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

import uvicorn

from tri_arb.api import create_app
from tri_arb.config import load_settings
from tri_arb.doctor import run_diagnostics
from tri_arb.observability import configure_logging


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tri-arb",
        description="MEXC triangular arbitrage scanner",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="validate local configuration")
    subparsers.add_parser("serve", help="start the local scanner service")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = load_settings()
    if args.command == "doctor":
        results = asyncio.run(run_diagnostics(settings))
        for result in results:
            marker = "ok" if result.ok else "fail"
            print(f"[{marker}] {result.name}: {result.detail}")
        return 0 if all(result.ok for result in results) else 1

    configure_logging()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
    return 0
