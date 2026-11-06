---
name: play-spotify
description: "Open Spotify and control music playback or playlists."
version: 1.0.0
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
- The boss asks to control playback (play, pause, next track).

## Prerequisites & Preferences
- Preferred playlist: "My Phonk Songs" (often spoken by the boss as funk or phonk songs).
- Browser/Desktop preference: When using browser Spotify, use the authenticated IRA profile (Brave or Chrome).
- **CRITICAL**: Always verify playback status using `capture_screen` or audio checks to ensure the track is actively playing.

## Procedure
1. **Open Spotify**:
   - Call `open_spotify` or launch the desktop Spotify app.
   - Wait for the Spotify interface to load.
2. **Select Playlist**:
   - Locate the target playlist (e.g., "My Phonk Songs") in the pinned sidebar library instead of using search if available.
   - Alternatively, call `spotify_play_playlist(name="My Phonk Songs")`.
3. **Verify Playback**:
   - Inspect the play/pause button state (must be in "Pause" state, indicating music is playing).
   - If not playing, click the play button.
