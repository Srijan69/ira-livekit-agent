---
name: Play my funk songs
goal: Play my funk songs on Spotify
version: 1
worker: lightweight
linear: true
requires_screen: false
allowed_tools: open_spotify, spotify_play_playlist
failure_policy: stop
---

# Play my favorite Spotify funk songs

Play my funk songs on Spotify

## Steps

1. open_spotify: browser=chrome
2. spotify_play_playlist: name=my funk songs

## Prerequisites

- The IRA Chrome profile is signed in to Spotify.

## Verification

- Spotify reports that playback is active.

## Recovery

- Stop at the failed step and report it to the boss.
