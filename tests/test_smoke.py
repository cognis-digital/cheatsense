"""Smoke tests for CHEATSENSE: import the engine, run it on the demo log,
and assert real detection behavior. No network, stdlib only.
"""
import json
import os
import subprocess
import sys

import cheatsense
from cheatsense.core import parse_events, analyze, analyze_file, Event

DEMO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "demos",
    "01-basic",
    "session.jsonl",
)


def test_exports():
    assert cheatsense.TOOL_NAME == "cheatsense"
    assert isinstance(cheatsense.TOOL_VERSION, str)
    assert cheatsense.TOOL_VERSION.count(".") == 2


def test_parse_jsonl_demo():
    with open(DEMO, encoding="utf-8") as fh:
        events = parse_events(fh.read())
    assert len(events) == 26
    assert all(isinstance(e, Event) for e in events)
    players = {e.player for e in events}
    assert players == {"honest_hank", "bot_betty", "aimbot_alice"}


def test_parse_supports_json_list():
    text = '[{"player":"p","t":0.0},{"player":"p","t":0.1}]'
    events = parse_events(text)
    assert len(events) == 2
    assert events[0].player == "p"


def test_parse_rejects_missing_fields():
    import pytest

    with pytest.raises(ValueError):
        parse_events('{"t": 1.0}')  # missing player
    with pytest.raises(ValueError):
        parse_events('{"player": "x"}')  # missing t


def _player(report, name):
    return next(p for p in report.players if p.player == name)


def test_analyze_demo_flags_cheaters_not_honest():
    report = analyze_file(DEMO)
    assert report.total_events == 26
    assert report.flagged_count == 2

    hank = _player(report, "honest_hank")
    assert hank.score == 0.0
    assert hank.flagged is False
    assert hank.findings == []

    betty = _player(report, "bot_betty")
    assert betty.flagged is True
    codes = {f.code for f in betty.findings}
    assert "robotic_cadence" in codes
    assert "autoclicker_interval" in codes

    alice = _player(report, "aimbot_alice")
    assert alice.flagged is True
    codes = {f.code for f in alice.findings}
    assert "inhuman_reaction" in codes
    assert "aim_snap" in codes


def test_findings_carry_evidence():
    report = analyze_file(DEMO)
    alice = _player(report, "aimbot_alice")
    react = next(f for f in alice.findings if f.code == "inhuman_reaction")
    assert react.evidence["count"] >= 3
    assert react.evidence["fastest_ms"] < 100


def test_impossible_apm_detected():
    # 50 actions inside 1 second -> 3000 APM over a 5s window.
    events = [Event(player="speedy", t=i * 0.02) for i in range(50)]
    report = analyze(events)
    speedy = _player(report, "speedy")
    codes = {f.code for f in speedy.findings}
    assert "impossible_apm" in codes
    assert speedy.flagged is True


def test_threshold_override_changes_outcome():
    # With an absurdly high flag_score, nobody is flagged.
    report = analyze_file(DEMO, {"flag_score": 999.0})
    assert report.flagged_count == 0
    assert all(not p.flagged for p in report.players)


def test_score_is_capped_at_100():
    report = analyze_file(DEMO)
    assert all(0.0 <= p.score <= 100.0 for p in report.players)


def test_to_dict_is_json_serializable():
    report = analyze_file(DEMO)
    blob = json.dumps(report.to_dict())
    parsed = json.loads(blob)
    assert parsed["tool"] == "cheatsense"
    assert parsed["flagged_count"] == 2


def test_cli_json_and_exit_code():
    proc = subprocess.run(
        [sys.executable, "-m", "cheatsense", "scan", DEMO, "--format", "json"],
        capture_output=True,
        text=True,
    )
    # Cheaters present -> exit 1 (CI gate trips).
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["flagged_count"] == 2
    assert data["tool"] == "cheatsense"


def test_cli_clean_log_exits_zero(tmp_path):
    clean = tmp_path / "clean.jsonl"
    lines = [
        {"player": "hank", "t": round(i * 0.7 + (i % 3) * 0.13, 3),
         "reaction": 0.25, "yaw": float(i * 7 % 40)}
        for i in range(6)
    ]
    clean.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "cheatsense", "scan", str(clean)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "flagged=0" in proc.stdout


def test_cli_version():
    proc = subprocess.run(
        [sys.executable, "-m", "cheatsense", "--version"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "cheatsense" in proc.stdout
