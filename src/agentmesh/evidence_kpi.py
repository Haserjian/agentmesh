"""Compute evidence-chain KPI from merged PR check runs."""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


GITHUB_API = "https://api.github.com"
DEFAULT_REQUIRED_CHECKS = ("lineage", "assay-gate", "assay-verify")


def parse_iso8601(value: str) -> datetime:
    """Parse GitHub-style ISO8601 timestamps into UTC datetime."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def select_latest_check_runs(check_runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Select latest check run per check name by numeric run id."""
    latest: dict[str, dict[str, Any]] = {}
    for run in check_runs:
        name = str(run.get("name", "")).strip()
        if not name:
            continue
        rid = int(run.get("id") or 0)
        curr = latest.get(name)
        curr_id = int(curr.get("id") or 0) if curr else -1
        if rid >= curr_id:
            latest[name] = run
    return latest


def evaluate_required_checks(
    latest_runs: dict[str, dict[str, Any]],
    required_checks: list[str],
) -> tuple[bool, dict[str, str], list[str], list[str]]:
    """Evaluate required check run conclusions."""
    statuses: dict[str, str] = {}
    missing: list[str] = []
    failed: list[str] = []

    for name in required_checks:
        run = latest_runs.get(name)
        if run is None:
            statuses[name] = "missing"
            missing.append(name)
            continue

        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        if status != "completed":
            statuses[name] = f"incomplete:{status or 'unknown'}"
            failed.append(name)
            continue
        if conclusion != "success":
            statuses[name] = conclusion or "unknown"
            failed.append(name)
            continue
        statuses[name] = "success"

    complete = not missing and not failed
    return complete, statuses, missing, failed


def compute_coverage(passing: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((passing / total) * 100.0, 2)


def _api_get_json(token: str, url: str) -> Any:
    req = urllib.request.Request(
        url=url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agentmesh-evidence-kpi/1",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _list_merged_prs(
    token: str,
    owner: str,
    repo: str,
    base: str,
    since: datetime,
    max_prs: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    per_page = 100

    while len(out) < max_prs:
        q = urllib.parse.urlencode(
            {
                "state": "closed",
                "base": base,
                "sort": "updated",
                "direction": "desc",
                "per_page": per_page,
                "page": page,
            }
        )
        url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls?{q}"
        pulls = _api_get_json(token, url)
        if not pulls:
            break

        for pr in pulls:
            merged_at = pr.get("merged_at")
            if not merged_at:
                continue
            merged_dt = parse_iso8601(str(merged_at))
            if merged_dt < since:
                continue
            out.append(
                {
                    "number": pr.get("number"),
                    "title": pr.get("title", ""),
                    "url": pr.get("html_url", ""),
                    "merged_at": merged_at,
                    "head_sha": pr.get("head", {}).get("sha", ""),
                }
            )
            if len(out) >= max_prs:
                break

        if len(pulls) < per_page:
            break
        page += 1

    out.sort(key=lambda p: p["merged_at"], reverse=True)
    return out


def _get_check_runs(
    token: str,
    owner: str,
    repo: str,
    sha: str,
) -> list[dict[str, Any]]:
    q = urllib.parse.urlencode({"per_page": 100})
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits/{sha}/check-runs?{q}"
    payload = _api_get_json(token, url)
    runs = payload.get("check_runs", [])
    return runs if isinstance(runs, list) else []


def _render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Evidence Chain KPI")
    lines.append("")
    lines.append(f"- Repo: `{report['repo']}`")
    lines.append(f"- Base branch: `{report['base']}`")
    lines.append(f"- Lookback: last `{report['window_days']}` day(s)")
    lines.append(
        f"- KPI: **{report['passing_prs']}/{report['ai_prs_total']} ({report['coverage_pct']}%)**"
    )
    lines.append(f"- Generated: `{report['generated_at']}`")
    lines.append("")

    checks = report["required_checks"]
    header = "| PR | Merged | Complete | " + " | ".join(checks) + " |"
    sep = "|" + "---|" * (3 + len(checks))
    lines.append(header)
    lines.append(sep)
    for pr in report["prs"][:25]:
        row = [
            f"[#{pr['number']}]({pr['url']})",
            str(pr["merged_at"])[:10],
            "yes" if pr["complete_verified_chain"] else "no",
        ]
        row.extend(pr["check_statuses"].get(c, "missing") for c in checks)
        lines.append("| " + " | ".join(row) + " |")

    if report["errors"]:
        lines.append("")
        lines.append("## Errors")
        for err in report["errors"]:
            lines.append(f"- {err}")

    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    repo_full = args.repo or os.getenv("GITHUB_REPOSITORY", "")
    if "/" not in repo_full:
        print("ERROR: repo must be in owner/repo form or GITHUB_REPOSITORY must be set")
        return 2

    token = os.getenv(args.token_env, "").strip()
    if not token:
        print(f"ERROR: required token env var {args.token_env!r} is empty")
        return 2

    owner, repo = repo_full.split("/", 1)
    required_checks = args.required_check or list(DEFAULT_REQUIRED_CHECKS)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)

    prs = _list_merged_prs(token, owner, repo, args.base, since, args.max_prs)
    evaluated: list[dict[str, Any]] = []
    errors: list[str] = []
    passing = 0

    for pr in prs:
        sha = pr.get("head_sha", "")
        if not sha:
            entry = dict(pr)
            entry["complete_verified_chain"] = False
            entry["check_statuses"] = {name: "missing_sha" for name in required_checks}
            entry["missing_checks"] = list(required_checks)
            entry["failed_checks"] = []
            evaluated.append(entry)
            continue

        try:
            runs = _get_check_runs(token, owner, repo, sha)
            latest = select_latest_check_runs(runs)
            complete, statuses, missing, failed = evaluate_required_checks(latest, required_checks)
        except Exception as exc:  # pragma: no cover - network failure path
            complete = False
            statuses = {name: "api_error" for name in required_checks}
            missing = list(required_checks)
            failed = []
            errors.append(f"PR #{pr['number']}: {exc}")

        if complete:
            passing += 1

        entry = dict(pr)
        entry["complete_verified_chain"] = complete
        entry["check_statuses"] = statuses
        entry["missing_checks"] = missing
        entry["failed_checks"] = failed
        evaluated.append(entry)

    total = len(evaluated)
    report: dict[str, Any] = {
        "repo": repo_full,
        "base": args.base,
        "window_days": args.days,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "required_checks": required_checks,
        "ai_prs_total": total,
        "passing_prs": passing,
        "coverage_pct": compute_coverage(passing, total),
        "prs": evaluated,
        "errors": errors,
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2) + "\n")

    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_render_markdown(report))

    print(
        f"Evidence KPI: {report['passing_prs']}/{report['ai_prs_total']} "
        f"({report['coverage_pct']}%) -- {out_json}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute evidence-chain KPI from merged PRs")
    parser.add_argument("--repo", default="", help="owner/repo (default: $GITHUB_REPOSITORY)")
    parser.add_argument("--base", default="main", help="base branch (default: main)")
    parser.add_argument("--days", type=int, default=7, help="lookback window in days")
    parser.add_argument(
        "--required-check",
        action="append",
        dest="required_check",
        default=[],
        help="required check run name (repeatable). Defaults: lineage + assay-gate + assay-verify",
    )
    parser.add_argument("--max-prs", type=int, default=200, help="max PRs to inspect")
    parser.add_argument("--token-env", default="GITHUB_TOKEN", help="env var holding GitHub token")
    parser.add_argument("--out-json", default=".kpi/evidence-chain-kpi.json", help="output JSON path")
    parser.add_argument("--out-md", default=".kpi/evidence-chain-kpi.md", help="output Markdown path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.days <= 0:
        parser.error("--days must be > 0")
    if args.max_prs <= 0:
        parser.error("--max-prs must be > 0")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
