"""Unit tests for the caption→Whisper fallback wiring in --frames mode.

Mirrors the transcript-mode regressions:
- a subtitle file with zero parseable cues must not block the Whisper fallback
- captions that parse fine but get emptied by the focus filter must NOT
  trigger Whisper (it would transcribe the whole video just to filter it away)
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import watch  # noqa: E402


def _run_main(argv: list[str]):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = None
    with mock.patch.object(sys, "argv", ["watch.py"] + argv):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = watch.main()
            except SystemExit as exc:
                code = exc.code
    return code, stdout.getvalue(), stderr.getvalue()


@contextlib.contextmanager
def _frames_env(dl: dict, duration: float = 60.0):
    meta = {"duration_seconds": duration, "width": 640, "height": 360, "codec": "h264"}
    with mock.patch.object(watch.shutil, "which", return_value="/usr/bin/fake"), \
            mock.patch.object(watch, "download_url", return_value=dl), \
            mock.patch.object(watch, "get_metadata", return_value=meta), \
            mock.patch.object(watch, "extract", return_value=[]):
        yield


class FramesFallbackTests(unittest.TestCase):
    SEGMENTS = [{"start": 0.0, "end": 2.0, "text": "hello world"}]

    def test_unparseable_captions_fall_back_to_whisper(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "video.en.vtt"
            sub.write_text("WEBVTT\n\ngarbage without any cues\n", encoding="utf-8")
            dl = {
                "video_path": str(Path(tmp) / "video.mp4"),
                "subtitle_path": str(sub),
                "info": {"title": "T", "duration": 60},
                "downloaded": True,
            }
            with _frames_env(dl), \
                    mock.patch.object(watch, "load_api_key", return_value=("groq", "gsk-test")), \
                    mock.patch.object(
                        watch, "transcribe_video",
                        return_value=(self.SEGMENTS, "groq"),
                    ) as m_tx:
                code, out, _ = _run_main(
                    ["https://example.com/v", "--frames", "--out-dir", tmp]
                )

        self.assertEqual(code, 0)
        m_tx.assert_called_once()
        self.assertIn("whisper (groq)", out)
        self.assertIn("## Frames", out)

    def test_focused_window_outside_captions_skips_whisper(self):
        vtt = (
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:10.000\nalpha\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "video.en.vtt"
            sub.write_text(vtt, encoding="utf-8")
            dl = {
                "video_path": str(Path(tmp) / "video.mp4"),
                "subtitle_path": str(sub),
                "info": {"title": "T", "duration": 60},
                "downloaded": True,
            }
            with _frames_env(dl), \
                    mock.patch.object(watch, "transcribe_video") as m_tx:
                code, out, _ = _run_main(
                    ["https://example.com/v", "--frames", "--start", "0:30", "--out-dir", tmp]
                )

        self.assertEqual(code, 0)
        m_tx.assert_not_called()
        self.assertIn("No transcript lines fell inside", out)

    def test_whisper_failure_is_reported_accurately(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl = {
                "video_path": str(Path(tmp) / "video.mp4"),
                "subtitle_path": None,
                "info": {"title": "T", "duration": 60},
                "downloaded": True,
            }
            with _frames_env(dl), \
                    mock.patch.object(watch, "load_api_key", return_value=("groq", "gsk-test")), \
                    mock.patch.object(
                        watch, "transcribe_video",
                        side_effect=SystemExit("Whisper request failed: HTTP Error 429"),
                    ):
                code, out, _ = _run_main(
                    ["https://example.com/v", "--frames", "--out-dir", tmp]
                )

        self.assertEqual(code, 0)
        self.assertIn("Whisper fallback ran but failed", out)
        self.assertIn("429", out)
        self.assertNotIn("no API key set", out)


if __name__ == "__main__":
    unittest.main()
