"""
Tests: Die Score-Spalte des Dashboards zeigt, woher der Wert stammt.

  "40 · Eignung"  — regelbasierte Eignungsbewertung (suitability)
  "32 · TF-IDF"   — Textähnlichkeit zu den Kompetenzprofilen (ältere Dateien)
  "55 · Keyword"  — Keyword-Vorbewertung aus evaluate_projects.py

Die Herkunft wird nur aus dem vorhandenen Dateiinhalt gelesen; bestehende
Projektdateien werden weder verändert noch rückwirkend überschrieben.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from server_enhanced import (
    SCORE_KIND_KEYWORD, SCORE_KIND_SUITABILITY, SCORE_KIND_TFIDF,
    app, detect_pre_eval_score_kind, parse_project_file,
)

REPO_ROOT = Path(__file__).parent.parent
FRONTEND = REPO_ROOT / "frontend" / "src"

HEAD = "---\ntitle: {title}\nstate: scraped\nsource_url: https://example.com/{slug}\n---\n\n# {title}\n\nText\n"

# So sehen Dateien aus, die vor der Eignungsbewertung importiert wurden
OLD_TFIDF = HEAD + "\n## Vorbewertung\n\n- **Score:** 32/100\n"
# Neue Dateien mit Eignungsbewertung (Format aus run_search_groups_rss.py)
NEW_SUITABILITY = HEAD + (
    "\n## Vorbewertung\n\n- **Score:** 40/100\n- **Score-Art:** Eignung (regelbasiert)\n"
    "- **Eignung:** 40/100 — fachlich 40/100 (mittel), Empfehlung: Prüfen\n"
    "- **Textähnlichkeit (TF-IDF):** 32/100 — bestes Profil: it_infrastructure_security\n"
)
# Neue Dateien ohne Eignungsbewertung
NEW_TFIDF = HEAD + (
    "\n## Vorbewertung\n\n- **Score:** 15/100\n- **Score-Art:** Textähnlichkeit (TF-IDF)\n"
    "- **Textähnlichkeit (TF-IDF):** 15/100 — bestes Profil: power_bi_sharepoint\n"
)
# Keyword-Vorbewertung aus evaluate_projects.py
KEYWORD = HEAD + (
    "\n## 🤖 AI Evaluation Results\n\n**Evaluation Timestamp:** 2026-10-05T09:00:00\n\n"
    "### Pre-Evaluation Phase\n- **Score:** 55/100\n"
)
NO_SCORE = HEAD


def _write(directory: Path, name: str, template: str) -> Path:
    path = directory / f"{name}.md"
    path.write_text(template.format(title=name, slug=name), encoding="utf-8")
    return path


class TestDetectScoreKind:

    @pytest.mark.parametrize("template,score,kind", [
        (OLD_TFIDF, 32, SCORE_KIND_TFIDF),
        (NEW_SUITABILITY, 40, SCORE_KIND_SUITABILITY),
        (NEW_TFIDF, 15, SCORE_KIND_TFIDF),
        (KEYWORD, 55, SCORE_KIND_KEYWORD),
        (NO_SCORE, None, None),
    ], ids=["alt_tfidf", "eignung", "neu_tfidf", "keyword", "ohne_score"])
    def test_kind(self, template, score, kind):
        content = template.format(title="x", slug="x")
        assert detect_pre_eval_score_kind(content, score) == kind

    def test_similarity_line_alone_is_not_mistaken_for_suitability(self):
        """Die Zeile "Eignung:" ohne "Score-Art: Eignung" reicht nicht."""
        content = OLD_TFIDF.format(title="x", slug="x") + "- **Eignung:** 99/100\n"
        assert detect_pre_eval_score_kind(content, 32) == SCORE_KIND_TFIDF

    def test_later_ai_evaluation_takes_over(self):
        """Nach einer Keyword-Bewertung liest das Dashboard deren Wert — und nennt ihn so."""
        content = NEW_SUITABILITY.format(title="x", slug="x") + (
            "\n## 🤖 AI Evaluation Results\n\n**Evaluation Timestamp:** 2026-10-11T09:00:00\n\n"
            "- **Score:** 70/100\n")
        assert detect_pre_eval_score_kind(content, 70) == SCORE_KIND_KEYWORD


class TestApiReportsScoreKind:

    @pytest.fixture
    def project_dir(self, tmp_path):
        directory = tmp_path / "projects"
        directory.mkdir()
        _write(directory, "alt", OLD_TFIDF)
        _write(directory, "neu", NEW_SUITABILITY)
        _write(directory, "keyword", KEYWORD)
        _write(directory, "leer", NO_SCORE)
        return directory

    def test_parse_project_file(self, project_dir):
        got = {
            name: (parsed["pre_eval_score"], parsed["pre_eval_score_kind"])
            for name in ("alt", "neu", "keyword", "leer")
            for parsed in [parse_project_file(str(project_dir / f"{name}.md"))]
        }
        assert got == {
            "alt": (32, "tfidf"), "neu": (40, "eignung"),
            "keyword": (55, "keyword"), "leer": (None, None),
        }

    def test_project_list_endpoint(self, project_dir, tmp_path):
        orig_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with app.test_client() as client:
                resp = client.get("/api/v1/projects?page_size=50")
            assert resp.status_code == 200
            projects = {p["id"]: p for p in resp.get_json()["projects"]}
        finally:
            os.chdir(orig_cwd)
        assert projects["alt"]["pre_eval_score_kind"] == "tfidf"
        assert projects["neu"]["pre_eval_score_kind"] == "eignung"
        assert projects["keyword"]["pre_eval_score_kind"] == "keyword"
        assert projects["leer"]["pre_eval_score_kind"] is None
        assert projects["neu"]["pre_eval_score"] == 40

    def test_reading_does_not_modify_existing_files(self, project_dir, tmp_path):
        def digest():
            return {
                p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in project_dir.glob("*.md")
            }

        before = digest()
        orig_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with app.test_client() as client:
                assert client.get("/api/v1/projects?page_size=50").status_code == 200
                assert client.get("/api/v1/dashboard/stats").status_code == 200
        finally:
            os.chdir(orig_cwd)
        assert digest() == before

    def test_score_filter_still_works_on_the_number(self, project_dir, tmp_path):
        orig_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with app.test_client() as client:
                resp = client.get("/api/v1/projects?pre_eval_score_min=35&page_size=50")
            ids = {p["id"] for p in resp.get_json()["projects"]}
        finally:
            os.chdir(orig_cwd)
        assert ids == {"neu", "keyword"}


class TestFrontendLabels:

    def test_label_helper_defines_the_three_kinds(self):
        text = (FRONTEND / "services" / "scoreLabel.js").read_text("utf-8")
        for snippet in ("eignung: 'Eignung'", "tfidf: 'TF-IDF'", "keyword: 'Keyword'",
                        "${project.pre_eval_score} · ${label}"):
            assert snippet in text, snippet

    @pytest.mark.parametrize("component", ["ProjectTable.vue", "ProjectDetailsModal.vue"])
    def test_components_use_the_labelled_score(self, component):
        text = (FRONTEND / "components" / component).read_text("utf-8")
        assert "import { formatPreEvalScore } from '../services/scoreLabel'" in text
        assert "{{ formatPreEvalScore(project) }}" in text
        assert "{{ project.pre_eval_score }}%" not in text   # keine unbeschriftete Zahl mehr

    def test_api_kinds_match_frontend_labels(self):
        text = (FRONTEND / "services" / "scoreLabel.js").read_text("utf-8")
        for kind in (SCORE_KIND_SUITABILITY, SCORE_KIND_TFIDF, SCORE_KIND_KEYWORD):
            assert f"{kind}: '" in text, kind
