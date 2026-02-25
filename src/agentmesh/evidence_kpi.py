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


def parse_date_or_datetime_utc(value: str) -> datetime:
    """Parse YYYY-MM-DD or ISO8601 into UTC datetime."""
    raw = value.strip()
    if not raw:
        raise ValueError("empty date")
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return parse_iso8601(raw)


def normalize_window_days(
    primary_days: int,
    extra_slices: list[int],
    include_default_slices: bool,
) -> list[int]:
    """Normalize and de-duplicate window sizes."""
    out = {primary_days}
    if include_default_slices:
        out.update({7, 30})
    out.update(extra_slices)
    return sorted(out)


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


def _evaluate_merged_prs(
    token: str,
    owner: str,
    repo: str,
    base: str,
    since: datetime,
    max_prs: int,
    required_checks: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Evaluate merged PRs from `since` for required check completeness."""
    prs = _list_merged_prs(token, owner, repo, base, since, max_prs)
    evaluated: list[dict[str, Any]] = []
    errors: list[str] = []

    for pr in prs:
        sha = pr.get("head_sha", "")
        if not sha:
            entry = dict(pr)
            entry["complete_verified_chain"] = False
            entry["check_statuses"] = {name: "missing_sha" for name in required_checks}
            entry["missing_checks"] = list(required_checks)
            entry["failed_checks"] = []
            entry["check_attempt_counts"] = {name: 0 for name in required_checks}
            entry["rerun_checks"] = []
            entry["rerun_detected"] = False
            evaluated.append(entry)
            continue

        try:
            runs = _get_check_runs(token, owner, repo, sha)
            attempt_counts, rerun_checks = _summarize_check_attempts(runs, required_checks)
            latest = select_latest_check_runs(runs)
            complete, statuses, missing, failed = evaluate_required_checks(latest, required_checks)
        except Exception as exc:  # pragma: no cover - network failure path
            attempt_counts = {name: 0 for name in required_checks}
            rerun_checks = []
            complete = False
            statuses = {name: "api_error" for name in required_checks}
            missing = list(required_checks)
            failed = []
            errors.append(f"PR #{pr['number']}: {exc}")

        entry = dict(pr)
        entry["complete_verified_chain"] = complete
        entry["check_statuses"] = statuses
        entry["missing_checks"] = missing
        entry["failed_checks"] = failed
        entry["check_attempt_counts"] = attempt_counts
        entry["rerun_checks"] = rerun_checks
        entry["rerun_detected"] = bool(rerun_checks)
        evaluated.append(entry)

    return evaluated, errors


def _subset_since(prs: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    """Filter PR records by merged timestamp >= since."""
    return [pr for pr in prs if parse_iso8601(str(pr["merged_at"])) >= since]


def _summarize_subset(prs: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize passing/total/coverage for a PR subset."""
    total = len(prs)
    passing = sum(1 for pr in prs if pr.get("complete_verified_chain"))
    return {
        "ai_prs_total": total,
        "passing_prs": passing,
        "coverage_pct": compute_coverage(passing, total),
    }


def _summarize_check_pass_rates(
    prs: list[dict[str, Any]],
    required_checks: list[str],
) -> dict[str, dict[str, Any]]:
    """Summarize per-check pass rates for a PR subset."""
    total = len(prs)
    out: dict[str, dict[str, Any]] = {}
    for check in required_checks:
        passing = 0
        missing = 0
        failed = 0
        incomplete = 0
        for pr in prs:
            statuses = pr.get("check_statuses")
            if isinstance(statuses, dict):
                status = str(statuses.get(check, "missing"))
            else:
                status = "missing"
            if status == "success":
                passing += 1
            elif status == "missing":
                missing += 1
            elif status.startswith("incomplete:"):
                incomplete += 1
                failed += 1
            else:
                failed += 1
        out[check] = {
            "passing_prs": passing,
            "ai_prs_total": total,
            "coverage_pct": compute_coverage(passing, total),
            "missing_prs": missing,
            "failed_prs": failed,
            "incomplete_prs": incomplete,
        }
    return out


def _summarize_check_attempts(
    check_runs: list[dict[str, Any]],
    required_checks: list[str],
) -> tuple[dict[str, int], list[str]]:
    """Count check attempts for required checks and flag reruns."""
    counts = {name: 0 for name in required_checks}
    required = set(required_checks)
    for run in check_runs:
        name = str(run.get("name", "")).strip()
        if name in required:
            counts[name] += 1
    rerun_checks = sorted(name for name, count in counts.items() if count > 1)
    return counts, rerun_checks


def _summarize_reliability(
    prs: list[dict[str, Any]],
    required_checks: list[str],
) -> dict[str, Any]:
    """Summarize rerun/reliability metrics for a PR subset."""
    total = len(prs)
    prs_with_reruns = sum(1 for pr in prs if bool(pr.get("rerun_detected")))
    by_check: dict[str, dict[str, Any]] = {}
    for check in required_checks:
        rerun_prs = 0
        for pr in prs:
            attempts = pr.get("check_attempt_counts")
            if isinstance(attempts, dict) and int(attempts.get(check, 0)) > 1:
                rerun_prs += 1
        by_check[check] = {
            "rerun_prs": rerun_prs,
            "ai_prs_total": total,
            "rerun_rate_pct": compute_coverage(rerun_prs, total),
        }
    return {
        "prs_with_reruns": prs_with_reruns,
        "ai_prs_total": total,
        "rerun_rate_pct": compute_coverage(prs_with_reruns, total),
        "by_check": by_check,
    }


def build_trend_point(
    report: dict[str, Any],
    *,
    run_id: str,
    run_attempt: str,
    run_url: str,
    workflow: str,
    event_name: str,
    ref_name: str,
) -> dict[str, Any]:
    """Build a normalized trend point from a KPI report."""
    since = report.get("since_enforcement")
    since_coverage = 0.0
    since_passing = 0
    since_total = 0
    since_check_rates: dict[str, Any] = {}
    since_reliability: dict[str, Any] = {}
    if isinstance(since, dict):
        since_coverage = float(since.get("coverage_pct", 0.0))
        since_passing = int(since.get("passing_prs", 0))
        since_total = int(since.get("ai_prs_total", 0))
        raw_rates = since.get("check_pass_rates")
        if isinstance(raw_rates, dict):
            since_check_rates = raw_rates
        raw_reliability = since.get("reliability")
        if isinstance(raw_reliability, dict):
            since_reliability = raw_reliability

    check_rates = report.get("check_pass_rates")
    if not isinstance(check_rates, dict):
        check_rates = {}
    reliability = report.get("reliability")
    if not isinstance(reliability, dict):
        reliability = {}

    return {
        "generated_at": str(report.get("generated_at", "")),
        "repo": str(report.get("repo", "")),
        "base": str(report.get("base", "")),
        "window_days": int(report.get("window_days", 0)),
        "coverage_pct": float(report.get("coverage_pct", 0.0)),
        "passing_prs": int(report.get("passing_prs", 0)),
        "ai_prs_total": int(report.get("ai_prs_total", 0)),
        "enforcement_date": str(report.get("enforcement_date", "")),
        "since_enforcement_coverage_pct": since_coverage,
        "since_enforcement_passing_prs": since_passing,
        "since_enforcement_ai_prs_total": since_total,
        "check_pass_rates": check_rates,
        "reliability": reliability,
        "since_enforcement_check_pass_rates": since_check_rates,
        "since_enforcement_reliability": since_reliability,
        "run_id": str(run_id),
        "run_attempt": str(run_attempt),
        "run_url": str(run_url),
        "workflow": str(workflow),
        "event_name": str(event_name),
        "ref_name": str(ref_name),
    }


def merge_trend_history(
    existing_points: list[dict[str, Any]],
    point: dict[str, Any],
    *,
    max_points: int = 365,
) -> list[dict[str, Any]]:
    """Merge one trend point into history with run-id dedupe and size cap."""
    merged: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()
    for row in existing_points:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("run_id", "")).strip()
        if rid and rid in seen_run_ids:
            continue
        if rid:
            seen_run_ids.add(rid)
        merged.append(row)

    point_run_id = str(point.get("run_id", "")).strip()
    if point_run_id:
        replaced = False
        for idx, row in enumerate(merged):
            if str(row.get("run_id", "")).strip() == point_run_id:
                merged[idx] = point
                replaced = True
                break
        if not replaced:
            merged.append(point)
    else:
        merged.append(point)

    merged.sort(key=lambda r: str(r.get("generated_at", "")))
    if max_points > 0 and len(merged) > max_points:
        merged = merged[-max_points:]
    return merged


def _render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Evidence Chain KPI")
    lines.append("")
    lines.append(f"- Repo: `{report['repo']}`")
    lines.append(f"- Base branch: `{report['base']}`")
    lines.append(f"- Lookback: last `{report['window_days']}` day(s)")
    lines.append(f"- Primary KPI: **{report['passing_prs']}/{report['ai_prs_total']} ({report['coverage_pct']}%)**")
    if report.get("enforcement_date"):
        since = report.get("since_enforcement") or {}
        lines.append(
            f"- Since enforcement (`{report['enforcement_date']}`): "
            f"**{since.get('passing_prs', 0)}/{since.get('ai_prs_total', 0)} "
            f"({since.get('coverage_pct', 0.0)}%)**"
        )
    lines.append(f"- Generated: `{report['generated_at']}`")
    lines.append("")
    lines.append("## Coverage Slices")
    lines.append("")
    lines.append("| Slice | Passing | Total | Coverage |")
    lines.append("|---|---:|---:|---:|")
    lines.append(
        f"| primary ({report['window_days']}d) | {report['passing_prs']} | "
        f"{report['ai_prs_total']} | {report['coverage_pct']}% |"
    )
    for key in sorted((report.get("slices") or {}).keys(), key=lambda x: int(str(x).rstrip("d"))):
        item = report["slices"][key]
        lines.append(
            f"| {key} | {item['passing_prs']} | {item['ai_prs_total']} | {item['coverage_pct']}% |"
        )
    if report.get("enforcement_date"):
        since = report.get("since_enforcement") or {}
        lines.append(
            f"| since_enforcement | {since.get('passing_prs', 0)} | "
            f"{since.get('ai_prs_total', 0)} | {since.get('coverage_pct', 0.0)}% |"
        )
    lines.append("")

    checks = report["required_checks"]
    lines.append("## Required Check Pass Rates")
    lines.append("")
    check_rates = report.get("check_pass_rates") or {}
    since_rates = (report.get("since_enforcement") or {}).get("check_pass_rates") if report.get("enforcement_date") else None
    if since_rates:
        lines.append("| Check | Primary pass/total | Primary coverage | Since enforcement pass/total | Since enforcement coverage |")
        lines.append("|---|---:|---:|---:|---:|")
        for check in checks:
            primary = check_rates.get(check) or {}
            since = since_rates.get(check) or {}
            lines.append(
                f"| {check} | {primary.get('passing_prs', 0)}/{primary.get('ai_prs_total', 0)} | "
                f"{primary.get('coverage_pct', 0.0)}% | {since.get('passing_prs', 0)}/{since.get('ai_prs_total', 0)} | "
                f"{since.get('coverage_pct', 0.0)}% |"
            )
    else:
        lines.append("| Check | Pass/total | Coverage |")
        lines.append("|---|---:|---:|")
        for check in checks:
            primary = check_rates.get(check) or {}
            lines.append(
                f"| {check} | {primary.get('passing_prs', 0)}/{primary.get('ai_prs_total', 0)} | "
                f"{primary.get('coverage_pct', 0.0)}% |"
            )
    lines.append("")

    lines.append("## Check Reliability")
    lines.append("")
    reliability = report.get("reliability") or {}
    lines.append(
        f"- PRs with reruns: **{reliability.get('prs_with_reruns', 0)}/"
        f"{reliability.get('ai_prs_total', 0)} ({reliability.get('rerun_rate_pct', 0.0)}%)**"
    )
    since_reliability = (report.get("since_enforcement") or {}).get("reliability") if report.get("enforcement_date") else None
    if since_reliability:
        lines.append(
            f"- Since enforcement reruns: **{since_reliability.get('prs_with_reruns', 0)}/"
            f"{since_reliability.get('ai_prs_total', 0)} ({since_reliability.get('rerun_rate_pct', 0.0)}%)**"
        )
    lines.append("")
    lines.append("| Check | Primary rerun PRs | Primary rerun rate | Since enforcement rerun PRs | Since enforcement rerun rate |")
    lines.append("|---|---:|---:|---:|---:|")
    primary_by_check = reliability.get("by_check") if isinstance(reliability, dict) else {}
    if not isinstance(primary_by_check, dict):
        primary_by_check = {}
    since_by_check = since_reliability.get("by_check") if isinstance(since_reliability, dict) else {}
    if not isinstance(since_by_check, dict):
        since_by_check = {}
    for check in checks:
        primary = primary_by_check.get(check) or {}
        since = since_by_check.get(check) or {}
        lines.append(
            f"| {check} | {primary.get('rerun_prs', 0)} | {primary.get('rerun_rate_pct', 0.0)}% | "
            f"{since.get('rerun_prs', 0)} | {since.get('rerun_rate_pct', 0.0)}% |"
        )
    lines.append("")

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
    window_days = normalize_window_days(args.days, args.slice_days, args.include_default_slices)
    now = datetime.now(timezone.utc)
    primary_since = now - timedelta(days=args.days)
    fetch_since = now - timedelta(days=max(window_days))

    enforcement_date = ""
    enforcement_dt: datetime | None = None
    if args.enforcement_date:
        enforcement_dt = parse_date_or_datetime_utc(args.enforcement_date)
        enforcement_date = enforcement_dt.isoformat().replace("+00:00", "Z")
        if enforcement_dt < fetch_since:
            fetch_since = enforcement_dt

    evaluated, errors = _evaluate_merged_prs(
        token=token,
        owner=owner,
        repo=repo,
        base=args.base,
        since=fetch_since,
        max_prs=args.max_prs,
        required_checks=required_checks,
    )

    primary_prs = _subset_since(evaluated, primary_since)
    primary_summary = _summarize_subset(primary_prs)
    primary_check_pass_rates = _summarize_check_pass_rates(primary_prs, required_checks)
    primary_reliability = _summarize_reliability(primary_prs, required_checks)

    slices: dict[str, dict[str, Any]] = {}
    for days in window_days:
        subset = _subset_since(evaluated, now - timedelta(days=days))
        summary = _summarize_subset(subset)
        summary["window_days"] = days
        slices[f"{days}d"] = summary

    since_enforcement: dict[str, Any] | None = None
    if enforcement_dt is not None:
        subset = _subset_since(evaluated, enforcement_dt)
        since_enforcement = _summarize_subset(subset)
        since_enforcement["enforcement_date"] = enforcement_date
        since_enforcement["check_pass_rates"] = _summarize_check_pass_rates(subset, required_checks)
        since_enforcement["reliability"] = _summarize_reliability(subset, required_checks)

    report: dict[str, Any] = {
        "repo": repo_full,
        "base": args.base,
        "window_days": args.days,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "required_checks": required_checks,
        "ai_prs_total": primary_summary["ai_prs_total"],
        "passing_prs": primary_summary["passing_prs"],
        "coverage_pct": primary_summary["coverage_pct"],
        "check_pass_rates": primary_check_pass_rates,
        "reliability": primary_reliability,
        "slices": slices,
        "enforcement_date": enforcement_date,
        "since_enforcement": since_enforcement,
        "all_prs_evaluated": len(evaluated),
        "prs": primary_prs,
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
        "--slice-days",
        type=int,
        action="append",
        default=[],
        help="additional lookback slices in days for reporting (repeatable)",
    )
    parser.add_argument(
        "--include-default-slices",
        action="store_true",
        help="include standard 7d and 30d slices in report output",
    )
    parser.add_argument(
        "--enforcement-date",
        default="",
        help="enforcement start date (YYYY-MM-DD or ISO8601) for since_enforcement metric",
    )
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
    for day in args.slice_days:
        if day <= 0:
            parser.error("--slice-days values must be > 0")
    if args.max_prs <= 0:
        parser.error("--max-prs must be > 0")
    if args.enforcement_date:
        try:
            parse_date_or_datetime_utc(args.enforcement_date)
        except ValueError as exc:
            parser.error(f"--enforcement-date invalid: {exc}")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
