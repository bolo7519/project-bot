"""
Tests für die Phase-0-Bugfix-Funktion read_llm_score_from_file() in main.py.

Die Funktion wird direkt aus dem main.py-Quelltext via exec() geladen, um den
kaputten Import von application_generator.py (veraltete LangChain-API) zu umgehen.
Das ist ein bekanntes Problem aus Phase 0 (LangChain-Kompatibilität wird in Phase 1
behoben). Solange der Import-Baum kaputt ist, testen wir die Funktion isoliert.
"""

import re
import pytest
from pathlib import Path


FIXTURE_DIR = Path(__file__).parent / "fixtures"
MAIN_PY = Path(__file__).parent.parent / "main.py"


def _extract_function(source: str, func_name: str) -> str:
    """Extrahiert eine einzelne Funktionsdefinition aus Python-Quelltext."""
    pattern = rf'^(def {re.escape(func_name)}\b.*?)(?=\n^def |\n^class |\Z)'
    match = re.search(pattern, source, re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError(f"Funktion '{func_name}' nicht in main.py gefunden")
    return match.group(1)


# Lade read_llm_score_from_file direkt aus dem Quelltext heraus
_source = MAIN_PY.read_text(encoding="utf-8")
_func_src = _extract_function(_source, "read_llm_score_from_file")
_namespace: dict = {}
exec(_func_src, _namespace)  # noqa: S102  (isolierter Test-Namespace, kein Produktionscode)
read_llm_score_from_file = _namespace["read_llm_score_from_file"]


class TestReadLlmScoreFromFile:
    def test_reads_llm_score_from_accepted_fixture(self):
        """Fixture project_accepted.md enthält 'LLM Score: 88%'."""
        score = read_llm_score_from_file(str(FIXTURE_DIR / "project_accepted.md"))
        assert score == 88

    def test_reads_pre_eval_score_as_fallback(self, tmp_path):
        """Wenn kein LLM Score vorhanden, Pre-Eval Score als Fallback."""
        content = """\
---
title: Test
state: accepted
---

**Pre-Evaluation Score:** 65%
"""
        p = tmp_path / "project.md"
        p.write_text(content)
        score = read_llm_score_from_file(str(p))
        assert score == 65

    def test_returns_zero_for_missing_score(self, tmp_path):
        """Datei ohne Score → 0 statt Crash."""
        content = """\
---
title: Kein Score
state: scraped
---

Nur Beschreibung, keine Bewertung.
"""
        p = tmp_path / "project.md"
        p.write_text(content)
        score = read_llm_score_from_file(str(p))
        assert score == 0

    def test_returns_zero_for_nonexistent_file(self, tmp_path):
        score = read_llm_score_from_file(str(tmp_path / "does_not_exist.md"))
        assert score == 0

    def test_score_not_hardcoded_95(self):
        """Regression: Bug war fit_score = 95 unabhängig vom Dateiinhalt."""
        score = read_llm_score_from_file(str(FIXTURE_DIR / "project_accepted.md"))
        assert score != 95, "Score darf nicht mehr hartcodiert 95 sein"
        assert 0 <= score <= 100

    def test_score_for_scraped_project_is_zero(self):
        """scraped-Projekte haben noch keinen Score → 0 erwartet."""
        score = read_llm_score_from_file(str(FIXTURE_DIR / "project_scraped.md"))
        assert score == 0

    def test_function_exists_in_main_py(self):
        """Sicherstellen, dass die Funktion noch in main.py vorhanden ist."""
        assert "def read_llm_score_from_file" in _source, (
            "read_llm_score_from_file fehlt in main.py"
        )

    def test_no_hardcoded_95_in_main_py(self):
        """Regression: fit_score = 95 darf nicht mehr im main.py vorkommen."""
        assert "fit_score = 95" not in _source, (
            "fit_score = 95 ist noch hartcodiert in main.py – Phase-0-Fix fehlt"
        )
