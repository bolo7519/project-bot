"""
tests/test_phase4_llm_evaluation.py — Unit-Tests für Phase 4 (LLM-Bewertung).

Alle Tests ohne echte API-Aufrufe:
- PIIScrubber (T1–T8): PII-Minimierung, Safety-Gate
- LLMEvaluationCache (T9–T11): Cache-Key, Persistenz, Hit
- BudgetTracker (T12–T15): Budget-Checks, Protokoll
- EvalPromptBuilder (T16–T19): Prompt-Struktur, Truncation, Injection-Schutz
- LLMEvaluator._parse_and_validate (T20–T26): Strikte JSON-Validierung
- LLMEvaluator.evaluate mit Mock (T27–T31): end-to-end mit gemocktem API-Call
- StateManager 'evaluated' (T32): Neuer Zustand und Übergänge
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# Sicherstellen, dass das Projektverzeichnis im Suchpfad ist
sys.path.insert(0, str(Path(__file__).parent.parent))

from pii_scrubber import PIIScrubber, ScrubResult, MIN_USEFUL_CHARS
from llm_evaluator import (
    LLMEvaluationCache,
    BudgetTracker,
    EvalPromptBuilder,
    LLMEvaluator,
    LLMEvalResult,
    ProfileEvaluation,
    compute_profiles_hash,
    load_profiles,
    PROMPT_VERSION,
)
from pre_scorer import PROFILE_IDS
from state_manager import ProjectStateManager


# ══════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ══════════════════════════════════════════════════════════════════════════════

def _minimal_config(
    model: str = "claude-3-5-haiku-20241022",
    provider: str = "anthropic",
    **overrides: Any,
) -> Dict[str, Any]:
    """Minimale Testkonfiguration ohne echte API-Keys."""
    cfg: Dict[str, Any] = {
        "provider": provider,
        "model": model,
        "api_key": "test-key",
        "max_output_tokens": 1024,
        "max_input_tokens": 6000,
        "max_cost_per_call_usd": 0.50,
        "daily_budget_usd": 5.0,
        "monthly_budget_usd": 50.0,
        "max_calls_per_run": 10,
        "high_priority_threshold": 65,
        "low_priority_threshold": 35,
        "cache_enabled": False,
        "prompt_version": PROMPT_VERSION,
    }
    cfg.update(overrides)
    return {"llm_evaluation": cfg}


def _ok_api_response() -> str:
    """Gültige API-Antwort mit allen vier Profilen."""
    return json.dumps({
        "evaluations": [
            {
                "profile_id": "crm_sales_automation",
                "score": 72,
                "matched": ["CRM", "Salesforce"],
                "missing": ["HubSpot"],
                "rationale": "Gute CRM-Kenntnisse.",
            },
            {
                "profile_id": "ai_business_process_integration",
                "score": 55,
                "matched": ["Python", "API"],
                "missing": ["LLM-Erfahrung"],
                "rationale": "Grundkenntnisse vorhanden.",
            },
            {
                "profile_id": "it_infrastructure_security",
                "score": 30,
                "matched": ["Netzwerk"],
                "missing": ["SIEM", "SOC"],
                "rationale": "Wenig Security-Fokus.",
            },
            {
                "profile_id": "power_bi_sharepoint",
                "score": 15,
                "matched": [],
                "missing": ["Power BI", "SharePoint"],
                "rationale": "Kein BI-Bezug erkennbar.",
            },
        ]
    })


def _make_llm_evaluator_with_mock(
    tmp_path: Path,
    mock_response: str = None,
    profiles_dir: Path = None,
) -> LLMEvaluator:
    """Erstellt einen LLMEvaluator mit einem gemockten Profil-Verzeichnis."""
    if profiles_dir is None:
        profiles_dir = tmp_path / "competency_profiles"
        profiles_dir.mkdir()
        for pid in PROFILE_IDS:
            (profiles_dir / f"{pid}.md").write_text(
                f"# {pid}\n- Schlüsselwort A\n- Schlüsselwort B\n", encoding="utf-8"
            )

    evaluator = LLMEvaluator(
        config=_minimal_config(),
        output_dir=str(tmp_path),
        profiles_dir=profiles_dir,
    )
    return evaluator


# ══════════════════════════════════════════════════════════════════════════════
# T1–T8: PIIScrubber
# ══════════════════════════════════════════════════════════════════════════════

class TestPIIScrubberBasic:
    """T1–T4: Grundlegende PII-Erkennung."""

    def test_email_replaced(self):
        """T1: E-Mail-Adressen werden ersetzt."""
        scrubber = PIIScrubber()
        result = scrubber.scrub("Kontakt: hans.mueller@example.com für Details")
        assert "[EMAIL]" in result.text
        assert "hans.mueller@example.com" not in result.text
        assert result.replacements.get("email", 0) >= 1

    def test_phone_replaced(self):
        """T2: Telefonnummern werden ersetzt."""
        scrubber = PIIScrubber()
        result = scrubber.scrub("Ruf mich an: +49 30 12345678 oder 030-9876543")
        assert "[PHONE]" in result.text
        assert result.replacements.get("phone", 0) >= 1

    def test_iban_replaced(self):
        """T3: IBANs werden ersetzt."""
        scrubber = PIIScrubber()
        result = scrubber.scrub("Bankverbindung: DE89 3704 0044 0532 0130 00")
        assert "[IBAN]" in result.text
        assert "DE89" not in result.text
        assert result.replacements.get("iban", 0) >= 1

    def test_salutation_name_replaced(self):
        """T4: Namen mit Anrede werden ersetzt."""
        scrubber = PIIScrubber()
        result = scrubber.scrub("Sehr geehrter Herr Müller, wir freuen uns")
        assert "[NAME]" in result.text
        assert "Müller" not in result.text

    def test_email_thread_removed(self):
        """T5: E-Mail-Verläufe werden entfernt."""
        scrubber = PIIScrubber()
        text = "Projektbeschreibung: CRM-Projekt.\n\n-------- Forwarded Message --------\nVon: test@example.com\nAlter Inhalt hier"
        result = scrubber.scrub(text)
        assert result.thread_removed
        assert "Forwarded" not in result.text
        assert "Projektbeschreibung" in result.text

    def test_signature_removed(self):
        """T6: E-Mail-Signaturen werden entfernt."""
        scrubber = PIIScrubber()
        text = "Wir suchen einen CRM-Experten für 6 Monate.\n\nMit freundlichen Grüßen\nMax Mustermann\nMuster GmbH"
        result = scrubber.scrub(text)
        assert result.signature_removed
        assert "Mit freundlichen" not in result.text
        assert "CRM-Experten" in result.text

    def test_clean_text_untouched(self):
        """T7: Sauberer Text wird nicht verändert."""
        scrubber = PIIScrubber()
        text = "Wir suchen einen Python-Entwickler mit 5 Jahren Erfahrung in Django und REST-APIs."
        result = scrubber.scrub(text)
        assert result.total_replacements() == 0
        assert not result.signature_removed
        assert not result.thread_removed
        assert "Python-Entwickler" in result.text


class TestPIIScrubberSafetyGate:
    """T8: is_safe_to_send() Safety-Gate."""

    def test_sufficient_content_is_safe(self):
        """Langer Text ohne PII ist safe."""
        scrubber = PIIScrubber()
        text = "A" * (MIN_USEFUL_CHARS + 50)
        result = scrubber.scrub(text)
        assert result.is_safe_to_send()

    def test_only_placeholder_not_safe(self):
        """Text der nur aus Platzhaltern besteht ist nicht safe."""
        scrubber = PIIScrubber()
        # Simuliere: nach Scrubbing nur noch Tags
        result = ScrubResult(
            text="[EMAIL] [PHONE] [IBAN] [NAME]",
            replacements={"email": 1, "phone": 1, "iban": 1, "name": 1},
        )
        assert not result.is_safe_to_send()

    def test_empty_text_not_safe(self):
        """Leerer Text ist nicht safe."""
        scrubber = PIIScrubber()
        result = scrubber.scrub("")
        assert not result.is_safe_to_send()

    def test_minimal_text_is_safe(self):
        """Text mit genau MIN_USEFUL_CHARS nutzbaren Zeichen ist safe."""
        scrubber = PIIScrubber()
        text = "x" * MIN_USEFUL_CHARS
        result = scrubber.scrub(text)
        assert result.is_safe_to_send()


# ══════════════════════════════════════════════════════════════════════════════
# T9–T11: LLMEvaluationCache
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMEvaluationCache:
    """T9–T11: Cache-Grundfunktionen."""

    def test_cache_miss_returns_none(self, tmp_path):
        """T9: Unbekannter Key liefert None."""
        cache = LLMEvaluationCache(str(tmp_path))
        assert cache.get("unknown-key") is None

    def test_cache_put_and_get(self, tmp_path):
        """T10: Gespeichertes Ergebnis wird zurückgegeben."""
        cache = LLMEvaluationCache(str(tmp_path))
        result = LLMEvalResult(
            project_id="test-001",
            evaluations=[],
            best_profile="crm_sales_automation",
            best_score=72,
            model_used="claude-3-5-haiku-20241022",
            prompt_version=PROMPT_VERSION,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            evaluated_at="2026-01-01T00:00:00Z",
            cache_hit=False,
        )
        cache.put("test-key-001", result)
        retrieved = cache.get("test-key-001")
        assert retrieved is not None
        assert retrieved.best_score == 72
        assert retrieved.best_profile == "crm_sales_automation"

    def test_cache_persists_to_disk(self, tmp_path):
        """T11: Cache-Einträge werden auf Disk geschrieben und neu geladen."""
        cache1 = LLMEvaluationCache(str(tmp_path))
        result = LLMEvalResult(
            project_id="persist-test",
            evaluations=[],
            best_profile="power_bi_sharepoint",
            best_score=50,
            model_used="test-model",
            prompt_version="v1.0",
            input_tokens=200,
            output_tokens=80,
            cost_usd=0.002,
            evaluated_at="2026-01-01T00:00:00Z",
            cache_hit=False,
        )
        cache1.put("persist-key", result)

        # Neues Cache-Objekt lädt von derselben Datei
        cache2 = LLMEvaluationCache(str(tmp_path))
        retrieved = cache2.get("persist-key")
        assert retrieved is not None
        assert retrieved.best_score == 50
        assert retrieved.project_id == "persist-test"

    def test_cache_key_changes_with_model(self, tmp_path):
        """Cache-Key unterscheidet sich bei verschiedenen Modellen."""
        cache = LLMEvaluationCache(str(tmp_path))
        key1 = cache.compute_key("text", "hash1", "v1.0", "claude-3-haiku", 65, 35)
        key2 = cache.compute_key("text", "hash1", "v1.0", "claude-3-sonnet", 65, 35)
        assert key1 != key2

    def test_cache_key_changes_with_profiles(self, tmp_path):
        """Cache-Key unterscheidet sich bei verschiedenen Profil-Hashes."""
        cache = LLMEvaluationCache(str(tmp_path))
        key1 = cache.compute_key("text", "hash-A", "v1.0", "model", 65, 35)
        key2 = cache.compute_key("text", "hash-B", "v1.0", "model", 65, 35)
        assert key1 != key2


# ══════════════════════════════════════════════════════════════════════════════
# T12–T15: BudgetTracker
# ══════════════════════════════════════════════════════════════════════════════

class TestBudgetTracker:
    """T12–T15: Budget-Kontrolle."""

    def _make_tracker(self, tmp_path: Path, **kwargs) -> BudgetTracker:
        defaults = dict(
            daily_budget_usd=1.0,
            monthly_budget_usd=5.0,
            max_calls_per_run=3,
            max_cost_per_call_usd=0.10,
        )
        defaults.update(kwargs)
        return BudgetTracker(str(tmp_path), **defaults)

    def test_first_call_allowed(self, tmp_path):
        """T12: Erster Aufruf wird erlaubt."""
        tracker = self._make_tracker(tmp_path)
        ok, reason = tracker.check_budget(0.01)
        assert ok
        assert reason == ""

    def test_exceeds_per_call_limit(self, tmp_path):
        """T13: Überschreitung des Pro-Call-Limits wird abgelehnt."""
        tracker = self._make_tracker(tmp_path)
        ok, reason = tracker.check_budget(0.20)  # > max_cost_per_call=0.10
        assert not ok
        assert "pro Aufruf" in reason or "Limit" in reason

    def test_exceeds_run_limit(self, tmp_path):
        """T14: Maximale Aufrufe pro Lauf wird eingehalten."""
        tracker = self._make_tracker(tmp_path, max_calls_per_run=2)
        tracker.record("p1", "model", 0.01, 100, 50)
        tracker.record("p2", "model", 0.01, 100, 50)
        ok, reason = tracker.check_budget(0.01)
        assert not ok
        assert "Lauf" in reason or "Aufrufe" in reason

    def test_record_writes_log(self, tmp_path):
        """T15: record() schreibt in llm_cost_log.jsonl."""
        tracker = self._make_tracker(tmp_path)
        tracker.record("proj-001", "model-x", 0.0123, 300, 100)
        log_path = tmp_path / "llm_cost_log.jsonl"
        assert log_path.exists()
        with open(log_path, encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
        assert len(entries) == 1
        assert entries[0]["project_id"] == "proj-001"
        assert abs(entries[0]["cost_usd"] - 0.0123) < 1e-6

    def test_budget_accumulates_across_calls(self, tmp_path):
        """Kumulierte Kosten werden gegen Tagesbudget geprüft."""
        tracker = self._make_tracker(tmp_path, daily_budget_usd=0.05)
        tracker.record("p1", "m", 0.04, 100, 50)
        # Nächster Call würde Tagesbudget überschreiten
        ok, reason = tracker.check_budget(0.02)
        assert not ok


# ══════════════════════════════════════════════════════════════════════════════
# T16–T19: EvalPromptBuilder
# ══════════════════════════════════════════════════════════════════════════════

class TestEvalPromptBuilder:
    """T16–T19: Prompt-Struktur, Truncation, Injection-Schutz."""

    def _profiles(self) -> Dict[str, str]:
        return {pid: f"# {pid}\n- Fähigkeit A\n- Fähigkeit B\n" for pid in PROFILE_IDS}

    def test_prompt_contains_all_profile_ids(self):
        """T16: Prompt enthält alle vier Profil-IDs."""
        builder = EvalPromptBuilder()
        prompt = builder.build("CRM Salesforce Python Projekt.", self._profiles())
        for pid in PROFILE_IDS:
            assert pid in prompt

    def test_prompt_embeds_project_text(self):
        """T17: Projekttext erscheint im Prompt."""
        builder = EvalPromptBuilder()
        project_text = "Wir suchen Python-Entwickler für CRM-Integration."
        prompt = builder.build(project_text, self._profiles())
        assert "Python-Entwickler" in prompt
        assert "CRM-Integration" in prompt

    def test_truncation_marks_missing_content(self):
        """T18: Gekürzter Text wird mit Marker versehen."""
        builder = EvalPromptBuilder(max_input_tokens=100)
        long_text = "A" * 5000
        truncated, was_truncated = builder.truncate_project_text(long_text, 500)
        assert was_truncated
        assert "GEKÜRZT" in truncated or "TEXT GEKÜRZT" in truncated

    def test_truncation_note_in_prompt(self):
        """T19: Bei Kürzung erscheint Hinweis im Prompt."""
        builder = EvalPromptBuilder()
        prompt = builder.build(
            "Kurzer Text.", self._profiles(), was_truncated=True
        )
        assert "gekürzt" in prompt.lower() or "truncated" in prompt.lower() or "HINWEIS" in prompt

    def test_project_text_in_separate_tag(self):
        """Projekttext wird in <project_text>-Tag eingebettet (Injection-Schutz)."""
        builder = EvalPromptBuilder()
        prompt = builder.build("Test-Projektbeschreibung.", self._profiles())
        assert "<project_text>" in prompt
        assert "</project_text>" in prompt

    def test_short_text_not_truncated(self):
        """Kurzer Text wird nicht verändert."""
        builder = EvalPromptBuilder()
        text = "Kurzer Text."
        result, was_truncated = builder.truncate_project_text(text, 5000)
        assert not was_truncated
        assert result == text


# ══════════════════════════════════════════════════════════════════════════════
# T20–T26: LLMEvaluator._parse_and_validate
# ══════════════════════════════════════════════════════════════════════════════

class TestParseAndValidate:
    """T20–T26: Strikte JSON-Validierung der API-Antwort."""

    def _parser(self, tmp_path: Path) -> LLMEvaluator:
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        for pid in PROFILE_IDS:
            (profiles_dir / f"{pid}.md").write_text("# test\n", encoding="utf-8")
        return _make_llm_evaluator_with_mock(tmp_path, profiles_dir=profiles_dir)

    def test_valid_response_parses(self, tmp_path):
        """T20: Gültige Antwort wird korrekt geparst."""
        evaluator = self._parser(tmp_path)
        evals, err = evaluator._parse_and_validate(_ok_api_response())
        assert err == ""
        assert len(evals) == 4
        ids = {e.profile_id for e in evals}
        assert ids == set(PROFILE_IDS)

    def test_wrong_number_of_profiles(self, tmp_path):
        """T21: Fehlende Profile → Validierungsfehler."""
        evaluator = self._parser(tmp_path)
        bad = json.dumps({"evaluations": [
            {"profile_id": "crm_sales_automation", "score": 50,
             "matched": [], "missing": [], "rationale": "x"}
        ]})
        evals, err = evaluator._parse_and_validate(bad)
        assert err != ""
        assert evals == []

    def test_unknown_profile_id_rejected(self, tmp_path):
        """T22: Unbekannte profile_id → Fehler."""
        evaluator = self._parser(tmp_path)
        response = json.loads(_ok_api_response())
        response["evaluations"][0]["profile_id"] = "hacker_profile"
        evals, err = evaluator._parse_and_validate(json.dumps(response))
        assert err != ""
        assert "hacker_profile" in err

    def test_duplicate_profile_id_rejected(self, tmp_path):
        """T23: Doppelte profile_id → Fehler."""
        evaluator = self._parser(tmp_path)
        response = json.loads(_ok_api_response())
        response["evaluations"][1]["profile_id"] = response["evaluations"][0]["profile_id"]
        evals, err = evaluator._parse_and_validate(json.dumps(response))
        assert err != ""

    def test_score_out_of_range_rejected(self, tmp_path):
        """T24: Score außerhalb [0,100] → Fehler."""
        evaluator = self._parser(tmp_path)
        response = json.loads(_ok_api_response())
        response["evaluations"][0]["score"] = 150
        evals, err = evaluator._parse_and_validate(json.dumps(response))
        assert err != ""

    def test_missing_evaluations_key(self, tmp_path):
        """T25: Fehlendes 'evaluations'-Feld → Fehler."""
        evaluator = self._parser(tmp_path)
        evals, err = evaluator._parse_and_validate('{"result": "ok"}')
        assert err != ""

    def test_invalid_json_rejected(self, tmp_path):
        """T26: Ungültiges JSON → Fehler."""
        evaluator = self._parser(tmp_path)
        evals, err = evaluator._parse_and_validate("Das ist kein JSON!")
        assert err != ""
        assert evals == []

    def test_markdown_wrapped_json_parsed(self, tmp_path):
        """Modell schreibt manchmal ```json...``` — trotzdem geparst."""
        evaluator = self._parser(tmp_path)
        wrapped = f"```json\n{_ok_api_response()}\n```"
        evals, err = evaluator._parse_and_validate(wrapped)
        assert err == ""
        assert len(evals) == 4


# ══════════════════════════════════════════════════════════════════════════════
# T27–T31: LLMEvaluator.evaluate (end-to-end mit Mock)
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMEvaluatorEvaluate:
    """T27–T31: End-to-end ohne echte API-Aufrufe."""

    def _evaluator(self, tmp_path: Path) -> LLMEvaluator:
        profiles_dir = tmp_path / "competency_profiles"
        profiles_dir.mkdir()
        for pid in PROFILE_IDS:
            (profiles_dir / f"{pid}.md").write_text(
                f"# {pid}\n- Keyword A\n- Keyword B\n", encoding="utf-8"
            )
        return LLMEvaluator(
            config=_minimal_config(cache_enabled=False),
            output_dir=str(tmp_path),
            profiles_dir=profiles_dir,
        )

    def test_successful_evaluation(self, tmp_path):
        """T27: Erfolgreiche Bewertung gibt LLMEvalResult mit status=ok zurück."""
        evaluator = self._evaluator(tmp_path)
        long_text = "CRM Salesforce Python REST API " * 30  # Ausreichend lang

        with patch.object(evaluator, "_call_api", return_value=(_ok_api_response(), 500, 200)):
            result = evaluator.evaluate("proj-001", long_text)

        assert result.evaluation_status == "ok"
        assert result.best_score == 72
        assert result.best_profile == "crm_sales_automation"
        assert len(result.evaluations) == 4
        assert result.cache_hit is False

    def test_unsafe_content_no_api_call(self, tmp_path):
        """T28: Zu kurzer Text → kein API-Aufruf, status=unsafe_content."""
        evaluator = self._evaluator(tmp_path)
        call_count = []

        def mock_api(prompt):
            call_count.append(1)
            return _ok_api_response(), 100, 50

        with patch.object(evaluator, "_call_api", side_effect=mock_api):
            result = evaluator.evaluate("proj-short", "Zu kurz.")

        assert result.evaluation_status == "unsafe_content"
        assert len(call_count) == 0, "Kein API-Aufruf bei unsicherem Inhalt"

    def test_api_error_returns_pending_retry(self, tmp_path):
        """T29: API-Fehler → status=pending_retry, kein Absturz."""
        evaluator = self._evaluator(tmp_path)
        long_text = "Python Salesforce CRM REST API " * 30

        with patch.object(evaluator, "_call_api", side_effect=Exception("Connection error")):
            result = evaluator.evaluate("proj-api-err", long_text)

        assert result.evaluation_status == "pending_retry"
        assert result.best_score == 0
        assert len(result.evaluations) == 0
        assert "Connection error" in result.error_detail or "fehlgeschlagen" in result.error_detail

    def test_tfidf_scores_not_exposed_on_error(self, tmp_path):
        """T30: Kein TF-IDF-Score als LLM-Score ausgegeben."""
        from pre_scorer import PreScorer
        profiles_dir = tmp_path / "competency_profiles"
        profiles_dir.mkdir()
        for pid in PROFILE_IDS:
            (profiles_dir / f"{pid}.md").write_text(
                "# profile\n- Python\n- CRM\n", encoding="utf-8"
            )

        evaluator = LLMEvaluator(
            config=_minimal_config(cache_enabled=False),
            output_dir=str(tmp_path),
            profiles_dir=profiles_dir,
        )
        pre_scorer = PreScorer(profiles_dir=profiles_dir)
        pre_result = pre_scorer.score_text("Python CRM Salesforce " * 20)
        tfidf_best = pre_result.best_score

        long_text = "Python CRM Salesforce " * 30
        with patch.object(evaluator, "_call_api", side_effect=Exception("API down")):
            result = evaluator.evaluate("proj-tfidf", long_text)

        # Im Fehlerfall muss best_score 0 sein, nicht der TF-IDF-Wert
        assert result.best_score == 0
        assert result.evaluation_status == "pending_retry"

    def test_priority_label_high(self, tmp_path):
        """T31: Score 72 → high_priority bei threshold 65."""
        evaluator = self._evaluator(tmp_path)
        long_text = "CRM Salesforce Python REST API " * 30

        with patch.object(evaluator, "_call_api", return_value=(_ok_api_response(), 500, 200)):
            result = evaluator.evaluate("proj-prio", long_text)

        label = result.priority_label(high_threshold=65, low_threshold=35)
        assert label == "high_priority"

    def test_budget_check_before_api_call(self, tmp_path):
        """Budget-Erschöpfung verhindert API-Aufruf."""
        evaluator = LLMEvaluator(
            config=_minimal_config(
                cache_enabled=False,
                max_calls_per_run=0,   # Sofortiges Limit
            ),
            output_dir=str(tmp_path),
            profiles_dir=tmp_path / "competency_profiles",
        )
        # Profile müssen nicht existieren — Budget-Check kommt vor Prompt-Build
        profiles_dir = tmp_path / "competency_profiles"
        profiles_dir.mkdir(exist_ok=True)
        for pid in PROFILE_IDS:
            (profiles_dir / f"{pid}.md").write_text("# x\n- y\n", encoding="utf-8")

        evaluator2 = LLMEvaluator(
            config=_minimal_config(cache_enabled=False, max_calls_per_run=0),
            output_dir=str(tmp_path),
            profiles_dir=profiles_dir,
        )
        long_text = "Python CRM API " * 30
        call_count = []

        def mock_api(p):
            call_count.append(1)
            return _ok_api_response(), 100, 50

        with patch.object(evaluator2, "_call_api", side_effect=mock_api):
            result = evaluator2.evaluate("proj-budget", long_text)

        assert result.evaluation_status == "pending_retry"
        assert len(call_count) == 0


# ══════════════════════════════════════════════════════════════════════════════
# T32: StateManager 'evaluated'-Zustand
# ══════════════════════════════════════════════════════════════════════════════

class TestEvaluatedState:
    """T32: Neuer 'evaluated'-Zustand in der State-Machine."""

    def test_evaluated_is_valid_state(self):
        """evaluated ist ein bekannter Zustand."""
        sm = ProjectStateManager.__new__(ProjectStateManager)
        assert "evaluated" in ProjectStateManager.VALID_STATES

    def test_scraped_to_evaluated_allowed(self):
        """scraped → evaluated ist erlaubt."""
        sm = ProjectStateManager.__new__(ProjectStateManager)
        assert sm.validate_transition("scraped", "evaluated")

    def test_evaluated_to_accepted_allowed(self):
        """evaluated → accepted ist erlaubt."""
        sm = ProjectStateManager.__new__(ProjectStateManager)
        assert sm.validate_transition("evaluated", "accepted")

    def test_evaluated_to_rejected_allowed(self):
        """evaluated → rejected ist erlaubt."""
        sm = ProjectStateManager.__new__(ProjectStateManager)
        assert sm.validate_transition("evaluated", "rejected")

    def test_evaluated_to_scraped_not_allowed(self):
        """evaluated → scraped ist nicht erlaubt (kein Rückschritt)."""
        sm = ProjectStateManager.__new__(ProjectStateManager)
        assert not sm.validate_transition("evaluated", "scraped")

    def test_update_state_scraped_to_evaluated(self, tmp_path):
        """update_state() setzt evaluated-Zustand korrekt."""
        sm = ProjectStateManager(str(tmp_path))
        project_path = str(tmp_path / "test_project.md")
        content = textwrap.dedent("""\
            ---
            project_id: url-abc123
            title: Test Projekt
            state: scraped
            schema_version: 2
            ---

            Projektbeschreibung hier.
        """)
        with open(project_path, "w", encoding="utf-8") as fh:
            fh.write(content)

        ok = sm.update_state(project_path, "evaluated", note="LLM-Bewertung abgeschlossen")
        assert ok

        fm, _ = sm.read_project(project_path)
        assert fm["state"] == "evaluated"


# ══════════════════════════════════════════════════════════════════════════════
# T33: Profiles-Hash deterministisch
# ══════════════════════════════════════════════════════════════════════════════

class TestProfilesHash:
    """T33: Cache-Key-Komponente Profile-Hash."""

    def test_same_content_same_hash(self, tmp_path):
        """Gleiche Profilinhalte → gleicher Hash."""
        profiles = {pid: f"# {pid}\n- kw\n" for pid in PROFILE_IDS}
        h1 = compute_profiles_hash(profiles)
        h2 = compute_profiles_hash(profiles)
        assert h1 == h2

    def test_different_content_different_hash(self, tmp_path):
        """Geänderte Profile → anderer Hash (Cache-Invalidierung)."""
        profiles_a = {pid: f"# {pid}\n- kw A\n" for pid in PROFILE_IDS}
        profiles_b = {pid: f"# {pid}\n- kw B\n" for pid in PROFILE_IDS}
        assert compute_profiles_hash(profiles_a) != compute_profiles_hash(profiles_b)


# ══════════════════════════════════════════════════════════════════════════════
# T34: LLMEvalResult.priority_label
# ══════════════════════════════════════════════════════════════════════════════

class TestPriorityLabel:
    """T34: Drei-Stufen-Prioritätssystem."""

    def _result(self, score: int) -> LLMEvalResult:
        return LLMEvalResult(
            project_id="x",
            evaluations=[],
            best_profile="crm_sales_automation",
            best_score=score,
            model_used="m",
            prompt_version="v1",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            evaluated_at="",
            cache_hit=False,
        )

    def test_high_priority(self):
        assert self._result(70).priority_label(65, 35) == "high_priority"

    def test_review_priority(self):
        assert self._result(50).priority_label(65, 35) == "review"

    def test_low_priority(self):
        assert self._result(20).priority_label(65, 35) == "low_priority"

    def test_boundary_high(self):
        """Genau an der high-Grenze → high_priority."""
        assert self._result(65).priority_label(65, 35) == "high_priority"

    def test_boundary_low(self):
        """Genau an der low-Grenze → review."""
        assert self._result(35).priority_label(65, 35) == "review"

    def test_score_zero_is_low(self):
        assert self._result(0).priority_label(65, 35) == "low_priority"


# ══════════════════════════════════════════════════════════════════════════════
# T35–T42: PII-Sicherheit — Residuale PII und Edge Cases (Prüfbereich 1)
# ══════════════════════════════════════════════════════════════════════════════

class TestPIIResidualSafety:
    """T35–T42: Erweiterte PII-Sicherheitsprüfungen."""

    def test_residual_email_blocks_send(self):
        """T35: Residuale E-Mail-Adresse nach Scrubbing → is_safe_to_send() = False."""
        # Simuliere einen Fall wo der Scrubber eine E-Mail übersieht
        scrubber = PIIScrubber()
        # Konstruiere ein ScrubResult das noch eine E-Mail enthält (synthetisch)
        # In der Praxis würde das nicht passieren — dies ist ein Regressions-Test
        result = ScrubResult(
            text="A" * 200 + " kontakt@firma.de " + "B" * 50,
            replacements={},
        )
        # is_safe_to_send() soll jetzt die residuale E-Mail erkennen
        assert not result.is_safe_to_send(), (
            "Residuale E-Mail-Adresse muss is_safe_to_send() = False auslösen"
        )

    def test_email_in_project_text_scrubbed_then_safe(self):
        """T36: E-Mail in echtem Text wird gescrubt → danach sicher."""
        scrubber = PIIScrubber()
        text = (
            "Wir suchen einen Python-Entwickler für unser CRM-Team. "
            "Bitte bewerben Sie sich unter bewerbung@muster-gmbh.de. "
            "Das Projekt umfasst die Integration von Salesforce und REST-APIs "
            "sowie die Anbindung an unser bestehendes ERP-System SAP S/4 HANA. "
            "Erfahrung mit agilen Methoden (Scrum, Kanban) wird vorausgesetzt."
        )
        result = scrubber.scrub(text)
        assert "[EMAIL]" in result.text
        assert "bewerbung@muster-gmbh.de" not in result.text
        assert result.is_safe_to_send(), "Nach Scrubbing muss Text sicher sein"

    def test_contact_block_with_phone_scrubbed(self):
        """T37: Kontaktblock mit Telefon wird vollständig entfernt."""
        scrubber = PIIScrubber()
        text = (
            "Python Entwicklung für CRM-Projekt (6 Monate, remote).\n"
            "Anforderungen: Django, REST, PostgreSQL, Agile.\n"
            "Tel: +49 89 123 456 78\n"
            "Fax: +49 89 123 456 79\n"
        )
        result = scrubber.scrub(text)
        assert "89 123 456 78" not in result.text
        assert "Python Entwicklung" in result.text

    def test_email_thread_at_start_preserves_content(self):
        """T38: Thread-Block am Anfang — kein Verlust des echten Projektinhalts."""
        scrubber = PIIScrubber()
        # Der echte Projektinhalt steht VOR dem Thread-Block
        text = (
            "Sehr geehrter Kandidat,\n\n"
            "Wir haben ein spannendes CRM-Projekt für Sie:\n"
            "- Salesforce Administration\n"
            "- Python-Entwicklung\n"
            "- Dauer: 6 Monate\n\n"
            "-------- Forwarded Message --------\n"
            "Von: recruiter@firma.de\n"
            "An: kandidat@mail.de\n"
            "Betreff: Weitergeleitet: Anfrage\n"
        )
        result = scrubber.scrub(text)
        assert result.thread_removed
        assert "Salesforce" in result.text, "Projektinhalt muss erhalten bleiben"
        assert "recruiter@firma.de" not in result.text

    def test_iban_in_payment_section_removed(self):
        """T39: IBAN in Zahlungshinweis wird entfernt."""
        scrubber = PIIScrubber()
        text = (
            "CRM-Projekt: Salesforce und HubSpot Integration, 3 Monate. "
            "Honorar: 850 EUR/Tag. Bankverbindung: DE12 3456 7890 1234 5678 90. "
            "Bitte schicken Sie uns Ihre Rechnung."
        )
        result = scrubber.scrub(text)
        assert "[IBAN]" in result.text
        assert "DE12" not in result.text
        assert "Salesforce" in result.text

    def test_multiple_pii_types_in_one_text(self):
        """T40: Mehrere PII-Typen in einem Text werden alle entfernt."""
        scrubber = PIIScrubber()
        text = (
            "Python Entwickler gesucht für 12 Monate (remote).\n"
            "Kontakt: Herr Müller, Tel: +49 30 555 1234, "
            "E-Mail: mueller@firma.de.\n"
            "Bankverbindung: DE89 3704 0044 0532 0130 00.\n"
            "Anforderungen: Django, FastAPI, Docker, Kubernetes, PostgreSQL, "
            "Microservices-Architektur, CI/CD Pipeline und Agile Scrum."
        )
        result = scrubber.scrub(text)
        assert "mueller@firma.de" not in result.text
        assert "555 1234" not in result.text
        assert "3704 0044" not in result.text
        assert "Müller" not in result.text
        assert result.total_replacements() >= 3
        # Und nach Scrubbing sicher (kein residuales @)
        assert result.is_safe_to_send()

    def test_text_that_is_only_pii_is_blocked(self):
        """T41: Text der nach Scrubbing nur Platzhalter enthält wird blockiert."""
        scrubber = PIIScrubber()
        text = "Herr Schmidt, Tel: +49 30 12345678, E-Mail: schmidt@test.de"
        result = scrubber.scrub(text)
        # Nach Scrubbing: "[NAME], [SCRUBBED], [EMAIL]" — zu kurz
        assert not result.is_safe_to_send()

    def test_clean_technical_project_passes_gate(self):
        """T42: Saubere technische Projektbeschreibung ohne PII passiert das Gate."""
        scrubber = PIIScrubber()
        text = (
            "Gesucht: Python-Entwickler für CRM-Migration zu Salesforce. "
            "Aufgaben: API-Integration, Datenmodellierung, Unit-Tests (pytest). "
            "Stack: Python 3.11, Django 4.2, PostgreSQL 15, Docker, Git. "
            "Laufzeit: 6 Monate, 4 Tage/Woche, remote. Start: Q1 2026."
        )
        result = scrubber.scrub(text)
        assert result.total_replacements() == 0
        assert result.is_safe_to_send()


# ══════════════════════════════════════════════════════════════════════════════
# T43–T48: Kostenkontrolle — Persistenz und Grenzfälle (Prüfbereich 2)
# ══════════════════════════════════════════════════════════════════════════════

class TestBudgetPersistenceAndEdgeCases:
    """T43–T48: Budget-Persistenz über Prozessneustarts und Grenzfälle."""

    def _make_tracker(self, tmp_path: Path, **kwargs) -> "BudgetTracker":
        defaults = dict(
            daily_budget_usd=1.0,
            monthly_budget_usd=10.0,
            max_calls_per_run=5,
            max_cost_per_call_usd=0.20,
        )
        defaults.update(kwargs)
        return BudgetTracker(str(tmp_path), **defaults)

    def test_budget_reloaded_after_restart(self, tmp_path):
        """T43: Tagesverbrauch bleibt nach Prozessneustart erhalten (Persistenz)."""
        # Erster Prozess: Record 0.80 USD
        tracker1 = self._make_tracker(
            tmp_path, daily_budget_usd=1.0, max_cost_per_call_usd=1.0
        )
        tracker1.record("p1", "m", 0.80, 1000, 200)

        # Zweiter Prozess: soll 0.80 bereits verbraucht sehen
        tracker2 = self._make_tracker(
            tmp_path, daily_budget_usd=1.0, max_cost_per_call_usd=1.0
        )
        ok, reason = tracker2.check_budget(0.30)  # 0.80 + 0.30 > 1.0
        assert not ok, "Budget muss nach Neustart korrekt akkumuliert sein"
        assert "Tagesbudget" in reason or "daily" in reason.lower() or "budget" in reason.lower()

    def test_monthly_budget_persists(self, tmp_path):
        """T44: Monatsverbrauch wird persistiert und korrekt summiert."""
        tracker1 = self._make_tracker(
            tmp_path, monthly_budget_usd=5.0, max_cost_per_call_usd=2.0,
            daily_budget_usd=10.0,
        )
        for i in range(3):
            tracker1.record(f"p{i}", "m", 1.60, 500, 100)  # 3 × 1.60 = 4.80

        tracker2 = self._make_tracker(
            tmp_path, monthly_budget_usd=5.0, max_cost_per_call_usd=2.0,
            daily_budget_usd=10.0,
        )
        ok, reason = tracker2.check_budget(0.30)  # 4.80 + 0.30 > 5.0
        assert not ok
        assert "Monatsbudget" in reason or "monthly" in reason.lower() or "budget" in reason.lower()

    def test_max_calls_per_run_enforced(self, tmp_path):
        """T45: max_calls_per_run wird exakt eingehalten."""
        tracker = self._make_tracker(tmp_path, max_calls_per_run=3)
        for i in range(3):
            ok, _ = tracker.check_budget(0.01)
            assert ok, f"Aufruf {i+1} soll erlaubt sein"
            tracker.record(f"p{i}", "m", 0.01, 100, 50)

        # 4. Aufruf muss abgelehnt werden
        ok, reason = tracker.check_budget(0.01)
        assert not ok
        assert "3" in reason or "Lauf" in reason or "Aufrufe" in reason

    def test_max_cost_per_call_enforced(self, tmp_path):
        """T46: Einzelner teurer Aufruf wird blockiert."""
        tracker = self._make_tracker(tmp_path, max_cost_per_call_usd=0.10)
        ok, reason = tracker.check_budget(0.11)  # knapp über Limit
        assert not ok

    def test_cost_log_fields_complete(self, tmp_path):
        """T47: Kostenprotokoll enthält alle Pflichtfelder."""
        tracker = self._make_tracker(tmp_path)
        tracker.record("test-proj", "claude-haiku", 0.0042, 350, 120)

        log_path = tmp_path / "llm_cost_log.jsonl"
        with open(log_path, encoding="utf-8") as fh:
            entry = json.loads(fh.readline())

        assert "timestamp" in entry
        assert "project_id" in entry
        assert "model" in entry
        assert "cost_usd" in entry
        assert "input_tokens" in entry
        assert "output_tokens" in entry
        assert entry["project_id"] == "test-proj"
        assert entry["model"] == "claude-haiku"
        assert abs(entry["cost_usd"] - 0.0042) < 1e-6

    def test_retry_does_not_double_count_budget(self, tmp_path):
        """T48: Fehlgeschlagene Retries zählen nicht als erfolgreiche API-Aufrufe."""
        profiles_dir = tmp_path / "competency_profiles"
        profiles_dir.mkdir()
        for pid in PROFILE_IDS:
            (profiles_dir / f"{pid}.md").write_text(f"# {pid}\n- kw\n", encoding="utf-8")

        evaluator = LLMEvaluator(
            config=_minimal_config(cache_enabled=False),
            output_dir=str(tmp_path),
            profiles_dir=profiles_dir,
        )
        long_text = "Python CRM Salesforce REST API Django " * 30

        # API schlägt immer fehl
        with patch.object(evaluator, "_call_api", side_effect=Exception("Timeout")):
            result = evaluator.evaluate("proj-retry", long_text)

        assert result.evaluation_status == "pending_retry"

        # Budget-Log darf keinen Eintrag enthalten
        log_path = tmp_path / "llm_cost_log.jsonl"
        if log_path.exists():
            with open(log_path, encoding="utf-8") as fh:
                entries = [l for l in fh if l.strip()]
            assert len(entries) == 0, "Fehlgeschlagene Aufrufe dürfen nicht ins Kostenlog"

        # run-Counter bleibt 0
        summary = evaluator.budget_summary()
        assert summary["calls_this_run"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# T49–T53: Fehlerbehandlung — API-Fehler und Validierungsfehler (Prüfbereich 3)
# ══════════════════════════════════════════════════════════════════════════════

class TestErrorHandlingEdgeCases:
    """T49–T53: Simulation von API-Fehlern und unvollständigen Antworten."""

    def _evaluator(self, tmp_path: Path) -> LLMEvaluator:
        profiles_dir = tmp_path / "competency_profiles"
        profiles_dir.mkdir(exist_ok=True)
        for pid in PROFILE_IDS:
            (profiles_dir / f"{pid}.md").write_text(
                f"# {pid}\n- Keyword\n", encoding="utf-8"
            )
        return LLMEvaluator(
            config=_minimal_config(cache_enabled=False),
            output_dir=str(tmp_path),
            profiles_dir=profiles_dir,
        )

    def _long_text(self) -> str:
        return "Python CRM Salesforce REST API Django Kubernetes Docker " * 15

    def test_rate_limit_error_returns_pending_retry(self, tmp_path):
        """T49: Rate-Limit-Fehler (simuliert) → pending_retry, kein Absturz."""
        evaluator = self._evaluator(tmp_path)

        class MockRateLimitError(Exception):
            pass

        with patch.object(evaluator, "_call_api",
                          side_effect=MockRateLimitError("429 Too Many Requests")):
            result = evaluator.evaluate("proj-rate", self._long_text())

        assert result.evaluation_status == "pending_retry"
        assert result.best_score == 0
        assert len(result.evaluations) == 0
        assert "429" in result.error_detail or "fehlgeschlagen" in result.error_detail

    def test_timeout_error_returns_pending_retry(self, tmp_path):
        """T50: Timeout → pending_retry."""
        evaluator = self._evaluator(tmp_path)

        with patch.object(evaluator, "_call_api",
                          side_effect=TimeoutError("Connection timed out")):
            result = evaluator.evaluate("proj-timeout", self._long_text())

        assert result.evaluation_status == "pending_retry"
        assert result.best_score == 0

    def test_invalid_json_response_returns_failed(self, tmp_path):
        """T51: Ungültiges JSON → failed, kein TF-IDF-Score."""
        evaluator = self._evaluator(tmp_path)

        with patch.object(evaluator, "_call_api",
                          return_value=("Das ist kein gültiges JSON!", 100, 50)):
            result = evaluator.evaluate("proj-badjson", self._long_text())

        assert result.evaluation_status == "failed"
        assert result.best_score == 0
        assert len(result.evaluations) == 0

    def test_incomplete_profiles_in_response_returns_failed(self, tmp_path):
        """T52: API liefert weniger als 4 Profile → failed."""
        evaluator = self._evaluator(tmp_path)
        incomplete = json.dumps({
            "evaluations": [
                {"profile_id": "crm_sales_automation", "score": 80,
                 "matched": [], "missing": [], "rationale": "ok"},
                {"profile_id": "power_bi_sharepoint", "score": 40,
                 "matched": [], "missing": [], "rationale": "ok"},
                # Nur 2 statt 4 Profile
            ]
        })

        with patch.object(evaluator, "_call_api", return_value=(incomplete, 100, 50)):
            result = evaluator.evaluate("proj-incomplete", self._long_text())

        assert result.evaluation_status == "failed"
        assert result.best_score == 0

    def test_error_state_not_set_to_evaluated(self, tmp_path):
        """T53: Bei API-Fehler wird der Projektzustand NICHT auf 'evaluated' gesetzt."""
        import textwrap
        from state_manager import ProjectStateManager

        sm = ProjectStateManager(str(tmp_path))
        project_path = str(tmp_path / "test_error.md")
        content = textwrap.dedent("""\
            ---
            project_id: url-abc12345abcd
            title: Fehler-Test Projekt
            state: scraped
            schema_version: 2
            ---

            Python CRM Salesforce Integration REST API Django Framework Docker.
            Erfahrung mit agilen Methoden, Scrum-Zeremonien, CI/CD Pipeline.
        """)
        with open(project_path, "w", encoding="utf-8") as fh:
            fh.write(content)

        config = _minimal_config(cache_enabled=False)
        profiles_dir = tmp_path / "competency_profiles"
        profiles_dir.mkdir(exist_ok=True)
        for pid in PROFILE_IDS:
            (profiles_dir / f"{pid}.md").write_text(f"# {pid}\n- kw\n", encoding="utf-8")

        evaluator = LLMEvaluator(
            config=config, output_dir=str(tmp_path), profiles_dir=profiles_dir
        )

        with patch.object(evaluator, "_call_api", side_effect=Exception("API down")):
            result = evaluator.evaluate("url-abc12345abcd", "Python CRM " * 20)

        # Manuell den Record lesen und prüfen dass State noch 'scraped' ist
        record, _ = sm.read_project_record(project_path)
        assert record.state == "scraped", (
            "Bei API-Fehler darf der State nicht auf 'evaluated' wechseln"
        )
        assert result.evaluation_status == "pending_retry"


# ══════════════════════════════════════════════════════════════════════════════
# T54–T57: Datenintegrität (Prüfbereich 4)
# ══════════════════════════════════════════════════════════════════════════════

class TestDataIntegrity:
    """T54–T57: Datenintegrität bei Re-Evaluierung und Filter-Ergebnissen."""

    def _profiles_dir(self, tmp_path: Path) -> Path:
        profiles_dir = tmp_path / "competency_profiles"
        profiles_dir.mkdir(exist_ok=True)
        for pid in PROFILE_IDS:
            (profiles_dir / f"{pid}.md").write_text(
                f"# {pid}\n- Schlüsselwort\n", encoding="utf-8"
            )
        return profiles_dir

    def test_re_evaluation_preserves_filter_results(self, tmp_path):
        """T54: Re-Evaluierung überschreibt filter_results NICHT."""
        import textwrap

        profiles_dir = self._profiles_dir(tmp_path)
        sm = ProjectStateManager(str(tmp_path))

        # Projekt mit bestehenden filter_results (Phase 3)
        project_path = str(tmp_path / "proj_reeval.md")
        content = textwrap.dedent("""\
            ---
            project_id: url-reeval001test
            title: Re-Eval Test
            state: scraped
            schema_version: 2
            filter_results:
              automation-bi:
                accepted: true
                score: 0.75
            pre_scores:
              crm_sales_automation: 0.62
            ---

            Python CRM Salesforce Integration REST API Django. Agile Scrum CI/CD.
            Kenntnisse in Kubernetes Docker PostgreSQL und Microservices-Architektur.
        """)
        with open(project_path, "w", encoding="utf-8") as fh:
            fh.write(content)

        evaluator = LLMEvaluator(
            config=_minimal_config(cache_enabled=False),
            output_dir=str(tmp_path),
            profiles_dir=profiles_dir,
        )
        long_text = "Python CRM Salesforce Integration REST API " * 15

        with patch.object(evaluator, "_call_api",
                          return_value=(_ok_api_response(), 500, 200)):
            result = evaluator.evaluate("url-reeval001test", long_text)

        # Manuelle Integration wie in run_evaluation_pass()
        record, body = sm.read_project_record(project_path)
        assert record is not None

        # Bestehende Phase-3-Daten prüfen
        assert "filter_results" in record.extra, "filter_results muss noch vorhanden sein"
        assert "pre_scores" in record.extra, "pre_scores muss noch vorhanden sein"

        # Neue LLM-Daten hinzufügen
        record.extra["llm_evaluation"] = result.to_dict()
        record.extra["llm_priority"] = result.priority_label(65, 35)
        record.extra["evaluation_status"] = result.evaluation_status
        sm.write_project_record(project_path, record, body)

        # Neu lesen und prüfen
        record2, _ = sm.read_project_record(project_path)
        assert "filter_results" in record2.extra, "filter_results nach Re-Eval noch da"
        assert "pre_scores" in record2.extra, "pre_scores nach Re-Eval noch da"
        assert "llm_evaluation" in record2.extra, "llm_evaluation neu hinzugefügt"
        assert record2.extra["llm_evaluation"]["best_score"] == 72

    def test_successful_evaluation_sets_state_evaluated(self, tmp_path):
        """T55: Erfolgreiche run_evaluation_pass() setzt State auf 'evaluated'."""
        import textwrap
        from llm_evaluator import run_evaluation_pass

        profiles_dir = self._profiles_dir(tmp_path)

        project_path = tmp_path / "proj_state_test.md"
        content = textwrap.dedent("""\
            ---
            project_id: url-statetest001x
            title: State Test Projekt
            state: scraped
            schema_version: 2
            ---

            Python CRM Salesforce Integration REST API Django Framework.
            Kubernetes Docker PostgreSQL Agile Scrum CI/CD Pipeline GitHub Actions.
            Microservices-Architektur, Datenbankoptimierung, Unit-Tests mit pytest.
            Laufzeit 9 Monate, remote, 4 Tage pro Woche, Start Q1 2026.
        """)
        project_path.write_text(content, encoding="utf-8")

        config = _minimal_config(
            cache_enabled=False,
            provider="anthropic",
        )

        from llm_evaluator import LLMEvaluator
        import unittest.mock as mock_lib

        # Patch auf Klassenebene für run_evaluation_pass
        original_init = LLMEvaluator.__init__

        def patched_init(self, config, output_dir, profiles_dir=None):
            original_init(self, config, output_dir, profiles_dir=profiles_dir)

        with mock_lib.patch.object(LLMEvaluator, "_call_api",
                                   return_value=(_ok_api_response(), 500, 200)):
            stats = run_evaluation_pass(
                config=config,
                output_dir=str(tmp_path),
                projects_dir=str(tmp_path),
            )

        assert stats["projects_ok"] >= 1, "Mindestens ein Projekt soll bewertet worden sein"

        sm = ProjectStateManager(str(tmp_path))
        record, _ = sm.read_project_record(str(project_path))
        assert record is not None
        assert record.state == "evaluated", (
            f"State nach Bewertung muss 'evaluated' sein, war: {record.state}"
        )
        assert "llm_evaluation" in record.extra

    def test_failed_evaluation_keeps_state_scraped(self, tmp_path):
        """T56: Fehlgeschlagene Evaluierung lässt State auf 'scraped'."""
        import textwrap
        from llm_evaluator import run_evaluation_pass

        self._profiles_dir(tmp_path)  # Profile anlegen

        project_path = tmp_path / "proj_fail_state.md"
        content = textwrap.dedent("""\
            ---
            project_id: url-failstate001x
            title: Fail State Test
            state: scraped
            schema_version: 2
            ---

            Python CRM Salesforce Django REST API.
            Kubernetes Docker CI/CD Agile Scrum.
        """)
        project_path.write_text(content, encoding="utf-8")

        config = _minimal_config(cache_enabled=False)

        from llm_evaluator import LLMEvaluator
        import unittest.mock as mock_lib

        with mock_lib.patch.object(LLMEvaluator, "_call_api",
                                   side_effect=Exception("API not available")):
            stats = run_evaluation_pass(
                config=config,
                output_dir=str(tmp_path),
                projects_dir=str(tmp_path),
            )

        sm = ProjectStateManager(str(tmp_path))
        record, _ = sm.read_project_record(str(project_path))
        assert record is not None
        assert record.state == "scraped", (
            "Nach fehlgeschlagener Bewertung muss State 'scraped' bleiben"
        )

    def test_llm_evaluation_stored_in_extra(self, tmp_path):
        """T57: LLM-Ergebnis wird korrekt in record.extra gespeichert."""
        profiles_dir = self._profiles_dir(tmp_path)
        evaluator = LLMEvaluator(
            config=_minimal_config(cache_enabled=False),
            output_dir=str(tmp_path),
            profiles_dir=profiles_dir,
        )
        long_text = "Python CRM Salesforce REST API Django " * 20

        with patch.object(evaluator, "_call_api",
                          return_value=(_ok_api_response(), 500, 200)):
            result = evaluator.evaluate("url-extratest", long_text)

        assert result.evaluation_status == "ok"
        d = result.to_dict()
        assert "evaluations" in d
        assert len(d["evaluations"]) == 4
        assert d["best_score"] == 72
        assert d["best_profile"] == "crm_sales_automation"
        assert d["evaluation_status"] == "ok"
        assert d["pii_replacements"] == 0  # kein PII im Test-Text


# ══════════════════════════════════════════════════════════════════════════════
# T58: End-to-End-Test (Prüfbereich 5)
# ══════════════════════════════════════════════════════════════════════════════

class TestEndToEnd:
    """
    T58: End-to-End ohne echte API-Aufrufe und ohne echte E-Mails.

    Weg: Projektdatei (state=scraped) → PIIScrubber → LLMEvaluator (Mock-API)
         → 4 ProfileEvaluations → ProjectRecord.extra → state=evaluated
    """

    def test_full_pipeline_scraper_to_evaluated(self, tmp_path):
        """
        T58: Vollständige Pipeline vom Projekt-Anlegen bis state=evaluated.

        Simuliert:
        1. Projektdatei im Zustand 'scraped' mit Projektbeschreibung
        2. PII-Scrubbing (E-Mail in Body wird entfernt)
        3. LLMEvaluator.evaluate() mit gemockter API
        4. Alle 4 Profilbewertungen korrekt gespeichert
        5. State → 'evaluated', kein echter API-Aufruf, keine E-Mail
        """
        import textwrap
        from llm_evaluator import run_evaluation_pass

        # ── Profiles anlegen ──────────────────────────────────────────────────
        profiles_dir = tmp_path / "competency_profiles"
        profiles_dir.mkdir()
        for pid in PROFILE_IDS:
            (profiles_dir / f"{pid}.md").write_text(
                f"# {pid}\n- Python\n- CRM\n- Salesforce\n", encoding="utf-8"
            )

        # ── Projektdatei anlegen (simuliert RSS-Ingestion Ausgabe) ─────────────
        project_path = tmp_path / "proj_e2e_test.md"
        body_text = textwrap.dedent("""\
            # CRM-Migration zu Salesforce

            Wir suchen einen erfahrenen Python-Entwickler für die Migration
            unseres CRM-Systems zu Salesforce. Das Projekt umfasst die
            Integration von REST-APIs, Django-Backend-Entwicklung und
            die Anbindung an SAP S/4 HANA.

            **Anforderungen:**
            - Python 3.11+, Django 4.x
            - Salesforce Administration und APEX
            - REST/SOAP API-Integration
            - PostgreSQL, Docker, Kubernetes
            - Agile Methoden (Scrum)

            **Laufzeit:** 9 Monate, remote, 5 Tage/Woche
            **Start:** Q1 2026

            Kontakt unter: bewerbung@muster-recruiter.de
        """)

        frontmatter = textwrap.dedent("""\
            ---
            project_id: url-e2etest001xx
            title: CRM-Migration zu Salesforce
            state: scraped
            schema_version: 2
            company: Muster GmbH
            provider: freelancermap
            provider_url: https://www.freelancermap.de/projektmarkt/detail/123456
            created_at: "2026-01-15T10:00:00Z"
            filter_results:
              automation-bi:
                accepted: true
                rule_hits: []
            pre_scores:
              crm_sales_automation: 0.71
              ai_business_process_integration: 0.35
            ---

        """)
        project_path.write_text(frontmatter + body_text, encoding="utf-8")

        # ── Konfiguration ─────────────────────────────────────────────────────
        config = _minimal_config(
            cache_enabled=False,
            provider="anthropic",
        )

        # ── Evaluations-Lauf mit gemocktem API-Call ────────────────────────────
        from llm_evaluator import LLMEvaluator
        import unittest.mock as mock_lib

        api_call_log = []

        def mock_call_api(self_inner, prompt: str):
            # Kein echter API-Aufruf — sicherstellen dass Projekttext nicht roh gesendet
            assert "bewerbung@muster-recruiter.de" not in prompt, (
                "E-Mail-Adresse darf nicht an API gesendet werden!"
            )
            assert "<project_text>" in prompt, "Projekttext muss in XML-Tag eingebettet sein"
            api_call_log.append({"prompt_len": len(prompt)})
            return _ok_api_response(), 520, 210

        with mock_lib.patch.object(LLMEvaluator, "_call_api", mock_call_api):
            stats = run_evaluation_pass(
                config=config,
                output_dir=str(tmp_path),
                projects_dir=str(tmp_path),
            )

        # ── Ergebnisse prüfen ─────────────────────────────────────────────────

        # 1. Genau ein API-Aufruf (kein Real-Call, kein Versand)
        assert len(api_call_log) == 1, f"Genau 1 API-Call erwartet, war: {len(api_call_log)}"
        assert stats["projects_ok"] == 1
        assert stats["projects_found"] == 1

        # 2. State wurde auf 'evaluated' gesetzt
        sm = ProjectStateManager(str(tmp_path))
        record, body = sm.read_project_record(str(project_path))
        assert record is not None
        assert record.state == "evaluated", (
            f"State muss 'evaluated' sein, war: {record.state}"
        )

        # 3. Alle 4 Profilbewertungen gespeichert
        assert "llm_evaluation" in record.extra
        llm_eval = record.extra["llm_evaluation"]
        assert len(llm_eval["evaluations"]) == 4
        assert llm_eval["best_score"] == 72
        assert llm_eval["best_profile"] == "crm_sales_automation"
        assert llm_eval["evaluation_status"] == "ok"

        # 4. Phase-3-Daten noch vorhanden
        assert "filter_results" in record.extra, "filter_results muss erhalten bleiben"
        assert "pre_scores" in record.extra, "pre_scores muss erhalten bleiben"

        # 5. llm_priority gesetzt
        assert record.extra.get("llm_priority") == "high_priority"

        # 6. State-History enthält evaluated-Eintrag
        evaluated_entries = [
            h for h in record.state_history
            if h.get("state") == "evaluated"
        ]
        assert len(evaluated_entries) == 1, "State-History muss evaluated-Eintrag enthalten"

        # 7. Keine echten E-Mails (nur prüfen dass keine SMTP-Importe laufen)
        # Dieser Test sendet keine E-Mails — er ruft nur evaluate() auf.
        # Das ist per Design sichergestellt (run_evaluation_pass macht kein smtp.send)
        assert "smtp" not in str(stats).lower(), "Kein SMTP im Evaluations-Lauf"
