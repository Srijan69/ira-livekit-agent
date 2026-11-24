"""Retirement edits operating recipes, preserving personal facts and history."""

from capability_cleanup import retire_music_recipes
from dual_memory import DualMemoryStore
from personal_memory import PersonalMemoryStore


def test_retirement_preserves_personal_facts_and_unrelated_profile(tmp_path):
    legacy = DualMemoryStore(tmp_path / "memories")
    legacy.add("memory", "IRA has browser automation, Spotify playback, and skills.")
    legacy.add("user", "My favorite playlist is Example Playlist.")
    legacy.add("user", "When asked to play the playlist, open it and play a song.")
    legacy.add("user", "Prefer concise answers.")
    profile = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    profile.save_profile(
        {"preferences": "Spotify: use Brave only.\nPrefer concise answers."},
        source="explicit",
    )
    result = retire_music_recipes(legacy, profile)
    assert result["profile_changed"] is True
    assert profile.get_profile()["preferences"] == "Prefer concise answers."
    assert legacy.list_entries("user") == [
        "My favorite playlist is Example Playlist.",
        "Prefer concise answers.",
    ]
    assert legacy.list_entries("memory") == ["IRA has browser automation, and skills."]
    assert retire_music_recipes(legacy, profile)["profile_changed"] is False
    profile.close()
