"""Eligibility and outcome adapter for nested adaptive SKILL.md packages."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from skill_runs import TERMINAL_STATUSES, SkillRunStore
from skills import package_snapshot


class ProcedureStateStore:
    def __init__(self, path, *, reviewed_manifest=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.reviewed_manifest = Path(reviewed_manifest) if reviewed_manifest else None
        self._active_inputs = {}
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS procedures (canonical_id TEXT PRIMARY KEY,digest TEXT,state TEXT,archived INTEGER DEFAULT 0,review_source TEXT DEFAULT '',use_count INTEGER DEFAULT 0,failure_count INTEGER DEFAULT 0,success_count INTEGER DEFAULT 0)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS procedure_versions (canonical_id TEXT,version INTEGER,digest TEXT,markdown TEXT,PRIMARY KEY(canonical_id,version))"
            )
        self.run_store = SkillRunStore(self.path)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _shipped_review(self, info):
        if self.reviewed_manifest is None:
            return False
        try:
            manifest = json.loads(self.reviewed_manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (
            isinstance(manifest, dict)
            and manifest.get(info.canonical_id) == info.digest
        )

    @staticmethod
    def _current_document(info):
        digest, text, _ = package_snapshot(info.path)
        if digest != info.digest:
            raise ValueError(
                "The selected procedure document changed; re-resolve its current metadata."
            )
        return text

    def observe(self, info):
        markdown = self._current_document(info)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM procedures WHERE canonical_id=?", (info.canonical_id,)
            ).fetchone()
            if row is None or row["digest"] != info.digest:
                version = db.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM procedure_versions WHERE canonical_id=?",
                    (info.canonical_id,),
                ).fetchone()[0]
                db.execute(
                    "INSERT INTO procedure_versions VALUES (?,?,?,?)",
                    (
                        info.canonical_id,
                        version,
                        info.digest,
                        markdown,
                    ),
                )
                db.execute(
                    "INSERT INTO procedures(canonical_id,digest,state) VALUES (?,?,'draft') ON CONFLICT(canonical_id) DO UPDATE SET digest=excluded.digest,state='draft',review_source='edited'",
                    (info.canonical_id, info.digest),
                )
            row = dict(
                db.execute(
                    "SELECT * FROM procedures WHERE canonical_id=?",
                    (info.canonical_id,),
                ).fetchone()
            )
            if (
                self._shipped_review(info)
                and row["review_source"] not in {"user", "edited"}
                and not row["archived"]
            ):
                db.execute(
                    "UPDATE procedures SET state='enabled',review_source='manifest' WHERE canonical_id=?",
                    (info.canonical_id,),
                )
                row.update(state="enabled", review_source="manifest")
            row["archived"] = bool(row["archived"])
            return row

    state = observe

    def eligible(self, info):
        row = self.state(info)
        return row["state"] == "enabled" and not row["archived"]

    @staticmethod
    def validate_metadata(info, *, available_tools=()):
        routing = info.routing
        required = ("task_kinds", "allowed_tools", "allowed_effects", "done_when")
        if not all(
            isinstance(routing.get(key), (list, tuple))
            and routing[key]
            and all(isinstance(item, str) and item.strip() for item in routing[key])
            for key in required
        ) or not isinstance(routing.get("inputs", {}), dict):
            raise ValueError(
                "Procedure needs reviewed structural routing metadata before execution."
            )
        if available_tools and set(routing["allowed_tools"]) - set(available_tools):
            raise ValueError("Required procedure tools are unavailable.")

    def can_run_once(self, info, *, available_tools=()):
        try:
            self.validate_metadata(info, available_tools=available_tools)
        except ValueError:
            return False
        return not self.state(info)["archived"]

    def validate(self, info, *, available_tools=()):
        self.validate_metadata(info, available_tools=available_tools)
        row = self.state(info)
        if row["archived"]:
            raise ValueError("Procedure is archived.")
        if row["state"] == "draft":
            with self._connect() as db:
                db.execute(
                    "UPDATE procedures SET state='validated',review_source='user' WHERE canonical_id=? AND digest=?",
                    (info.canonical_id, info.digest),
                )
        return self.state(info)

    def enable(self, info, *, available_tools=()):
        self.validate(info, available_tools=available_tools)
        with self._connect() as db:
            db.execute(
                "UPDATE procedures SET state='enabled',review_source='user' WHERE canonical_id=? AND digest=?",
                (info.canonical_id, info.digest),
            )
        return self.state(info)

    def invalidate(self, info):
        self.observe(info)
        with self._connect() as db:
            db.execute(
                "UPDATE procedures SET state='draft',review_source='edited' WHERE canonical_id=?",
                (info.canonical_id,),
            )

    def archive(self, info):
        self.observe(info)
        with self._connect() as db:
            db.execute(
                "UPDATE procedures SET archived=1,review_source='user' WHERE canonical_id=?",
                (info.canonical_id,),
            )
        return True

    def restore(self, info):
        self.observe(info)
        with self._connect() as db:
            db.execute(
                "UPDATE procedures SET archived=0,state='draft',review_source='user' WHERE canonical_id=?",
                (info.canonical_id,),
            )
        return self.state(info)

    def versions(self, canonical_id):
        with self._connect() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT version FROM procedure_versions WHERE canonical_id=? ORDER BY version",
                    (canonical_id,),
                )
            ]

    def version_text(self, canonical_id, version):
        with self._connect() as db:
            row = db.execute(
                "SELECT markdown FROM procedure_versions WHERE canonical_id=? AND version=?",
                (canonical_id, version),
            ).fetchone()
        return row[0] if row else None

    def load_run(self, run_id, info, decision):
        if (
            decision.selected_id != info.canonical_id
            or decision.document_digest != info.digest
        ):
            raise ValueError("Procedure decision identity changed before loading.")
        markdown = self._current_document(info)
        self.observe(info)
        record = {
            "run_id": run_id,
            "mode": "procedure",
            "skill_slug": info.canonical_id,
            "skill_name": info.name,
            "skill_version": info.version,
            "skill_markdown": markdown,
            "request_id": decision.request_id,
            "document_digest": info.digest,
            "supporting_digests": list(info.supporting_digests),
            "decision_digest": decision.digest,
            "decision": decision.record(),
            "input_names": [item.name for item in decision.inputs],
            "input_provenance": {item.name: item.source for item in decision.inputs},
            "current_goal": decision.task_kind,
            "selection_reason": decision.reason,
            "done_when": list(decision.done_when),
            "status": "loaded",
            "verified": False,
            "started_at": time.time() * 1000,
            "finished_at": None,
            "steps": [],
            "completed_steps": 0,
            "total_steps": 0,
            "message": "Instructions loaded; no action has run.",
        }
        if not self.run_store.create(record):
            existing = self.run_store.get(run_id)
            if (
                existing["request_id"] != decision.request_id
                or existing["document_digest"] != decision.document_digest
            ):
                raise ValueError(
                    "Procedure delivery identity was reused for a different task or document."
                )
            return existing
        self._active_inputs[run_id] = tuple(
            str(item.value) for item in decision.inputs if item.source != "default"
        )
        return record

    def run(self, run_id):
        return self.run_store.get(run_id)

    def _bound_run(self, run_id, request_id):
        record = self.run(run_id)
        if not record or not request_id or record["request_id"] != request_id:
            raise ValueError("Procedure evidence belongs to a different task.")
        return record

    def _redact(self, run_id, value):
        text = str(value)
        for private in sorted(
            self._active_inputs.get(run_id, ()), key=len, reverse=True
        ):
            if private:
                text = re.sub(re.escape(private), "[redacted]", text, flags=re.I)
        text = re.sub(
            r"(?i)(?:bearer\s+\S+|(?:password|api[_-]?key|secret|token)\s*[=:]\s*\S+)",
            "[redacted]",
            text,
        )
        return text[:2000]

    def record_action(self, run_id, request_id, action):
        self._bound_run(run_id, request_id)
        # The adaptive action and usage counter share one transaction. Tool
        # settlement may be delivered twice or concurrently after reconnect.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = json.loads(
                db.execute(
                    "SELECT record FROM skill_runs WHERE run_id=?", (run_id,)
                ).fetchone()[0]
            )
            if record["status"] in TERMINAL_STATUSES:
                return record
            if action.get("tool") not in record["decision"]["allowed_tools"]:
                raise ValueError(
                    "The action is outside this procedure's current task tools."
                )
            steps = record.setdefault("steps", [])
            action_id = action.get("action_id") or str(len(steps) + 1)
            if any(step.get("action_id") == action_id for step in steps):
                return record
            if record["status"] == "loaded":
                record.update(
                    status="running",
                    message="Work started; objective is not yet verified.",
                )
                db.execute(
                    "UPDATE procedures SET use_count=use_count+1 WHERE canonical_id=?",
                    (record["skill_slug"],),
                )
            steps.append(
                {
                    "step_index": len(steps) + 1,
                    "action_id": action_id,
                    "tool": action["tool"],
                    "outcome": action.get("outcome", "observed"),
                    "evidence": self._redact(run_id, action.get("evidence", "")),
                }
            )
            record.update(completed_steps=len(steps), total_steps=len(steps))
            db.execute(
                "UPDATE skill_runs SET status=?,record=? WHERE run_id=?",
                (record["status"], json.dumps(record, ensure_ascii=False), run_id),
            )
            return record

    def finish_run(self, run_id, request_id, status, *, criteria=None, summary=""):
        self._bound_run(run_id, request_id)
        if status not in TERMINAL_STATUSES:
            raise ValueError("Unknown procedure terminal status.")
        criteria = dict(criteria or {})
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = json.loads(
                db.execute(
                    "SELECT record FROM skill_runs WHERE run_id=?", (run_id,)
                ).fetchone()[0]
            )
            if record["status"] in TERMINAL_STATUSES:
                return record
            verified = (
                status == "done"
                and record["status"] == "running"
                and bool(record["steps"])
                and all(
                    step.get("outcome")
                    in {
                        "observed",
                        "done",
                        "success",
                        "verified",
                        "succeeded",
                        "returned",
                    }
                    for step in record["steps"]
                )
                and all(criteria.get(name) is True for name in record["done_when"])
            )
            if status == "done" and not verified:
                status, summary = (
                    "error",
                    "The current task completion criteria are not verified.",
                )
            if record["status"] != "loaded":
                field = "success_count" if verified else "failure_count"
                db.execute(
                    f"UPDATE procedures SET {field}={field}+1 WHERE canonical_id=?",
                    (record["skill_slug"],),
                )
            record.update(
                status=status,
                verified=verified,
                finished_at=time.time() * 1000,
                criteria={
                    name: criteria.get(name) is True for name in record["done_when"]
                },
                message=self._redact(run_id, summary),
                question="",
                question_id="",
                input_name="",
            )
            db.execute(
                "UPDATE skill_runs SET status=?,record=? WHERE run_id=?",
                (status, json.dumps(record, ensure_ascii=False), run_id),
            )
        self._active_inputs.pop(run_id, None)
        return record
