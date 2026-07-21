#!/usr/bin/env python3
"""Generate the pinned, public-only MEXC protobuf Python modules."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "vendor" / "mexc-websocket-proto"
OUTPUT_DIR = ROOT / "src" / "tri_arb" / "exchange" / "mexc" / "proto"
PROTO_FILES = ("PublicLimitDepthsV3Api.proto", "PushDataV3ApiWrapper.proto")


def _generate() -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="tri-arb-proto-") as raw_temp:
        temp = Path(raw_temp)
        command = [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"-I{SOURCE_DIR}",
            f"--python_out={temp}",
            *(str(SOURCE_DIR / name) for name in PROTO_FILES),
        ]
        subprocess.run(command, check=True)
        generated: dict[str, str] = {}
        for proto_name in PROTO_FILES:
            output_name = f"{Path(proto_name).stem}_pb2.py"
            text = (temp / output_name).read_text(encoding="utf-8")
            text = text.replace(
                "import PublicLimitDepthsV3Api_pb2 as PublicLimitDepthsV3Api__pb2",
                "from . import PublicLimitDepthsV3Api_pb2 as PublicLimitDepthsV3Api__pb2",
            )
            generated[output_name] = text
        return generated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated files drift")
    args = parser.parse_args()
    generated = _generate()
    if args.check:
        drift = [
            name
            for name, expected in generated.items()
            if not (OUTPUT_DIR / name).is_file()
            or (OUTPUT_DIR / name).read_text(encoding="utf-8") != expected
        ]
        if drift:
            print(f"generated MEXC protobuf drift: {', '.join(drift)}", file=sys.stderr)
            return 1
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in generated.items():
        (OUTPUT_DIR / name).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
