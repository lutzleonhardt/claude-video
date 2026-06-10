"""Unit tests for the download layer (transcript-first fetching)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import download  # noqa: E402


class PickSubtitleTests(unittest.TestCase):
    def _touch(self, out_dir: Path, names: list[str]) -> None:
        for name in names:
            (out_dir / name).write_text("WEBVTT\n", encoding="utf-8")

    def test_prefers_first_language_in_priority_list(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            self._touch(out_dir, ["video.en.vtt", "video.de.vtt"])
            picked = download._pick_subtitle(out_dir, ["de", "en"])
            self.assertIsNotNone(picked)
            self.assertEqual(picked.name, "video.de.vtt")

    def test_skips_missing_language_and_takes_next(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            self._touch(out_dir, ["video.en.vtt"])
            picked = download._pick_subtitle(out_dir, ["de", "en"])
            self.assertEqual(picked.name, "video.en.vtt")

    def test_falls_back_to_any_vtt_when_no_language_matches(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            self._touch(out_dir, ["video.fr.vtt"])
            picked = download._pick_subtitle(out_dir, ["de", "en"])
            self.assertEqual(picked.name, "video.fr.vtt")

    def test_returns_none_when_no_vtt_present(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            self.assertIsNone(download._pick_subtitle(out_dir, ["de", "en"]))

    def test_accepts_comma_separated_string(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            self._touch(out_dir, ["video.en.vtt", "video.de.vtt"])
            picked = download._pick_subtitle(out_dir, "de,en")
            self.assertEqual(picked.name, "video.de.vtt")


class FetchCaptionsOnlyArgvTests(unittest.TestCase):
    def test_argv_contains_skip_download_and_sub_langs(self):
        import tempfile

        captured = {}

        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            with mock.patch.object(download.shutil, "which", return_value="/usr/bin/yt-dlp"), \
                    mock.patch.object(download.subprocess, "run", side_effect=fake_run):
                result = download.fetch_captions_only(
                    "https://example.com/v", out_dir, "de,de-orig,en"
                )

        cmd = captured["cmd"]
        self.assertEqual(cmd[0], "yt-dlp")
        self.assertIn("--skip-download", cmd)
        self.assertIn("--sub-langs", cmd)
        self.assertEqual(cmd[cmd.index("--sub-langs") + 1], "de,de-orig,en")
        # option-injection hardening: -- separates options from the URL
        self.assertIn("--", cmd)
        self.assertEqual(cmd[-1], "https://example.com/v")
        self.assertLess(cmd.index("--"), cmd.index("https://example.com/v"))
        # transcript-first contract: no video download requested
        self.assertNotIn("--merge-output-format", cmd)

        self.assertIsNone(result["video_path"])
        self.assertFalse(result["downloaded"])
        self.assertIsNone(result["subtitle_path"])

    def test_subtitle_path_set_when_vtt_produced(self):
        import tempfile

        def fake_run(cmd, *args, **kwargs):
            out_dir = Path(cmd[cmd.index("-o") + 1]).parent
            (out_dir / "video.de.vtt").write_text("WEBVTT\n", encoding="utf-8")
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            with mock.patch.object(download.shutil, "which", return_value="/usr/bin/yt-dlp"), \
                    mock.patch.object(download.subprocess, "run", side_effect=fake_run):
                result = download.fetch_captions_only(
                    "https://example.com/v", out_dir, "de,en"
                )

        self.assertIsNotNone(result["subtitle_path"])
        self.assertTrue(result["subtitle_path"].endswith("video.de.vtt"))

    def test_missing_yt_dlp_exits_code_2(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            with mock.patch.object(download.shutil, "which", return_value=None):
                with self.assertRaises(SystemExit) as ctx:
                    download.fetch_captions_only("https://example.com/v", out_dir, "en")
        self.assertEqual(ctx.exception.code, 2)


class FetchAudioOnlyTests(unittest.TestCase):
    def test_argv_and_returns_audio_path(self):
        import tempfile

        captured = {}

        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            out_dir = Path(cmd[cmd.index("-o") + 1]).parent
            (out_dir / "audio.m4a").write_bytes(b"\x00")
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            with mock.patch.object(download.shutil, "which", return_value="/usr/bin/yt-dlp"), \
                    mock.patch.object(download.subprocess, "run", side_effect=fake_run):
                audio = download.fetch_audio_only("https://example.com/v", out_dir)

        cmd = captured["cmd"]
        self.assertEqual(cmd[0], "yt-dlp")
        self.assertEqual(cmd[cmd.index("-f") + 1], "ba")
        self.assertIn("--no-playlist", cmd)
        self.assertNotIn("--merge-output-format", cmd)
        self.assertIn("--", cmd)
        self.assertEqual(cmd[-1], "https://example.com/v")
        self.assertEqual(Path(audio).name, "audio.m4a")


class DownloadUrlSubLangsTests(unittest.TestCase):
    def test_sub_langs_threaded_into_argv(self):
        import tempfile

        captured = {}

        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            out_dir = Path(cmd[cmd.index("-o") + 1]).parent
            (out_dir / "video.mp4").write_bytes(b"\x00")
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            with mock.patch.object(download.shutil, "which", return_value="/usr/bin/yt-dlp"), \
                    mock.patch.object(download.subprocess, "run", side_effect=fake_run):
                download.download_url(
                    "https://example.com/v", out_dir, sub_langs="de,de-orig,en"
                )

        cmd = captured["cmd"]
        self.assertEqual(cmd[cmd.index("--sub-langs") + 1], "de,de-orig,en")

    def test_default_sub_langs_preserves_upstream(self):
        import tempfile

        captured = {}

        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            out_dir = Path(cmd[cmd.index("-o") + 1]).parent
            (out_dir / "video.mp4").write_bytes(b"\x00")
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            with mock.patch.object(download.shutil, "which", return_value="/usr/bin/yt-dlp"), \
                    mock.patch.object(download.subprocess, "run", side_effect=fake_run):
                download.download_url("https://example.com/v", out_dir)

        cmd = captured["cmd"]
        self.assertEqual(cmd[cmd.index("--sub-langs") + 1], "en,en-US,en-GB,en-orig")

    def test_missing_yt_dlp_exits_code_2(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            with mock.patch.object(download.shutil, "which", return_value=None):
                with self.assertRaises(SystemExit) as ctx:
                    download.download_url("https://example.com/v", out_dir)
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
