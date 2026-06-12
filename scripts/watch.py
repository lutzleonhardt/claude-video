#!/usr/bin/env python3
"""/watch entry point: download video, extract frames, parse transcript.

Prints a markdown report to stdout listing frame paths + transcript. Claude
then Reads each frame path to see the video.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from download import (  # noqa: E402
    download_url,
    fetch_audio_only,
    fetch_captions_only,
    is_url,
    resolve_local,
)
from frames import MAX_FPS, auto_fps, auto_fps_focus, extract, format_time, get_metadata, parse_time  # noqa: E402
from transcribe import filter_range, format_transcript, parse_vtt  # noqa: E402
from whisper import load_api_key, transcribe_video  # noqa: E402

DEFAULT_LANGS = "de,de-orig,en,en-US,en-GB,en-orig"
FFMPEG_INSTALL_HINT = (
    "ffmpeg is required for --frames. Install with: brew install ffmpeg "
    "(or apt install ffmpeg / choco install ffmpeg)"
)


def _run_frames_mode(args: argparse.Namespace, work: Path) -> int:
    missing = [binary for binary in ("ffmpeg", "ffprobe") if shutil.which(binary) is None]
    if missing:
        print(f"[watch] {FFMPEG_INSTALL_HINT}", file=sys.stderr)
        raise SystemExit(2)

    max_frames = min(args.max_frames if args.max_frames is not None else 80, 100)
    resolution = args.resolution if args.resolution is not None else 512

    print(
        "[watch] downloading via yt-dlp…" if is_url(args.source) else "[watch] using local file…",
        file=sys.stderr,
    )
    if is_url(args.source):
        dl = download_url(args.source, work / "download", sub_langs=args.lang)
    else:
        dl = resolve_local(args.source)
    video_path = dl["video_path"]

    meta = get_metadata(video_path)
    full_duration = meta["duration_seconds"]

    start_sec = parse_time(args.start)
    end_sec = parse_time(args.end)

    if start_sec is not None and start_sec < 0:
        raise SystemExit("--start must be non-negative")
    if end_sec is not None and start_sec is not None and end_sec <= start_sec:
        raise SystemExit("--end must be greater than --start")
    if full_duration > 0 and start_sec is not None and start_sec >= full_duration:
        raise SystemExit(f"--start {start_sec:.1f}s is past end of video ({full_duration:.1f}s)")

    effective_start = start_sec if start_sec is not None else 0.0
    effective_end = end_sec if end_sec is not None else full_duration
    effective_duration = max(0.0, effective_end - effective_start)
    focused = start_sec is not None or end_sec is not None

    if focused:
        fps, target = auto_fps_focus(effective_duration, max_frames=max_frames)
    else:
        fps, target = auto_fps(effective_duration, max_frames=max_frames)
    if args.fps is not None:
        fps = min(args.fps, MAX_FPS)
        target = max(1, int(round(fps * effective_duration)))

    scope = (
        f"{format_time(effective_start)}-{format_time(effective_end)} ({effective_duration:.1f}s)"
        if focused else f"full {effective_duration:.1f}s"
    )
    print(f"[watch] extracting ~{target} frames at {fps:.3f} fps over {scope}…", file=sys.stderr)

    frames = extract(
        video_path,
        work / "frames",
        fps=fps,
        resolution=resolution,
        max_frames=max_frames,
        start_seconds=start_sec,
        end_seconds=end_sec,
    )

    transcript_segments: list[dict] = []
    transcript_text: str | None = None
    transcript_source: str | None = None
    parsed_any = False  # a transcript source yielded segments before focus filtering
    whisper_error: str | None = None
    if dl.get("subtitle_path"):
        try:
            all_segments = parse_vtt(dl["subtitle_path"])
            parsed_any = bool(all_segments)
            transcript_segments = filter_range(all_segments, start_sec, end_sec) if focused else all_segments
            transcript_text = format_transcript(transcript_segments)
            transcript_source = "captions"
        except Exception as exc:
            print(f"[watch] subtitle parse failed: {exc}", file=sys.stderr)

    if not transcript_segments and not parsed_any and not args.no_whisper:
        backend, api_key = load_api_key(args.whisper)
        if backend and api_key:
            try:
                all_segments, used_backend = transcribe_video(
                    video_path,
                    work / "audio.mp3",
                    backend=backend,
                    api_key=api_key,
                )
                parsed_any = bool(all_segments)
                transcript_segments = filter_range(all_segments, start_sec, end_sec) if focused else all_segments
                transcript_text = format_transcript(transcript_segments)
                transcript_source = f"whisper ({used_backend})"
            except SystemExit as exc:
                whisper_error = str(exc) or "unknown error"
                print(f"[watch] whisper fallback failed: {whisper_error}", file=sys.stderr)
        else:
            _whisper_unavailable_hint(args)

    info = dl.get("info") or {}

    print()
    print("# watch: video report")
    print()
    print(f"- **Source:** {args.source}")
    if info.get("title"):
        print(f"- **Title:** {info['title']}")
    if info.get("uploader"):
        print(f"- **Uploader:** {info['uploader']}")
    print(f"- **Duration:** {format_time(full_duration)} ({full_duration:.1f}s)")
    if focused:
        print(
            f"- **Focus range:** {format_time(effective_start)} → {format_time(effective_end)} "
            f"({effective_duration:.1f}s)"
        )
    if meta.get("width") and meta.get("height"):
        print(f"- **Resolution:** {meta['width']}x{meta['height']} ({meta.get('codec') or 'unknown codec'})")
    mode = "focused" if focused else "full"
    print(f"- **Frames:** {len(frames)} @ {fps:.3f} fps, {mode} mode (budget {target}, max {max_frames})")
    print(f"- **Frame size:** {resolution}px wide")
    if transcript_segments:
        in_range = " in range" if focused else ""
        print(
            f"- **Transcript:** {len(transcript_segments)} segments{in_range} "
            f"(via {transcript_source or 'captions'})"
        )
    else:
        print("- **Transcript:** none available")

    if not focused and full_duration > 600:
        mins = int(full_duration // 60)
        print()
        print(
            f"> **Warning:** This is a {mins}-minute video. Frame coverage is sparse at this length — "
            "accuracy degrades noticeably on anything over 10 minutes. For better results, "
            "re-run with `--start HH:MM:SS --end HH:MM:SS` to zoom into a specific section."
        )

    print()
    print("## Frames")
    print()
    print(f"Frames live at: `{work / 'frames'}`")
    print()
    print(
        "**Read each frame path below with the Read tool to view the image.** "
        "Frames are in chronological order; `t=MM:SS` is the absolute timestamp in the source video."
    )
    print()
    for frame in frames:
        print(f"- `{frame['path']}` (t={format_time(frame['timestamp_seconds'])})")

    print()
    print("## Transcript")
    print()
    if transcript_text:
        label = transcript_source or "captions"
        if focused:
            print(f"_Source: {label}. Filtered to {format_time(effective_start)} → {format_time(effective_end)}:_")
        else:
            print(f"_Source: {label}._")
        print()
        print("```")
        print(transcript_text)
        print("```")
    elif focused and parsed_any:
        print(f"_No transcript lines fell inside {format_time(effective_start)} → {format_time(effective_end)}._")
    elif whisper_error:
        print(
            f"_No transcript available — the Whisper fallback ran but failed: {whisper_error} — "
            "proceed with frames only, or retry with `--whisper openai` / `--whisper groq` "
            "to switch backends._"
        )
    else:
        setup_py = SCRIPT_DIR / "setup.py"
        print(
            "_No transcript available — proceed with frames only. "
            "Captions were missing and the Whisper fallback was unavailable "
            "(no API key set, or `--no-whisper` was used). "
            f"Run `python3 {setup_py}` to enable Whisper, then re-run._"
        )

    print()
    print("---")
    print(f"_Work dir: `{work}` — delete when done._")

    return 0


def _whisper_unavailable_hint(args: argparse.Namespace) -> None:
    hint = (
        f"--whisper {args.whisper} was set but the matching API key is missing"
        if args.whisper else
        "no subtitles and no Whisper API key found"
    )
    setup_py = SCRIPT_DIR / "setup.py"
    print(
        f"[watch] {hint} — run `python3 {setup_py}` to enable the Whisper fallback",
        file=sys.stderr,
    )


def _run_transcript_mode(args: argparse.Namespace, work: Path) -> int:
    start_sec = parse_time(args.start)
    end_sec = parse_time(args.end)
    if start_sec is not None and start_sec < 0:
        raise SystemExit("--start must be non-negative")
    if end_sec is not None and start_sec is not None and end_sec <= start_sec:
        raise SystemExit("--end must be greater than --start")
    focused = start_sec is not None or end_sec is not None

    transcript_segments: list[dict] = []
    transcript_text: str | None = None
    transcript_source: str | None = None
    parsed_any = False  # a transcript source yielded segments before focus filtering
    whisper_error: str | None = None

    if is_url(args.source):
        print("[watch] fetching captions via yt-dlp…", file=sys.stderr)
        dl = fetch_captions_only(args.source, work / "download", langs=args.lang)
        info = dl.get("info") or {}
        if dl.get("subtitle_path"):
            try:
                all_segments = parse_vtt(dl["subtitle_path"])
                parsed_any = bool(all_segments)
                transcript_segments = (
                    filter_range(all_segments, start_sec, end_sec) if focused else all_segments
                )
                transcript_text = format_transcript(transcript_segments)
                transcript_source = "captions"
            except Exception as exc:
                print(f"[watch] subtitle parse failed: {exc}", file=sys.stderr)

        if not transcript_segments and not parsed_any and not args.no_whisper:
            backend, api_key = load_api_key(args.whisper)
            if backend and api_key:
                try:
                    audio_path = fetch_audio_only(args.source, work / "download")
                    all_segments, used_backend = transcribe_video(
                        str(audio_path),
                        work / "audio.mp3",
                        backend=backend,
                        api_key=api_key,
                    )
                    parsed_any = bool(all_segments)
                    transcript_segments = (
                        filter_range(all_segments, start_sec, end_sec) if focused else all_segments
                    )
                    transcript_text = format_transcript(transcript_segments)
                    transcript_source = f"whisper ({used_backend})"
                except SystemExit as exc:
                    whisper_error = str(exc) or "unknown error"
                    print(f"[watch] whisper fallback failed: {whisper_error}", file=sys.stderr)
            else:
                _whisper_unavailable_hint(args)
    else:
        print("[watch] using local file…", file=sys.stderr)
        dl = resolve_local(args.source)
        info = dl.get("info") or {}
        if not args.no_whisper:
            backend, api_key = load_api_key(args.whisper)
            if backend and api_key:
                try:
                    all_segments, used_backend = transcribe_video(
                        dl["video_path"],
                        work / "audio.mp3",
                        backend=backend,
                        api_key=api_key,
                    )
                    parsed_any = bool(all_segments)
                    transcript_segments = (
                        filter_range(all_segments, start_sec, end_sec) if focused else all_segments
                    )
                    transcript_text = format_transcript(transcript_segments)
                    transcript_source = f"whisper ({used_backend})"
                except SystemExit as exc:
                    whisper_error = str(exc) or "unknown error"
                    print(f"[watch] whisper fallback failed: {whisper_error}", file=sys.stderr)
            else:
                _whisper_unavailable_hint(args)

    duration: float | None = None
    raw_duration = info.get("duration")
    if raw_duration is not None:
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError):
            duration = None

    effective_start = start_sec if start_sec is not None else 0.0
    effective_end = end_sec if end_sec is not None else duration

    print()
    print("# watch: transcript report")
    print()
    print(f"- **Source:** {args.source}")
    if info.get("title"):
        print(f"- **Title:** {info['title']}")
    if info.get("uploader"):
        print(f"- **Uploader:** {info['uploader']}")
    print("- **Mode:** transcript-only")
    if duration is not None:
        print(f"- **Duration:** {format_time(duration)} ({duration:.1f}s)")
    if focused:
        end_label = format_time(effective_end) if effective_end is not None else "end"
        print(f"- **Focus range:** {format_time(effective_start)} → {end_label}")
    if transcript_segments:
        in_range = " in range" if focused else ""
        print(
            f"- **Transcript:** {len(transcript_segments)} segments{in_range} "
            f"(via {transcript_source or 'captions'})"
        )
    else:
        print("- **Transcript:** none available")

    print()
    print("## Transcript")
    print()
    if transcript_text:
        label = transcript_source or "captions"
        if focused:
            end_label = format_time(effective_end) if effective_end is not None else "end"
            print(f"_Source: {label}. Filtered to {format_time(effective_start)} → {end_label}:_")
        else:
            print(f"_Source: {label}._")
        print()
        print("```")
        print(transcript_text)
        print("```")
    elif focused and parsed_any:
        end_label = format_time(effective_end) if effective_end is not None else "end"
        print(f"_No transcript lines fell inside {format_time(effective_start)} → {end_label}._")
    elif whisper_error:
        print(
            f"_No transcript available — the Whisper fallback ran but failed: {whisper_error} — "
            "retry with `--whisper openai` / `--whisper groq` to switch backends, "
            "or re-run with `--frames` to at least see the visuals._"
        )
    else:
        setup_py = SCRIPT_DIR / "setup.py"
        print(
            "_No transcript available. Captions were missing and the Whisper fallback "
            "was unavailable (no API key set, or `--no-whisper` was used). "
            f"Run `python3 {setup_py}` to enable Whisper, then re-run._"
        )

    print()
    print(
        "> **Tip:** Re-run with `--frames` (optionally `--start HH:MM:SS --end HH:MM:SS`) "
        "to extract on-screen visuals if the question needs what is shown, not just said."
    )

    print()
    print("---")
    print(f"_Work dir: `{work}` — delete when done._")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="watch",
        description="Surface a video's transcript by default; opt into frame extraction with --frames.",
    )
    ap.add_argument("source", help="Video URL or local file path")
    ap.add_argument(
        "--frames",
        action="store_true",
        help="Opt in to full video download + frame extraction (upstream behavior).",
    )
    ap.add_argument(
        "--lang",
        type=str,
        default=DEFAULT_LANGS,
        help=f"Comma-separated caption language priority (default: {DEFAULT_LANGS})",
    )
    ap.add_argument(
        "--max-frames", type=int, default=None,
        help="Cap on frame count (default 80, hard max 100). Requires --frames.",
    )
    ap.add_argument(
        "--resolution", type=int, default=None,
        help="Frame width in pixels (default 512). Requires --frames.",
    )
    ap.add_argument("--fps", type=float, default=None, help="Override auto-fps. Requires --frames.")
    ap.add_argument("--start", type=str, default=None, help="Range start (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument("--end", type=str, default=None, help="Range end (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument("--out-dir", type=str, default=None, help="Working directory (default: tmp)")
    ap.add_argument(
        "--no-whisper",
        action="store_true",
        help="Disable Whisper fallback. Videos without captions return no transcript.",
    )
    ap.add_argument(
        "--whisper",
        choices=["groq", "openai"],
        default=None,
        help="Force a specific Whisper backend. Default: prefer Groq, fall back to OpenAI.",
    )
    args = ap.parse_args()

    if (
        args.max_frames is not None or args.resolution is not None or args.fps is not None
    ) and not args.frames:
        print("error: --max-frames/--resolution/--fps require --frames", file=sys.stderr)
        raise SystemExit(2)

    if args.out_dir:
        work = Path(args.out_dir).expanduser().resolve()
    else:
        work = Path(tempfile.mkdtemp(prefix="watch-"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"[watch] working dir: {work}", file=sys.stderr)

    if args.frames:
        return _run_frames_mode(args, work)

    return _run_transcript_mode(args, work)


if __name__ == "__main__":
    raise SystemExit(main())
