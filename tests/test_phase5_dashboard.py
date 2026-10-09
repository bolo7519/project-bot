"""
tests/test_phase5_dashboard.py — Phase-5-Tests: Dashboard-API, LLM-Filter,
Statusübergänge, Undo-Endpunkt, Kostenübersicht.

Testanforderungen:
  T63. GET /api/v1/projects?evaluation_statuses=ok gibt nur ok-bewertete zurück
  T64. GET /api/v1/projects?priority_labels=high gibt nur high-priority zurück
  T65. GET /api/v1/projects?search_groups=crm gibt nur passende Gruppe zurück
  T66. GET /api/v1/dashboard/stats enthält llm_evaluated, llm_high_priority etc.
  T67. GET /api/v1/llm/costs gibt cost_today_usd, calls_today, cache_hits zurück
  T68. GET /api/v1/llm/costs ignoriert type="reserved" Einträge
  T69. POST /api/v1/projects/{id}/undo_state kehrt letzten Zustand zurück
  T70. POST /api/v1/projects/{id}/undo_state schlägt fehl wenn History < 2 Einträge
  T71. parse_project_file() extrahiert llm_priority aus extra-Frontmatter
  T72. parse_project_file() extrahiert llm_evaluation.best_profile korrekt
  T73. parse_project_file() liefert search_groups aus Frontmatter
  T74. Filter: profile_ids filtert nach best_profile korrekt
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

# Projektroot auf sys.path setzen
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Minimale Markdown-Projekt-Hilfsfunktion ───────────────────────────────────

def _write_project_md(projects_dir: Path, project_id: str, **kwargs) -> Path:
    """Schreibt eine minimale .md-Projektdatei mit Frontmatter."""
    title = kwargs.get("title", f"Testprojekt {project_id}")
    status = kwargs.get("status", "scraped")
    llm_priority = kwargs.get("llm_priority", None)
    evaluation_status = kwargs.get("evaluation_status", None)
    best_profile = kwargs.get("best_profile", None)
    search_groups = kwargs.get("search_groups", [])
    llm_evaluation = kwargs.get("llm_evaluation", None)
    state_history = kwargs.get("state_history", [{"state": status, "timestamp": "2026-10-09T10:00:00"}])

    extra: Dict[str, Any] = {}
    if llm_priority is not None:
        extra["llm_priority"] = llm_priority
    if evaluation_status is not None:
        extra["evaluation_status"] = evaluation_status
    if llm_evaluation is not None:
        extra["llm_evaluation"] = llm_evaluation

    import yaml  # type: ignore

    frontmatter: Dict[str, Any] = {
        "title": title,
        "status": status,
        "state_history": state_history,
        "search_groups": search_groups,
    }
    if extra:
        frontmatter["extra"] = extra

    md_content = "---\n" + yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False) + "---\n\nBeschreibung des Projekts."
    path = projects_dir / f"{project_id}.md"
    path.write_text(md_content, encoding="utf-8")
    return path


def _write_cost_log(log_path: Path, entries: list) -> None:
    """Schreibt llm_cost_log.jsonl mit gegebenen Einträgen."""
    lines = [json.dumps(e) for e in entries]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# T71–T73: parse_project_file()
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseProjectFile:
    """Tests für Phase-5-Extraktion in parse_project_file()."""

    def _parse(self, tmp_path, **kwargs):
        """Hilfsmethode: schreibt Datei und parsed sie."""
        from server_enhanced import parse_project_file
        path = _write_project_md(tmp_path, "test-001", **kwargs)
        return parse_project_file(str(path))

    def test_t71_extracts_llm_priority(self, tmp_path):
        """T71: llm_priority aus extra-Frontmatter wird korrekt extrahiert."""
        result = self._parse(tmp_path, llm_priority="high", evaluation_status="ok")
        assert result["llm_priority"] == "high"

    def test_t72_extracts_best_profile(self, tmp_path):
        """T72: best_profile aus llm_evaluation wird korrekt extrahiert."""
        llm_eval = {
            "best_profile": "crm_sales",
            "best_score": 78,
            "evaluations": [],
            "cost_usd": 0.004,
        }
        result = self._parse(tmp_path, llm_evaluation=llm_eval, evaluation_status="ok")
        assert result["best_profile"] == "crm_sales"
        assert result["best_score"] == 78

    def test_t73_extracts_search_groups(self, tmp_path):
        """T73: search_groups aus Frontmatter wird als Liste zurückgegeben."""
        result = self._parse(tmp_path, search_groups=["crm_sales", "bi_analytics"])
        assert "crm_sales" in result["search_groups"]
        assert "bi_analytics" in result["search_groups"]

    def test_parse_missing_llm_fields_returns_none(self, tmp_path):
        """Projekte ohne LLM-Felder liefern None/[] ohne Fehler."""
        from server_enhanced import parse_project_file
        path = _write_project_md(tmp_path, "no-llm")
        result = parse_project_file(str(path))
        assert result["llm_priority"] is None
        assert result["evaluation_status"] is None
        assert result["best_profile"] is None
        assert result["search_groups"] == []
        assert result["llm_evaluation"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# T63–T65, T74: get_projects_with_filters() — Phase-5-Filter
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase5Filters:
    """Tests für neue Filter-Felder in get_projects_with_filters()."""

    def _setup_projects(self, base: Path):
        """Legt projects/-Unterverzeichnis mit 3 Testprojekten an."""
        proj_dir = base / "projects"
        proj_dir.mkdir(exist_ok=True)
        _write_project_md(proj_dir, "proj-high", llm_priority="high",
                          evaluation_status="ok",
                          llm_evaluation={"best_profile": "crm", "best_score": 80, "evaluations": [], "cost_usd": 0.004},
                          search_groups=["crm_sales"])
        _write_project_md(proj_dir, "proj-medium", llm_priority="medium",
                          evaluation_status="ok",
                          llm_evaluation={"best_profile": "bi_analytics", "best_score": 55, "evaluations": [], "cost_usd": 0.003},
                          search_groups=["bi_analytics"])
        _write_project_md(proj_dir, "proj-pending", llm_priority=None,
                          evaluation_status="pending_retry",
                          search_groups=[])

    def _run_filter(self, tmp_path, **filter_kwargs):
        from server_enhanced import get_projects_with_filters, ProjectFilters
        self._setup_projects(tmp_path)
        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            return get_projects_with_filters(ProjectFilters(**filter_kwargs))
        finally:
            os.chdir(orig)

    def test_t63_filter_by_evaluation_status_ok(self, tmp_path):
        """T63: evaluation_statuses=['ok'] filtert nur bewertete Projekte."""
        results = self._run_filter(tmp_path, evaluation_statuses=["ok"])
        ids = [r["id"] for r in results]
        assert "proj-high" in ids
        assert "proj-medium" in ids
        assert "proj-pending" not in ids

    def test_t64_filter_by_priority_high(self, tmp_path):
        """T64: priority_labels=['high'] gibt nur high-priority Projekte zurück."""
        results = self._run_filter(tmp_path, priority_labels=["high"])
        ids = [r["id"] for r in results]
        assert "proj-high" in ids
        assert "proj-medium" not in ids
        assert "proj-pending" not in ids

    def test_t65_filter_by_search_group(self, tmp_path):
        """T65: search_groups=['crm_sales'] filtert nach Suchgruppe."""
        results = self._run_filter(tmp_path, search_groups=["crm_sales"])
        ids = [r["id"] for r in results]
        assert "proj-high" in ids
        assert "proj-medium" not in ids

    def test_t74_filter_by_profile_id(self, tmp_path):
        """T74: profile_ids filtert nach best_profile."""
        results = self._run_filter(tmp_path, profile_ids=["bi_analytics"])
        ids = [r["id"] for r in results]
        assert "proj-medium" in ids
        assert "proj-high" not in ids


# ═══════════════════════════════════════════════════════════════════════════════
# T66: GET /api/v1/dashboard/stats — LLM-Stats
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardStatsLLM:
    """T66: LLM-Stats-Felder in /api/v1/dashboard/stats."""

    def test_t66_stats_contain_llm_fields(self, tmp_path):
        """T66: Stats-Antwort enthält llm_evaluated, llm_high_priority etc."""
        from server_enhanced import app
        proj_dir = tmp_path / "projects"
        proj_dir.mkdir()
        _write_project_md(proj_dir, "eval-ok", llm_priority="high", evaluation_status="ok",
                          llm_evaluation={"best_profile": "crm", "best_score": 80, "evaluations": [], "cost_usd": 0.003})
        _write_project_md(proj_dir, "eval-failed", evaluation_status="failed")

        orig_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with app.test_client() as client:
                resp = client.get("/api/v1/dashboard/stats")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "llm_evaluated" in data, "llm_evaluated fehlt in Stats"
            assert "llm_high_priority" in data, "llm_high_priority fehlt in Stats"
            assert "llm_pending" in data, "llm_pending fehlt in Stats"
            assert "llm_failed" in data, "llm_failed fehlt in Stats"
            assert data["llm_evaluated"] >= 1
            assert data["llm_high_priority"] >= 1
            assert data["llm_failed"] >= 1
        finally:
            os.chdir(orig_cwd)


# ═══════════════════════════════════════════════════════════════════════════════
# T67–T68: GET /api/v1/llm/costs
# ═══════════════════════════════════════════════════════════════════════════════

class TestLLMCostsEndpoint:
    """Tests für GET /api/v1/llm/costs."""

    def _make_log_entry(self, cost_usd: float, entry_type: str = "actual",
                        cache_hit: bool = False, ts: str = None) -> dict:
        if ts is None:
            ts = datetime.now(timezone.utc).isoformat()
        return {
            "type": entry_type,
            "cost_usd": cost_usd,
            "cache_hit": cache_hit,
            "timestamp": ts,
        }

    def test_t67_costs_returns_required_fields(self, tmp_path):
        """T67: /api/v1/llm/costs gibt cost_today_usd, calls_today, cache_hits zurück."""
        from server_enhanced import app
        log_path = tmp_path / "llm_cost_log.jsonl"
        today = date.today().isoformat()
        _write_cost_log(log_path, [
            self._make_log_entry(0.002, ts=f"{today}T10:00:00+00:00"),
            self._make_log_entry(0.003, ts=f"{today}T11:00:00+00:00", cache_hit=True),
        ])

        orig_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with app.test_client() as client:
                resp = client.get("/api/v1/llm/costs")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "cost_today_usd" in data
            assert "cost_month_usd" in data
            assert "calls_today" in data
            assert "cache_hits" in data
            assert abs(data["cost_today_usd"] - 0.005) < 0.0001
            assert data["calls_today"] == 2
            assert data["cache_hits"] == 1
        finally:
            os.chdir(orig_cwd)

    def test_t68_costs_ignores_reserved_entries(self, tmp_path):
        """T68: type='reserved' Einträge werden NICHT in die Kosten eingerechnet."""
        from server_enhanced import app
        log_path = tmp_path / "llm_cost_log.jsonl"
        today = date.today().isoformat()
        _write_cost_log(log_path, [
            self._make_log_entry(0.001, entry_type="actual", ts=f"{today}T10:00:00+00:00"),
            self._make_log_entry(0.500, entry_type="reserved", ts=f"{today}T10:01:00+00:00"),
        ])

        orig_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with app.test_client() as client:
                resp = client.get("/api/v1/llm/costs")
            data = resp.get_json()
            assert abs(data["cost_today_usd"] - 0.001) < 0.0001, (
                f"Reservierter Eintrag wurde mitgezählt: {data['cost_today_usd']}"
            )
            assert data["calls_today"] == 1
        finally:
            os.chdir(orig_cwd)

    def test_costs_empty_log(self, tmp_path):
        """Leere Log-Datei → Nullwerte, kein Fehler."""
        from server_enhanced import app
        log_path = tmp_path / "llm_cost_log.jsonl"
        log_path.write_text("", encoding="utf-8")

        orig_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with app.test_client() as client:
                resp = client.get("/api/v1/llm/costs")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["cost_today_usd"] == 0.0
            assert data["calls_today"] == 0
        finally:
            os.chdir(orig_cwd)

    def test_costs_missing_log(self, tmp_path):
        """Fehlende Log-Datei → 200 mit Nullwerten (kein 500)."""
        from server_enhanced import app
        orig_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with app.test_client() as client:
                resp = client.get("/api/v1/llm/costs")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["cost_today_usd"] == 0.0
        finally:
            os.chdir(orig_cwd)


# ═══════════════════════════════════════════════════════════════════════════════
# T69–T70: POST /api/v1/projects/{id}/undo_state
# ═══════════════════════════════════════════════════════════════════════════════

class TestUndoStateEndpoint:
    """Tests für POST /api/v1/projects/{id}/undo_state."""

    def _make_project_with_history(self, tmp_path, states: list) -> str:
        """Legt Projekt mit gegebener State-History an."""
        import yaml  # type: ignore
        project_id = "undo-test-001"
        history = [
            {"state": s, "timestamp": f"2026-10-09T{10+i:02d}:00:00"}
            for i, s in enumerate(states)
        ]
        frontmatter = {
            "title": "Undo-Testprojekt",
            "state": states[-1],
            "state_history": history,
            "search_groups": [],
        }
        content = "---\n" + yaml.dump(frontmatter, allow_unicode=True) + "---\n\nBeschreibung."
        (tmp_path / "projects").mkdir(exist_ok=True)
        (tmp_path / "projects" / f"{project_id}.md").write_text(content, encoding="utf-8")
        return project_id

    def test_t69_undo_reverts_to_previous_state(self, tmp_path):
        """T69: undo_state kehrt den letzten Zustand zurück."""
        from server_enhanced import app
        project_id = self._make_project_with_history(tmp_path, ["scraped", "accepted"])

        orig_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with app.test_client() as client:
                resp = client.post(
                    f"/api/v1/projects/{project_id}/undo_state",
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    json={}
                )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["new_state"] == "scraped", f"Erwartet 'scraped', got {data.get('new_state')}"
            assert data["previous_state"] == "accepted"
        finally:
            os.chdir(orig_cwd)

    def test_t70_undo_fails_with_single_history_entry(self, tmp_path):
        """T70: undo_state schlägt fehl wenn State-History nur 1 Eintrag hat."""
        from server_enhanced import app
        project_id = self._make_project_with_history(tmp_path, ["scraped"])

        orig_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with app.test_client() as client:
                resp = client.post(
                    f"/api/v1/projects/{project_id}/undo_state",
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    json={}
                )
            assert resp.status_code == 400, (
                f"Erwartet 400 bei zu kurzer History, got {resp.status_code}"
            )
        finally:
            os.chdir(orig_cwd)

    def test_undo_nonexistent_project_returns_404(self, tmp_path):
        """Nicht existierendes Projekt → 404."""
        from server_enhanced import app
        (tmp_path / "projects").mkdir(exist_ok=True)

        orig_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with app.test_client() as client:
                resp = client.post(
                    "/api/v1/projects/does-not-exist/undo_state",
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    json={}
                )
            assert resp.status_code == 404
        finally:
            os.chdir(orig_cwd)
