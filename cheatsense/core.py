"""CHEATSENSE engine.

A game *session log* is a list of input *events*. Each event is one
player action with a timestamp. We support two on-disk formats:

  * JSONL  - one JSON object per line
  * JSON   - a top-level list of objects, OR {"events": [...]}

Each event object may contain:
    player    (str)   required - who performed the action
    t         (float) required - timestamp in seconds (monotonic)
    action    (str)   optional - e.g. "click", "fire", "move"
    reaction  (float) optional - reaction time in seconds for this action
                                 (e.g. time from stimulus to response)
    yaw       (float) optional - view yaw in degrees (for aim-snap)
    pitch     (float) optional - view pitch in degrees (for aim-snap)
    hit       (bool)  optional - whether the action resulted in a hit

The analyzer groups events per player, sorts by time, and runs a set of
independent heuristics. Each heuristic may emit Findings with a severity
weight; weights accumulate (capped) into a 0..100 cheat-likelihood score.

Nothing here is a verdict - it is an *auditable* signal. Every finding
carries the evidence (counts, sample values) that produced it.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Tunable thresholds. Defaults are conservative, human-plausible floors
# drawn from common anti-cheat heuristics. Override per-game via analyze().
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLDS: dict[str, float] = {
    # Human visual reaction floor; sub-this responses are physiologically
    # implausible repeatedly. ~100ms is elite; we flag below 100ms.
    "min_reaction_s": 0.100,
    # How many sub-floor reactions before we care (single outliers happen).
    "min_reaction_count": 3,
    # Coefficient of variation (std/mean) of inter-action intervals below
    # which timing is suspiciously regular (macro / autoclicker).
    "max_interval_cv": 0.05,
    # Minimum number of intervals required to judge cadence regularity.
    "min_cadence_samples": 12,
    # Degrees of view change within one tick considered an instantaneous
    # snap; combined with a hit it looks like an aimbot lock.
    "aim_snap_deg": 80.0,
    # Max gap (s) between the snap and the action to count them together.
    "aim_snap_window_s": 0.060,
    "aim_snap_count": 2,
    # Sustained actions-per-minute ceiling. Pro StarCraft tops ~500-600 APM
    # in bursts; sustained >800 over a window implies injection.
    "max_apm": 800.0,
    "apm_window_s": 5.0,
    # Autoclicker: count of inter-event intervals that are byte-identical
    # (to the millisecond) - real input always jitters.
    "identical_interval_count": 5,
    "identical_interval_quantum_s": 0.001,
    # Score at/above which a player is flagged (CI gate / exit code).
    "flag_score": 50.0,
}

# Severity weight contributed to the player's score by each heuristic hit.
_WEIGHTS: dict[str, float] = {
    "inhuman_reaction": 35.0,
    "robotic_cadence": 30.0,
    "aim_snap": 40.0,
    "impossible_apm": 45.0,
    "autoclicker_interval": 25.0,
}


@dataclass
class Event:
    player: str
    t: float
    action: str = ""
    reaction: float | None = None
    yaw: float | None = None
    pitch: float | None = None
    hit: bool | None = None


@dataclass
class Finding:
    code: str
    severity: float
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlayerReport:
    player: str
    event_count: int
    score: float
    flagged: bool
    findings: list[Finding] = field(default_factory=list)


@dataclass
class AnalysisReport:
    tool: str
    version: str
    total_events: int
    players: list[PlayerReport]
    flagged_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _coerce_event(obj: dict[str, Any], line_no: int) -> Event:
    if not isinstance(obj, dict):
        raise ValueError(f"event #{line_no} is not an object: {obj!r}")
    if "player" not in obj:
        raise ValueError(f"event #{line_no} missing required 'player'")
    if "t" not in obj:
        raise ValueError(f"event #{line_no} missing required 't' (timestamp)")
    try:
        t = float(obj["t"])
    except (TypeError, ValueError):
        raise ValueError(f"event #{line_no} has non-numeric 't': {obj['t']!r}")

    def _optfloat(key: str) -> float | None:
        v = obj.get(key)
        if v is None:
            return None
        return float(v)

    return Event(
        player=str(obj["player"]),
        t=t,
        action=str(obj.get("action", "")),
        reaction=_optfloat("reaction"),
        yaw=_optfloat("yaw"),
        pitch=_optfloat("pitch"),
        hit=(bool(obj["hit"]) if obj.get("hit") is not None else None),
    )


def parse_events(text: str) -> list[Event]:
    """Parse a session log from text (JSONL or a JSON list/{events:[...]})."""
    stripped = text.strip()
    if not stripped:
        return []

    events: list[Event] = []
    # Try whole-document JSON first (list or {"events": [...]}).
    if stripped[0] in "[{":
        try:
            doc = json.loads(stripped)
        except json.JSONDecodeError:
            doc = None
        if isinstance(doc, list):
            for i, obj in enumerate(doc, 1):
                events.append(_coerce_event(obj, i))
            return events
        if isinstance(doc, dict) and isinstance(doc.get("events"), list):
            for i, obj in enumerate(doc["events"], 1):
                events.append(_coerce_event(obj, i))
            return events

    # Fall back to JSONL (one object per non-blank line).
    for i, raw in enumerate(stripped.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {i}: invalid JSON: {exc.msg}") from exc
        events.append(_coerce_event(obj, i))
    return events


# ---------------------------------------------------------------------------
# Heuristics. Each takes a player's time-sorted events + thresholds and
# returns zero or more Findings.
# ---------------------------------------------------------------------------
def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _stdev(xs: list[float], mu: float) -> float:
    if len(xs) < 2:
        return 0.0
    var = sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def _intervals(events: list[Event]) -> list[float]:
    return [
        events[i].t - events[i - 1].t
        for i in range(1, len(events))
        if events[i].t - events[i - 1].t >= 0
    ]


def _check_inhuman_reaction(events, th) -> list[Finding]:
    floor = th["min_reaction_s"]
    fast = [e.reaction for e in events if e.reaction is not None and e.reaction < floor]
    need = int(th["min_reaction_count"])
    if len(fast) >= need:
        return [
            Finding(
                code="inhuman_reaction",
                severity=_WEIGHTS["inhuman_reaction"],
                message=(
                    f"{len(fast)} reactions below the {floor*1000:.0f}ms human "
                    f"floor (fastest {min(fast)*1000:.0f}ms)"
                ),
                evidence={
                    "count": len(fast),
                    "floor_ms": floor * 1000,
                    "fastest_ms": round(min(fast) * 1000, 2),
                    "samples_ms": [round(x * 1000, 2) for x in fast[:10]],
                },
            )
        ]
    return []


def _check_robotic_cadence(events, th) -> list[Finding]:
    ivs = _intervals(events)
    if len(ivs) < int(th["min_cadence_samples"]):
        return []
    mu = _mean(ivs)
    if mu <= 0:
        return []
    cv = _stdev(ivs, mu) / mu
    if cv <= th["max_interval_cv"]:
        return [
            Finding(
                code="robotic_cadence",
                severity=_WEIGHTS["robotic_cadence"],
                message=(
                    f"action timing too regular: CV={cv:.4f} over {len(ivs)} "
                    f"intervals (mean {mu*1000:.1f}ms)"
                ),
                evidence={
                    "cv": round(cv, 5),
                    "threshold": th["max_interval_cv"],
                    "sample_count": len(ivs),
                    "mean_interval_ms": round(mu * 1000, 2),
                },
            )
        ]
    return []


def _check_aim_snap(events, th) -> list[Finding]:
    window = th["aim_snap_window_s"]
    snap = th["aim_snap_deg"]
    snaps = []
    for i in range(1, len(events)):
        a, b = events[i - 1], events[i]
        if a.yaw is None or b.yaw is None:
            continue
        dt = b.t - a.t
        if dt < 0 or dt > window:
            continue
        dyaw = abs(b.yaw - a.yaw)
        # normalize wrap-around (e.g. 359 -> 1 is 2deg, not 358)
        dyaw = min(dyaw, 360.0 - dyaw)
        dpitch = abs((b.pitch or 0.0) - (a.pitch or 0.0))
        delta = math.hypot(dyaw, dpitch)
        if delta >= snap and b.hit:
            snaps.append((round(delta, 1), round(dt * 1000, 1)))
    need = int(th["aim_snap_count"])
    if len(snaps) >= need:
        return [
            Finding(
                code="aim_snap",
                severity=_WEIGHTS["aim_snap"],
                message=(
                    f"{len(snaps)} instant view snaps >={snap:.0f} deg "
                    f"landing a hit within {window*1000:.0f}ms"
                ),
                evidence={
                    "count": len(snaps),
                    "snap_deg_threshold": snap,
                    "samples_deg_ms": snaps[:10],
                },
            )
        ]
    return []


def _check_impossible_apm(events, th) -> list[Finding]:
    window = th["apm_window_s"]
    ceiling = th["max_apm"]
    if len(events) < 2 or window <= 0:
        return []
    ts = [e.t for e in events]
    peak = 0.0
    peak_at = 0.0
    j = 0
    # sliding window: count events within [t-window, t]
    for i in range(len(ts)):
        while ts[i] - ts[j] > window:
            j += 1
        count = i - j + 1
        apm = count / window * 60.0
        if apm > peak:
            peak = apm
            peak_at = ts[i]
    if peak > ceiling:
        return [
            Finding(
                code="impossible_apm",
                severity=_WEIGHTS["impossible_apm"],
                message=(
                    f"peak {peak:.0f} APM over a {window:.0f}s window exceeds "
                    f"the {ceiling:.0f} APM ceiling"
                ),
                evidence={
                    "peak_apm": round(peak, 1),
                    "ceiling_apm": ceiling,
                    "window_s": window,
                    "peak_at_t": round(peak_at, 3),
                },
            )
        ]
    return []


def _check_autoclicker_interval(events, th) -> list[Finding]:
    ivs = _intervals(events)
    if len(ivs) < int(th["identical_interval_count"]):
        return []
    quantum = th["identical_interval_quantum_s"]
    buckets: dict[int, int] = {}
    for iv in ivs:
        key = int(round(iv / quantum))
        buckets[key] = buckets.get(key, 0) + 1
    if not buckets:
        return []
    top_key, top_count = max(buckets.items(), key=lambda kv: kv[1])
    need = int(th["identical_interval_count"])
    if top_count >= need:
        return [
            Finding(
                code="autoclicker_interval",
                severity=_WEIGHTS["autoclicker_interval"],
                message=(
                    f"{top_count} inter-action intervals identical to "
                    f"{top_key*quantum*1000:.0f}ms (no human jitter)"
                ),
                evidence={
                    "repeat_count": top_count,
                    "interval_ms": round(top_key * quantum * 1000, 3),
                    "total_intervals": len(ivs),
                },
            )
        ]
    return []


_HEURISTICS = (
    _check_inhuman_reaction,
    _check_robotic_cadence,
    _check_aim_snap,
    _check_impossible_apm,
    _check_autoclicker_interval,
)


# ---------------------------------------------------------------------------
# Top-level analysis
# ---------------------------------------------------------------------------
def analyze(
    events: Iterable[Event],
    thresholds: dict[str, float] | None = None,
) -> AnalysisReport:
    """Run all heuristics, grouped per player, and build a report."""
    from cheatsense import TOOL_NAME, TOOL_VERSION

    th = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        th.update(thresholds)

    events = list(events)
    by_player: dict[str, list[Event]] = {}
    for e in events:
        by_player.setdefault(e.player, []).append(e)

    flag_score = th["flag_score"]
    players: list[PlayerReport] = []
    for player in sorted(by_player):
        evs = sorted(by_player[player], key=lambda e: e.t)
        findings: list[Finding] = []
        for heuristic in _HEURISTICS:
            findings.extend(heuristic(evs, th))
        # Accumulate weighted severity, capped at 100.
        score = min(100.0, sum(f.severity for f in findings))
        players.append(
            PlayerReport(
                player=player,
                event_count=len(evs),
                score=round(score, 1),
                flagged=score >= flag_score,
                findings=findings,
            )
        )

    # Most suspicious first.
    players.sort(key=lambda p: p.score, reverse=True)
    flagged = sum(1 for p in players if p.flagged)
    return AnalysisReport(
        tool=TOOL_NAME,
        version=TOOL_VERSION,
        total_events=len(events),
        players=players,
        flagged_count=flagged,
    )


def analyze_file(
    path: str,
    thresholds: dict[str, float] | None = None,
) -> AnalysisReport:
    """Read a session log file and analyze it."""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return analyze(parse_events(text), thresholds)
