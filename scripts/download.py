#!/usr/bin/env python3
"""Download a video via yt-dlp, or resolve a local file path.

Also fetches subtitles (manual first, then auto-generated) in VTT format so
transcribe.py can parse them without needing Whisper.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}

DEFAULT_SUB_LANGS = "en,en-US,en-GB,en-orig"

YT_DLP_INSTALL_HINT = "yt-dlp is not installed. Install with: brew install yt-dlp (or pipx install yt-dlp)"


def _require_yt_dlp() -> None:
    if shutil.which("yt-dlp") is None:
        print(f"[watch] {YT_DLP_INSTALL_HINT}", file=sys.stderr)
        raise SystemExit(2)


def is_url(source: str) -> bool:
    if source.startswith("-"):
        return False
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def resolve_local(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"File not found: {p}")
    if p.suffix.lower() not in VIDEO_EXTS:
        print(
            f"[watch] warning: {p.suffix} is not a known video extension, proceeding anyway",
            file=sys.stderr,
        )
    return {
        "video_path": str(p),
        "subtitle_path": None,
        "info": {"title": p.name, "url": str(p)},
        "downloaded": False,
    }


def _lang_list(langs: str | list[str]) -> list[str]:
    if isinstance(langs, str):
        langs = langs.split(",")
    return [lang.strip() for lang in langs if lang and lang.strip()]


def _pick_subtitle(out_dir: Path, langs: str | list[str]) -> Path | None:
    candidates = sorted(out_dir.glob("video*.vtt"))
    if not candidates:
        return None
    for lang in _lang_list(langs):
        for candidate in candidates:
            if f".{lang}." in candidate.name:
                return candidate
    return candidates[0]


def _pick_audio(out_dir: Path) -> Path | None:
    for candidate in sorted(out_dir.glob("audio.*")):
        if candidate.suffix.lower() != ".json":
            return candidate
    return None


def _load_info(out_dir: Path, url: str) -> dict:
    info_path = out_dir / "video.info.json"
    if not info_path.exists():
        return {"url": url}
    try:
        raw = json.loads(info_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[watch] info.json parse failed: {exc}", file=sys.stderr)
        return {"url": url}
    return {
        "title": raw.get("title"),
        "uploader": raw.get("uploader") or raw.get("channel"),
        "duration": raw.get("duration"),
        "url": raw.get("webpage_url") or url,
    }


def _pick_video(out_dir: Path) -> Path | None:
    for ext in (".mp4", ".mkv", ".webm", ".mov"):
        for candidate in out_dir.glob(f"video*{ext}"):
            return candidate
    for candidate in out_dir.glob("video.*"):
        if candidate.suffix.lower() in VIDEO_EXTS:
            return candidate
    return None


def download_url(url: str, out_dir: Path, sub_langs: str = DEFAULT_SUB_LANGS) -> dict:
    _require_yt_dlp()

    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / "video.%(ext)s")

    cmd = [
        "yt-dlp",
        "-N", "8",
        "-f", "bv*[height<=720]+ba/b[height<=720]/bv+ba/b",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", sub_langs,
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "--ignore-errors",
        "-o", output_template,
        "--",
        url,
    ]

    # yt-dlp may exit non-zero if a subtitle variant fails (e.g. 429) even when
    # the video itself downloaded fine. Treat "video file present" as success.
    result = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    video = _pick_video(out_dir)
    if video is None:
        raise SystemExit(
            f"yt-dlp did not produce a video file in {out_dir} (exit {result.returncode})"
        )

    subtitle = _pick_subtitle(out_dir, sub_langs)
    return {
        "video_path": str(video),
        "subtitle_path": str(subtitle) if subtitle else None,
        "info": _load_info(out_dir, url),
        "downloaded": True,
    }


def fetch_captions_only(url: str, out_dir: Path, langs: str) -> dict:
    _require_yt_dlp()

    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / "video.%(ext)s")

    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", langs,
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "--ignore-errors",
        "-o", output_template,
        "--",
        url,
    ]

    subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    subtitle = _pick_subtitle(out_dir, langs)
    return {
        "video_path": None,
        "subtitle_path": str(subtitle) if subtitle else None,
        "info": _load_info(out_dir, url),
        "downloaded": False,
    }


def fetch_audio_only(url: str, out_dir: Path) -> Path:
    _require_yt_dlp()

    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / "audio.%(ext)s")

    cmd = [
        "yt-dlp",
        "-f", "ba",
        "--no-playlist",
        "-o", output_template,
        "--",
        url,
    ]

    result = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    audio = _pick_audio(out_dir)
    if audio is None:
        raise SystemExit(
            f"yt-dlp did not produce an audio file in {out_dir} (exit {result.returncode})"
        )
    return audio


def download(source: str, out_dir: Path) -> dict:
    if is_url(source):
        return download_url(source, out_dir)
    return resolve_local(source)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: download.py <url-or-path> <out-dir>", file=sys.stderr)
        raise SystemExit(2)
    result = download(sys.argv[1], Path(sys.argv[2]))
    print(json.dumps(result, indent=2))
