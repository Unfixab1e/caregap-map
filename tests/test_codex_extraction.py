"""Codex batch extraction: all tests run WITHOUT the Codex CLI, auth or network."""

import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from caregap_map.codex_extraction import (
    DEFAULT_MODEL,
    PROMPT_VERSION,
    BatchProcessor,
    CodexAuthError,
    CodexError,
    CodexNotInstalledError,
    CodexRecordResult,
    CodexRunner,
    CodexRunStore,
    build_batch_input,
    build_codex_command,
    check_resume_compatible,
    new_manifest,
    parse_batch_result,
    score_codex_record,
)
from caregap_map.config import CLASS_NEEDS_REVIEW, CLASS_TRUSTED
from conftest import make_record

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "config" / "codex_icu_output_schema.json"


def codex_record(uid="f1", **overrides) -> dict:
    payload = {
        "unique_id": uid,
        "explicit_icu_claim": False,
        "icu_bed_count": None,
        "icu_subtypes": [],
        "fragments": [],
        "unclear_claims": [],
        "explanation": "test",
    }
    payload.update(overrides)
    return payload


def batch_json(*records) -> str:
    return json.dumps({"records": list(records)})


class StubExec:
    """Simulates codex exec: writes the output file named after -o in the cmd."""

    def __init__(self, outputs=None, returncode=0, stderr="", raise_exc=None):
        self.outputs = list(outputs or [])
        self.returncode = returncode
        self.stderr = stderr
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    def __call__(self, cmd, stdin_text, timeout):
        self.calls.append({"cmd": cmd, "stdin": stdin_text, "timeout": timeout})
        if self.raise_exc is not None:
            raise self.raise_exc
        output_path = Path(cmd[cmd.index("--output-last-message") + 1])
        if self.outputs:
            output_path.write_text(self.outputs.pop(0), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, self.returncode, stdout="", stderr=self.stderr)


class TestCommandConstruction:
    def test_default_model_and_flags(self, tmp_path):
        cmd = build_codex_command(SCHEMA_PATH, tmp_path / "o.json", DEFAULT_MODEL, "low", tmp_path)
        assert cmd[:2] == ["codex", "exec"]
        assert "--ephemeral" in cmd and "--skip-git-repo-check" in cmd
        assert cmd[cmd.index("--sandbox") + 1] == "read-only"
        assert cmd[cmd.index("-m") + 1] == "gpt-5.6-luna"
        assert "model_reasoning_effort=low" in cmd
        assert "--output-schema" in cmd

    def test_model_override(self, tmp_path):
        cmd = build_codex_command(SCHEMA_PATH, tmp_path / "o.json", "gpt-5.6-terra", "medium", tmp_path)
        assert cmd[cmd.index("-m") + 1] == "gpt-5.6-terra"
        assert "model_reasoning_effort=medium" in cmd

    def test_batch_input_reaches_stdin_and_omits_biasing_fields(self, tmp_path):
        stub = StubExec(outputs=[batch_json(codex_record("f1"))])
        runner = CodexRunner(exec_fn=stub)
        record = make_record(unique_id="f1")
        runner.run_batch(
            build_batch_input([record]), SCHEMA_PATH, tmp_path / "o.json", DEFAULT_MODEL, "low", 60, tmp_path
        )
        sent = json.loads(stub.calls[0]["stdin"])
        assert sent["records"][0]["unique_id"] == "f1"
        for banned in ("name", "state_final", "latitude", "classification", "source_urls"):
            assert banned not in sent["records"][0]


class TestResultParsing:
    def test_valid_roundtrip(self):
        result = parse_batch_result(batch_json(codex_record("a"), codex_record("b")), ["a", "b"])
        assert [r.unique_id for r in result.records] == ["a", "b"]

    def test_missing_id_rejected(self):
        with pytest.raises(CodexError, match="missing"):
            parse_batch_result(batch_json(codex_record("a")), ["a", "b"])

    def test_unknown_id_rejected(self):
        with pytest.raises(CodexError, match="unknown"):
            parse_batch_result(batch_json(codex_record("a"), codex_record("zz")), ["a"])

    def test_duplicate_id_rejected(self):
        with pytest.raises(CodexError, match="duplicate"):
            parse_batch_result(batch_json(codex_record("a"), codex_record("a")), ["a"])

    def test_malformed_json_rejected(self):
        with pytest.raises(CodexError, match="malformed"):
            parse_batch_result("{not json", ["a"])

    def test_empty_response_rejected(self):
        with pytest.raises(CodexError, match="empty"):
            parse_batch_result("   ", ["a"])

    def test_extra_keys_rejected(self):
        bad = codex_record("a")
        bad["surprise"] = 1
        with pytest.raises(CodexError, match="schema"):
            parse_batch_result(batch_json(bad), ["a"])


class TestGuardrailReuse:
    """Codex proposals pass through the exact OpenAI-extractor guardrails."""

    def test_hallucinated_quote_dropped(self, config):
        record = make_record(unique_id="f1")
        proposal = CodexRecordResult(
            **codex_record(
                "f1",
                explicit_icu_claim=True,
                fragments=[{"field": "description", "group": "explicit_icu", "quote": "gleaming ICU tower"}],
            )
        )
        row = score_codex_record(proposal, record, config)
        assert row["verified_fragments"] == 0
        assert row["dropped_fragments"] == 1
        assert row["explicit_icu_claim"] is False

    def test_low_information_quote_dropped(self, config):
        record = make_record(unique_id="f1", capability=json.dumps(["True", "Medical ICU"]))
        proposal = CodexRecordResult(
            **codex_record(
                "f1",
                explicit_icu_claim=True,
                fragments=[
                    {"field": "capability", "group": "equipment", "quote": "True"},
                    {"field": "capability", "group": "explicit_icu", "quote": "Medical ICU"},
                ],
            )
        )
        row = score_codex_record(proposal, record, config)
        assert row["low_information_fragments"] == 1
        assert row["verified_fragments"] == 1

    def test_bed_count_anchoring(self, config):
        record = make_record(
            unique_id="f1",
            description="ICU available on site.",
            equipment=json.dumps(["10 ventilators"]),
        )
        proposal = CodexRecordResult(
            **codex_record(
                "f1",
                explicit_icu_claim=True,
                icu_bed_count=10,
                fragments=[
                    {"field": "description", "group": "explicit_icu", "quote": "ICU available"},
                    {"field": "equipment", "group": "equipment", "quote": "10 ventilators"},
                ],
            )
        )
        row = score_codex_record(proposal, record, config)
        assert row["icu_bed_count"] is None  # number+bed+ICU never co-occur in ONE fragment

    def test_anchored_bed_count_and_trusted_classification(self, config):
        record = make_record(
            unique_id="f1",
            description="Tertiary hospital with a 20-bed intensive care unit.",
            equipment=json.dumps(["Ventilator x 10"]),
        )
        proposal = CodexRecordResult(
            **codex_record(
                "f1",
                explicit_icu_claim=True,
                icu_bed_count=20,
                fragments=[
                    {"field": "description", "group": "explicit_icu", "quote": "20-bed intensive care unit"},
                    {"field": "equipment", "group": "equipment", "quote": "Ventilator x 10"},
                ],
            )
        )
        row = score_codex_record(proposal, record, config)
        assert row["icu_bed_count"] == 20
        assert row["classification"] == CLASS_TRUSTED  # existing scoring code decided this
        assert row["extractor"] == "codex_cli"

    def test_model_subtypes_are_provenance_only(self, config):
        record = make_record(unique_id="f1", capability=json.dumps(["Level III NICU"]))
        proposal = CodexRecordResult(
            **codex_record(
                "f1",
                explicit_icu_claim=True,
                icu_subtypes=["cardiac_icu"],  # model is wrong on purpose
                fragments=[{"field": "capability", "group": "explicit_icu", "quote": "Level III NICU"}],
            )
        )
        row = score_codex_record(proposal, record, config)
        assert row["icu_subtypes"] == ["neonatal_icu"]  # deterministic detection wins
        assert row["model_proposed_subtypes"] == ["cardiac_icu"]

    def test_uncorroborated_claim_still_routes_to_review(self, config):
        record = make_record(unique_id="f1", capability=json.dumps(["ICU available"]))
        proposal = CodexRecordResult(
            **codex_record(
                "f1",
                explicit_icu_claim=True,
                fragments=[{"field": "capability", "group": "explicit_icu", "quote": "ICU available"}],
            )
        )
        row = score_codex_record(proposal, record, config)
        assert row["classification"] == CLASS_NEEDS_REVIEW


class TestRunnerErrors:
    def _run(self, stub, tmp_path):
        return CodexRunner(exec_fn=stub).run_batch(
            build_batch_input([make_record(unique_id="f1")]),
            SCHEMA_PATH,
            tmp_path / "o.json",
            DEFAULT_MODEL,
            "low",
            60,
            tmp_path,
        )

    def test_missing_executable(self, tmp_path):
        with pytest.raises(CodexNotInstalledError):
            self._run(StubExec(raise_exc=FileNotFoundError("codex")), tmp_path)

    def test_timeout(self, tmp_path):
        exc = subprocess.TimeoutExpired(cmd="codex", timeout=60)
        with pytest.raises(CodexError, match="timed out"):
            self._run(StubExec(raise_exc=exc), tmp_path)

    def test_nonzero_exit(self, tmp_path):
        with pytest.raises(CodexError, match="exit 3"):
            self._run(StubExec(returncode=3, stderr="boom"), tmp_path)

    def test_auth_failure_is_distinct(self, tmp_path):
        with pytest.raises(CodexAuthError):
            self._run(StubExec(returncode=1, stderr="You have hit your usage limit"), tmp_path)

    def test_missing_output_file(self, tmp_path):
        with pytest.raises(CodexError, match="no output"):
            self._run(StubExec(outputs=[]), tmp_path)


def make_processor(tmp_path, stub, max_retries=2):
    from caregap_map.config import ScoringConfig

    store = CodexRunStore(tmp_path / "run")
    processor = BatchProcessor(
        CodexRunner(exec_fn=stub),
        store,
        ScoringConfig(),
        SCHEMA_PATH,
        DEFAULT_MODEL,
        "low",
        60,
        max_retries=max_retries,
    )
    return processor, store


class TestBatchProcessor:
    def test_success_appends_checkpoint(self, tmp_path):
        records = [make_record(unique_id="a"), make_record(unique_id="b")]
        stub = StubExec(outputs=[batch_json(codex_record("a"), codex_record("b"))])
        processor, store = make_processor(tmp_path, stub)
        ok, failed = processor.process(records)
        assert (ok, failed) == (2, 0)
        assert store.completed_ids() == {"a", "b"}
        row = store.load_completed()[0]
        assert row["extractor"] == "codex_cli" and row["prompt_version"] == PROMPT_VERSION
        assert (store.batches_dir / "batch_000001.input.json").exists()
        assert (store.batches_dir / "batch_000001.meta.json").exists()

    def test_retry_once_then_succeed(self, tmp_path):
        records = [make_record(unique_id="a")]
        stub = StubExec(outputs=["{broken", batch_json(codex_record("a"))])
        processor, store = make_processor(tmp_path, stub)
        ok, failed = processor.process(records)
        assert (ok, failed) == (1, 0)
        assert processor.retries == 1

    def test_split_after_repeated_batch_failure(self, tmp_path):
        records = [make_record(unique_id="a"), make_record(unique_id="b")]
        # Batch of 2 fails twice (missing id), then each half succeeds.
        stub = StubExec(
            outputs=[
                batch_json(codex_record("a")),  # missing b
                batch_json(codex_record("a")),  # missing b again
                batch_json(codex_record("a")),  # half 1 ok
                batch_json(codex_record("b")),  # half 2 ok
            ]
        )
        processor, store = make_processor(tmp_path, stub)
        ok, failed = processor.process(records)
        assert (ok, failed) == (2, 0)
        assert store.completed_ids() == {"a", "b"}

    def test_persistent_single_record_failure_is_quarantined(self, tmp_path):
        records = [make_record(unique_id="a"), make_record(unique_id="b")]
        stub = StubExec(
            outputs=[
                "{broken",  # batch fail 1
                "{broken",  # batch fail 2 -> split
                batch_json(codex_record("a")),  # half 1 ok
                "{broken",  # half 2 fail 1
                "{broken",  # half 2 fail 2 -> single record quarantined
            ]
        )
        processor, store = make_processor(tmp_path, stub)
        ok, failed = processor.process(records)
        assert (ok, failed) == (1, 1)
        errors = store._read_jsonl(store.errors_path)
        assert errors[0]["unique_id"] == "b" and "malformed" in errors[0]["error"]
        assert store.completed_ids() == {"a"}  # progress kept

    def test_auth_error_propagates_to_pause(self, tmp_path):
        stub = StubExec(returncode=1, stderr="not logged in - run codex login")
        processor, _ = make_processor(tmp_path, stub)
        with pytest.raises(CodexAuthError):
            processor.process([make_record(unique_id="a")])


class TestStoreAndResume:
    def test_manifest_roundtrip_and_torn_jsonl(self, tmp_path):
        store = CodexRunStore(tmp_path / "run")
        manifest = new_manifest(
            source_path="x.parquet",
            source_hash="h",
            model=DEFAULT_MODEL,
            reasoning_effort="low",
            batch_size=25,
            timeout=600,
            requested=10,
            codex_version="v",
            repo_commit="sha",
        )
        store.save_manifest(manifest)
        assert store.load_manifest()["run_id"] == manifest["run_id"]
        store.append_completed({"unique_id": "a"})
        with open(store.completed_path, "a", encoding="utf-8") as fh:
            fh.write('{"unique_id": "torn')  # interrupted write
        assert store.completed_ids() == {"a"}

    def test_resume_config_mismatch_detected(self):
        manifest = new_manifest(
            source_path="x",
            source_hash="hash-1",
            model=DEFAULT_MODEL,
            reasoning_effort="low",
            batch_size=25,
            timeout=600,
            requested=1,
            codex_version="v",
            repo_commit="sha",
        )
        assert check_resume_compatible(manifest, model=DEFAULT_MODEL, source_hash="hash-1") == []
        problems = check_resume_compatible(manifest, model="gpt-5.6-terra", source_hash="hash-2")
        assert any("model changed" in p for p in problems)
        assert any("hash changed" in p for p in problems)

    def test_restart_archives_never_deletes(self, tmp_path):
        store = CodexRunStore(tmp_path / "run")
        store.append_completed({"unique_id": "a"})
        archived = store.archive()
        assert archived is not None
        assert (archived / "completed.jsonl").exists()
        assert store.completed_ids() == set()


class TestCliScript:
    def _write_source(self, tmp_path) -> Path:
        rows = [
            {
                "unique_id": f"f{i:03d}",
                "state_final": "Kerala",
                "classification": "Likely Medical Gap",
                "capability_evidence_score": 0,
                "icu_subtypes_json": "[]",
                "description": "General hospital.",
                "capability": "[]",
                "specialties": "[]",
                "procedure": "[]",
                "equipment": "[]",
            }
            for i in range(30)
        ]
        path = tmp_path / "facilities_scored.parquet"
        pd.DataFrame(rows).to_parquet(path, index=False)
        return path

    def _main(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "run_codex_extraction",
            Path(__file__).resolve().parents[1] / "scripts" / "run_codex_extraction.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.main

    def test_dry_run_makes_no_codex_call_and_needs_no_api_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        source = self._write_source(tmp_path)
        rc = self._main()(
            [
                "--dry-run",
                "--limit",
                "5",
                "--batch-size",
                "2",
                "--input-path",
                str(source),
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )
        assert rc == 0  # no codex executable involved anywhere

    def test_gate_refuses_over_500_selected(self, tmp_path, monkeypatch):
        rows = [
            {
                "unique_id": f"g{i:04d}",
                "state_final": "Kerala",
                "classification": "Likely Medical Gap",
                "capability_evidence_score": 0,
                "icu_subtypes_json": "[]",
                "description": "x",
                "capability": "[]",
                "specialties": "[]",
                "procedure": "[]",
                "equipment": "[]",
            }
            for i in range(501)
        ]
        source = tmp_path / "big.parquet"
        pd.DataFrame(rows).to_parquet(source, index=False)
        rc = self._main()(
            ["--limit", "501", "--input-path", str(source), "--output-dir", str(tmp_path / "out")]
        )
        assert rc == 1  # refused without --yes, before any Codex involvement
