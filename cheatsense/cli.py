"""CHEATSENSE command-line interface.

Examples:
    # Analyze a session log and print a table
    cheatsense scan demos/01-basic/session.jsonl

    # Machine-readable output for CI / piping
    cheatsense scan session.jsonl --format json | jq '.flagged_count'

    # Tighten the cheat-flag threshold and the APM ceiling
    cheatsense scan session.jsonl --flag-score 40 --max-apm 600

Exit codes:
    0  no players flagged
    1  one or more players flagged (use as a CI gate)
    2  usage / input error
"""
from __future__ import annotations

import argparse
import json
import sys

from cheatsense import TOOL_NAME, TOOL_VERSION, DEFAULT_THRESHOLDS
from cheatsense.core import analyze_file


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Anti-cheat analyzer: ingest game session input logs and flag "
            "anomalous input signatures (inhuman reaction time, robotic "
            "cadence, aim snaps, impossible APM, autoclicker intervals)."
        ),
        epilog=(
            "example: cheatsense scan session.jsonl --format json | "
            "jq '.players[0]'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{TOOL_NAME} {TOOL_VERSION}",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="output format (default: table)",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser(
        "scan",
        help="analyze a session log (JSONL or JSON) for cheat signatures",
        description="Analyze a game session input log for anomalous signatures.",
    )
    scan.add_argument(
        "logfile",
        help="path to the session log (.jsonl lines, or a JSON list/object)",
    )
    scan.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="output format (default: table)",
    )
    scan.add_argument(
        "--flag-score",
        type=float,
        default=DEFAULT_THRESHOLDS["flag_score"],
        metavar="N",
        help="cheat-likelihood score (0-100) at which a player is flagged "
        f"(default: {DEFAULT_THRESHOLDS['flag_score']:g})",
    )
    scan.add_argument(
        "--min-reaction-ms",
        type=float,
        default=DEFAULT_THRESHOLDS["min_reaction_s"] * 1000,
        metavar="MS",
        help="human reaction-time floor in ms "
        f"(default: {DEFAULT_THRESHOLDS['min_reaction_s']*1000:g})",
    )
    scan.add_argument(
        "--max-apm",
        type=float,
        default=DEFAULT_THRESHOLDS["max_apm"],
        metavar="APM",
        help="sustained actions-per-minute ceiling "
        f"(default: {DEFAULT_THRESHOLDS['max_apm']:g})",
    )
    scan.add_argument(
        "--max-interval-cv",
        type=float,
        default=DEFAULT_THRESHOLDS["max_interval_cv"],
        metavar="CV",
        help="max coefficient-of-variation of action intervals before timing "
        f"is robotic (default: {DEFAULT_THRESHOLDS['max_interval_cv']:g})",
    )
    return parser


def _render_table(report) -> str:
    lines: list[str] = []
    lines.append(f"{report.tool} {report.version}")
    lines.append(
        f"events={report.total_events}  players={len(report.players)}  "
        f"flagged={report.flagged_count}"
    )
    lines.append("")
    header = f"{'PLAYER':<16} {'EVENTS':>7} {'SCORE':>6} {'FLAG':>5}  FINDINGS"
    lines.append(header)
    lines.append("-" * len(header))
    if not report.players:
        lines.append("(no events)")
    for p in report.players:
        codes = ",".join(f.code for f in p.findings) or "-"
        flag = "YES" if p.flagged else ""
        lines.append(
            f"{p.player:<16} {p.event_count:>7} {p.score:>6.1f} {flag:>5}  {codes}"
        )
    # Detail block for flagged players.
    flagged = [p for p in report.players if p.flagged]
    if flagged:
        lines.append("")
        lines.append("DETAIL")
        for p in flagged:
            lines.append(f"  [{p.player}] score={p.score:.1f}")
            for f in p.findings:
                lines.append(f"    - {f.code} (+{f.severity:g}): {f.message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    thresholds = {
        "flag_score": args.flag_score,
        "min_reaction_s": args.min_reaction_ms / 1000.0,
        "max_apm": args.max_apm,
        "max_interval_cv": args.max_interval_cv,
    }

    try:
        report = analyze_file(args.logfile, thresholds)
    except FileNotFoundError:
        print(f"{TOOL_NAME}: error: no such file: {args.logfile}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"{TOOL_NAME}: error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(_render_table(report))

    # Non-zero exit when any player is flagged -> usable as a CI gate.
    return 1 if report.flagged_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
