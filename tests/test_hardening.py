"""Tests for hardened input validation, error handling, and edge cases.

All tests target the new guard code added in the hardening pass.
No existing tests are modified or removed.
"""
from __future__ import annotations

import subprocess
import sys
import os

import pytest

from cheatsense.core import parse_events, analyze, analyze_file, Event


# ---------------------------------------------------------------------------
# core.py — _coerce_event / parse_events hardening
# ---------------------------------------------------------------------------

class TestCoerceEventEdgeCases:
    def test_non_numeric_reaction_raises(self):
        """Non-numeric optional float field must raise ValueError with message."""
        with pytest.raises(ValueError, match="reaction.*non-numeric"):
            parse_events('{"player": "p", "t": 1.0, "reaction": "fast"}')

    def test_non_numeric_yaw_raises(self):
        with pytest.raises(ValueError, match="yaw.*non-numeric"):
            parse_events('{"player": "p", "t": 1.0, "yaw": "left"}')

    def test_infinite_t_raises(self):
        """Infinite timestamps should be rejected."""
        with pytest.raises(ValueError, match="finite"):
            parse_events('{"player": "p", "t": 1e999}')

    def test_nan_t_raises(self):
        """NaN timestamps (injected as Python float) must be rejected."""
        from cheatsense.core import _coerce_event
        with pytest.raises(ValueError, match="finite"):
            _coerce_event({"player": "p", "t": float("nan")}, 1)

    def test_inf_t_raises(self):
        """Infinite timestamps must be rejected."""
        from cheatsense.core import _coerce_event
        with pytest.raises(ValueError, match="finite"):
            _coerce_event({"player": "p", "t": float("inf")}, 1)

    def test_dict_player_raises(self):
        """player field must be a scalar."""
        with pytest.raises(ValueError, match="player"):
            parse_events('{"player": {}, "t": 0.0}')

    def test_list_player_raises(self):
        """player as a list must raise."""
        with pytest.raises(ValueError, match="player"):
            parse_events('{"player": [], "t": 0.0}')


class TestAnalyzeFileHardening:
    def test_unicode_decode_error_becomes_value_error(self, tmp_path):
        """Binary (non-UTF-8) file should raise ValueError, not UnicodeDecodeError."""
        bad = tmp_path / "bad.jsonl"
        bad.write_bytes(b"\xff\xfe\x00\x01")
        with pytest.raises(ValueError, match="UTF-8"):
            analyze_file(str(bad))

    def test_missing_file_raises_file_not_found(self, tmp_path):
        """analyze_file on a nonexistent path must raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            analyze_file(str(tmp_path / "ghost.jsonl"))

    def test_empty_file_returns_empty_report(self, tmp_path):
        """An empty file (zero events) must return a valid report with no players."""
        empty = tmp_path / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        report = analyze_file(str(empty))
        assert report.total_events == 0
        assert report.players == []
        assert report.flagged_count == 0


# ---------------------------------------------------------------------------
# core.py — analyze() edge cases
# ---------------------------------------------------------------------------

class TestAnalyzeEdgeCases:
    def test_single_event_no_crash(self):
        """One event per player must not crash any heuristic."""
        report = analyze([Event(player="solo", t=0.0)])
        assert report.total_events == 1
        assert len(report.players) == 1
        assert report.players[0].score == 0.0

    def test_empty_events_list(self):
        """analyze() with an empty event list returns a valid empty report."""
        report = analyze([])
        assert report.total_events == 0
        assert report.players == []
        assert report.flagged_count == 0

    def test_events_with_equal_timestamps(self):
        """Duplicate timestamps (zero intervals) must not divide by zero."""
        events = [Event(player="p", t=1.0) for _ in range(5)]
        report = analyze(events)
        assert report.players[0].score >= 0.0

    def test_score_always_non_negative(self):
        """Score must never be negative regardless of thresholds."""
        events = [Event(player="p", t=float(i)) for i in range(5)]
        report = analyze(events, {"flag_score": 0.0})
        for p in report.players:
            assert p.score >= 0.0


# ---------------------------------------------------------------------------
# cli.py — threshold validation
# ---------------------------------------------------------------------------

class TestCLIThresholdValidation:
    def _run(self, *extra_args, logfile=None):
        """Run the CLI via subprocess; returns (returncode, stdout, stderr)."""
        if logfile is None:
            # Use a minimal valid log.
            import tempfile as _tf
            fd, path = _tf.mkstemp(suffix=".jsonl")
            os.write(fd, b'{"player":"p","t":0.0}\n')
            os.close(fd)
            logfile = path
            cleanup = True
        else:
            cleanup = False
        proc = subprocess.run(
            [sys.executable, "-m", "cheatsense", "scan", logfile] + list(extra_args),
            capture_output=True, text=True,
            cwd=r"C:\Users\user\AppData\Local\Temp\cheatsense-harden",
        )
        if cleanup:
            os.unlink(logfile)
        return proc.returncode, proc.stdout, proc.stderr

    def test_flag_score_above_100_rejected(self):
        rc, _, err = self._run("--flag-score", "150")
        assert rc == 2
        assert "flag-score" in err

    def test_flag_score_negative_rejected(self):
        rc, _, err = self._run("--flag-score", "-5")
        assert rc == 2
        assert "flag-score" in err

    def test_max_apm_zero_rejected(self):
        rc, _, err = self._run("--max-apm", "0")
        assert rc == 2
        assert "max-apm" in err

    def test_max_apm_negative_rejected(self):
        rc, _, err = self._run("--max-apm", "-100")
        assert rc == 2
        assert "max-apm" in err

    def test_min_reaction_negative_rejected(self):
        rc, _, err = self._run("--min-reaction-ms", "-10")
        assert rc == 2
        assert "min-reaction-ms" in err

    def test_malformed_jsonl_exits_2(self, tmp_path):
        bad = tmp_path / "bad.jsonl"
        bad.write_text("this is not json\n", encoding="utf-8")
        rc, _, err = self._run(logfile=str(bad))
        assert rc == 2
        assert "error" in err.lower()

    def test_binary_file_exits_2(self, tmp_path):
        bad = tmp_path / "bad.bin"
        bad.write_bytes(b"\xff\xfe binary garbage")
        rc, _, err = self._run(logfile=str(bad))
        assert rc == 2
        assert "error" in err.lower()


# ---------------------------------------------------------------------------
# webhook.py — input validation
# ---------------------------------------------------------------------------

class TestWebhookHardening:
    def _run_webhook(self, *args, stdin_data="{}"):
        return subprocess.run(
            [sys.executable,
             r"C:\Users\user\AppData\Local\Temp\cheatsense-harden\integrations\webhook.py"]
            + list(args),
            input=stdin_data,
            capture_output=True, text=True, timeout=10,
        )

    def test_empty_stdin_exits_2(self):
        proc = self._run_webhook("--url", "http://localhost:9999", stdin_data="")
        assert proc.returncode == 2
        assert "empty" in proc.stderr.lower()

    def test_non_http_scheme_rejected(self):
        proc = self._run_webhook("--url", "file:///etc/passwd", stdin_data='{"ok":1}')
        assert proc.returncode == 2
        assert "scheme" in proc.stderr.lower()

    def test_malformed_header_rejected(self):
        proc = self._run_webhook(
            "--url", "http://localhost:9999",
            "--header", "NoColonHere",
            stdin_data='{"ok":1}',
        )
        assert proc.returncode == 2
        assert "Key: Value" in proc.stderr or "form" in proc.stderr.lower()
