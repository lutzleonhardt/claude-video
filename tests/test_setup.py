"""Unit tests for the relaxed setup.py preflight precedence.

Transcript-only usage only hard-requires yt-dlp; ffmpeg/ffprobe are optional
(needed for --frames and local-file Whisper). Precedence:
  yt-dlp missing -> exit 2 (over key/ffmpeg) ; key missing -> exit 3 ; else 0.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import setup  # noqa: E402


def _which_for(present: set[str]):
    def _which(name: str):
        return f"/usr/bin/{name}" if name in present else None

    return _which


@contextlib.contextmanager
def _env(present: set[str], has_key: bool):
    backend = "openai" if has_key else None
    with mock.patch.object(setup, "_which", side_effect=_which_for(present)), \
            mock.patch.object(setup, "_have_api_key", return_value=(has_key, backend)):
        yield


def _run_check():
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        code = setup.cmd_check()
    return code, stderr.getvalue()


def _run_json():
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = setup.cmd_json()
    return code, json.loads(stdout.getvalue())


class CheckPrecedenceTests(unittest.TestCase):
    def test_ffmpeg_optional_when_ytdlp_and_key_present(self):
        with _env({"yt-dlp"}, has_key=True):
            code, err = _run_check()
        self.assertEqual(code, 0)
        self.assertIn("ffmpeg", err)
        self.assertIn("--frames", err)

    def test_all_present_with_key_is_silent_zero(self):
        with _env({"yt-dlp", "ffmpeg", "ffprobe"}, has_key=True):
            code, err = _run_check()
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

    def test_missing_ytdlp_exits_2_even_with_key(self):
        with _env({"ffmpeg", "ffprobe"}, has_key=True):
            code, err = _run_check()
        self.assertEqual(code, 2)
        self.assertIn("yt-dlp", err)

    def test_missing_ytdlp_takes_precedence_over_missing_key(self):
        with _env(set(), has_key=False):
            code, _ = _run_check()
        self.assertEqual(code, 2)

    def test_missing_key_with_binaries_ok_exits_3(self):
        with _env({"yt-dlp", "ffmpeg", "ffprobe"}, has_key=False):
            code, err = _run_check()
        self.assertEqual(code, 3)
        self.assertIn("API key", err)

    def test_missing_key_exits_3_even_when_ffmpeg_missing(self):
        with _env({"yt-dlp"}, has_key=False):
            code, _ = _run_check()
        self.assertEqual(code, 3)


class JsonStatusTests(unittest.TestCase):
    def test_json_lists_ffmpeg_and_ffprobe_when_absent_and_exits_0(self):
        with _env({"yt-dlp"}, has_key=True):
            code, payload = _run_json()
        self.assertEqual(code, 0)
        self.assertIn("ffmpeg", payload["missing_binaries"])
        self.assertIn("ffprobe", payload["missing_binaries"])
        self.assertEqual(payload["status"], "ready")

    def test_status_consistent_with_check_for_missing_ytdlp(self):
        with _env({"ffmpeg", "ffprobe"}, has_key=True):
            _, payload = _run_json()
            check_code, _ = _run_check()
        self.assertIn("yt-dlp", payload["missing_binaries"])
        self.assertNotEqual(payload["status"], "ready")
        self.assertEqual(check_code, 2)


if __name__ == "__main__":
    unittest.main()
