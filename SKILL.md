---
name: watch
description: Watch a video (URL or local path). By default, pulls the timestamped transcript only (native captions via yt-dlp, or Whisper API fallback) with no video download — cheap and fast. Add --frames to also download the video and extract frames with ffmpeg when the question needs what's on screen. Hands the result to Claude so it can answer questions about the video.
argument-hint: "<video-url-or-path> [question]"
allowed-tools: Bash, Read, AskUserQuestion
homepage: https://github.com/bradautomates/claude-video
repository: https://github.com/bradautomates/claude-video
author: bradautomates
license: MIT
user-invocable: true
---

# /watch — Claude watches a video

You don't have a video input; this skill gives you one. By default a Python script pulls the video's timestamped transcript (native captions first, then Whisper API as a fallback) **without downloading the video** — most questions about a video are answered by what's *said*, and a transcript costs a few thousand tokens instead of tens of thousands of image tokens. When the question is about what's *on screen* — visuals, slides, UI, on-screen code, a specific moment, a bug recording — add `--frames` and the script also downloads the video, extracts frames as JPEGs, and prints frame paths for you to `Read`.

## Step 0 — Setup preflight (runs every `/watch` invocation, silent on success)

**Python interpreter:** every `python3 ...` command in this skill is for macOS/Linux. On **Windows**, substitute `python` — the `python3` command on Windows is the Microsoft Store stub and will not run the script.

Before every `/watch` run, verify that dependencies are in place:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" --check
```

This is a <100ms lookup. On exit 0, the script emits **nothing** to stdout — proceed to Step 1 without comment. **Do NOT announce "setup is complete" to the user** — they don't need a status message on every turn. The only acceptable user-visible output from Step 0 is when remediation is required.

`yt-dlp` is the only hard requirement: transcript-only mode (the default) needs it to fetch captions or audio. Everything else is **optional** and never fails the preflight: `ffmpeg`/`ffprobe` are only used for `--frames` and for transcribing local files via Whisper, and a Whisper API key is only needed for videos without native captions. When either is missing, `--check` still exits 0 and prints a one-line note to **stderr** so you know which fallback paths are unavailable.

On non-zero exit, follow the table:

| Exit | Meaning | Action |
|------|---------|--------|
| `0` | Ready (yt-dlp present; ffmpeg/ffprobe and Whisper key optional) | Proceed. Stderr notes may say ffmpeg/ffprobe are missing (only relevant for `--frames` / local-file Whisper) or that no Whisper key is set (only relevant for caption-less videos) |
| `2` | `yt-dlp` missing (hard requirement) | Run installer |

(The **installer** — not `--check` — exits `3` when deps are in place but no Whisper key is configured yet; see below.)

The installer is idempotent — safe to re-run:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py"
```

On macOS with Homebrew, it auto-installs `yt-dlp` and `ffmpeg`. On Linux/Windows, it prints the exact install commands for the user to run. It scaffolds `~/.config/watch/.env` with commented placeholders at `0600` perms, and writes `SETUP_COMPLETE=true` once deps + a key are in place so the next session knows this user has already been through the wizard.

**If an API key is still missing after install:** use `AskUserQuestion` to ask the user whether they have a Groq API key (preferred — cheaper, faster) or an OpenAI key. Then write it into `~/.config/watch/.env` — set the matching `GROQ_API_KEY=...` or `OPENAI_API_KEY=...` line. If they don't want to set up Whisper, proceed with `--no-whisper` and tell them videos without native captions will come back with no transcript. (Whisper is only the fallback; videos with native captions never need a key.)

**Structured mode (optional):** `python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" --json` emits `{status, first_run, missing_binaries, missing_required, missing_optional, whisper_backend, has_api_key, config_file, platform}` where `status` is one of `ready | needs_install | needs_key | needs_install_and_key`. `missing_required` lists only hard deps (yt-dlp); `missing_optional` lists ffmpeg/ffprobe. Use this when you need to branch on specifics (e.g. "is this the user's very first run?" → `first_run: true`, or "is ffmpeg available for `--frames`?" → check `missing_optional`).

Within a single session, you can skip Step 0 on follow-up `/watch` calls — once `--check` returned 0, nothing about the environment changes between turns.

## When to use

- User pastes a video URL (YouTube, Vimeo, X, TikTok, Twitch clip, most yt-dlp-supported sites) and asks about it.
- User points at a local video file (`.mp4`, `.mov`, `.mkv`, `.webm`, etc.) and asks about it.
- User types `/watch <url-or-path> [question]`.

## Transcript-first by default — when to add `--frames`

The default run is **transcript-only**: no video download, no frames. This is the right call for most questions, because they're about what was *said*: "summarize this", "what does she mention about X?", "what's the main argument?", podcasts, talking-head videos, lectures, interviews.

**Add `--frames` when the answer depends on what's *on screen*, not just what's said:**

- The question is about visuals: "what's in the thumbnail?", "what's on the slide?", "what does the UI look like?", "read the code/terminal on screen".
- A "what happens at…" / moment-specific question where the on-screen action matters ("what happens at 0:30?", "show me the intro").
- Diagnosing a bug from a screen recording — you need to *see* the frame where it breaks.
- Analyzing creative/structure where visuals matter (hooks, ad creative, on-screen text, B-roll).
- The user explicitly asks to see/look at the video or asks about something the transcript can't answer.

If a transcript-only run doesn't contain enough to answer a visual question, re-run with `--frames` (optionally scoped with `--start`/`--end`). The transcript-mode report prints a one-line tip reminding you of this.

## Recommended limits (frames mode)

These apply only when you use `--frames`; transcript-only mode has no frame budget.

- **Best accuracy: videos under 10 minutes.** Frame coverage scales inversely with duration.
- **Hard caps: 100 frames total and 2 fps.** Token cost grows with frame count, so the script targets a frame budget by duration (and never exceeds 2 fps even when the budget would imply more):
  - ≤30s → ~1-2 fps (up to 30 frames)
  - 30s-1min → ~40 frames
  - 1-3min → ~60 frames
  - 3-10min → ~80 frames
  - \>10min → 100 frames, sparsely spaced (warning printed)
- If the user hands you a long video and wants frames, consider asking whether they want a specific section (`--start`/`--end`) before burning tokens on a sparse scan.

## How to invoke

**Step 1 — parse the user input.** Separate the video source (URL or path) from any question the user asked. Example: `/watch https://youtu.be/abc what language is this in?` → source = `https://youtu.be/abc`, question = `what language is this in?`. Use the question to decide whether you need `--frames` (see "When to add `--frames`" above).

**Step 2 — run the watch script.** Pass the source verbatim. Do not shell-escape it yourself beyond normal quoting. Default (transcript-only):

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/watch.py" "<source>"
```

Opt into frames when the question needs on-screen content:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/watch.py" "<source>" --frames
```

Flags:
- `--frames` — opt in to downloading the video and extracting frames (the legacy upstream behavior). Without it, the script never downloads the video; it only fetches captions/metadata (and audio for the Whisper fallback).
- `--lang L` — comma-separated caption-language priority (default: `de,de-orig,en,en-US,en-GB,en-orig`). The script selects the first language in the list that has a caption track, e.g. `--lang en` to force English, `--lang de` to prefer German.
- `--start T` / `--end T` — focus on a section. Accepts `SS`, `MM:SS`, or `HH:MM:SS`. In transcript mode this filters the transcript to that window; in `--frames` mode fps also auto-scales denser (see "Focusing on a section").
- `--max-frames N` — lower the frame cap for a tighter token budget (e.g. `--max-frames 40`). **Requires `--frames`.**
- `--resolution W` — change frame width in px (default 512; bump to 1024 only if the user needs to read on-screen text). **Requires `--frames`.**
- `--fps F` — override auto-fps (clamped to 2 fps max). **Requires `--frames`.**
- `--out-dir DIR` — keep working files somewhere specific (default: an auto-generated tmp dir).
- `--whisper groq|openai` — force a specific Whisper backend (default: prefer Groq if both keys exist).
- `--no-whisper` — disable the Whisper fallback entirely (no transcript if no captions).

> Passing `--max-frames`, `--resolution`, or `--fps` **without** `--frames` is an error: the script exits `2` with `error: --max-frames/--resolution/--fps require --frames`. Add `--frames` when you want any of those.

### Focusing on a section

When the user asks about a specific moment — "what happens at the 2 minute mark?", "zoom into 0:45 to 1:00", "the first 10 seconds" — pass `--start` and/or `--end`.

In **transcript mode** this filters the transcript to segments overlapping the requested window. In **`--frames` mode** the script also switches to focused-mode frame budgets, which are denser than full-video budgets (still capped at 2 fps):

- ≤5s → 2 fps (up to 10 frames)
- 5-15s → 2 fps (up to 30 frames)
- 15-30s → ~2 fps (up to 60 frames)
- 30-60s → ~1.3 fps (up to 80 frames)
- 60-180s → ~0.6 fps (100 frames, capped)

Focused mode is the right call for:
- Any moment/range the user names explicitly ("around 2:30", "the intro", "the last 30 seconds").
- Any video longer than ~10 minutes where the user's question is about a specific part — running focused on the relevant section is far more useful than a sparse scan of the whole thing.
- Re-runs after a full scan didn't have enough detail in some region.

With `--frames`, frame timestamps are absolute (real video timeline, not offset-from-start).

Examples:
```bash
# Transcript only, German captions preferred
python3 "${CLAUDE_SKILL_DIR}/scripts/watch.py" "$URL" --lang de

# Frames: last 10 seconds of a 1 minute video
python3 "${CLAUDE_SKILL_DIR}/scripts/watch.py" video.mp4 --frames --start 50 --end 60

# Frames: zoom into 2:15 → 2:45 at 3 fps (capped at 2 fps)
python3 "${CLAUDE_SKILL_DIR}/scripts/watch.py" "$URL" --frames --start 2:15 --end 2:45 --fps 3

# Transcript only, from 1h12m to the end of the video
python3 "${CLAUDE_SKILL_DIR}/scripts/watch.py" "$URL" --start 1:12:00
```

**Step 3 — read the report.**

- **Transcript-only mode (default):** the report has a `## Transcript` section and a `**Mode:** transcript-only` header. There are **no frames to read** — answer from the transcript. The report ends with a one-line tip suggesting you re-run with `--frames` if the question needs on-screen content.
- **Frames mode (`--frames`):** the report has a `## Frames` section listing frame paths. **Read every frame path the script lists** — the Read tool renders JPEGs directly as images for you. Read all frames in a single message (parallel tool calls) so you see them together. Frames are in chronological order with a `t=MM:SS` timestamp so you can align them to the transcript.

**Step 4 — answer the user.** Your evidence depends on the mode:
- **Transcript** — what's said at each timestamp. The report's header shows the source (`captions` = yt-dlp pulled native subs; `whisper (groq)` or `whisper (openai)` = transcribed by API).
- **Frames** (only when `--frames` was used) — what's on screen at each timestamp.

If the user asked a specific question, answer it directly citing timestamps. If they didn't ask anything, summarize what happens in the video — structure, key moments, spoken content (and notable visuals if you ran with `--frames`).

**Step 5 — clean up.** The script prints a working directory at the end. If the user isn't going to ask follow-ups about this video, delete it with `rm -rf <dir>`. If they might, leave it in place.

## Transcription

The script gets a timestamped transcript in one of two ways:

1. **Native captions (free, preferred).** yt-dlp pulls manual or auto-generated subtitles from the source platform if available. In the default mode this uses `--skip-download` so **no video is downloaded** — only the caption track and a small metadata `info.json`. Caption-language priority is controlled by `--lang`.
2. **Whisper API fallback.** If no captions came back (or the source is a local file), the script gets the audio and ships it to Whisper:
   - For a **URL**, it downloads **audio only** (`yt-dlp -f ba`, smallest format — still no full video download), then extracts a mono 16 kHz clip (`ffmpeg -vn -ac 1 -ar 16000 -b:a 64k`, ~0.5 MB/min).
   - For a **local file**, it extracts the audio clip directly.
   - The clip is uploaded to whichever Whisper API has a key configured:
     - **Groq** — `whisper-large-v3`. Preferred default: cheaper, faster. Get a key at console.groq.com/keys.
     - **OpenAI** — `whisper-1`. Fallback. Get a key at platform.openai.com/api-keys.

Both keys live in `~/.config/watch/.env`. The script prefers Groq when both are set; override with `--whisper openai` to force OpenAI. Use `--no-whisper` to skip the fallback entirely.

## Failure modes and handling

- **Setup preflight failed** → run `python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py"` (auto-installs yt-dlp/ffmpeg via brew on macOS, scaffolds the `.env`). `--check` exit `2` means yt-dlp is missing (hard requirement). A missing Whisper key is only a stderr note on `--check` (captioned videos need no key); the **installer** exits `3` for it — then ask the user via `AskUserQuestion` and write the key to `~/.config/watch/.env`, or proceed without one if they don't want Whisper.
- **ffmpeg/ffprobe missing** → only matters for `--frames` and local-file Whisper. Transcript-only mode on a captioned URL works fine without them. If the user wants `--frames`, prompt to install ffmpeg first.
- **`--frames` without ffmpeg** → the script exits `2` with an install hint and does **not** silently fall back to transcript-only. Install ffmpeg, then re-run.
- **No transcript available** → captions missing AND (no Whisper key OR `--no-whisper` OR Whisper API failed). The report says `Transcript: none available` (exit 0) with a hint pointing to setup. Tell the user; offer `--frames` if the question is visual.
- **Long video warning (frames mode only)** → with `--frames` on a >10-minute video the script prints a "sparse scan" warning. Acknowledge it and offer to re-run focused on a specific section via `--start`/`--end`. (Transcript mode does not print this warning.)
- **Download fails** → yt-dlp's error goes to stderr. If it's a login-required or region-locked video, tell the user plainly; do not keep retrying.
- **Whisper request fails** → the error is printed to stderr (likely: invalid key, rate limit, or 25 MB upload limit on a very long video). The report will say "none available" for transcript. You can retry with `--whisper openai` if Groq failed (or vice versa).

## Token efficiency

The default transcript-only mode is **cheap**: it downloads no video and only emits a timestamped transcript — typically a few thousand tokens even for a 10-minute video, and no image tokens at all. Prefer it unless the question genuinely needs what's on screen.

Frames are where tokens add up — that's why they're opt-in:
- 80 frames at 512px wide is roughly 50-80k image tokens depending on aspect ratio.
- Bumping `--resolution` to 1024 roughly quadruples the image tokens per frame. Only do it when necessary (reading on-screen text).

If you already watched a video this session and the user asks a follow-up, do **not** re-run the script — you already have the transcript (and frames, if you used `--frames`) in context. Just answer from what you have. The exception: a follow-up that needs on-screen content after a transcript-only run — re-run with `--frames`.

## Security & Permissions

**What this skill does:**
- Runs `yt-dlp` locally. In the **default transcript-only mode it does NOT download the video** — it uses `--skip-download` to fetch only the native caption track and a small metadata `info.json` (public data; the request goes directly to whatever host the URL points at). It downloads **audio only** (smallest format) **only** when captions are missing and the Whisper fallback is enabled.
- With `--frames`, additionally downloads the video (≤720p) into the working directory and runs `ffmpeg`/`ffprobe` locally to extract frames as JPEGs.
- Runs `ffmpeg` locally to extract a mono 16 kHz audio clip when Whisper is needed.
- Sends the extracted audio clip to Groq's Whisper API (`api.groq.com/openai/v1/audio/transcriptions`) when `GROQ_API_KEY` is set (preferred — cheaper, faster)
- Sends the extracted audio clip to OpenAI's audio transcription API (`api.openai.com/v1/audio/transcriptions`) when `OPENAI_API_KEY` is set and Groq is not, or when `--whisper openai` is forced
- Writes captions, metadata, and (only when applicable) audio, downloaded video, and frames to a working directory under the system temp dir (or `--out-dir` if specified) so Claude can `Read` them
- Reads / creates `~/.config/watch/.env` (mode `0600`) to store the Whisper API key(s) and a `SETUP_COMPLETE` marker. As a fallback, also reads `.env` in the current working directory

**What this skill does NOT do:**
- Does not download the video at all in the default mode — only captions/metadata, plus audio-only as a Whisper fallback. The full video is downloaded solely when you pass `--frames`.
- Does not upload the video itself to any API — only the extracted audio goes out, and only when native captions are missing AND Whisper is not disabled with `--no-whisper`
- Does not access any platform account (no login, no session cookies, no posting)
- Does not share API keys between providers (Groq key only goes to `api.groq.com`, OpenAI key only goes to `api.openai.com`)
- Does not log, cache, or write API keys to stdout, stderr, or output files
- Does not persist anything outside the working directory and `~/.config/watch/.env` — clean up the working directory when you're done (Step 5)

**Bundled scripts:** `scripts/watch.py` (entry point), `scripts/download.py` (yt-dlp wrapper: caption-only / audio-only / full download), `scripts/frames.py` (ffmpeg frame extraction), `scripts/transcribe.py` (caption selection + Whisper orchestration), `scripts/whisper.py` (Groq / OpenAI clients), `scripts/setup.py` (preflight + installer)

Review scripts before first use to verify behavior.
