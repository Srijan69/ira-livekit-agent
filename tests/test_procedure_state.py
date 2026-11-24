import hashlib
import json

import pytest
from test_task_routing import RESEARCH

from skills import SkillCatalog


def test_revisions_and_authored_enabled_metadata_are_not_self_approval(tmp_path):
    catalog = SkillCatalog(tmp_path)
    catalog.create_skill(
        "research-topic",
        RESEARCH.replace("task_kinds:", "state: enabled\n    task_kinds:"),
        category="web",
    )
    info = catalog.get_skill("research-topic")
    assert catalog.state_store.state(info)["state"] == "draft"
    catalog.enable_skill(info.canonical_id, available_tools=("browse_to", "read_page"))
    assert catalog.state_store.state(info)["state"] == "enabled"
    catalog.edit_skill(info.canonical_id, RESEARCH + "\nChanged.")
    edited = catalog.get_skill(info.canonical_id)
    assert catalog.state_store.state(edited)["state"] == "draft"
    assert len(catalog.state_store.versions(info.canonical_id)) == 2
    assert catalog.state_store.version_text(info.canonical_id, 1).endswith(
        "summarize them."
    )


def test_archive_restore_preserve_source_but_restore_as_draft(tmp_path):
    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("research-topic", RESEARCH, category="web")
    original = catalog.get_skill("research-topic")
    catalog.enable_skill(
        original.canonical_id, available_tools=("browse_to", "read_page")
    )
    assert catalog.state_store.archive(original)
    assert not catalog.state_store.eligible(original)
    restored = catalog.state_store.restore(original)
    assert restored["state"] == "draft" and not restored["archived"]
    assert (
        catalog.state_store.version_text(original.canonical_id, 1) == RESEARCH.strip()
    )


def test_shipped_manifest_only_approves_exact_digest_and_respects_archive(tmp_path):
    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("research-topic", RESEARCH, category="web")
    info = catalog.get_skill("research-topic")
    (tmp_path / "reviewed.json").write_text(
        json.dumps({info.canonical_id: info.digest}), encoding="utf-8"
    )
    assert catalog.state_store.state(info)["state"] == "enabled"
    catalog.state_store.archive(info)
    assert catalog.state_store.state(info)["archived"]
    catalog.edit_skill(info.canonical_id, RESEARCH + "\nChanged.")
    changed = catalog.get_skill(info.canonical_id, include_archived=True)
    assert changed.digest != hashlib.sha256(RESEARCH.strip().encode()).hexdigest()
    assert not catalog.state_store.eligible(changed)


def test_validation_requires_structural_metadata_and_available_tools(tmp_path):
    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("old-package", "Old readable instructions")
    assert catalog.view_skill("old-package")["success"]
    with pytest.raises(ValueError, match="routing metadata"):
        catalog.enable_skill("old-package", available_tools=("browse_to",))
    catalog.create_skill("research-topic", RESEARCH, category="web")
    with pytest.raises(ValueError, match="unavailable"):
        catalog.enable_skill("research-topic", available_tools=("read_page",))


def test_loaded_run_has_no_usage_and_evidence_is_task_bound_redacted(tmp_path):
    from test_task_routing import request

    from task_routing import decide_task

    catalog = SkillCatalog(tmp_path / "skills")
    catalog.create_skill("research-topic", RESEARCH, category="web")
    info = catalog.get_skill("research-topic")
    decision = decide_task(
        request(),
        catalog,
        selected=info.canonical_id,
        explicit=True,
        available_tools=("browse_to", "read_page"),
    )
    store = catalog.state_store
    store.load_run("run-one", info, decision)
    assert store.run("run-one")["status"] == "loaded"
    assert store.state(info)["use_count"] == 0
    with pytest.raises(ValueError, match="task"):
        store.record_action(
            "run-one", "wrong-task", {"action_id": "a", "tool": "read_page"}
        )
    store.record_action(
        "run-one",
        "request-one",
        {
            "action_id": "a",
            "tool": "read_page",
            "outcome": "observed",
            "evidence": "Microsoft private slot body",
        },
    )
    assert store.state(info)["use_count"] == 1
    assert "Microsoft" not in json.dumps(store.run("run-one"))
    assert not store.finish_run(
        "run-one", "request-one", "done", criteria={"read_sources": True}
    )["verified"]
    assert store.run("run-one")["status"] == "error"


def test_only_full_current_goal_evidence_is_verified_and_terminal_immutable(tmp_path):
    from test_task_routing import request

    from task_routing import decide_task

    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("research-topic", RESEARCH, category="web")
    info = catalog.get_skill("research-topic")
    decision = decide_task(
        request(),
        catalog,
        selected=info.canonical_id,
        explicit=True,
        available_tools=("browse_to", "read_page"),
    )
    store = catalog.state_store
    store.load_run("verified", info, decision)
    store.record_action(
        "verified",
        "request-one",
        {"action_id": "read", "tool": "read_page", "outcome": "observed"},
    )
    record = store.finish_run(
        "verified",
        "request-one",
        "done",
        criteria={"read_sources": True, "summary_with_sources": True},
    )
    assert record["verified"] and record["status"] == "done"
    assert store.finish_run("verified", "request-one", "uncertain") == record
    assert store.state(info)["success_count"] == 1


def test_old_parsed_metadata_cannot_approve_changed_document(tmp_path):
    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("research-topic", RESEARCH, category="web")
    parsed = catalog.get_skill("research-topic")
    parsed.path.write_text(RESEARCH + "\nChanged after selection.", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        catalog.state_store.enable(parsed, available_tools=("browse_to", "read_page"))


def test_create_never_overwrites_an_existing_reviewed_package(tmp_path):
    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("research-topic", RESEARCH, category="web")
    catalog.enable_skill("research-topic", available_tools=("browse_to", "read_page"))
    result = catalog.create_skill(
        "research-topic", "Different unreviewed body", category="web"
    )
    assert not result["success"]
    assert catalog.get_skill("research-topic").body.endswith("summarize them.")


def test_support_change_invalidates_an_already_selected_decision(tmp_path):
    from test_task_routing import request

    from task_routing import decide_task

    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("research-topic", RESEARCH, category="web")
    decision = decide_task(
        request(),
        catalog,
        available_tools=("browse_to", "read_page"),
        selected="research-topic",
        explicit=True,
    )
    assert decision.validate_current(request(), catalog)
    catalog.write_supporting_file(
        "research-topic", "references/new.md", "Changed guidance"
    )
    assert not decision.validate_current(request(), catalog)
    catalog.validate_skill("research-topic", available_tools=("browse_to", "read_page"))
    assert not decision.validate_current(request(), catalog)


def test_package_digest_tracks_support_content_and_normalizes_document_newlines(
    tmp_path,
):
    from skills import package_digest

    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("research-topic", RESEARCH, category="web")
    info = catalog.get_skill("research-topic")
    original = info.digest
    info.path.write_bytes(RESEARCH.strip().replace("\n", "\r\n").encode())
    assert package_digest(info.path) == original
    catalog.write_supporting_file(
        "research-topic", "scripts/action.py", "print('first')"
    )
    first = catalog.get_skill("research-topic").digest
    assert first != original
    catalog.write_supporting_file(
        "research-topic", "scripts/action.py", "print('second')"
    )
    assert catalog.get_skill("research-topic").digest != first
    catalog.remove_supporting_file("research-topic", "scripts/action.py")
    assert catalog.get_skill("research-topic").digest == original


@pytest.mark.parametrize("verified_criteria", [True, False])
def test_returned_preliminary_actions_need_independent_completion_evidence(
    tmp_path, verified_criteria
):
    from test_task_routing import request

    from task_routing import decide_task

    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("research-topic", RESEARCH, category="web")
    info = catalog.get_skill("research-topic")
    decision = decide_task(
        request(),
        catalog,
        selected=info.canonical_id,
        explicit=True,
        available_tools=("browse_to", "read_page"),
    )
    store = catalog.state_store
    store.load_run("returned", info, decision)
    store.record_action(
        "returned", "request-one", {"tool": "browse_to", "outcome": "returned"}
    )
    store.record_action(
        "returned", "request-one", {"tool": "read_page", "outcome": "observed"}
    )
    result = store.finish_run(
        "returned",
        "request-one",
        "done",
        criteria={
            "read_sources": verified_criteria,
            "summary_with_sources": verified_criteria,
        },
    )
    assert result["verified"] is verified_criteria
    assert result["status"] == ("done" if verified_criteria else "error")


def test_every_authoring_revision_is_retained_and_support_edit_invalidates(tmp_path):
    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("research-topic", RESEARCH, category="web")
    info = catalog.get_skill("research-topic")
    catalog.edit_skill(info.canonical_id, RESEARCH + "\nFirst edit.")
    catalog.edit_skill(info.canonical_id, RESEARCH + "\nSecond edit.")
    assert catalog.state_store.versions(info.canonical_id) == [1, 2, 3]
    catalog.enable_skill(info.canonical_id, available_tools=("browse_to", "read_page"))
    catalog.write_supporting_file(
        info.canonical_id, "references/source.md", "Changed instructions"
    )
    assert (
        catalog.state_store.state(catalog.get_skill(info.canonical_id))["state"]
        == "draft"
    )


def test_deleted_package_remains_recoverable_with_support_files(tmp_path):
    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("research-topic", RESEARCH, category="web")
    catalog.write_supporting_file(
        "research-topic", "references/source.md", "Sources guide"
    )
    info = catalog.get_skill("research-topic")
    assert catalog.delete_skill(info.canonical_id)["success"]
    assert catalog.get_skill(info.canonical_id) is None
    assert catalog.restore_skill(info.canonical_id)["state"] == "draft"
    assert catalog.get_skill(info.canonical_id).path.exists()
    assert (
        "Sources guide"
        in catalog.view_skill(info.canonical_id, "references/source.md")["content"]
    )


def test_failed_action_cannot_become_verified_by_completion_flags(tmp_path):
    from test_task_routing import request

    from task_routing import decide_task

    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("research-topic", RESEARCH, category="web")
    info = catalog.get_skill("research-topic")
    decision = decide_task(
        request(),
        catalog,
        selected=info.canonical_id,
        explicit=True,
        available_tools=("browse_to", "read_page"),
    )
    store = catalog.state_store
    store.load_run("failed", info, decision)
    store.record_action(
        "failed", "request-one", {"tool": "read_page", "outcome": "uncertain"}
    )
    result = store.finish_run(
        "failed",
        "request-one",
        "done",
        criteria={"read_sources": True, "summary_with_sources": True},
    )
    assert result["status"] == "error" and not result["verified"]


def test_duplicate_parallel_callbacks_count_usage_and_success_once(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from test_task_routing import request

    from task_routing import decide_task

    catalog = SkillCatalog(tmp_path)
    catalog.create_skill("research-topic", RESEARCH, category="web")
    info = catalog.get_skill("research-topic")
    decision = decide_task(
        request(),
        catalog,
        selected=info.canonical_id,
        explicit=True,
        available_tools=("browse_to", "read_page"),
    )
    store = catalog.state_store
    store.load_run("concurrent", info, decision)
    original = store._bound_run
    barrier = Barrier(2)

    def synchronized_snapshot(*args):
        record = original(*args)
        barrier.wait(timeout=5)
        return record

    monkeypatch.setattr(store, "_bound_run", synchronized_snapshot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda _: store.record_action(
                    "concurrent",
                    "request-one",
                    {"action_id": "same", "tool": "read_page", "outcome": "observed"},
                ),
                range(2),
            )
        )
    assert store.state(info)["use_count"] == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda _: store.finish_run(
                    "concurrent",
                    "request-one",
                    "done",
                    criteria={"read_sources": True, "summary_with_sources": True},
                ),
                range(2),
            )
        )
    assert store.state(info)["success_count"] == 1
