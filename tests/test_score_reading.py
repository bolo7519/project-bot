"""
Tests für read_llm_score_from_file() aus scores.py (Phase-0-Bugfix).

Die Funktion wurde aus main.py in das eigenständige Modul scores.py ausgelagert,
damit Tests sie direkt importieren können – ohne den kaputten Import-Baum von
main.py (LangChain-Kompatibilität, Phase 1) zu berühren.
"""

import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scores import read_llm_score_from_file  # Direkt aus Produktionsmodul


FIXTURE_DIR = Path(__file__).parent / "fixtures"
MAIN_PY = Path(__file__).parent.parent / "main.py"
SCORES_PY = Path(__file__).parent.parent / "scores.py"


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

    def test_function_lives_in_scores_module(self):
        """Sicherstellen, dass die Funktion in scores.py (nicht nur main.py) vorhanden ist."""
        src = SCORES_PY.read_text(encoding="utf-8")
        assert "def read_llm_score_from_file" in src, (
            "read_llm_score_from_file fehlt in scores.py"
        )

    def test_main_py_imports_from_scores(self):
        """main.py soll scores.read_llm_score_from_file importieren, nicht selbst definieren."""
        src = MAIN_PY.read_text(encoding="utf-8")
        assert "from scores import read_llm_score_from_file" in src, (
            "main.py importiert read_llm_score_from_file nicht aus scores.py"
        )
        assert "def read_llm_score_from_file" not in src, (
            "read_llm_score_from_file darf nicht mehr direkt in main.py definiert sein"
        )

    def test_no_hardcoded_95_in_main_py(self):
        """Regression: fit_score = 95 darf nicht mehr im main.py vorkommen."""
        src = MAIN_PY.read_text(encoding="utf-8")
        assert "fit_score = 95" not in src, (
            "fit_score = 95 ist noch hartcodiert in main.py – Phase-0-Fix fehlt"
        )
