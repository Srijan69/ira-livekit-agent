---
name: play-spotify
description: "Play a requested Spotify song or playlist in the preferred browser and verify its identity and playback."
version: 1.1.0
author: "IRA Agent"
platforms: [windows]
metadata:
  hermes:
    category: media
    tags: [spotify, music, media, audio, phonk]
---

# Play Spotify Skill

This skill guides IRA in opening Spotify, selecting playlists, and verifying playback.

## When to Use
- The boss asks to play music, play phonk songs, play funk songs, or put on a playlist.
- The boss asks to control playback (play, pause, rewind, forward).

## Prerequisites & Preferences
- Read the current request and saved preferences. Use Brave when Spotify is assigned to Brave; never substitute Chrome, the system browser, or the desktop Spotify app.
- Use the song/playlist actually requested. Do not substitute a default playlist or the first search result. Ask for the artist if the same song title is ambiguous.
- Spotify needs a signed-in IRA browser profile for full playback. Never request passwords in chat; let the boss sign in directly when needed.

## Procedure
1. For a song, call `spotify_play_track(name=<requested title>, artist=<known artist or empty>)`. It opens the correct browser as needed, selects a matching track, and verifies now-playing identity and playback.
2. For a playlist, call `spotify_play_playlist(name=<requested playlist>)`. For play/pause/rewind/forward use `spotify_control`. `open_spotify` alone only opens the player.
3. Inspect the result. A matching track and observed active playback are required for success. A requested click, opened page, or unrelated playing song is insufficient.
4. On a blocker, use `read_page`, `list_page_elements`, or `capture_screen` to inspect the same browser. Ask a specific question for an ambiguous track or let the boss sign in. Never repeat an uncertain play click blindly or switch browsers.
5. Continue the current requested goal until verified or a specific blocker needs the boss. Report only what was observed, addressing the boss as Boss or Sir.

This is an ordinary Hermes-style procedure. Load it with `skill_view`; follow it with actual tools. Loading it is not a completed playback task.
