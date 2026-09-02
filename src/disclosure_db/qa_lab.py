"""Team QA ledger: reviews, performance runs, and development milestones."""

from __future__ import annotations

import html
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .gold_workbench import automatic_checks, build_record, default_annotation, hydrate_packet

KST = timezone(timedelta(hours=9))
VERDICTS = frozenset({"correct", "partial", "abstain_ok", "incorrect", "unsafe", "error"})
MILESTONE_CATEGORIES = frozenset({"serving", "retrieval", "gold", "deploy", "process", "perf", "other"})
GOLD_STATUSES = frozenset({"candidate", "approved", "rejected"})
GOLD_DECISIONS = frozenset({"approve", "reject"})
_MAX = {
    "reviewer": 80,
    "author": 80,
    "recorder": 80,
    "question": 4000,
    "answer": 20000,
    "notes": 8000,
    "title": 200,
    "body": 8000,
    "suite": 120,
    "git_commit": 64,
    "question_id": 200,
    "request_id": 64,
    "corpus_revision": 80,
    "endpoint": 80,
    "category": 32,
    "candidate_id": 64,
    "decision": 16,
}


class QaLabError(ValueError):
    pass


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _text(value: object, field: str, *, required: bool = True) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise QaLabError(f"{field}_required")
    if len(text) > _MAX[field]:
        raise QaLabError(f"{field}_too_long")
    return text


def _optional_bool(value: object) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def _optional_float(value: object, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise QaLabError(f"{field}_invalid") from exc
    if number < 0 or number > 1_000_000:
        raise QaLabError(f"{field}_invalid")
    return number


def _optional_int(value: object, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise QaLabError(f"{field}_invalid") from exc
    if number < 0 or number > 1_000_000:
        raise QaLabError(f"{field}_invalid")
    return number


def _citations_json(value: object) -> tuple[str, int]:
    if value is None:
        return "[]", 0
    if not isinstance(value, list) or len(value) > 40:
        raise QaLabError("citations_invalid")
    cleaned: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise QaLabError("citations_invalid")
        locator = item.get("locator")
        if locator is None:
            locator = {}
        if not isinstance(locator, Mapping):
            raise QaLabError("citations_invalid")
        cleaned_item = {
            "evidence_id": str(item.get("evidence_id") or "")[:160],
            "filing_id": str(item.get("filing_id") or "")[:32],
            "report_name": str(item.get("report_name") or "")[:120],
            "filed_at": str(item.get("filed_at") or "")[:32],
            "locator": dict(locator),
            "excerpt": str(item.get("excerpt") or "")[:6000],
        }
        cleaned.append(cleaned_item)
    encoded = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded) > 262144:
        raise QaLabError("citations_too_long")
    return encoded, len(cleaned)


def _json_payload(value: object, field: str, *, maximum: int = 524288) -> str:
    if not isinstance(value, Mapping):
        raise QaLabError(f"{field}_invalid")
    encoded = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded) > maximum:
        raise QaLabError(f"{field}_too_long")
    return encoded


def _decoded_object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _metrics_json(value: object) -> str:
    if value is None or value == "":
        return "{}"
    if not isinstance(value, Mapping):
        raise QaLabError("metrics_invalid")
    encoded = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded) > 65536:
        raise QaLabError("metrics_too_long")
    return encoded


class QaLabStore:
    def __init__(self, database: Path, *, corpus_database: Path | None = None):
        self.database = Path(database)
        self.corpus_database = Path(corpus_database) if corpus_database is not None else None
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS qa_review (
                    review_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    question TEXT NOT NULL,
                    question_id TEXT,
                    answer TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    answerable INTEGER,
                    verified INTEGER,
                    latency_ms REAL,
                    request_id TEXT,
                    corpus_revision TEXT,
                    citation_count INTEGER NOT NULL DEFAULT 0,
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    endpoint TEXT
                );
                CREATE INDEX IF NOT EXISTS qa_review_created ON qa_review(created_at DESC);
                CREATE TABLE IF NOT EXISTS perf_run (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    recorder TEXT NOT NULL,
                    suite TEXT NOT NULL,
                    git_commit TEXT,
                    passed INTEGER,
                    failed INTEGER,
                    skipped INTEGER,
                    p50_ms REAL,
                    p95_ms REAL,
                    notes TEXT NOT NULL DEFAULT '',
                    metrics_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS perf_run_created ON perf_run(created_at DESC);
                CREATE TABLE IF NOT EXISTS milestone (
                    milestone_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    author TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    category TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS milestone_created ON milestone(created_at DESC);
                CREATE TABLE IF NOT EXISTS gold_candidate (
                    candidate_id TEXT PRIMARY KEY,
                    review_id TEXT NOT NULL UNIQUE REFERENCES qa_review(review_id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    annotator TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('candidate','approved','rejected')),
                    annotation_json TEXT NOT NULL,
                    evidence_packet_json TEXT NOT NULL,
                    automatic_checks_json TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    reviewer TEXT,
                    reviewed_at TEXT,
                    review_notes TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS gold_candidate_updated ON gold_candidate(updated_at DESC);
                CREATE TABLE IF NOT EXISTS gold_review (
                    gold_review_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES gold_candidate(candidate_id),
                    candidate_revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK (decision IN ('approve','reject')),
                    notes TEXT NOT NULL DEFAULT '',
                    checks_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS gold_review_candidate
                    ON gold_review(candidate_id, created_at DESC);
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def add_review(
        self,
        *,
        reviewer: object,
        question: object,
        answer: object,
        verdict: object,
        notes: object = "",
        question_id: object = None,
        answerable: object = None,
        verified: object = None,
        latency_ms: object = None,
        request_id: object = None,
        corpus_revision: object = None,
        citations: object = None,
        endpoint: object = None,
    ) -> dict[str, Any]:
        verdict_text = str(verdict or "").strip()
        if verdict_text not in VERDICTS:
            raise QaLabError("verdict_invalid")
        citations_json, citation_count = _citations_json(citations)
        row = {
            "review_id": uuid4().hex,
            "created_at": _now(),
            "reviewer": _text(reviewer, "reviewer"),
            "question": _text(question, "question"),
            "question_id": _text(question_id, "question_id", required=False) or None,
            "answer": _text(answer, "answer"),
            "verdict": verdict_text,
            "notes": _text(notes, "notes", required=False),
            "answerable": _optional_bool(answerable),
            "verified": _optional_bool(verified),
            "latency_ms": _optional_float(latency_ms, "latency_ms"),
            "request_id": _text(request_id, "request_id", required=False) or None,
            "corpus_revision": _text(corpus_revision, "corpus_revision", required=False) or None,
            "citation_count": citation_count,
            "citations_json": citations_json,
            "endpoint": _text(endpoint, "endpoint", required=False) or None,
        }
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO qa_review(
                    review_id, created_at, reviewer, question, question_id, answer, verdict, notes,
                    answerable, verified, latency_ms, request_id, corpus_revision, citation_count,
                    citations_json, endpoint
                ) VALUES (
                    :review_id, :created_at, :reviewer, :question, :question_id, :answer, :verdict, :notes,
                    :answerable, :verified, :latency_ms, :request_id, :corpus_revision, :citation_count,
                    :citations_json, :endpoint
                )""",
                row,
            )
            connection.commit()
        return self._review_public(row)

    def create_gold_candidate(self, *, review_id: object, annotator: object) -> dict[str, Any]:
        review_id_text = _text(review_id, "candidate_id")
        annotator_text = _text(annotator, "reviewer")
        with closing(self._connect()) as connection:
            review_row = connection.execute(
                "SELECT * FROM qa_review WHERE review_id=?",
                (review_id_text,),
            ).fetchone()
            if review_row is None:
                raise QaLabError("review_not_found")
            if connection.execute(
                "SELECT 1 FROM gold_candidate WHERE review_id=?",
                (review_id_text,),
            ).fetchone() is not None:
                raise QaLabError("gold_candidate_exists")
        review = self._review_public(dict(review_row))
        annotation = default_annotation(review)
        packet, hydration_issues = hydrate_packet(
            self.corpus_database,
            evidence_ids=list(annotation["evidence_ids"]),
            filing_ids=list(annotation["candidate_filing_ids"]),
        )
        record = build_record(
            question=review["question"],
            annotator=annotator_text,
            annotation=annotation,
            packet=packet,
        )
        checks = automatic_checks(record, hydration_issues)
        now = _now()
        row = {
            "candidate_id": f"goldcand_{uuid4().hex}",
            "review_id": review_id_text,
            "created_at": now,
            "updated_at": now,
            "revision": 1,
            "annotator": annotator_text,
            "status": "candidate",
            "annotation_json": _json_payload(annotation, "annotation"),
            "evidence_packet_json": _json_payload(packet, "evidence_packet"),
            "automatic_checks_json": _json_payload(checks, "automatic_checks"),
            "record_json": _json_payload(record, "record"),
            "reviewer": None,
            "reviewed_at": None,
            "review_notes": "",
        }
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """INSERT INTO gold_candidate(
                        candidate_id,review_id,created_at,updated_at,revision,annotator,status,
                        annotation_json,evidence_packet_json,automatic_checks_json,record_json,
                        reviewer,reviewed_at,review_notes
                    ) VALUES (
                        :candidate_id,:review_id,:created_at,:updated_at,:revision,:annotator,:status,
                        :annotation_json,:evidence_packet_json,:automatic_checks_json,:record_json,
                        :reviewer,:reviewed_at,:review_notes
                    )""",
                    row,
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise QaLabError("gold_candidate_exists") from exc
        return self.get_gold_candidate(row["candidate_id"])

    def update_gold_candidate(
        self,
        *,
        candidate_id: object,
        editor: object,
        annotation: object,
    ) -> dict[str, Any]:
        candidate_id_text = _text(candidate_id, "candidate_id")
        _text(editor, "reviewer")
        if not isinstance(annotation, Mapping):
            raise QaLabError("annotation_invalid")
        current = self._gold_row(candidate_id_text)
        if current["status"] == "approved":
            raise QaLabError("approved_candidate_immutable")
        review = self._review_for_candidate(current)
        annotation_object = dict(annotation)
        evidence_ids = annotation_object.get("evidence_ids")
        filing_ids = annotation_object.get("candidate_filing_ids")
        if not isinstance(evidence_ids, list) or not isinstance(filing_ids, list):
            raise QaLabError("annotation_evidence_invalid")
        packet, hydration_issues = hydrate_packet(
            self.corpus_database,
            evidence_ids=[str(item) for item in evidence_ids],
            filing_ids=[str(item) for item in filing_ids],
        )
        record = build_record(
            question=review["question"],
            annotator=str(current["annotator"]),
            annotation=annotation_object,
            packet=packet,
        )
        checks = automatic_checks(record, hydration_issues)
        with closing(self._connect()) as connection:
            connection.execute(
                """UPDATE gold_candidate
                      SET updated_at=?,revision=revision+1,status='candidate',annotation_json=?,
                          evidence_packet_json=?,automatic_checks_json=?,record_json=?,reviewer=NULL,
                          reviewed_at=NULL,review_notes=''
                    WHERE candidate_id=?""",
                (
                    _now(),
                    _json_payload(annotation_object, "annotation"),
                    _json_payload(packet, "evidence_packet"),
                    _json_payload(checks, "automatic_checks"),
                    _json_payload(record, "record"),
                    candidate_id_text,
                ),
            )
            connection.commit()
        return self.get_gold_candidate(candidate_id_text)

    def decide_gold_candidate(
        self,
        *,
        candidate_id: object,
        reviewer: object,
        decision: object,
        notes: object = "",
    ) -> dict[str, Any]:
        candidate_id_text = _text(candidate_id, "candidate_id")
        reviewer_text = _text(reviewer, "reviewer")
        decision_text = str(decision or "").strip()
        if decision_text not in GOLD_DECISIONS:
            raise QaLabError("decision_invalid")
        notes_text = _text(notes, "notes", required=False)
        current = self._gold_row(candidate_id_text)
        if current["status"] == "approved":
            raise QaLabError("approved_candidate_immutable")
        annotation = _decoded_object(current["annotation_json"])
        evidence_ids = annotation.get("evidence_ids")
        filing_ids = annotation.get("candidate_filing_ids")
        if not isinstance(evidence_ids, list) or not isinstance(filing_ids, list):
            raise QaLabError("annotation_evidence_invalid")
        # Re-read immutable corpus metadata at the decision boundary so approval
        # cannot rely on a stale packet saved in the QA ledger.
        packet, hydration_issues = hydrate_packet(
            self.corpus_database,
            evidence_ids=[str(item) for item in evidence_ids],
            filing_ids=[str(item) for item in filing_ids],
        )
        review = self._review_for_candidate(current)
        reviewed_at = _now()
        target_status = "approved" if decision_text == "approve" else "rejected"
        if target_status == "approved" and reviewer_text == str(current["annotator"]):
            raise QaLabError("self_approval_forbidden")
        record = build_record(
            question=review["question"],
            annotator=str(current["annotator"]),
            annotation=annotation,
            packet=packet,
            status=target_status,
            reviewer=reviewer_text,
            reviewed_at=reviewed_at,
            review_notes=notes_text,
        )
        checks = automatic_checks(record, hydration_issues)
        if target_status == "approved" and not checks["passed"]:
            rules = ",".join(str(item.get("rule_id")) for item in checks["issues"][:8])
            raise QaLabError(f"gold_checks_failed:{rules}")
        with closing(self._connect()) as connection:
            connection.execute(
                """UPDATE gold_candidate SET updated_at=?,status=?,evidence_packet_json=?,automatic_checks_json=?,record_json=?,
                          reviewer=?,reviewed_at=?,review_notes=? WHERE candidate_id=?""",
                (
                    reviewed_at,
                    target_status,
                    _json_payload(packet, "evidence_packet"),
                    _json_payload(checks, "automatic_checks"),
                    _json_payload(record, "record"),
                    reviewer_text,
                    reviewed_at,
                    notes_text,
                    candidate_id_text,
                ),
            )
            connection.execute(
                """INSERT INTO gold_review(
                    gold_review_id,candidate_id,candidate_revision,created_at,reviewer,decision,notes,checks_json
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    uuid4().hex,
                    candidate_id_text,
                    int(current["revision"]),
                    reviewed_at,
                    reviewer_text,
                    decision_text,
                    notes_text,
                    _json_payload(checks, "automatic_checks"),
                ),
            )
            connection.commit()
        return self.get_gold_candidate(candidate_id_text)

    def get_gold_candidate(self, candidate_id: object) -> dict[str, Any]:
        row = self._gold_row(_text(candidate_id, "candidate_id"))
        return self._candidate_public(row, self._review_for_candidate(row))

    def list_gold_candidates(self, *, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT g.*,q.question,q.answer,q.verdict,q.citations_json,q.question_id AS qa_question_id,
                          q.answerable,q.verified,q.created_at AS qa_created_at
                     FROM gold_candidate g JOIN qa_review q ON q.review_id=g.review_id
                    ORDER BY g.updated_at DESC LIMIT ?""",
                (bounded,),
            ).fetchall()
        return [self._candidate_public(dict(row), self._joined_review_public(dict(row))) for row in rows]

    def export_gold_jsonl(self) -> str:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT record_json FROM gold_candidate WHERE status='approved' ORDER BY reviewed_at,candidate_id"
            ).fetchall()
        records = [json.loads(str(row[0])) for row in rows]
        return "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in records)

    def source_path(self, source_id: object) -> Path:
        source_id_text = str(source_id or "").strip()
        if not source_id_text or len(source_id_text) > 200:
            raise QaLabError("source_id_invalid")
        if self.corpus_database is None or not self.corpus_database.is_file():
            raise QaLabError("corpus_unavailable")
        try:
            connection = sqlite3.connect(
                f"file:{self.corpus_database.resolve().as_posix()}?mode=ro&immutable=1",
                uri=True,
            )
            with closing(connection):
                row = connection.execute(
                    "SELECT source_path FROM source_document WHERE source_id=?",
                    (source_id_text,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise QaLabError("corpus_read_failed") from exc
        if row is None:
            raise QaLabError("source_not_found")
        path = Path(str(row[0]))
        if not path.is_file():
            raise QaLabError("source_file_unavailable")
        return path

    def add_perf_run(
        self,
        *,
        recorder: object,
        suite: object,
        passed: object = None,
        failed: object = None,
        skipped: object = None,
        p50_ms: object = None,
        p95_ms: object = None,
        git_commit: object = None,
        notes: object = "",
        metrics: object = None,
    ) -> dict[str, Any]:
        row = {
            "run_id": uuid4().hex,
            "created_at": _now(),
            "recorder": _text(recorder, "recorder"),
            "suite": _text(suite, "suite"),
            "git_commit": _text(git_commit, "git_commit", required=False) or None,
            "passed": _optional_int(passed, "passed"),
            "failed": _optional_int(failed, "failed"),
            "skipped": _optional_int(skipped, "skipped"),
            "p50_ms": _optional_float(p50_ms, "p50_ms"),
            "p95_ms": _optional_float(p95_ms, "p95_ms"),
            "notes": _text(notes, "notes", required=False),
            "metrics_json": _metrics_json(metrics),
        }
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO perf_run(
                    run_id, created_at, recorder, suite, git_commit, passed, failed, skipped,
                    p50_ms, p95_ms, notes, metrics_json
                ) VALUES (
                    :run_id, :created_at, :recorder, :suite, :git_commit, :passed, :failed, :skipped,
                    :p50_ms, :p95_ms, :notes, :metrics_json
                )""",
                row,
            )
            connection.commit()
        return self._perf_public(row)

    def add_milestone(
        self,
        *,
        author: object,
        title: object,
        body: object,
        category: object = "process",
    ) -> dict[str, Any]:
        category_text = str(category or "process").strip() or "process"
        if category_text not in MILESTONE_CATEGORIES:
            raise QaLabError("category_invalid")
        row = {
            "milestone_id": uuid4().hex,
            "created_at": _now(),
            "author": _text(author, "author"),
            "title": _text(title, "title"),
            "body": _text(body, "body"),
            "category": category_text,
        }
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO milestone(milestone_id, created_at, author, title, body, category)
                   VALUES (:milestone_id, :created_at, :author, :title, :body, :category)""",
                row,
            )
            connection.commit()
        return dict(row)

    def list_reviews(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [self._review_public(dict(row)) for row in self._fetch("qa_review", "created_at DESC", limit)]

    def list_perf_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [self._perf_public(dict(row)) for row in self._fetch("perf_run", "created_at DESC", limit)]

    def list_milestones(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(row) for row in self._fetch("milestone", "created_at DESC", limit)]

    def summary(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            review_total = int(connection.execute("SELECT COUNT(*) FROM qa_review").fetchone()[0])
            verdict_rows = connection.execute(
                "SELECT verdict, COUNT(*) AS n FROM qa_review GROUP BY verdict"
            ).fetchall()
            latency = connection.execute(
                "SELECT AVG(latency_ms), MIN(latency_ms), MAX(latency_ms) FROM qa_review WHERE latency_ms IS NOT NULL"
            ).fetchone()
            perf_total = int(connection.execute("SELECT COUNT(*) FROM perf_run").fetchone()[0])
            mile_total = int(connection.execute("SELECT COUNT(*) FROM milestone").fetchone()[0])
            gold_rows = connection.execute(
                "SELECT status, COUNT(*) AS n FROM gold_candidate GROUP BY status"
            ).fetchall()
        by_verdict = {name: 0 for name in sorted(VERDICTS)}
        by_verdict.update({str(row["verdict"]): int(row["n"]) for row in verdict_rows})
        return {
            "reviews": {
                "total": review_total,
                "by_verdict": by_verdict,
                "latency_avg_ms": latency[0],
                "latency_min_ms": latency[1],
                "latency_max_ms": latency[2],
            },
            "perf_runs": {"total": perf_total},
            "milestones": {"total": mile_total},
            "gold": {
                "total": sum(int(row["n"]) for row in gold_rows),
                "by_status": {
                    **{name: 0 for name in sorted(GOLD_STATUSES)},
                    **{str(row["status"]): int(row["n"]) for row in gold_rows},
                },
            },
            "recent_reviews": self.list_reviews(limit=12),
            "recent_perf_runs": self.list_perf_runs(limit=8),
            "recent_milestones": self.list_milestones(limit=8),
            "recent_gold_candidates": self.list_gold_candidates(limit=20),
        }

    def export_html(self) -> str:
        summary = self.summary()
        reviews = self.list_reviews(limit=500)
        perfs = self.list_perf_runs(limit=200)
        miles = self.list_milestones(limit=200)
        golds = self.list_gold_candidates(limit=500)
        review_rows = "".join(
            "<tr>"
            f"<td>{html.escape(item['created_at'])}</td>"
            f"<td>{html.escape(item['reviewer'])}</td>"
            f"<td>{html.escape(item['verdict'])}</td>"
            f"<td>{html.escape(item['question'])}</td>"
            f"<td>{html.escape(item['answer'])}</td>"
            f"<td>{html.escape(item.get('notes') or '')}</td>"
            "</tr>"
            for item in reviews
        )
        perf_rows = "".join(
            "<tr>"
            f"<td>{html.escape(item['created_at'])}</td>"
            f"<td>{html.escape(item['suite'])}</td>"
            f"<td>{item.get('passed') if item.get('passed') is not None else ''}</td>"
            f"<td>{item.get('failed') if item.get('failed') is not None else ''}</td>"
            f"<td>{item.get('p95_ms') if item.get('p95_ms') is not None else ''}</td>"
            f"<td>{html.escape(item.get('notes') or '')}</td>"
            "</tr>"
            for item in perfs
        )
        mile_items = "".join(
            "<li>"
            f"<strong>{html.escape(item['created_at'])} · {html.escape(item['title'])}</strong>"
            f"<p>{html.escape(item['body'])}</p>"
            "</li>"
            for item in miles
        )
        gold_rows = "".join(
            "<tr>"
            f"<td>{html.escape(item['updated_at'])}</td>"
            f"<td>{html.escape(item['status'])}</td>"
            f"<td>{html.escape(item['annotator'])}</td>"
            f"<td>{html.escape(item.get('reviewer') or '')}</td>"
            f"<td>{html.escape(str(item['source_review'].get('question') or ''))}</td>"
            f"<td>{html.escape(str(item['annotation'].get('question_id') or ''))}</td>"
            "</tr>"
            for item in golds
        )
        verdicts = "".join(
            f"<li>{html.escape(name)}: {count}</li>"
            for name, count in summary["reviews"]["by_verdict"].items()
        )
        return (
            "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<title>공시 에이전트 검수 원장</title>"
            "<style>body{font-family:serif;margin:2rem auto;max-width:72rem;color:#172033}"
            "table{border-collapse:collapse;width:100%}td,th{border:1px solid #d7dbe3;padding:.4rem;vertical-align:top}"
            "h1,h2{font-weight:700}</style></head><body>"
            "<h1>공시 에이전트 검수 원장</h1>"
            f"<p>검수 {summary['reviews']['total']}건 · Gold {summary['gold']['total']}건 · 성능 {summary['perf_runs']['total']}건 · "
            f"발전 기록 {summary['milestones']['total']}건</p>"
            f"<ul>{verdicts}</ul>"
            "<h2>검수 기록</h2><table><thead><tr>"
            "<th>시각</th><th>검수자</th><th>판정</th><th>질문</th><th>답변</th><th>메모</th>"
            f"</tr></thead><tbody>{review_rows}</tbody></table>"
            "<h2>Gold 후보·승인</h2><table><thead><tr>"
            "<th>갱신</th><th>상태</th><th>작성자</th><th>검수자</th><th>질문</th><th>질문 ID</th>"
            f"</tr></thead><tbody>{gold_rows}</tbody></table>"
            "<h2>성능 기록</h2><table><thead><tr>"
            "<th>시각</th><th>스위트</th><th>통과</th><th>실패</th><th>p95 ms</th><th>메모</th>"
            f"</tr></thead><tbody>{perf_rows}</tbody></table>"
            f"<h2>발전 과정</h2><ol>{mile_items}</ol>"
            "</body></html>"
        )

    def _gold_row(self, candidate_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM gold_candidate WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise QaLabError("gold_candidate_not_found")
        return dict(row)

    def _review_for_candidate(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM qa_review WHERE review_id=?",
                (candidate["review_id"],),
            ).fetchone()
        if row is None:
            raise QaLabError("review_not_found")
        return self._review_public(dict(row))

    @classmethod
    def _candidate_public(
        cls,
        row: Mapping[str, Any],
        source_review: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "candidate_id": row["candidate_id"],
            "review_id": row["review_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revision": int(row["revision"]),
            "annotator": row["annotator"],
            "status": row["status"],
            "annotation": _decoded_object(row.get("annotation_json")),
            "evidence_packet": _decoded_object(row.get("evidence_packet_json")),
            "automatic_checks": _decoded_object(row.get("automatic_checks_json")),
            "record": _decoded_object(row.get("record_json")),
            "reviewer": row.get("reviewer"),
            "reviewed_at": row.get("reviewed_at"),
            "review_notes": row.get("review_notes") or "",
            "source_review": dict(source_review),
        }

    @classmethod
    def _joined_review_public(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        citations = row.get("citations_json") or "[]"
        try:
            parsed = json.loads(str(citations))
        except json.JSONDecodeError:
            parsed = []
        return {
            "review_id": row["review_id"],
            "created_at": row.get("qa_created_at"),
            "question": row.get("question"),
            "question_id": row.get("qa_question_id"),
            "answer": row.get("answer"),
            "verdict": row.get("verdict"),
            "answerable": None if row.get("answerable") is None else bool(row["answerable"]),
            "verified": None if row.get("verified") is None else bool(row["verified"]),
            "citations": parsed,
        }

    def _fetch(self, table: str, order: str, limit: int) -> list[sqlite3.Row]:
        if table not in {"qa_review", "perf_run", "milestone"} or order != "created_at DESC":
            raise QaLabError("query_invalid")
        bounded = max(1, min(int(limit), 500))
        with closing(self._connect()) as connection:
            return list(connection.execute(f"SELECT * FROM {table} ORDER BY created_at DESC LIMIT ?", (bounded,)))

    @staticmethod
    def _review_public(row: Mapping[str, Any]) -> dict[str, Any]:
        citations = row.get("citations_json") or "[]"
        if isinstance(citations, str):
            try:
                parsed = json.loads(citations)
            except json.JSONDecodeError:
                parsed = []
        else:
            parsed = citations
        return {
            "review_id": row["review_id"],
            "created_at": row["created_at"],
            "reviewer": row["reviewer"],
            "question": row["question"],
            "question_id": row.get("question_id"),
            "answer": row["answer"],
            "verdict": row["verdict"],
            "notes": row.get("notes") or "",
            "answerable": None if row.get("answerable") is None else bool(row["answerable"]),
            "verified": None if row.get("verified") is None else bool(row["verified"]),
            "latency_ms": row.get("latency_ms"),
            "request_id": row.get("request_id"),
            "corpus_revision": row.get("corpus_revision"),
            "citation_count": row.get("citation_count") or 0,
            "citations": parsed,
            "endpoint": row.get("endpoint"),
        }

    @staticmethod
    def _perf_public(row: Mapping[str, Any]) -> dict[str, Any]:
        metrics = row.get("metrics_json") or "{}"
        if isinstance(metrics, str):
            try:
                parsed = json.loads(metrics)
            except json.JSONDecodeError:
                parsed = {}
        else:
            parsed = metrics
        return {
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "recorder": row["recorder"],
            "suite": row["suite"],
            "git_commit": row.get("git_commit"),
            "passed": row.get("passed"),
            "failed": row.get("failed"),
            "skipped": row.get("skipped"),
            "p50_ms": row.get("p50_ms"),
            "p95_ms": row.get("p95_ms"),
            "notes": row.get("notes") or "",
            "metrics": parsed,
        }
