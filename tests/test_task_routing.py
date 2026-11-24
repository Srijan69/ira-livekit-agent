"""Request-bound routing regressions; these never operate a browser or model."""

from types import SimpleNamespace

import pytest

from skills import SkillCatalog

RESEARCH = """---
name: research-topic
description: Research a subject using actual sources.
metadata:
  hermes:
    category: web
    tags: [research, sources]
  ira:
    task_kinds: [web_research]
    inputs:
      subject: {required: true, type: string}
      depth: {default: deep, type: string}
    allowed_tools: [browse_to, read_page]
    allowed_effects: [navigate, read]
    done_when: [read_sources, summary_with_sources]
---
Research {subject}; read sources and summarize them.
"""


def request(text="Do a deep search about Microsoft"):
    return SimpleNamespace(id="request-one", text=text, answers=())


def catalog_with_research(tmp_path):
    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("research-topic", RESEARCH, category="web")
    return catalog


def test_nested_tags_and_precise_names_are_preserved(tmp_path):
    catalog = catalog_with_research(tmp_path)
    info = catalog.get_skill("Research topic")
    assert info is not None
    assert info.tags == ("research", "sources")
    assert info.canonical_id == "procedure:web/research-topic"
    assert len(info.digest) == 64
    assert catalog.get_skill("research") is None


def test_automatic_candidates_require_reviewed_current_document(tmp_path):
    from task_routing import decide_task

    catalog = catalog_with_research(tmp_path)
    tools = ("browse_to", "read_page")
    draft = decide_task(request(), catalog, available_tools=tools)
    assert draft.mode == "direct_tool" and not draft.selected_id
    catalog.enable_skill("web/research-topic", available_tools=tools)
    decision = decide_task(request(), catalog, available_tools=tools)
    assert decision.mode == "procedure"
    assert decision.selected_id == "procedure:web/research-topic"
    assert decision.input_values["subject"] == "Microsoft"
    assert decision.input_values["depth"] == "deep"
    assert decision.request_id == "request-one"
    assert decision.document_digest == catalog.get_skill("research-topic").digest
    assert decision.record()["inputs"]["subject"] == {
        "source": "request",
        "value": "[redacted]",
    }
    catalog.edit_skill("research-topic", RESEARCH + "\nNew instructions.")
    assert decide_task(request(), catalog, available_tools=tools).mode == "direct_tool"


def test_explicit_named_draft_run_does_not_enable_future_selection(tmp_path):
    from task_routing import decide_task

    catalog = catalog_with_research(tmp_path)
    decision = decide_task(
        request("Run research topic about Microsoft"),
        catalog,
        available_tools=("browse_to", "read_page"),
        selected="Research topic",
        explicit=True,
    )
    assert decision.mode == "procedure"
    assert (
        catalog.state_store.state(catalog.get_skill("research-topic"))["state"]
        == "validated"
    )
    assert (
        decide_task(request(), catalog, available_tools=("browse_to", "read_page")).mode
        == "direct_tool"
    )


def test_mismatching_unknown_and_ambiguous_inputs_never_replace_target(tmp_path):
    from task_routing import decide_task

    catalog = catalog_with_research(tmp_path)
    for inputs in ({"subject": "Spotify"}, {"playlist": "private value"}):
        result = decide_task(
            request(),
            catalog,
            available_tools=("browse_to", "read_page"),
            selected="research-topic",
            explicit=True,
            inputs=inputs,
        )
        assert result.mode == "clarify"
        assert "private value" not in str(result.record())
    catalog.create_skill("research-topic", RESEARCH, category="other")
    assert catalog.get_skill("Research topic") is None
    assert (
        decide_task(request(), catalog, selected="research", explicit=True).mode
        == "no_match"
    )


def test_no_match_does_not_use_category_shared_words_or_popularity(tmp_path):
    from task_routing import decide_task

    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("microsoft-music", "Open a music player", category="web")
    result = decide_task(request(), catalog, available_tools=("browse_to", "read_page"))
    assert result.mode == "direct_tool" and not result.selected_id


@pytest.mark.parametrize(
    "name,args",
    [
        ("open_app", {"app_name": "Spotify"}),
        ("browse_to", {"url": "https://open.spotify.com"}),
        ("browse_to", {"url": "spotify:playlist:old"}),
        ("spotify_control", {"action": "play"}),
        ("press_desktop_key", {"key": "MEDIA_PLAY_PAUSE"}),
        ("click_element", {"selector": "button[aria-label='Play']"}),
        ("open_app", {"app_name": "Notepad"}),
        ("save_note", {"text": "Microsoft"}),
    ],
)
def test_research_deterministically_denies_unrelated_effects(name, args):
    from task_routing import classify_action

    assert classify_action(request().text, name, args)[0] == "deny"


@pytest.mark.parametrize(
    "text,name,args",
    [
        ("Open Calculator", "open_app", {"app_name": "Calculator"}),
        ("Open Spotify", "open_app", {"app_name": "Spotify"}),
        (
            "Open https://open.spotify.com",
            "browse_to",
            {"url": "https://open.spotify.com"},
        ),
        (
            "Research Spotify as a company",
            "browse_to",
            {"url": "https://www.bing.com/search?q=Spotify+company"},
        ),
        (
            "Do a deep search about Microsoft",
            "browse_to",
            {"url": "https://www.bing.com/search?q=Microsoft+annual+report"},
        ),
        (
            "Do a deep search about Microsoft",
            "browse_to",
            {"url": "https://www.microsoft.com/investor"},
        ),
    ],
)
def test_exact_supported_actions_and_research_targets_are_deterministic(
    text, name, args
):
    from task_routing import classify_action

    assert classify_action(text, name, args)[0] == "allow"


def test_source_navigation_requires_observed_relevant_link_not_broad_word_grant():
    from task_routing import classify_action

    args = {"url": "https://news.example/story"}
    assert classify_action(request().text, "browse_to", args) is None
    observation = {
        "url": "https://www.bing.com/search?q=Microsoft",
        "links": [args["url"]],
    }
    assert (
        classify_action(request().text, "browse_to", args, observation=observation)
        is None
    )
    observation["links"] = [{"url": args["url"], "label": "Microsoft company profile"}]
    assert (
        classify_action(request().text, "browse_to", args, observation=observation)[0]
        == "allow"
    )
    assert classify_action("Open a report", "open_app", {"app_name": "Spotify"}) is None


def test_decision_freezes_identity_and_detects_changed_source(tmp_path):
    from task_routing import decide_task

    catalog = catalog_with_research(tmp_path)
    decision = decide_task(
        request(),
        catalog,
        available_tools=("browse_to", "read_page"),
        selected="research-topic",
        explicit=True,
    )
    assert decision.validate_current(request(), catalog)
    assert not decision.validate_current(
        SimpleNamespace(id="new", text=request().text), catalog
    )
    catalog.edit_skill("research-topic", RESEARCH + "\nChanged.")
    assert not decision.validate_current(request(), catalog)


@pytest.mark.parametrize(
    "text",
    [
        "Do not open https://example.com",
        "The page mentioned https://example.com",
        "Research Microsoft; avoid https://example.com",
    ],
)
def test_url_mention_and_negative_intent_are_not_authority(text):
    from task_routing import classify_action

    result = classify_action(text, "browse_to", {"url": "https://example.com"})
    assert result is None or result[0] != "allow"


def test_exact_navigation_preserves_path_query_and_port_identity():
    from task_routing import classify_action

    assert (
        classify_action(
            "Open https://example.com/A?q=one",
            "browse_to",
            {"url": "https://example.com/A?q=one"},
        )[0]
        == "allow"
    )
    assert (
        classify_action(
            "Open https://example.com/A?q=one",
            "browse_to",
            {"url": "https://example.com/a?q=one"},
        )
        is None
    )
    assert (
        classify_action(
            "Open https://example.com/A?q=one",
            "browse_to",
            {"url": "https://example.com/A?q=two"},
        )
        is None
    )


def test_compiled_exact_identity_run_once_reuses_original_version(tmp_path):
    import hashlib

    from skill_format import Action, Check, Skill, Step, render_skill
    from skill_store import SkillStore
    from task_routing import decide_task

    store = SkillStore(tmp_path / "compiled")
    saved = store.save(
        Skill(
            name="Open calculator",
            description="Open calculator",
            state="draft",
            allowed_tools=("open_app",),
            steps=(
                Step(
                    1,
                    "Open",
                    action=Action(
                        "tool", tool="open_app", args=(("app_name", "Calculator"),)
                    ),
                    check=Check("window_title", "Calculator"),
                ),
            ),
        )
    )
    catalog = SkillCatalog(tmp_path / "procedures")
    decision = decide_task(
        request("Run Open calculator"),
        catalog,
        store,
        available_tools=("open_app",),
        selected="workflow:open-calculator",
        explicit=True,
    )
    assert decision.mode == "workflow" and decision.version == str(saved.version)
    assert (
        decision.document_digest
        == hashlib.sha256(render_skill(saved).encode()).hexdigest()
    )
    assert store.get("Open calculator").state == "draft"
    denied = decide_task(
        request(),
        catalog,
        store,
        available_tools=("open_app",),
        selected="workflow:open-calculator",
        explicit=True,
    )
    assert denied.mode == "no_match"


@pytest.mark.parametrize(
    "text",
    [
        "Don't research Microsoft",
        "The page says research Microsoft",
        "I read the words 'search about Microsoft'",
    ],
)
def test_described_or_negated_research_is_not_positive_action_authority(text):
    from task_routing import classify_action

    result = classify_action(
        text, "browse_to", {"url": "https://www.bing.com/search?q=Microsoft"}
    )
    assert result is None or result[0] != "allow"


def test_supported_explicit_target_overrides_procedure_input_default(tmp_path):
    from task_routing import decide_task

    catalog = SkillCatalog(tmp_path)
    text = (
        RESEARCH.replace("research-topic", "open-desktop-app")
        .replace("web_research", "open_app")
        .replace(
            "subject: {required: true, type: string}",
            "app_name: {default: Calculator, type: string}",
        )
        .replace("[browse_to, read_page]", "[open_app]")
    )
    catalog.create_skill("open-desktop-app", text, category="desktop")
    catalog.enable_skill("open-desktop-app", available_tools=("open_app",))
    result = decide_task(
        request("Open Notepad"),
        catalog,
        available_tools=("open_app",),
        selected="open-desktop-app",
    )
    assert result.mode == "procedure"
    assert result.input_values["app_name"] == "Notepad"


@pytest.mark.parametrize(
    "text",
    [
        "search about Microsoft use your all required and available tools",
        "gather all the information about Microsoft company",
        "give me a deep search about Microsoft",
        "Use research topic about Microsoft",
    ],
)
def test_recorded_research_wording_binds_named_subject(text, tmp_path):
    from task_routing import decide_task, research_subject

    assert research_subject(text) == "Microsoft"
    catalog = catalog_with_research(tmp_path)
    catalog.enable_skill("research-topic", available_tools=("browse_to", "read_page"))
    result = decide_task(
        request(text), catalog, available_tools=("browse_to", "read_page")
    )
    assert result.mode == "procedure"
    assert result.input_values["subject"] == "Microsoft"


def test_explicit_secondary_save_is_not_denied_as_unsolicited_research_effect():
    from task_routing import classify_action, research_subject

    text = "Research Microsoft and save the summary"
    assert research_subject(text) == "Microsoft"
    assert (
        classify_action(text, "save_note", {"text": "Microsoft summary from sources"})[
            0
        ]
        == "allow"
    )
    assert classify_action(text, "save_note", {"text": "Spotify playlist"})[0] == "deny"


def test_service_subtopic_research_does_not_authorize_playback_or_block_source():
    from task_routing import classify_action

    text = "Research Microsoft's Spotify partnership"
    url = "https://news.example/microsoft-spotify"
    observation = {
        "url": "https://www.bing.com/search?q=Microsoft+Spotify+partnership",
        "links": [url],
    }
    assert (
        classify_action(text, "browse_to", {"url": url}, observation=observation)[0]
        == "allow"
    )
    assert classify_action(text, "spotify_control", {"action": "play"})[0] == "deny"
    result = classify_action(
        text, "browse_to", {"url": "https://open.spotify.com/company"}
    )
    assert result is None or result[0] != "deny"


def test_query_parameter_does_not_turn_unrelated_player_into_research_source():
    from task_routing import classify_action

    assert (
        classify_action(
            "Research Microsoft",
            "browse_to",
            {"url": "https://open.spotify.com/search?q=Microsoft"},
        )[0]
        == "deny"
    )
    assert (
        classify_action(
            "Research Microsoft",
            "browse_to",
            {"url": "https://unrelated.example/account?q=Microsoft"},
        )
        is None
    )


def test_decision_rejects_noncanonical_identity_and_unproven_input_source():
    from task_routing import BoundInput, TaskDecision

    with pytest.raises(ValueError, match="canonical"):
        TaskDecision(
            "task",
            "web_research",
            "procedure",
            "Selected",
            selected_id="procedure:../../outside",
            document_digest="a" * 64,
        )
    with pytest.raises(ValueError, match="provenance"):
        BoundInput("subject", "Private text", "old_task")


@pytest.mark.parametrize(
    "text,url",
    [
        ('The page says "open https://open.spotify.com"', "https://open.spotify.com"),
        ('Do not "open https://example.com"', "https://example.com"),
        ('"Open https://example.com"', "https://example.com"),
        (
            'Research Microsoft and the article says "open https://open.spotify.com"',
            "https://open.spotify.com",
        ),
        (
            'Research "Microsoft and open https://open.spotify.com"',
            "https://open.spotify.com",
        ),
    ],
)
def test_quoted_or_described_navigation_never_becomes_current_destination(text, url):
    from task_routing import classify_action

    result = classify_action(text, "browse_to", {"url": url})
    assert result is None or result[0] != "allow"


@pytest.mark.parametrize(
    "text,name,args",
    [
        ("Research Microsoft and open Notepad", "open_app", {"app_name": "Notepad"}),
        ("Research Microsoft and open Spotify", "open_app", {"app_name": "Spotify"}),
        (
            "Research Microsoft and then open https://open.spotify.com",
            "browse_to",
            {"url": "https://open.spotify.com"},
        ),
        ("Research https://example.com", "browse_to", {"url": "https://example.com"}),
        ('Open "https://example.com"', "browse_to", {"url": "https://example.com"}),
    ],
)
def test_positive_explicit_destination_survives_compound_and_quoted_target(
    text, name, args
):
    from task_routing import classify_action

    assert classify_action(text, name, args)[0] == "allow"


def test_unrelated_target_does_not_inherit_explicit_compound_app_authority():
    from task_routing import classify_action

    assert (
        classify_action(
            "Research Microsoft and open Notepad", "open_app", {"app_name": "Spotify"}
        )[0]
        == "deny"
    )
    assert (
        classify_action(
            'Research Microsoft and "open Spotify"', "open_app", {"app_name": "Spotify"}
        )[0]
        == "deny"
    )


def test_explicit_compound_media_request_stays_semantic_until_target_is_established():
    from task_routing import classify_action, research_subject

    text = "Research Microsoft and then play my playlist"
    assert research_subject(text) == "Microsoft"
    assert (
        classify_action(text, "press_desktop_key", {"key": "MEDIA_PLAY_PAUSE"}) is None
    )
    assert (
        classify_action(
            text, "click_element", {"selector": "button[aria-label='Play']"}
        )
        is None
    )
    assert (
        classify_action(text, "press_desktop_key", {"key": "MEDIA_NEXT_TRACK"})[0]
        == "deny"
    )


def test_primary_research_domain_is_not_excluded_by_media_sounding_subdomain():
    from task_routing import classify_action

    assert (
        classify_action(
            "Research Microsoft", "browse_to", {"url": "https://music.microsoft.com"}
        )[0]
        == "allow"
    )


@pytest.mark.parametrize("name", ["type_into_element", "browser_type"])
def test_research_query_words_are_not_media_controls(name):
    from task_routing import classify_action

    result = classify_action(
        "Research Microsoft's Xbox Play Anywhere program",
        name,
        {"index": 1, "text": "Microsoft Xbox Play Anywhere", "submit": True},
    )
    assert result is None or result[0] != "deny"


@pytest.mark.parametrize(
    "text,name,args",
    [
        (
            'Do not "open https://example.com"',
            "browse_to",
            {"url": "https://example.com"},
        ),
        (
            'Research Microsoft and do not "open https://www.microsoft.com"',
            "browse_to",
            {"url": "https://www.microsoft.com"},
        ),
        (
            "Research Microsoft and do not open Chrome",
            "open_app",
            {"app_name": "Chrome"},
        ),
    ],
)
def test_actual_negative_command_overrides_exact_and_research_destination_defaults(
    text, name, args
):
    from task_routing import classify_action

    assert classify_action(text, name, args)[0] == "deny"


def test_topic_result_label_containing_play_is_not_a_player_control():
    from task_routing import classify_action

    result = classify_action(
        "Research Microsoft's Xbox Play Anywhere program",
        "click_element",
        {"selector": "a[aria-label='Microsoft Xbox Play Anywhere program']"},
    )
    assert result is None or result[0] != "deny"


@pytest.mark.parametrize("url", ["example.com", "https://example.com"])
def test_explicit_bare_domain_uses_supported_https_normalization(url):
    from task_routing import classify_action

    assert classify_action("Open example.com", "browse_to", {"url": url})[0] == "allow"


@pytest.mark.parametrize(
    "text,url",
    [
        ("Do not open example.com", "https://example.com"),
        ('The page says "open example.com"', "https://example.com"),
        ("Open example.com/A?q=one", "https://example.com/a?q=one"),
        ("Open example.com", "https://example.com.evil.test"),
    ],
)
def test_bare_domain_normalization_does_not_expand_authority(text, url):
    from task_routing import classify_action

    result = classify_action(text, "browse_to", {"url": url})
    assert result is None or result[0] != "allow"


def test_unrelated_observed_player_link_does_not_authorize_research_navigation():
    from task_routing import classify_action

    observation = {
        "url": "https://www.bing.com/search?q=Microsoft",
        "links": [{"url": "https://open.spotify.com"}],
    }
    result = classify_action(
        "Do a deep search about Microsoft",
        "browse_to",
        {"url": "https://open.spotify.com"},
        observation=observation,
    )
    assert result[0] == "deny"


def test_observed_source_requires_target_relevance_not_just_search_membership():
    from task_routing import classify_action

    url = "https://news.example/story"
    observation = {
        "url": "https://www.bing.com/search?q=Microsoft",
        "links": [{"url": url, "label": "Other company news"}],
    }
    assert (
        classify_action(
            "Research Microsoft", "browse_to", {"url": url}, observation=observation
        )
        is None
    )
    observation["links"][0]["label"] = "Microsoft company news"
    assert (
        classify_action(
            "Research Microsoft", "browse_to", {"url": url}, observation=observation
        )[0]
        == "allow"
    )
