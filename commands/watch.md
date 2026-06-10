---
description: Watch a video (URL or local path). By default pulls the timestamped transcript only (yt-dlp captions or Whisper fallback, no video download); add --frames to also download the video and extract frames with ffmpeg. Answers questions about what's in the video.
argument-hint: <video-url-or-path> [question]
allowed-tools: [Bash, Read, AskUserQuestion]
---

Invoke the `watch` skill (defined in SKILL.md) with the user's arguments: $ARGUMENTS

Follow the skill's full pipeline: preflight setup check → fetch the transcript (yt-dlp native captions via `--skip-download`, or audio-only Whisper fallback) → answer the user grounded in the transcript. This is the default, transcript-only path — no video is downloaded and there are no frames to Read. Only when the question depends on what's *on screen* (visuals, slides, UI, on-screen code, a "what happens at…" moment, a bug recording, or an explicit request to look at the video), add `--frames` so the skill also downloads the video, extracts frames at an auto-scaled fps, and lists frame paths for you to Read. If the user provided no arguments, ask them for a video URL or local path before proceeding.
