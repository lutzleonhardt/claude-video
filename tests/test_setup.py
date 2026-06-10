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


@contextlib.contextmanager
def _install_env(system: str, present: set[str], has_key: bool):
    backend = "openai" if has_key else None
    scaffold = mock.Mock(return_value=False)
    write_complete = mock.Mock()
    install_macos = mock.Mock(return_value=(True, "installed via brew"))
    with mock.patch.object(setup, "_which", side_effect=_which_for(present)), \
            mock.patch.object(setup.platform, "system", return_value=system), \
            mock.patch.object(setup, "_have_api_key", return_value=(has_key, backend)), \
            mock.patch.object(setup, "_scaffold_env", scaffold), \
            mock.patch.object(setup, "_write_setup_complete", write_complete), \
            mock.patch.object(setup, "_install_macos", install_macos):
        yield {
            "scaffold": scaffold,
            "write_complete": write_complete,
            "install_macos": install_macos,
        }


def _run_install():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = setup.cmd_install()
    return code, out.getvalue(), err.getvalue()


class InstallPrecedenceTests(unittest.TestCase):
    def test_linux_ffmpeg_optional_proceeds_to_key_step_and_completes(self):
        with _install_env("Linux", {"yt-dlp"}, has_key=True) as mocks:
            code, out, err = _run_install()
        self.assertEqual(code, 0)
        mocks["scaffold"].assert_called_once()
        mocks["write_complete"].assert_called_once()
        self.assertIn("ffmpeg", err)
        self.assertIn("--frames", err)

    def test_linux_ffmpeg_optional_no_key_reaches_key_step_exits_3(self):
        with _install_env("Linux", {"yt-dlp"}, has_key=False) as mocks:
            code, out, err = _run_install()
        self.assertEqual(code, 3)
        mocks["scaffold"].assert_called_once()
        self.assertIn("ffmpeg", err)

    def test_linux_missing_ytdlp_hard_fails_before_scaffold(self):
        with _install_env("Linux", {"ffmpeg", "ffprobe"}, has_key=True) as mocks:
            code, out, err = _run_install()
        self.assertEqual(code, 2)
        mocks["scaffold"].assert_not_called()
        mocks["write_complete"].assert_not_called()
        self.assertIn("yt-dlp", err)

    def test_windows_ffmpeg_optional_proceeds_to_key_step(self):
        with _install_env("Windows", {"yt-dlp"}, has_key=True) as mocks:
            code, out, err = _run_install()
        self.assertEqual(code, 0)
        mocks["scaffold"].assert_called_once()
        self.assertIn("ffmpeg", err)
        self.assertIn("--frames", err)

    def test_windows_missing_ytdlp_hard_fails(self):
        with _install_env("Windows", {"ffmpeg", "ffprobe"}, has_key=True) as mocks:
            code, out, err = _run_install()
        self.assertEqual(code, 2)
        mocks["scaffold"].assert_not_called()
        self.assertIn("yt-dlp", err)

    def test_macos_brew_install_attempted_and_proceeds(self):
        with _install_env("Darwin", {"yt-dlp", "ffmpeg", "ffprobe"}, has_key=True) as mocks:
            with mock.patch.object(setup, "_check_binaries", side_effect=[["ffmpeg"], []]):
                code, out, err = _run_install()
        self.assertEqual(code, 0)
        mocks["install_macos"].assert_called_once()
        mocks["scaffold"].assert_called_once()

    def test_macos_brew_failure_still_exits_2(self):
        with _install_env("Darwin", {"yt-dlp"}, has_key=True) as mocks:
            mocks["install_macos"].return_value = (False, "brew install failed")
            with mock.patch.object(setup, "_check_binaries", return_value=["ffmpeg"]):
                code, out, err = _run_install()
        self.assertEqual(code, 2)
        mocks["scaffold"].assert_not_called()


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
