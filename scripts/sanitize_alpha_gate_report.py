#!/usr/bin/env python3
"""Sanitize an alpha gate report for public publication."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmesh.alpha_gate import write_sanitized_alpha_gate_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--in",
        dest="in_path",
        default=".agentmesh/runs/alpha-gate-report.json",
        help="Raw/private alpha gate report JSON",
    )
    parser.add_argument(
        "--out",
        dest="out_path",
        default="docs/alpha-gate-report.public.json",
        help="Sanitized/public alpha gate report JSON",
    )
    args = parser.parse_args()

    in_path = Path(args.in_path)
    out_path = Path(args.out_path)
    if not in_path.exists():
        print(f"input not found: {in_path}", file=sys.stderr)
        return 1

    report = write_sanitized_alpha_gate_report(in_path, out_path)
    print(f"wrote {out_path}  overall_pass={report.get('overall_pass', False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
