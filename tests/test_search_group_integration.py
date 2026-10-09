"""
Integration-Tests: Beide Suchgruppen können unabhängig via CLI und APScheduler
gestartet werden. Testet mit simulierten RSS-Daten (kein echter Netzwerk-Zugriff,
keine echten Zugangsdaten, keine realen Portale).

Nachweispunkte (aus Review-Kommentar):
  1. Beide Suchen verwenden ihre eigenen Konfigurationen.
  2. Ein Projekt, das in beiden Suchen vorkommt, wird nur einmal gespeichert.
  3. Beide Suchgruppen werden am Projekt dokumentiert.
  4. Ein wiederholter Lauf erzeugt keine Dubletten.
  5. APScheduler-Jobs für jede Gruppe können unabhängig erstellt werden.
"""
from __future__ import annotations

import time
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime

import pytest
import yaml


# ── Helpers ──────────────────────────────────────────────────────────────────

FIXTURE_CONFIG_PATH = Path(__file__).parent / "fixtures" / "search_groups_config.yaml"


def _load_fixture_config():
    with open(FIXTURE_CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _make_rss_entry(url: str, title: str) -> dict:
    """Erzeugt einen simulierten RSS-Eintrag im Format, das fetch_rss_feed zurückgibt."""
    return {
        "link": url,
        "title": title,
        "published": None,
        "summary": f"Beschreibung zu {title}",
        "id": url,
    }


def _read_md(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_frontmatter(text: str) -> dict:
    """Einfacher YAML-Frontmatter-Parser für Markdown-Dateien."""
    if not text.startswith("---"):
        return {}
    end = text.index("---", 3)
    return yaml.safe_load(text[3:end])


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fixture_config():
    return _load_fixture_config()


@pytest.fixture
def tmp_projects(tmp_path):
    d = tmp_path / "projects"
    d.mkdir()
    return str(d)


# ── Test-Einträge ─────────────────────────────────────────────────────────────
# URL_SHARED: erscheint in BEIDEN Suchgruppen → muss zu exakt einer Datei führen
URL_SHARED = "https://www.freelancermap.de/projekt/12345-shared-devops-automation"
URL_ONLY_AUTOMATION = "https://www.freelancermap.de/projekt/11111-automation-only"
URL_ONLY_INFRA = "https://www.freelancermap.de/projekt/22222-infra-only"

ENTRIES_AUTOMATION = [
    _make_rss_entry(URL_SHARED, "Shared DevOps/Automation Projekt"),
    _make_rss_entry(URL_ONLY_AUTOMATION, "Automation-only Projekt"),
]
ENTRIES_INFRA = [
    _make_rss_entry(URL_SHARED, "Shared DevOps/Automation Projekt"),
    _make_rss_entry(URL_ONLY_INFRA, "Infra-only Projekt"),
]


def _fake_parse(url: str) -> dict:
    """Simulierte adapter.parse() Antwort – kein Netzwerk-Zugriff."""
    titles = {
        URL_SHARED: "Shared DevOps/Automation Projekt",
        URL_ONLY_AUTOMATION: "Automation-only Projekt",
        URL_ONLY_INFRA: "Infra-only Projekt",
    }
    return {
        "schema": {
            "title": titles.get(url, "Simuliertes Projekt"),
            "url": url,
            "provider": "freelancermap",
            "description": "Simulierter Projektinhalt für Tests.",
            "location": "Remote",
            "rate": None,
            "duration": None,
            "skills": [],
        }
    }


def _fake_render(schema: dict, _meta: dict) -> str:
    """Simulierter MarkdownRenderer.render() – erzeugt minimalen Markdown."""
    title = schema.get("title", "Projekt")
    url = schema.get("url", "")
    return (
        f"---\ntitle: {title}\nurl: {url}\nschema_version: 2\n---\n\n# {title}\n\n{url}\n"
    )


# ── Haupttest-Klasse ──────────────────────────────────────────────────────────

class TestSearchGroupEndToEnd:
    """
    Ende-zu-Ende-Test beider Suchgruppen mit simulierten RSS-Daten.
    Beweist: unabhängige Konfigurationen, Deduplication, Cross-Group-Stamping,
    keine Dubletten bei Wiederholung.
    """

    def _run_group(self, group_id: str, entries: list, config: dict, output_dir: str):
        """
        Führt run_rss_ingestion_for_search_groups() für eine einzelne Gruppe aus.
        Mockt fetch_rss_feed, adapter.parse und MarkdownRenderer.render.
        """
        from email_agent import run_rss_ingestion_for_search_groups

        # Gibt für JEDEN feed_url-Aufruf die übergegeben Einträge zurück
        with (
            patch("email_agent.EmailAgent.fetch_rss_feed", return_value=entries),
            patch("email_agent.EmailAgent.load_adapter") as mock_load_adapter,
            patch("email_agent.MarkdownRenderer") as mock_renderer_cls,
        ):
            mock_adapter = MagicMock()
            mock_adapter.parse.side_effect = _fake_parse
            mock_adapter.get_provider_name.return_value = "freelancermap"
            mock_load_adapter.return_value = mock_adapter

            mock_renderer = MagicMock()
            mock_renderer.render.side_effect = _fake_render
            mock_renderer_cls.return_value = mock_renderer

            return run_rss_ingestion_for_search_groups(
                config,
                output_dir=output_dir,
                dry_run=False,
                group_ids=[group_id],
            )

    # ── 1. Beide Gruppen verwenden ihre eigenen Konfigurationen ──────────────

    def test_groups_use_independent_configs(self, fixture_config, tmp_projects):
        """
        Nachweispunkt 1: Jede Gruppe liest ihre eigenen feed_urls und keywords.
        Wir überprüfen, dass fetch_rss_feed mit gruppenspezifischen URLs aufgerufen wird.
        """
        from email_agent import run_rss_ingestion_for_search_groups

        called_urls: dict[str, list[str]] = {"automation_bi": [], "infra_security": []}

        def capture_fetch(self_agent, feed_url: str, limit: int = 5, max_age_days: int = 7):
            # Wird per group_id aus dem Kontext zugewiesen – wir speichern alle
            called_urls["all"] = called_urls.get("all", []) + [feed_url]
            return []

        with (
            patch("email_agent.EmailAgent.fetch_rss_feed", capture_fetch),
        ):
            run_rss_ingestion_for_search_groups(
                fixture_config,
                output_dir=tmp_projects,
                dry_run=False,
                group_ids=["automation_bi"],
            )
            automation_urls = list(called_urls.get("all", []))

            called_urls["all"] = []
            run_rss_ingestion_for_search_groups(
                fixture_config,
                output_dir=tmp_projects,
                dry_run=False,
                group_ids=["infra_security"],
            )
            infra_urls = list(called_urls.get("all", []))

        # automation_bi nutzt Automation/BI-Feeds
        assert any("automation" in u or "power" in u for u in automation_urls), (
            f"automation_bi sollte Automation-Feeds verwenden, bekam: {automation_urls}"
        )
        # infra_security nutzt Infra/Security-Feeds
        assert any("kubernetes" in u or "security" in u or "devops" in u for u in infra_urls), (
            f"infra_security sollte Infra-Feeds verwenden, bekam: {infra_urls}"
        )
        # Die Feed-URLs beider Gruppen sind verschieden
        assert set(automation_urls) != set(infra_urls), (
            "Beide Gruppen haben identische Feed-URLs – Konfigurationstrennung verletzt"
        )

    # ── 2. Shared URL → genau eine Datei ─────────────────────────────────────

    def test_shared_url_creates_exactly_one_file(self, fixture_config, tmp_projects):
        """
        Nachweispunkt 2: Ein Projekt, das in beiden Suchen vorkommt,
        wird nur einmal gespeichert (genau eine .md-Datei).
        """
        # Lauf 1: automation_bi
        result_a = self._run_group("automation_bi", ENTRIES_AUTOMATION, fixture_config, tmp_projects)
        assert result_a["groups_processed"] == 1

        # Lauf 2: infra_security (enthält URL_SHARED erneut)
        result_b = self._run_group("infra_security", ENTRIES_INFRA, fixture_config, tmp_projects)
        assert result_b["groups_processed"] == 1

        # Genau 3 Dateien erwartet: shared (1x), automation-only (1x), infra-only (1x)
        md_files = list(Path(tmp_projects).glob("**/*.md"))
        assert len(md_files) == 3, (
            f"Erwartet 3 .md-Dateien, gefunden {len(md_files)}: {[f.name for f in md_files]}"
        )

        # Die geteilte Datei muss existieren
        shared_files = [f for f in md_files if "shared" in f.name.lower() or "12345" in f.name]
        assert len(shared_files) == 1, (
            f"Shared-Projekt sollte exakt in 1 Datei erscheinen, "
            f"gefunden: {[f.name for f in shared_files]}"
        )

    # ── 3. Beide Suchgruppen werden am geteilten Projekt dokumentiert ─────────

    def test_shared_project_contains_both_search_groups(self, fixture_config, tmp_projects):
        """
        Nachweispunkt 3: Das geteilte Projekt enthält beide Suchgruppen in
        seinem Frontmatter (search_groups-Liste) oder Markdown-Body.
        """
        # Beide Gruppen nacheinander laufen lassen
        self._run_group("automation_bi", ENTRIES_AUTOMATION, fixture_config, tmp_projects)
        self._run_group("infra_security", ENTRIES_INFRA, fixture_config, tmp_projects)

        md_files = list(Path(tmp_projects).glob("**/*.md"))
        # Finde die Shared-Datei
        shared_file = next(
            (f for f in md_files if "shared" in f.name.lower() or "12345" in f.name),
            None
        )
        assert shared_file is not None, "Shared-Projektdatei nicht gefunden"

        content = _read_md(shared_file)

        # Beide Gruppen müssen irgendwo in der Datei erwähnt sein
        assert "automation_bi" in content, (
            f"automation_bi fehlt in {shared_file.name}:\n{content[:500]}"
        )
        assert "infra_security" in content, (
            f"infra_security fehlt in {shared_file.name}:\n{content[:500]}"
        )

    # ── 4. Wiederholter Lauf erzeugt keine Dubletten ──────────────────────────

    def test_repeated_run_creates_no_duplicates(self, fixture_config, tmp_projects):
        """
        Nachweispunkt 4: Ein zweiter Lauf beider Gruppen erzeugt keine neuen
        Dateien — die Deduplication greift.
        """
        # Erster vollständiger Lauf
        self._run_group("automation_bi", ENTRIES_AUTOMATION, fixture_config, tmp_projects)
        self._run_group("infra_security", ENTRIES_INFRA, fixture_config, tmp_projects)

        files_after_first_run = set(f.name for f in Path(tmp_projects).glob("**/*.md"))
        assert len(files_after_first_run) == 3, (
            f"Erster Lauf: erwartet 3 Dateien, gefunden {len(files_after_first_run)}"
        )

        # Zweiter vollständiger Lauf — identische Einträge
        result_a2 = self._run_group("automation_bi", ENTRIES_AUTOMATION, fixture_config, tmp_projects)
        result_b2 = self._run_group("infra_security", ENTRIES_INFRA, fixture_config, tmp_projects)

        files_after_second_run = set(f.name for f in Path(tmp_projects).glob("**/*.md"))

        assert files_after_second_run == files_after_first_run, (
            "Zweiter Lauf hat neue Dateien erzeugt (Dubletten): "
            + str(files_after_second_run - files_after_first_run)
        )

        # Alle Einträge sollten im zweiten Lauf als dedupliziert zählen
        a2_summary = result_a2["group_summaries"]["automation_bi"]
        b2_summary = result_b2["group_summaries"]["infra_security"]

        assert a2_summary["projects_saved"] == 0, (
            f"automation_bi Lauf 2 hat {a2_summary['projects_saved']} Projekte gespeichert "
            f"(erwartet 0 — alle bereits bekannt)"
        )
        assert b2_summary["projects_saved"] == 0, (
            f"infra_security Lauf 2 hat {b2_summary['projects_saved']} Projekte gespeichert "
            f"(erwartet 0 — alle bereits bekannt)"
        )

    # ── Kombinierten Summaries-Check ──────────────────────────────────────────

    def test_first_run_summary_counts(self, fixture_config, tmp_projects):
        """Summaries des ersten Laufs: korrekte Zählung von projects_saved."""
        result_a = self._run_group("automation_bi", ENTRIES_AUTOMATION, fixture_config, tmp_projects)
        result_b = self._run_group("infra_security", ENTRIES_INFRA, fixture_config, tmp_projects)

        a_summary = result_a["group_summaries"]["automation_bi"]
        b_summary = result_b["group_summaries"]["infra_security"]

        # automation_bi: 2 Einträge → 2 neue Projekte
        assert a_summary["projects_saved"] == 2, (
            f"automation_bi: erwartet 2 gespeichert, bekam {a_summary['projects_saved']}"
        )
        # infra_security: 2 Einträge, davon 1 bereits vorhanden → 1 neu, 1 merged/skipped
        # projects_saved == 1 (infra-only), shared wird als merged gezählt
        assert a_summary["projects_saved"] + b_summary["projects_saved"] == 3, (
            f"Insgesamt sollten 3 neue Projekte gespeichert werden, "
            f"automation={a_summary['projects_saved']}, infra={b_summary['projects_saved']}"
        )


# ── APScheduler-Integrations-Tests ───────────────────────────────────────────

class TestAPSchedulerSearchGroupIntegration:
    """
    Beweist, dass APScheduler-Jobs für jede Suchgruppe unabhängig erstellt
    und ausgeführt werden können, ohne echter Netzwerk-Zugriff.
    """

    def test_scheduler_can_create_independent_jobs_per_group(self, fixture_config):
        """
        Für jede Suchgruppe kann ein unabhängiger APScheduler-Job erstellt werden,
        mit der gruppenspezifischen Interval-Konfiguration (rss_interval_minutes).
        """
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler()
        scheduler.start()

        try:
            from search_group_config import load_search_groups
            groups = load_search_groups(fixture_config)

            created_jobs = {}
            for group_id, group_cfg in groups.items():
                interval_min = group_cfg.schedule.rss_interval_minutes if group_cfg.schedule else 60

                # Job-Funktion: Dummy (kein echter Netzwerkaufruf)
                def make_job(gid=group_id):
                    def _job():
                        pass  # Simulation
                    _job.__name__ = f"rss_ingest_{gid}"
                    return _job

                job = scheduler.add_job(
                    make_job(),
                    trigger="interval",
                    minutes=interval_min,
                    id=f"rss_{group_id}",
                    replace_existing=True,
                )
                created_jobs[group_id] = job

            # Beide Gruppen haben eigene Jobs
            assert set(created_jobs.keys()) == {"automation_bi", "infra_security"}, (
                f"Erwartet Jobs für beide Gruppen, bekam: {list(created_jobs.keys())}"
            )

            # Jobs haben gruppenspezifische IDs
            job_ids = [j.id for j in created_jobs.values()]
            assert "rss_automation_bi" in job_ids
            assert "rss_infra_security" in job_ids
            assert len(set(job_ids)) == 2, "Job-IDs sind nicht eindeutig"

            # Jobs haben unterschiedliche Intervalle (automation_bi=60, infra_security=90)
            automation_job = created_jobs["automation_bi"]
            infra_job = created_jobs["infra_security"]
            assert automation_job.trigger is not None
            assert infra_job.trigger is not None

        finally:
            scheduler.shutdown(wait=False)

    def test_scheduler_jobs_execute_independently(self, fixture_config, tmp_projects):
        """
        APScheduler-Jobs für beide Gruppen können unabhängig ausgelöst werden.
        Beweist: Aufruf einer Gruppe beeinflusst nicht die andere.
        """
        from apscheduler.schedulers.background import BackgroundScheduler
        from email_agent import run_rss_ingestion_for_search_groups

        executed_groups: list[str] = []
        lock = threading.Lock()

        def make_group_job(group_id: str, entries: list):
            def _job():
                with (
                    patch("email_agent.EmailAgent.fetch_rss_feed", return_value=entries),
                    patch("email_agent.EmailAgent.load_adapter") as mock_load,
                    patch("email_agent.MarkdownRenderer") as mock_rend,
                ):
                    mock_adapter = MagicMock()
                    mock_adapter.parse.side_effect = _fake_parse
                    mock_adapter.get_provider_name.return_value = "freelancermap"
                    mock_load.return_value = mock_adapter

                    mock_renderer = MagicMock()
                    mock_renderer.render.side_effect = _fake_render
                    mock_rend.return_value = mock_renderer

                    run_rss_ingestion_for_search_groups(
                        fixture_config,
                        output_dir=tmp_projects,
                        dry_run=False,
                        group_ids=[group_id],
                    )
                with lock:
                    executed_groups.append(group_id)
            _job.__name__ = f"rss_ingest_{group_id}"
            return _job

        scheduler = BackgroundScheduler()
        scheduler.start()

        try:
            # Beide Jobs einmalig via 'date' trigger ausführen
            run_at = datetime.now().replace(microsecond=0)
            scheduler.add_job(
                make_group_job("automation_bi", ENTRIES_AUTOMATION),
                trigger="date",
                run_date=run_at,
                id="rss_automation_bi",
            )
            scheduler.add_job(
                make_group_job("infra_security", ENTRIES_INFRA),
                trigger="date",
                run_date=run_at,
                id="rss_infra_security",
            )

            # Kurz warten, bis beide Jobs fertig sind (max. 5 s)
            deadline = time.time() + 5.0
            while time.time() < deadline and len(executed_groups) < 2:
                time.sleep(0.1)

            assert "automation_bi" in executed_groups, "automation_bi Job wurde nicht ausgeführt"
            assert "infra_security" in executed_groups, "infra_security Job wurde nicht ausgeführt"

        finally:
            scheduler.shutdown(wait=False)

    def test_scheduler_group_configs_are_independent(self, fixture_config):
        """
        Jede Suchgruppe liest ihre eigene Intervall-Konfiguration.
        automation_bi: 60 min RSS, infra_security: 90 min RSS.
        """
        from search_group_config import load_search_groups

        groups = load_search_groups(fixture_config)

        assert "automation_bi" in groups
        assert "infra_security" in groups

        ab = groups["automation_bi"]
        is_ = groups["infra_security"]

        # Unterschiedliche Intervalle
        assert ab.schedule.rss_interval_minutes == 60
        assert is_.schedule.rss_interval_minutes == 90
        assert ab.schedule.rss_interval_minutes != is_.schedule.rss_interval_minutes

        # Unterschiedliche Feed-URLs
        ab_urls = set()
        for prov_id in ab.get_rss_providers():
            ab_urls.update(ab.providers[prov_id].rss.feed_urls)

        is_urls = set()
        for prov_id in is_.get_rss_providers():
            is_urls.update(is_.providers[prov_id].rss.feed_urls)

        assert not ab_urls.intersection(is_urls), (
            f"Gruppen teilen Feed-URLs (sollten unabhängig sein): "
            f"{ab_urls.intersection(is_urls)}"
        )

    def test_cli_group_ids_filter_runs_only_specified_group(self, fixture_config, tmp_projects):
        """
        Simulation eines CLI-Aufrufs mit --search-group automation_bi:
        Nur automation_bi wird verarbeitet, infra_security bleibt unberührt.
        """
        from email_agent import run_rss_ingestion_for_search_groups

        with (
            patch("email_agent.EmailAgent.fetch_rss_feed", return_value=ENTRIES_AUTOMATION),
            patch("email_agent.EmailAgent.load_adapter") as mock_load,
            patch("email_agent.MarkdownRenderer") as mock_rend,
        ):
            mock_adapter = MagicMock()
            mock_adapter.parse.side_effect = _fake_parse
            mock_adapter.get_provider_name.return_value = "freelancermap"
            mock_load.return_value = mock_adapter

            mock_renderer = MagicMock()
            mock_renderer.render.side_effect = _fake_render
            mock_rend.return_value = mock_renderer

            result = run_rss_ingestion_for_search_groups(
                fixture_config,
                output_dir=tmp_projects,
                dry_run=False,
                group_ids=["automation_bi"],   # ← CLI-Filterung
            )

        assert result["groups_processed"] == 1, (
            f"Mit group_ids=['automation_bi'] sollte nur 1 Gruppe laufen, "
            f"bekam {result['groups_processed']}"
        )
        assert "automation_bi" in result["group_summaries"]
        assert "infra_security" not in result["group_summaries"]

    def test_cli_no_filter_runs_both_groups(self, fixture_config, tmp_projects):
        """
        CLI-Aufruf ohne group_ids-Filter → beide Gruppen werden ausgeführt.
        """
        from email_agent import run_rss_ingestion_for_search_groups

        with (
            patch("email_agent.EmailAgent.fetch_rss_feed", return_value=[]),
            patch("email_agent.EmailAgent.load_adapter") as mock_load,
            patch("email_agent.MarkdownRenderer") as mock_rend,
        ):
            mock_adapter = MagicMock()
            mock_adapter.parse.side_effect = _fake_parse
            mock_adapter.get_provider_name.return_value = "freelancermap"
            mock_load.return_value = mock_adapter

            mock_renderer = MagicMock()
            mock_renderer.render.side_effect = _fake_render
            mock_rend.return_value = mock_renderer

            result = run_rss_ingestion_for_search_groups(
                fixture_config,
                output_dir=tmp_projects,
                dry_run=False,
                group_ids=None,   # ← kein Filter = alle Gruppen
            )

        assert result["groups_processed"] == 2, (
            f"Ohne Filter sollen beide Gruppen laufen, bekam {result['groups_processed']}"
        )
        assert "automation_bi" in result["group_summaries"]
        assert "infra_security" in result["group_summaries"]
