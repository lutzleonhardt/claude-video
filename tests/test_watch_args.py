"""Unit tests for watch.py CLI argument validation (F1) and the --frames ffmpeg guard (F2)."""
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

F1_MESSAGE = "error: --max-frames/--resolution/--fps require --frames"


class FrameFlagValidationTests(unittest.TestCase):
    def _run(self, argv: list[str]):
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", ["watch.py"] + argv):
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as ctx:
                    watch.main()
        return ctx.exception, stderr.getvalue()

    def test_max_frames_without_frames_exits_2(self):
        exc, err = self._run(["https://example.com/v", "--max-frames", "40"])
        self.assertEqual(exc.code, 2)
        self.assertIn(F1_MESSAGE, err)

    def test_resolution_without_frames_exits_2(self):
        exc, err = self._run(["https://example.com/v", "--resolution", "256"])
        self.assertEqual(exc.code, 2)
        self.assertIn(F1_MESSAGE, err)

    def test_fps_without_frames_exits_2(self):
        exc, err = self._run(["https://example.com/v", "--fps", "1.5"])
        self.assertEqual(exc.code, 2)
        self.assertIn(F1_MESSAGE, err)


class FramesFfmpegGuardTests(unittest.TestCase):
    def test_frames_without_ffmpeg_exits_2_with_hint(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            argv = ["watch.py", "https://example.com/v", "--frames", "--out-dir", tmp]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(watch.shutil, "which", return_value=None):
                with contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as ctx:
                        watch.main()
        self.assertEqual(ctx.exception.code, 2)
        err = stderr.getvalue()
        self.assertIn("ffmpeg", err)
        # The ffmpeg guard, not the F1 arg-validation, must be the failure here.
        self.assertNotIn(F1_MESSAGE, err)


if __name__ == "__main__":
    unittest.main()
