"""
Tests für Phase 2: Suchgruppen-Konfiguration und -Dispatching.

Geprüft:
- search_group_config.py: Laden, Validierung, unabhängige Konfigurationen
- search_group_dispatcher.py: Erstellen, Mergen, Überspringen
- email_agent.py: process_rss_entries mit search_group_id
- Regressionstests: Existierender Ingestion-Pfad (ohne search_group_id) weiterhin funktionsfähig
- Sicherheitstests: Verschiedene URLs → niemals zusammengeführt

Alle Tests verwenden ausschließlich Fixtures und simulierte Feeds.
Keine echten Portale oder Mail-Server werden abgefragt.
"""

from __future__ import annotations

import os
import tempfile
import textwrap
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
import yaml

from dedupe_service import DedupeService
from project_record import ProjectRecord, ProjectSource, _make_project_id
from search_group_config import (
    EmailChannelConfig,
    ProviderChannels,
    RSSChannelConfig,
    SearchGroupConfig,
    SearchGroupConfigError,
    SearchGroupSchedule,
    load_search_groups,
)
from search_group_dispatcher import DispatchResult, SearchGroupDispatcher
from state_manager import ProjectStateManager


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures und Hilfsfunktionen
# ──────────────────────────────────────────────────────────────────────────────

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture_config() -> Dict[str, Any]:
    """Lädt die Zwei-Gruppen-Testkonfiguration aus der Fixture-Datei."""
    config_path = os.path.join(FIXTURE_DIR, "search_groups_config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _make_minimal_markdown(title: str = "Test Projekt", url: str = "https://example.com/1") -> str:
    """Erzeugt minimalen Markdown-Inhalt für einen Dispatcher-Test."""
    return textwrap.dedent(f"""\
        ---
        title: {title}
        provider_url: {url}
        state: scraped
        ---

        ## Projektbeschreibung

        Testprojekt für automatisierte Tests.
    """)


@pytest.fixture
def tmp_projects(tmp_path):
    """Temporäres Projektverzeichnis."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    return str(projects_dir)


@pytest.fixture
def dedupe(tmp_projects):
    """DedupeService mit temporärem Verzeichnis."""
    return DedupeService(tmp_projects)


@pytest.fixture
def dispatcher(tmp_projects, dedupe):
    """SearchGroupDispatcher mit injizierten Diensten."""
    return SearchGroupDispatcher(tmp_projects, dedupe_service=dedupe)


@pytest.fixture
def fixture_config():
    """Vollständige Zwei-Gruppen-Fixture-Konfiguration."""
    return _load_fixture_config()


# ──────────────────────────────────────────────────────────────────────────────
# 1. Konfigurationsmodell: load_search_groups
# ──────────────────────────────────────────────────────────────────────────────

class TestLoadSearchGroups:
    """Tests für search_group_config.load_search_groups()."""

    def test_loads_two_groups(self, fixture_config):
        """Beide Suchgruppen werden aus der Fixture geladen."""
        groups = load_search_groups(fixture_config)
        assert "automation_bi" in groups
        assert "infra_security" in groups

    def test_groups_sorted_by_priority(self, fixture_config):
        """Suchgruppen sind nach Priorität (aufsteigend) sortiert."""
        groups = load_search_groups(fixture_config)
        priorities = [g.priority for g in groups.values()]
        assert priorities == sorted(priorities)

    def test_automation_bi_priority(self, fixture_config):
        """automation_bi hat Priorität 1."""
        groups = load_search_groups(fixture_config)
        assert groups["automation_bi"].priority == 1

    def test_infra_security_priority(self, fixture_config):
        """infra_security hat Priorität 2."""
        groups = load_search_groups(fixture_config)
        assert groups["infra_security"].priority == 2

    def test_groups_are_enabled(self, fixture_config):
        """Beide Gruppen sind aktiviert."""
        groups = load_search_groups(fixture_config)
        assert groups["automation_bi"].enabled is True
        assert groups["infra_security"].enabled is True

    def test_empty_config_returns_empty_dict(self):
        """Leere Konfiguration ohne search_groups gibt leeres Dict zurück."""
        groups = load_search_groups({})
        assert groups == {}

    def test_disabled_group_is_still_loaded(self):
        """Eine deaktivierte Gruppe wird geladen (Filterung liegt beim Aufrufer)."""
        config = {
            "search_groups": {
                "disabled_group": {
                    "display_name": "Deactivated",
                    "priority": 5,
                    "enabled": False,
                    "providers": {
                        "freelancermap": {
                            "channels": {
                                "rss": {
                                    "feed_urls": ["https://example.com/rss"],
                                }
                            }
                        }
                    },
                }
            }
        }
        groups = load_search_groups(config)
        assert "disabled_group" in groups
        assert groups["disabled_group"].enabled is False

    def test_invalid_config_raises_error(self):
        """Fehlerhafte Konfiguration (leere feed_urls) wirft SearchGroupConfigError."""
        config = {
            "search_groups": {
                "bad_group": {
                    "display_name": "Bad Group",
                    "priority": 1,
                    "providers": {
                        "freelancermap": {
                            "channels": {
                                "rss": {
                                    "feed_urls": [],  # leer → ungültig
                                }
                            }
                        }
                    },
                }
            }
        }
        with pytest.raises(SearchGroupConfigError):
            load_search_groups(config)

    def test_group_without_providers_raises_error(self):
        """Gruppe ohne Provider wirft SearchGroupConfigError."""
        config = {
            "search_groups": {
                "no_providers": {
                    "display_name": "No Providers",
                    "priority": 1,
                    "providers": {},
                }
            }
        }
        with pytest.raises(SearchGroupConfigError):
            load_search_groups(config)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Unabhängige Konfigurationen
# ──────────────────────────────────────────────────────────────────────────────

class TestIndependentGroupConfigs:
    """Jede Gruppe hat eigene Provider-Konfiguration."""

    def test_independent_feed_urls(self, fixture_config):
        """automation_bi und infra_security haben verschiedene Feed-URLs."""
        groups = load_search_groups(fixture_config)
        bi_urls = groups["automation_bi"].providers["freelancermap"].rss.feed_urls
        infra_urls = groups["infra_security"].providers["freelancermap"].rss.feed_urls
        assert bi_urls != infra_urls

    def test_independent_keywords(self, fixture_config):
        """Jede Gruppe hat eigene Keywords."""
        groups = load_search_groups(fixture_config)
        bi_kw = set(groups["automation_bi"].keywords)
        infra_kw = set(groups["infra_security"].keywords)
        # Keine vollständige Überschneidung (Gruppen sind inhaltlich verschieden)
        assert bi_kw != infra_kw

    def test_independent_schedules(self, fixture_config):
        """Jede Gruppe hat eigene Zeitpläne."""
        groups = load_search_groups(fixture_config)
        bi_sched = groups["automation_bi"].schedule
        infra_sched = groups["infra_security"].schedule
        assert bi_sched.rss_interval_minutes != infra_sched.rss_interval_minutes
        assert bi_sched.email_interval_minutes != infra_sched.email_interval_minutes

    def test_automation_bi_has_email_config(self, fixture_config):
        """automation_bi hat E-Mail-Konfiguration für freelancermap."""
        groups = load_search_groups(fixture_config)
        email_cfg = groups["automation_bi"].providers["freelancermap"].email
        assert email_cfg is not None
        assert len(email_cfg.senders) > 0

    def test_get_rss_providers(self, fixture_config):
        """get_rss_providers() gibt die richtigen Provider zurück."""
        groups = load_search_groups(fixture_config)
        rss_providers = groups["automation_bi"].get_rss_providers()
        assert "freelancermap" in rss_providers
        assert "gulp" in rss_providers

    def test_get_email_providers(self, fixture_config):
        """get_email_providers() gibt nur Provider mit E-Mail-Kanal zurück."""
        groups = load_search_groups(fixture_config)
        email_providers = groups["automation_bi"].get_email_providers()
        assert "freelancermap" in email_providers
        # gulp hat in der Fixture keinen E-Mail-Kanal
        assert "gulp" not in email_providers


# ──────────────────────────────────────────────────────────────────────────────
# 3. Dispatcher: Neues Projekt anlegen
# ──────────────────────────────────────────────────────────────────────────────

class TestDispatcherCreate:
    """Tests für SearchGroupDispatcher.dispatch() – Neuanlage."""

    def test_new_project_creates_file(self, dispatcher, tmp_projects):
        """Neues Projekt → ACTION_CREATED und Datei vorhanden."""
        result = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url="https://www.freelancermap.de/projekt/11111",
            title="Neues Testprojekt",
            markdown_content=_make_minimal_markdown(
                "Neues Testprojekt",
                "https://www.freelancermap.de/projekt/11111",
            ),
        )
        assert result.action == DispatchResult.ACTION_CREATED
        assert result.filepath is not None
        assert os.path.exists(result.filepath)

    def test_new_project_has_correct_search_group(self, dispatcher, tmp_projects):
        """Neuangelegte Datei enthält die richtige search_group."""
        url = "https://www.freelancermap.de/projekt/22222"
        result = dispatcher.dispatch(
            search_group_id="infra_security",
            provider_id="freelancermap",
            provider_url=url,
            title="Infra Projekt",
            markdown_content=_make_minimal_markdown("Infra Projekt", url),
        )
        state_manager = ProjectStateManager(tmp_projects)
        record, _ = state_manager.read_project_record(result.filepath)
        assert record is not None
        assert "infra_security" in record.search_groups

    def test_new_project_has_correct_project_id(self, dispatcher, tmp_projects):
        """Die project_id im Record stimmt mit der berechneten ID überein."""
        url = "https://www.freelancermap.de/projekt/33333"
        expected_id = _make_project_id(provider_url=url, title=None)
        result = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url,
            title="ID-Test Projekt",
            markdown_content=_make_minimal_markdown("ID-Test Projekt", url),
        )
        state_manager = ProjectStateManager(tmp_projects)
        record, _ = state_manager.read_project_record(result.filepath)
        assert record.project_id == expected_id

    def test_new_project_has_source(self, dispatcher, tmp_projects):
        """Neuangelegte Datei enthält die korrekte Source."""
        url = "https://www.freelancermap.de/projekt/44444"
        result = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url,
            title="Source-Test Projekt",
            markdown_content=_make_minimal_markdown("Source-Test Projekt", url),
        )
        state_manager = ProjectStateManager(tmp_projects)
        record, _ = state_manager.read_project_record(result.filepath)
        source_urls = [s.url for s in record.sources]
        assert url in source_urls

    def test_project_registered_in_dedupe(self, dispatcher, dedupe):
        """Nach dem Dispatch ist das Projekt im DedupeService registriert."""
        url = "https://www.freelancermap.de/projekt/55555"
        project_id = _make_project_id(provider_url=url, title=None)
        dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url,
            title="Dedupe-Test Projekt",
            markdown_content=_make_minimal_markdown("Dedupe-Test Projekt", url),
        )
        assert dedupe.already_processed_by_id(project_id)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Dispatcher: Duplikat von anderer Suchgruppe → Merge
# ──────────────────────────────────────────────────────────────────────────────

class TestDispatcherMerge:
    """Tests für Duplikat-Behandlung: zweite Suchgruppe wird gemergt."""

    def test_duplicate_from_second_group_merges(self, dispatcher, tmp_projects):
        """Dieselbe URL durch zwei verschiedene Gruppen → ACTION_MERGED, eine Datei."""
        url = "https://www.freelancermap.de/projekt/99999"
        content = _make_minimal_markdown("Cross-Group Projekt", url)

        result1 = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url,
            title="Cross-Group Projekt",
            markdown_content=content,
        )
        result2 = dispatcher.dispatch(
            search_group_id="infra_security",
            provider_id="freelancermap",
            provider_url=url,
            title="Cross-Group Projekt",
            markdown_content=content,
        )

        assert result1.action == DispatchResult.ACTION_CREATED
        assert result2.action == DispatchResult.ACTION_MERGED

    def test_duplicate_creates_no_second_file(self, dispatcher, tmp_projects):
        """Nach Merge darf keine zweite Projektdatei entstehen."""
        url = "https://www.freelancermap.de/projekt/88888"
        content = _make_minimal_markdown("Kein Duplikat", url)

        dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url,
            title="Kein Duplikat",
            markdown_content=content,
        )

        files_before = set(os.listdir(tmp_projects))

        dispatcher.dispatch(
            search_group_id="infra_security",
            provider_id="freelancermap",
            provider_url=url,
            title="Kein Duplikat",
            markdown_content=content,
        )

        files_after = set(os.listdir(tmp_projects))
        # Keine neuen .md-Dateien
        new_md_files = [f for f in (files_after - files_before) if f.endswith(".md")]
        assert len(new_md_files) == 0

    def test_merged_project_has_both_groups(self, dispatcher, tmp_projects):
        """Nach Merge enthält die Datei beide Suchgruppen."""
        url = "https://www.freelancermap.de/projekt/77777"
        content = _make_minimal_markdown("Beide Gruppen", url)

        result1 = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url,
            title="Beide Gruppen",
            markdown_content=content,
        )
        dispatcher.dispatch(
            search_group_id="infra_security",
            provider_id="freelancermap",
            provider_url=url,
            title="Beide Gruppen",
            markdown_content=content,
        )

        state_manager = ProjectStateManager(tmp_projects)
        record, _ = state_manager.read_project_record(result1.filepath)
        assert "automation_bi" in record.search_groups
        assert "infra_security" in record.search_groups

    def test_merged_filepath_is_original(self, dispatcher, tmp_projects):
        """Nach Merge zeigt result.filepath auf die ursprüngliche Datei."""
        url = "https://www.freelancermap.de/projekt/66666"
        content = _make_minimal_markdown("Filepath Check", url)

        result1 = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url,
            title="Filepath Check",
            markdown_content=content,
        )
        result2 = dispatcher.dispatch(
            search_group_id="infra_security",
            provider_id="freelancermap",
            provider_url=url,
            title="Filepath Check",
            markdown_content=content,
        )

        assert result2.filepath == result1.filepath


# ──────────────────────────────────────────────────────────────────────────────
# 5. Dispatcher: Duplikat derselben Suchgruppe → Skipped
# ──────────────────────────────────────────────────────────────────────────────

class TestDispatcherSkip:
    """Tests für Duplikat durch dieselbe Suchgruppe → ACTION_SKIPPED."""

    def test_same_group_second_dispatch_skipped(self, dispatcher):
        """Dieselbe URL zweimal durch dieselbe Gruppe → ACTION_SKIPPED beim zweiten Mal."""
        url = "https://www.freelancermap.de/projekt/11112"
        content = _make_minimal_markdown("Skip Test", url)

        dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url,
            title="Skip Test",
            markdown_content=content,
        )
        result2 = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url,
            title="Skip Test",
            markdown_content=content,
        )

        assert result2.action == DispatchResult.ACTION_SKIPPED


# ──────────────────────────────────────────────────────────────────────────────
# 6. Sicherheitstests: Verschiedene Projekte niemals zusammenführen
# ──────────────────────────────────────────────────────────────────────────────

class TestDispatcherSafety:
    """Sicherheitsgarantie: verschiedene URLs → immer verschiedene project_ids."""

    def test_different_urls_different_project_ids(self):
        """Zwei verschiedene URLs erzeugen immer verschiedene project_ids."""
        id1 = _make_project_id(provider_url="https://www.freelancermap.de/projekt/111", title=None)
        id2 = _make_project_id(provider_url="https://www.freelancermap.de/projekt/222", title=None)
        assert id1 != id2

    def test_different_urls_create_separate_files(self, dispatcher, tmp_projects):
        """Zwei verschiedene URLs erzeugen immer separate Projektdateien."""
        url1 = "https://www.freelancermap.de/projekt/10001"
        url2 = "https://www.freelancermap.de/projekt/10002"

        result1 = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url1,
            title="Projekt A",
            markdown_content=_make_minimal_markdown("Projekt A", url1),
        )
        result2 = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url2,
            title="Projekt B",
            markdown_content=_make_minimal_markdown("Projekt B", url2),
        )

        assert result1.action == DispatchResult.ACTION_CREATED
        assert result2.action == DispatchResult.ACTION_CREATED
        assert result1.filepath != result2.filepath

    def test_different_urls_both_groups_never_merged(self, dispatcher, tmp_projects):
        """Verschiedene Projekte mit kreuzweise Suchgruppen-Kombinationen → niemals gemergt."""
        url_a = "https://www.freelancermap.de/projekt/20001"
        url_b = "https://www.freelancermap.de/projekt/20002"

        # Gruppe 1 findet Projekt A
        r1 = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url_a,
            title="Projekt A",
            markdown_content=_make_minimal_markdown("Projekt A", url_a),
        )
        # Gruppe 2 findet Projekt B (andere URL!)
        r2 = dispatcher.dispatch(
            search_group_id="infra_security",
            provider_id="freelancermap",
            provider_url=url_b,
            title="Projekt B",
            markdown_content=_make_minimal_markdown("Projekt B", url_b),
        )

        assert r1.action == DispatchResult.ACTION_CREATED
        assert r2.action == DispatchResult.ACTION_CREATED
        assert r1.project_id != r2.project_id

    def test_title_based_id_is_stable(self):
        """Titel-basierte project_id ist deterministisch (kein URL-Fallback)."""
        id_a = _make_project_id(provider_url=None, title="DevOps Engineer gesucht")
        id_b = _make_project_id(provider_url=None, title="DevOps Engineer gesucht")
        assert id_a == id_b

    def test_different_titles_different_ids(self):
        """Verschiedene Titel → immer verschiedene IDs."""
        id_a = _make_project_id(provider_url=None, title="Python Developer")
        id_b = _make_project_id(provider_url=None, title="Java Developer")
        assert id_a != id_b


# ──────────────────────────────────────────────────────────────────────────────
# 7. Dispatcher: project_id wird korrekt im Record gesetzt
# ──────────────────────────────────────────────────────────────────────────────

class TestDispatcherProjectId:
    """Die project_id wird korrekt berechnet und persistiert."""

    def test_url_based_project_id_prefix(self, dispatcher, tmp_projects):
        """URL-basierte project_id beginnt mit 'url-'."""
        url = "https://www.freelancermap.de/projekt/30001"
        result = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url,
            title="ID-Prefix Test",
            markdown_content=_make_minimal_markdown("ID-Prefix Test", url),
        )
        assert result.project_id.startswith("url-")

    def test_title_based_project_id_prefix(self, dispatcher, tmp_projects):
        """Titel-basierte project_id beginnt mit 'ttl-' wenn keine URL vorhanden."""
        result = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url="",
            title="Nur Titel Projekt",
            markdown_content=_make_minimal_markdown("Nur Titel Projekt", ""),
        )
        assert result.project_id.startswith("ttl-")

    def test_project_id_stored_in_record(self, dispatcher, tmp_projects):
        """project_id wird im Record gespeichert."""
        url = "https://www.freelancermap.de/projekt/30002"
        result = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url,
            title="Record ID Test",
            markdown_content=_make_minimal_markdown("Record ID Test", url),
        )
        sm = ProjectStateManager(tmp_projects)
        record, _ = sm.read_project_record(result.filepath)
        assert record.project_id == result.project_id


# ──────────────────────────────────────────────────────────────────────────────
# 8. email_agent.py: process_rss_entries mit search_group_id (Phase-2-Pfad)
# ──────────────────────────────────────────────────────────────────────────────

class TestEmailAgentRSSWithSearchGroup:
    """process_rss_entries() mit search_group_id nutzt den Dispatcher."""

    def _make_rss_entries(self, urls: List[str]) -> List[Dict]:
        return [
            {
                "title": f"RSS Eintrag {i}",
                "link": url,
                "published": None,
                "summary": "Testbeschreibung",
                "id": url,
            }
            for i, url in enumerate(urls, 1)
        ]

    def _make_provider_config(self, provider_id: str = "freelancermap") -> Dict:
        return {
            "provider_id": provider_id,
            "feed_urls": ["https://example.com/rss"],
            "limit": 10,
            "max_age_days": 7,
        }

    def _make_mock_adapter(self, title: str = "Mock Projekt"):
        adapter = MagicMock()
        adapter.get_provider_name.return_value = "FreelancerMap"
        adapter.parse.return_value = {"schema": {"title": title}}
        return adapter

    def _make_mock_renderer(self):
        renderer = MagicMock()
        renderer.render.return_value = "---\ntitle: Test\n---\n\nInhalt"
        return renderer

    def test_new_project_created_via_dispatcher(self, tmp_projects):
        """Neues Projekt über search_group_id → SearchGroupDispatcher wird genutzt."""
        from email_agent import EmailAgent

        config = {"channels": {}, "providers": {}}
        agent = EmailAgent(config)

        url = "https://www.freelancermap.de/projekt/40001"
        entries = self._make_rss_entries([url])
        provider_config = self._make_provider_config()

        with (
            patch.object(agent, "load_adapter", return_value=self._make_mock_adapter()),
            patch("email_agent.MarkdownRenderer", return_value=self._make_mock_renderer()),
        ):
            result = agent.process_rss_entries(
                entries, provider_config, tmp_projects, search_group_id="automation_bi"
            )

        assert result["projects_saved"] == 1
        assert result["urls_skipped_dedupe"] == 0

    def test_duplicate_from_second_group_counts_as_skipped(self, tmp_projects):
        """Duplikat einer zweiten Gruppe wird als skipped gezählt (kein zweites Projekt)."""
        from email_agent import EmailAgent

        config = {"channels": {}, "providers": {}}
        agent = EmailAgent(config)

        url = "https://www.freelancermap.de/projekt/40002"
        entries = self._make_rss_entries([url])
        provider_config = self._make_provider_config()

        with (
            patch.object(agent, "load_adapter", return_value=self._make_mock_adapter()),
            patch("email_agent.MarkdownRenderer", return_value=self._make_mock_renderer()),
        ):
            # Erste Gruppe legt Projekt an
            result1 = agent.process_rss_entries(
                entries, provider_config, tmp_projects, search_group_id="automation_bi"
            )
            # Zweite Gruppe findet dasselbe Projekt → kein neues Projekt
            result2 = agent.process_rss_entries(
                entries, provider_config, tmp_projects, search_group_id="infra_security"
            )

        assert result1["projects_saved"] == 1
        # Merge: zweite Gruppe ergänzt, zählt als skipped (kein neues Projekt)
        assert result2["projects_saved"] == 0
        assert result2["urls_skipped_dedupe"] == 1

        # Nur eine .md-Datei vorhanden
        md_files = [f for f in os.listdir(tmp_projects) if f.endswith(".md")]
        assert len(md_files) == 1

    def test_same_group_second_run_skipped(self, tmp_projects):
        """Zweite Verarbeitung derselben URL durch dieselbe Gruppe → skipped."""
        from email_agent import EmailAgent

        config = {"channels": {}, "providers": {}}
        agent = EmailAgent(config)

        url = "https://www.freelancermap.de/projekt/40003"
        entries = self._make_rss_entries([url])
        provider_config = self._make_provider_config()

        with (
            patch.object(agent, "load_adapter", return_value=self._make_mock_adapter()),
            patch("email_agent.MarkdownRenderer", return_value=self._make_mock_renderer()),
        ):
            result1 = agent.process_rss_entries(
                entries, provider_config, tmp_projects, search_group_id="automation_bi"
            )
            result2 = agent.process_rss_entries(
                entries, provider_config, tmp_projects, search_group_id="automation_bi"
            )

        assert result1["projects_saved"] == 1
        assert result2["projects_saved"] == 0
        assert result2["urls_skipped_dedupe"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# 9. Regression: Bisheriger Pfad ohne search_group_id weiterhin funktionsfähig
# ──────────────────────────────────────────────────────────────────────────────

class TestRegressionLegacyPath:
    """Der bestehende Ingestion-Pfad (ohne search_group_id) funktioniert weiterhin."""

    def _make_mock_adapter(self, title: str = "Legacy Projekt"):
        adapter = MagicMock()
        adapter.get_provider_name.return_value = "FreelancerMap"
        adapter.parse.return_value = {"schema": {"title": title}}
        return adapter

    def _make_mock_renderer(self):
        renderer = MagicMock()
        renderer.render.return_value = "---\ntitle: Legacy\n---\n\nInhalt"
        return renderer

    def test_legacy_rss_path_without_search_group_id(self, tmp_projects):
        """process_rss_entries ohne search_group_id nutzt den Legacy-Pfad."""
        from email_agent import EmailAgent

        config = {"channels": {}, "providers": {}}
        agent = EmailAgent(config)

        entries = [
            {
                "title": "Legacy RSS Eintrag",
                "link": "https://www.freelancermap.de/projekt/50001",
                "published": None,
                "summary": "",
                "id": "https://www.freelancermap.de/projekt/50001",
            }
        ]
        provider_config = {
            "provider_id": "freelancermap",
            "feed_urls": ["https://example.com/rss"],
        }

        with (
            patch.object(agent, "load_adapter", return_value=self._make_mock_adapter()),
            patch("email_agent.MarkdownRenderer", return_value=self._make_mock_renderer()),
        ):
            result = agent.process_rss_entries(entries, provider_config, tmp_projects)

        assert result["projects_saved"] == 1
        assert result["urls_skipped_dedupe"] == 0

    def test_legacy_dedup_still_works(self, tmp_projects):
        """Legacy-Pfad: doppelte URL wird per mark_processed/already_processed gefiltert."""
        from email_agent import EmailAgent

        config = {"channels": {}, "providers": {}}
        agent = EmailAgent(config)

        url = "https://www.freelancermap.de/projekt/50002"
        entries = [
            {"title": "Legacy Dup", "link": url, "published": None, "summary": "", "id": url}
        ]
        provider_config = {"provider_id": "freelancermap", "feed_urls": []}

        with (
            patch.object(agent, "load_adapter", return_value=self._make_mock_adapter()),
            patch("email_agent.MarkdownRenderer", return_value=self._make_mock_renderer()),
        ):
            result1 = agent.process_rss_entries(entries, provider_config, tmp_projects)
            result2 = agent.process_rss_entries(entries, provider_config, tmp_projects)

        assert result1["projects_saved"] == 1
        assert result2["projects_saved"] == 0
        assert result2["urls_skipped_dedupe"] == 1

    def test_run_rss_ingestion_dry_run_unchanged(self, tmp_projects):
        """run_rss_ingestion(dry_run=True) schreibt keine Dateien."""
        from email_agent import EmailAgent

        config = {
            "channels": {},
            "providers": {
                "freelancermap": {
                    "enabled": True,
                    "channels": {
                        "rss": {
                            "feed_urls": ["https://example.com/rss"],
                            "limit": 5,
                        }
                    },
                }
            },
        }
        agent = EmailAgent(config)
        result = agent.run_rss_ingestion("freelancermap", tmp_projects, dry_run=True)
        assert result["dry_run"] is True
        assert result["projects_saved"] == 0
        # Kein .md-File im Verzeichnis
        md_files = [f for f in os.listdir(tmp_projects) if f.endswith(".md")]
        assert len(md_files) == 0


# ──────────────────────────────────────────────────────────────────────────────
# 10. DispatchResult-Konstanten
# ──────────────────────────────────────────────────────────────────────────────

class TestDispatchResultConstants:
    """Konstanten von DispatchResult haben die erwarteten Werte."""

    def test_action_created(self):
        assert DispatchResult.ACTION_CREATED == "created"

    def test_action_merged(self):
        assert DispatchResult.ACTION_MERGED == "merged"

    def test_action_skipped(self):
        assert DispatchResult.ACTION_SKIPPED == "skipped"

    def test_dispatch_result_repr(self, dispatcher, tmp_projects):
        url = "https://www.freelancermap.de/projekt/60001"
        result = dispatcher.dispatch(
            search_group_id="automation_bi",
            provider_id="freelancermap",
            provider_url=url,
            title="Repr Test",
            markdown_content=_make_minimal_markdown("Repr Test", url),
        )
        r = repr(result)
        assert "created" in r
        assert "url-" in r


# ──────────────────────────────────────────────────────────────────────────────
# 11. Modulebene: run_rss_ingestion_for_search_groups
# ──────────────────────────────────────────────────────────────────────────────

class TestModuleLevelSearchGroupIngestion:
    """Modulebene: run_rss_ingestion_for_search_groups() verarbeitet beide Gruppen."""

    def test_function_exists(self):
        """run_rss_ingestion_for_search_groups ist importierbar."""
        from email_agent import run_rss_ingestion_for_search_groups
        assert callable(run_rss_ingestion_for_search_groups)

    def test_dry_run_returns_summary(self, fixture_config, tmp_projects):
        """Dry-Run gibt ein Summary-Dict zurück ohne Fehler."""
        from email_agent import run_rss_ingestion_for_search_groups

        result = run_rss_ingestion_for_search_groups(
            fixture_config, output_dir=tmp_projects, dry_run=True
        )
        assert "group_summaries" in result
        assert "automation_bi" in result["group_summaries"]
        assert "infra_security" in result["group_summaries"]
        assert result["total_errors"] == 0

    def test_both_groups_processed_in_dry_run(self, fixture_config, tmp_projects):
        """Beide Gruppen werden im Dry-Run verarbeitet."""
        from email_agent import run_rss_ingestion_for_search_groups

        result = run_rss_ingestion_for_search_groups(
            fixture_config, output_dir=tmp_projects, dry_run=True
        )
        assert result["groups_processed"] == 2

    def test_group_filter_works(self, fixture_config, tmp_projects):
        """Mit group_ids-Filter wird nur die angegebene Gruppe verarbeitet."""
        from email_agent import run_rss_ingestion_for_search_groups

        result = run_rss_ingestion_for_search_groups(
            fixture_config,
            output_dir=tmp_projects,
            dry_run=True,
            group_ids=["automation_bi"],
        )
        assert result["groups_processed"] == 1
        assert "automation_bi" in result["group_summaries"]
        assert "infra_security" not in result["group_summaries"]

    def test_full_workflow_function_exists(self):
        """run_full_workflow_for_search_groups ist importierbar."""
        from email_agent import run_full_workflow_for_search_groups
        assert callable(run_full_workflow_for_search_groups)


# ---------------------------------------------------------------------------
# Regression: No duplicate method definitions in EmailAgent
# ---------------------------------------------------------------------------

class TestNoDuplicateMethodDefinitions:
    """Regressionstest: EmailAgent darf keine doppelten Methodendefinitionen enthalten."""

    def test_no_duplicate_method_definitions(self):
        """
        AST-basierter Check: Jede Methode der EmailAgent-Klasse darf nur einmal
        definiert sein. Verhindert die stille Überschreibung durch Python.
        """
        import ast
        import pathlib

        source = pathlib.Path(__file__).parent.parent / "email_agent.py"
        tree = ast.parse(source.read_text())

        duplicates = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "EmailAgent":
                seen: dict[str, int] = {}
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        name = item.name
                        if name in seen:
                            duplicates.setdefault(name, []).append(
                                (seen[name], item.lineno)
                            )
                        else:
                            seen[name] = item.lineno
                break  # nur eine EmailAgent-Klasse erwartet

        assert duplicates == {}, (
            "Doppelte Methodendefinitionen in EmailAgent gefunden:\n"
            + "\n".join(
                f"  {name}: Zeilen {lines}"
                for name, lines in duplicates.items()
            )
        )

    def test_email_agent_class_found(self):
        """Stellt sicher, dass EmailAgent im AST überhaupt gefunden wird."""
        import ast
        import pathlib

        source = pathlib.Path(__file__).parent.parent / "email_agent.py"
        tree = ast.parse(source.read_text())
        class_names = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
        ]
        assert "EmailAgent" in class_names, "Klasse EmailAgent nicht in email_agent.py gefunden"
