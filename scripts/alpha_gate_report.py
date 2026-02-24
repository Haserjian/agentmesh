#!/usr/bin/env python3
"""Generate a machine-readable Alpha Gate report for an orchestrated run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmesh.alpha_gate import write_alpha_gate_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="AgentMesh data dir used by the run")
    parser.add_argument(
        "--out",
        default=".agentmesh/runs/alpha-gate-report.json",
        help="Output report path (raw artifacts should stay private)",
    )
    parser.add_argument("--ci-log", default="", help="Optional CI log file path to scan for VERIFIED")
    parser.add_argument(
        "--ci-result-json",
        default="",
        help="Optional structured CI result JSON (preferred over --ci-log)",
    )
    parser.add_argument(
        "--witness-optional",
        action="store_true",
        help="Do not fail gate if CI witness status is unavailable",
    )
    args = parser.parse_args()

    ci_log_text = ""
    if args.ci_log:
        ci_path = Path(args.ci_log)
        if ci_path.exists():
            ci_log_text = ci_path.read_text()
    ci_result = None
    if args.ci_result_json:
        ci_json_path = Path(args.ci_result_json)
        if ci_json_path.exists():
            import json
            parsed = json.loads(ci_json_path.read_text())
            if isinstance(parsed, dict):
                ci_result = parsed

    report = write_alpha_gate_report(
        out_path=Path(args.out),
        data_dir=Path(args.data_dir),
        ci_log_text=ci_log_text,
        ci_result=ci_result,
        require_witness_verified=not args.witness_optional,
    )
    print(f"wrote {args.out}  overall_pass={report['overall_pass']}")
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
