"""Unit tests for the default transcript-only mode in watch.py.

Covers the filter_range regression and the mocked transcribe_video wiring:
- captionless URL -> fetch_audio_only then transcribe_video on the audio file
- local file -> transcribe_video on the file directly (no audio download)
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

import transcribe  # noqa: E402
import watch  # noqa: E402


class FilterRangeTests(unittest.TestCase):
    SEGMENTS = [
        {"start": 0.0, "end": 10.0, "text": "a"},
        {"start": 10.0, "end": 20.0, "text": "b"},
        {"start": 20.0, "end": 30.0, "text": "c"},
        {"start": 30.0, "end": 40.0, "text": "d"},
    ]

    def test_keeps_only_overlapping_segments(self):
        out = transcribe.filter_range(self.SEGMENTS, 12.0, 25.0)
        self.assertEqual([s["text"] for s in out], ["b", "c"])

    def test_none_bounds_returns_all(self):
        self.assertEqual(
            transcribe.filter_range(self.SEGMENTS, None, None), self.SEGMENTS
        )

    def test_open_ended_start(self):
        out = transcribe.filter_range(self.SEGMENTS, None, 15.0)
        self.assertEqual([s["text"] for s in out], ["a", "b"])

    def test_open_ended_end(self):
        out = transcribe.filter_range(self.SEGMENTS, 25.0, None)
        self.assertEqual([s["text"] for s in out], ["c", "d"])


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


class TranscribeVideoWiringTests(unittest.TestCase):
    SEGMENTS = [{"start": 0.0, "end": 2.0, "text": "hello world"}]

    def test_url_captionless_uses_audio_only_then_whisper(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "download" / "audio.m4a"

            cap = {
                "video_path": None,
                "subtitle_path": None,
                "info": {"title": "T", "uploader": "U", "duration": 123},
                "downloaded": False,
            }

            with mock.patch.object(watch, "fetch_captions_only", return_value=cap) as m_cap, \
                    mock.patch.object(watch, "fetch_audio_only", return_value=audio_path) as m_audio, \
                    mock.patch.object(watch, "load_api_key", return_value=("openai", "sk-test")), \
                    mock.patch.object(
                        watch, "transcribe_video",
                        return_value=(self.SEGMENTS, "openai"),
                    ) as m_tx:
                code, out, _ = _run_main(
                    ["https://example.com/v", "--out-dir", tmp]
                )

        self.assertEqual(code, 0)
        m_cap.assert_called_once()
        m_audio.assert_called_once()
        # transcribe_video must receive the audio file produced by fetch_audio_only.
        m_tx.assert_called_once()
        self.assertEqual(m_tx.call_args.args[0], str(audio_path))
        self.assertIn("**Mode:** transcript-only", out)
        self.assertIn("## Transcript", out)
        self.assertIn("whisper (openai)", out)
        self.assertNotIn("## Frames", out)

    def test_local_file_calls_transcribe_video_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = {
                "video_path": "/tmp/clip.mp3",
                "subtitle_path": None,
                "info": {"title": "clip.mp3", "url": "/tmp/clip.mp3"},
                "downloaded": False,
            }

            with mock.patch.object(watch, "resolve_local", return_value=local), \
                    mock.patch.object(watch, "fetch_audio_only") as m_audio, \
                    mock.patch.object(watch, "load_api_key", return_value=("openai", "sk-test")), \
                    mock.patch.object(
                        watch, "transcribe_video",
                        return_value=(self.SEGMENTS, "openai"),
                    ) as m_tx:
                code, out, _ = _run_main(["clip.mp3", "--out-dir", tmp])

        self.assertEqual(code, 0)
        # Local files are fed straight to transcribe_video; no audio download.
        m_audio.assert_not_called()
        m_tx.assert_called_once()
        self.assertEqual(m_tx.call_args.args[0], "/tmp/clip.mp3")
        self.assertIn("**Mode:** transcript-only", out)
        self.assertIn("## Transcript", out)
        self.assertNotIn("## Frames", out)

    def test_captionless_url_no_whisper_reports_none_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = {
                "video_path": None,
                "subtitle_path": None,
                "info": {"title": "T", "duration": 90},
                "downloaded": False,
            }
            with mock.patch.object(watch, "fetch_captions_only", return_value=cap), \
                    mock.patch.object(watch, "fetch_audio_only") as m_audio, \
                    mock.patch.object(watch, "transcribe_video") as m_tx:
                code, out, _ = _run_main(
                    ["https://example.com/v", "--no-whisper", "--out-dir", tmp]
                )

        self.assertEqual(code, 0)
        m_audio.assert_not_called()
        m_tx.assert_not_called()
        self.assertIn("Transcript: none available", out.replace("**", ""))
        self.assertNotIn("## Frames", out)

    def test_unparseable_captions_fall_back_to_whisper(self):
        # A subtitle file that yields zero cues (e.g. truncated by a 429 while
        # downloading) must not block the Whisper fallback.
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "video.en.vtt"
            sub.write_text("WEBVTT\n\ngarbage without any cues\n", encoding="utf-8")
            audio_path = Path(tmp) / "download" / "audio.m4a"
            cap = {
                "video_path": None,
                "subtitle_path": str(sub),
                "info": {"title": "T", "duration": 60},
                "downloaded": False,
            }
            with mock.patch.object(watch, "fetch_captions_only", return_value=cap), \
                    mock.patch.object(watch, "fetch_audio_only", return_value=audio_path) as m_audio, \
                    mock.patch.object(watch, "load_api_key", return_value=("groq", "gsk-test")), \
                    mock.patch.object(
                        watch, "transcribe_video",
                        return_value=(self.SEGMENTS, "groq"),
                    ) as m_tx:
                code, out, _ = _run_main(["https://example.com/v", "--out-dir", tmp])

        self.assertEqual(code, 0)
        m_audio.assert_called_once()
        m_tx.assert_called_once()
        self.assertIn("whisper (groq)", out)

    def test_caption_parse_error_falls_back_to_whisper(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "video.en.vtt"
            sub.write_text("WEBVTT\n", encoding="utf-8")
            audio_path = Path(tmp) / "download" / "audio.m4a"
            cap = {
                "video_path": None,
                "subtitle_path": str(sub),
                "info": {"title": "T", "duration": 60},
                "downloaded": False,
            }
            with mock.patch.object(watch, "fetch_captions_only", return_value=cap), \
                    mock.patch.object(watch, "parse_vtt", side_effect=ValueError("boom")), \
                    mock.patch.object(watch, "fetch_audio_only", return_value=audio_path), \
                    mock.patch.object(watch, "load_api_key", return_value=("openai", "sk-test")), \
                    mock.patch.object(
                        watch, "transcribe_video",
                        return_value=(self.SEGMENTS, "openai"),
                    ) as m_tx:
                code, out, err = _run_main(["https://example.com/v", "--out-dir", tmp])

        self.assertEqual(code, 0)
        m_tx.assert_called_once()
        self.assertIn("subtitle parse failed", err)
        self.assertIn("whisper (openai)", out)

    def test_focused_window_outside_captions_skips_whisper(self):
        # Captions parsed fine but the focus window is empty: Whisper must NOT
        # run (it would transcribe the whole video just to filter it away).
        vtt = (
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:10.000\nalpha\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "video.en.vtt"
            sub.write_text(vtt, encoding="utf-8")
            cap = {
                "video_path": None,
                "subtitle_path": str(sub),
                "info": {"title": "T", "duration": 60},
                "downloaded": False,
            }
            with mock.patch.object(watch, "fetch_captions_only", return_value=cap), \
                    mock.patch.object(watch, "transcribe_video") as m_tx:
                code, out, _ = _run_main(
                    ["https://example.com/v", "--start", "0:30", "--out-dir", tmp]
                )

        self.assertEqual(code, 0)
        m_tx.assert_not_called()
        self.assertIn("No transcript lines fell inside", out)

    def test_whisper_failure_is_reported_accurately(self):
        # When Whisper ran and failed, the report must say so — not claim
        # "no API key set" and send the agent into the wrong remediation.
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "download" / "audio.m4a"
            cap = {
                "video_path": None,
                "subtitle_path": None,
                "info": {"title": "T", "duration": 60},
                "downloaded": False,
            }
            with mock.patch.object(watch, "fetch_captions_only", return_value=cap), \
                    mock.patch.object(watch, "fetch_audio_only", return_value=audio_path), \
                    mock.patch.object(watch, "load_api_key", return_value=("groq", "gsk-test")), \
                    mock.patch.object(
                        watch, "transcribe_video",
                        side_effect=SystemExit("Whisper request failed: HTTP Error 429"),
                    ):
                code, out, _ = _run_main(["https://example.com/v", "--out-dir", tmp])

        self.assertEqual(code, 0)
        self.assertIn("Whisper fallback ran but failed", out)
        self.assertIn("429", out)
        self.assertNotIn("no API key set", out)

    def test_captions_path_filters_range_and_skips_whisper(self):
        vtt = (
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:10.000\nalpha\n\n"
            "00:00:40.000 --> 00:00:50.000\nbravo\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "video.en.vtt"
            sub.write_text(vtt, encoding="utf-8")
            cap = {
                "video_path": None,
                "subtitle_path": str(sub),
                "info": {"title": "T", "duration": 60},
                "downloaded": False,
            }
            with mock.patch.object(watch, "fetch_captions_only", return_value=cap), \
                    mock.patch.object(watch, "transcribe_video") as m_tx:
                code, out, _ = _run_main(
                    ["https://example.com/v", "--start", "0:30", "--out-dir", tmp]
                )

        self.assertEqual(code, 0)
        m_tx.assert_not_called()
        self.assertIn("bravo", out)
        self.assertNotIn("alpha", out)
        self.assertIn("--frames", out)


if __name__ == "__main__":
    unittest.main()
