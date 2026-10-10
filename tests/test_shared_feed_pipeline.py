"""
Tests: gemeinsamer RSS-Abruf mit Seen-Ledger (rss_feed_pipeline / seen_ledger).

Geprüft wird:
  - jeder Feed wird je Lauf einmal geladen, auch für zwei Suchgruppen
  - eine Projektseite wird höchstens einmal abgerufen; Wiederholungsläufe und
    Neustarts kosten keine Seitenabrufe
  - bereits bekannte Projektdateien werden weder geladen noch verändert
  - fehlgeschlagene Feed- und Seitenabrufe werden nicht als verarbeitet
    vermerkt und später wiederholt
  - URL-Deduplizierung und Zuordnung zu beiden Suchgruppen bleiben erhalten
  - geänderte Suchbegriffe führen zu einer kontrollierten Neubewertung
  - DE, AT und CH werden getrennt ausgewiesen

Kein Netzwerkzugriff, keine LLM-Aufrufe, kein SMTP.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from search_group_config import RSSChannelConfig, load_rss_prefilter
from seen_ledger import LEDGER_FILENAME, SeenLedger

REPO_ROOT = Path(__file__).parent.parent
A, B = "automation_bi", "infra_security"
BASE = "https://www.freelancermap.de/projekt/"

# slug → (Titel, RSS-Kurztext, vollständige Beschreibung der Projektseite)
PROJECTS = {
    "power-bi-berater": (
        "Power BI Berater (m/w/d)", "Aufbau von Berichten ...",
        "Aufbau von Berichten in Power BI mit DAX und Power Query. Remote."),
    "firewall-sophos": (
        "Firewall Spezialist Sophos (m/w/d)", "Migration der Firewalls ...",
        "Migration der Firewalls an drei Standorten, VPN und VLAN."),
    "m365-sharepoint": (
        "Microsoft 365 Consultant – SharePoint Migration", "Migration der Fileserver ...",
        "Migration nach SharePoint Online, Intune-Rollout und Active Directory."),
    "controller-lucanet": (
        "Interim Controller (m/w/d)", "Unterstützung im Abschluss ...",
        "Monatsabschluss, Konsolidierung und Management Reporting mit LucaNet."),
    "java-entwickler": (
        "Java Entwickler (m/w/d)", "Backend-Entwicklung ...",
        "Backend-Entwicklung mit Spring Boot im Scrum-Team."),
    "assistenzarzt": (
        "Assistenzarzt Innere Medizin (m/w/d)", "Für eine Klinik ...",
        "Stationsdienst in einer Klinik der Inneren Medizin."),
}
AT_PROJECTS = {
    "wien-netzwerk": (
        "Netzwerkadministrator Wien (m/w/d)", "Betrieb des Netzwerks ...",
        "Betrieb von Firewalls und Netzwerksegmentierung in Wien."),
}
CH_PROJECTS = {
    "zuerich-crm": (
        "CRM-Berater Zürich (m/w/d)", "Einführung ...",
        "Einführung von Salesforce im Vertrieb, Standort Zürich."),
}
ALL = {**PROJECTS, **AT_PROJECTS, **CH_PROJECTS}


def _entries(projects: dict) -> list:
    return [
        {"link": f"{BASE}{slug}?ref=rss", "title": title, "summary": summary,
         "published": None, "id": slug}
        for slug, (title, summary, _full) in projects.items()
    ]


class Harness:
    """Simulierte Plattform mit Zählern für Feed- und Seitenabrufe."""

    def __init__(self, tmp_path: Path, config: dict = None) -> None:
        self.out = tmp_path / "projects"
        self.out.mkdir(parents=True, exist_ok=True)
        self.config = config or yaml.safe_load(
            (REPO_ROOT / "search_groups_patch.yaml").read_text("utf-8"))
        self.config.setdefault(
            "providers", {"freelancermap": {"enabled": True, "channels": {"rss": {}}}})
        self.feeds = {"de": _entries(PROJECTS), "at": _entries(AT_PROJECTS),
                      "ch": _entries(CH_PROJECTS)}
        self.feed_calls: list = []
        self.page_calls: list = []
        self.failing_feeds: set = set()
        self.failing_pages: set = set()

    def _fetch(self, url, *args, **kwargs):
        name = url.split("/")[-1].split(".")[0]
        self.feed_calls.append(name)
        if name in self.failing_feeds:
            raise ConnectionError(f"Feed {name} nicht erreichbar")
        return list(self.feeds.get(name, []))

    def _parse(self, url):
        slug = url.split("/")[-1].split("?")[0]
        self.page_calls.append(slug)
        if slug in self.failing_pages:
            raise TimeoutError(f"Projektseite {slug} nicht erreichbar")
        title, _summary, full = ALL[slug]
        return {"schema": {"title": title, "url": url, "description": full}}

    @staticmethod
    def _render(schema, _meta):
        return (f"---\ntitle: \"{schema['title']}\"\n---\n\n# {schema['title']}\n\n"
                f"{schema['description']}\n")

    def run(self, **kwargs) -> dict:
        from email_agent import run_rss_ingestion_for_search_groups

        with (
            patch("email_agent.EmailAgent.fetch_rss_feed", side_effect=self._fetch),
            patch("email_agent.EmailAgent.load_adapter") as load_adapter,
            patch("email_agent.MarkdownRenderer") as renderer_cls,
        ):
            adapter = MagicMock()
            adapter.parse.side_effect = self._parse
            adapter.get_provider_name.return_value = "freelancermap"
            load_adapter.return_value = adapter
            renderer_cls.return_value.render.side_effect = self._render
            kwargs.setdefault("group_ids", [A, B])
            return run_rss_ingestion_for_search_groups(
                self.config, output_dir=str(self.out), dry_run=False, **kwargs)

    def run_old_per_group_path(self) -> None:
        """Der bisherige Ablauf: je Gruppe eigener Feed-Abruf, jede Seite je Lauf neu."""
        from email_agent import EmailAgent
        from search_group_config import apply_default_topic_filters, load_search_groups

        config = apply_default_topic_filters(self.config)
        with (
            patch("email_agent.EmailAgent.fetch_rss_feed", side_effect=self._fetch),
            patch("email_agent.EmailAgent.load_adapter") as load_adapter,
            patch("email_agent.MarkdownRenderer") as renderer_cls,
        ):
            adapter = MagicMock()
            adapter.parse.side_effect = self._parse
            adapter.get_provider_name.return_value = "freelancermap"
            load_adapter.return_value = adapter
            renderer_cls.return_value.render.side_effect = self._render
            agent = EmailAgent(config)
            for group in load_search_groups(config).values():
                agent.run_rss_ingestion_for_group(group, str(self.out), False)

    def files(self) -> dict:
        result = {}
        for md in self.out.glob("*.md"):
            text = md.read_text("utf-8")
            fm = yaml.safe_load(text[3:text.index("\n---", 3)])
            slug = fm["sources"][0]["url"].split("/")[-1].split("?")[0]
            result[slug] = fm
        return result

    def ledger(self) -> dict:
        return json.loads((self.out / LEDGER_FILENAME).read_text("utf-8"))["entries"]

    def digest(self) -> dict:
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.out.glob("*.md")}


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path)


# ══════════════════════════════════════════════════════════════════════════════
# 1 — Konfiguration
# ══════════════════════════════════════════════════════════════════════════════

class TestConfiguration:

    def test_default_limit_is_25(self):
        assert RSSChannelConfig.from_dict({"feed_urls": ["x"]}).limit == 25

    def test_config_template_has_de_at_ch_and_limit_25(self):
        config = yaml.safe_load((REPO_ROOT / "search_groups_patch.yaml").read_text("utf-8"))
        for group in (A, B):
            rss = config["search_groups"][group]["providers"]["freelancermap"]["channels"]["rss"]
            assert rss["limit"] == 25
            assert [u.split("/")[-1].split(".")[0] for u in rss["feed_urls"]] == ["de", "at", "ch"]

    def test_prefilter_terms_are_configured_and_narrow(self):
        terms = load_rss_prefilter()["title_exclude_terms"]
        assert "Assistenzarzt*" in terms
        # keine IT-nahen Rollen im Vorfilter
        lowered = " ".join(terms).lower()
        for it_term in ("entwickler", "consultant", "berater", "admin", "controller",
                        "projektleiter", "manager*", "architekt", "support"):
            assert it_term not in lowered.replace("baumanager*", ""), it_term


# ══════════════════════════════════════════════════════════════════════════════
# 2 — Abrufe: einmal je Feed, einmal je Projektseite
# ══════════════════════════════════════════════════════════════════════════════

class TestRequestCounts:

    def test_each_feed_is_loaded_once_for_both_groups(self, harness):
        summary = harness.run()
        assert sorted(harness.feed_calls) == ["at", "ch", "de"]
        assert summary["feed_requests"] == 3
        # beide Gruppen sehen alle 8 Einträge
        assert summary["group_summaries"][A]["entries_found"] == 8
        assert summary["group_summaries"][B]["entries_found"] == 8

    def test_page_is_requested_at_most_once_per_entry(self, harness):
        summary = harness.run()
        assert len(harness.page_calls) == len(set(harness.page_calls))
        # 8 Einträge, der Assistenzarzt wird anhand des RSS-Titels aussortiert
        assert summary["page_requests"] == len(harness.page_calls) == 7
        assert "assistenzarzt" not in harness.page_calls

    def test_second_run_needs_no_page_requests(self, harness):
        first = harness.run()
        harness.page_calls.clear()
        second = harness.run()
        assert harness.page_calls == [] and second["page_requests"] == 0
        assert second["total_projects_saved"] == 0 and second["total_errors"] == 0
        # dieselbe Einordnung wie im ersten Lauf, nur aus dem Ledger
        for group in (A, B):
            f, s = first["group_summaries"][group], second["group_summaries"][group]
            assert s["projects_filtered"] == f["projects_filtered"]
            assert s["urls_skipped_dedupe"] == f["projects_saved"] + f["urls_skipped_dedupe"]

    def test_before_after_request_comparison(self, tmp_path):
        """Zwei Läufe über denselben Feed: alter Ablauf gegen neuen Ablauf."""
        old = Harness(tmp_path / "alt")
        old.run_old_per_group_path()
        old.run_old_per_group_path()

        new = Harness(tmp_path / "neu")
        new.run()
        new.run()

        # alt: je Lauf und Gruppe 3 Feeds und jede der 8 Seiten → 2 × 2 × (3 + 8)
        assert (len(old.feed_calls), len(old.page_calls)) == (12, 32)
        # neu: je Lauf 3 Feeds; 7 Seiten nur im ersten Lauf
        assert (len(new.feed_calls), len(new.page_calls)) == (6, 7)
        # gleiche Projekte, gleiche Gruppen
        assert {s: fm["search_groups"] for s, fm in new.files().items()} == \
               {s: fm["search_groups"] for s, fm in old.files().items()}

    def test_dry_run_makes_no_requests(self, harness):
        from email_agent import run_rss_ingestion_for_search_groups
        with patch("email_agent.EmailAgent.fetch_rss_feed", side_effect=harness._fetch):
            summary = run_rss_ingestion_for_search_groups(
                harness.config, output_dir=str(harness.out), dry_run=True)
        assert harness.feed_calls == [] and summary["feed_requests"] == 0
        assert not (harness.out / LEDGER_FILENAME).exists()

    def test_page_request_cap_defers_and_catches_up(self, harness):
        first = harness.run(max_page_requests=3)
        assert first["page_requests"] == 3 and first["page_requests_deferred"] == 4
        second = harness.run(max_page_requests=3)
        third = harness.run(max_page_requests=3)
        assert second["page_requests"] == 3 and third["page_requests"] == 1
        assert third["page_requests_deferred"] == 0
        assert len(harness.page_calls) == len(set(harness.page_calls)) == 7

    def test_delay_between_page_requests(self, harness):
        with patch("rss_feed_pipeline.time.sleep") as sleep:
            harness.run(page_delay=0.5)
        # vor jedem Abruf außer dem ersten
        assert sleep.call_count == 6
        sleep.assert_called_with(0.5)


# ══════════════════════════════════════════════════════════════════════════════
# 3 — Zuordnung, Deduplizierung, getrennte Ausweisung
# ══════════════════════════════════════════════════════════════════════════════

class TestAssignment:

    def test_groups_per_project(self, harness):
        harness.run()
        got = {slug: sorted(fm["search_groups"]) for slug, fm in harness.files().items()}
        assert got == {
            "power-bi-berater": [A],
            "firewall-sophos": [B],
            "m365-sharepoint": [A, B],        # eine Datei, beide Gruppen, ein Seitenabruf
            "controller-lucanet": [A],        # Treffer erst im Text der Projektseite
            "wien-netzwerk": [B],
            "zuerich-crm": [A],
        }
        assert harness.page_calls.count("m365-sharepoint") == 1

    def test_one_file_per_url(self, harness):
        harness.run()
        harness.run()
        assert len(list(harness.out.glob("*.md"))) == 6

    def test_feeds_are_reported_separately(self, harness):
        feeds = harness.run()["feed_summaries"]
        assert set(feeds) == {"DE", "AT", "CH"}
        assert feeds["DE"] == {"entries": 6, "new": 4, "known": 0, "filtered": 2,
                               "errors": 0, "deferred": 0, "page_requests": 5}
        assert feeds["AT"] == {"entries": 1, "new": 1, "known": 0, "filtered": 0,
                               "errors": 0, "deferred": 0, "page_requests": 1}
        assert feeds["CH"] == {"entries": 1, "new": 1, "known": 0, "filtered": 0,
                               "errors": 0, "deferred": 0, "page_requests": 1}

    def test_second_run_reports_known_and_filtered(self, harness):
        harness.run()
        feeds = harness.run()["feed_summaries"]
        assert (feeds["DE"]["new"], feeds["DE"]["known"], feeds["DE"]["filtered"]) == (0, 4, 2)
        assert (feeds["AT"]["known"], feeds["CH"]["known"]) == (1, 1)

    def test_same_url_in_two_feeds_is_processed_once(self, harness):
        harness.feeds["at"] = harness.feeds["at"] + [harness.feeds["de"][0]]
        harness.run()
        assert harness.page_calls.count("power-bi-berater") == 1
        assert len(list(harness.out.glob("*.md"))) == 6

    def test_separate_group_runs_still_merge_into_one_file(self, harness):
        """Wie bisher: läuft Gruppe B später allein, wird sie am Projekt ergänzt."""
        harness.run(group_ids=[A])
        assert harness.files()["m365-sharepoint"]["search_groups"] == [A]
        harness.run(group_ids=[B])
        assert sorted(harness.files()["m365-sharepoint"]["search_groups"]) == [A, B]
        assert len([p for p in harness.out.glob("*.md")]) == 6


# ══════════════════════════════════════════════════════════════════════════════
# 4 — Seen-Ledger
# ══════════════════════════════════════════════════════════════════════════════

class TestSeenLedger:

    def test_ledger_records_every_entry(self, harness):
        harness.run()
        ledger = harness.ledger()
        assert len(ledger) == 8
        by_slug = {k.split("/")[-1]: v for k, v in ledger.items()}
        assert by_slug["power-bi-berater"]["status"] == "captured"
        assert by_slug["java-entwickler"]["status"] == "filtered"
        assert by_slug["java-entwickler"]["basis"] == "page"
        assert by_slug["assistenzarzt"]["status"] == "filtered"
        assert by_slug["assistenzarzt"]["basis"] == "rss_title"
        assert by_slug["wien-netzwerk"]["feed"] == "AT"
        assert {g: d["decision"] for g, d in by_slug["m365-sharepoint"]["groups"].items()} == {
            A: "captured", B: "captured"}
        assert {g: d["decision"] for g, d in by_slug["firewall-sophos"]["groups"].items()} == {
            A: "filtered", B: "captured"}

    def test_ledger_keys_ignore_query_string(self, harness):
        harness.run()
        assert all("?" not in key for key in harness.ledger())

    def test_restart_uses_persisted_ledger(self, harness, tmp_path):
        harness.run()
        # neuer Prozess: nichts im Speicher, nur die Dateien im Projektordner
        restarted = Harness(tmp_path)
        summary = restarted.run()
        assert restarted.page_calls == []
        assert summary["total_projects_saved"] == 0
        assert len(list(restarted.out.glob("*.md"))) == 6

    def test_no_temporary_file_is_left_behind(self, harness):
        harness.run()
        assert not list(harness.out.glob("*.tmp"))

    def test_corrupt_ledger_is_set_aside_without_duplicates(self, harness):
        harness.run()
        (harness.out / LEDGER_FILENAME).write_text("{kaputt", "utf-8")
        harness.page_calls.clear()
        summary = harness.run()
        assert (harness.out / (LEDGER_FILENAME + ".corrupt")).exists()
        assert summary["total_projects_saved"] == 0
        assert len(list(harness.out.glob("*.md"))) == 6
        # bekannte Projekte werden nicht erneut geladen, nur die zuvor gefilterten
        assert sorted(harness.page_calls) == ["java-entwickler"]

    def test_unchanged_ledger_is_not_rewritten(self, tmp_path):
        ledger = SeenLedger(str(tmp_path))
        ledger.save()
        assert not (tmp_path / LEDGER_FILENAME).exists()

    def test_prune_removes_only_old_entries(self, tmp_path):
        ledger = SeenLedger(str(tmp_path))
        ledger.touch("a", url="a", title="", summary="", provider="p", feed="DE")
        ledger.touch("b", url="b", title="", summary="", provider="p", feed="DE")
        ledger.get("a")["last_seen"] = "2025-01-01T00:00:00"
        assert ledger.prune(max_age_days=120) == 1
        assert "a" not in ledger and "b" in ledger


# ══════════════════════════════════════════════════════════════════════════════
# 5 — Bestehende Projektdateien bleiben unangetastet
# ══════════════════════════════════════════════════════════════════════════════

class TestExistingProjectsUntouched:

    def test_projects_known_before_the_ledger_are_not_fetched_or_modified(self, harness):
        # Bestand wie auf dem Mac: mit dem alten Ablauf importiert, kein Ledger
        harness.run_old_per_group_path()
        assert not (harness.out / LEDGER_FILENAME).exists()
        before = harness.digest()
        assert len(before) == 6
        harness.page_calls.clear()

        summary = harness.run()

        assert harness.digest() == before                      # Dateien bytegleich
        assert summary["total_projects_saved"] == 0
        # die 6 bekannten Projekte: kein Seitenabruf; geladen wird nur der neue,
        # nicht anhand des Titels entscheidbare Eintrag
        assert sorted(harness.page_calls) == ["java-entwickler"]
        ledger = {k.split("/")[-1]: v for k, v in harness.ledger().items()}
        assert ledger["power-bi-berater"]["status"] == "existing"
        assert ledger["power-bi-berater"]["basis"] == "existing"

    def test_existing_projects_do_not_gain_groups_when_terms_change(self, harness):
        harness.run_old_per_group_path()
        before = harness.digest()
        harness.run()
        # neuer Fachbegriff, der auf ein Bestandsprojekt passen würde
        harness.config["search_groups"][B]["filters"] = {"include_terms": ["Power BI"]}
        harness.page_calls.clear()
        harness.run()
        assert harness.digest() == before
        assert "power-bi-berater" not in harness.page_calls


# ══════════════════════════════════════════════════════════════════════════════
# 6 — Fehler: nichts wird als verarbeitet vermerkt, später Wiederholung
# ══════════════════════════════════════════════════════════════════════════════

class TestFailures:

    def test_failed_page_request_is_retried_later(self, harness):
        harness.failing_pages = {"firewall-sophos"}
        first = harness.run()
        assert first["total_errors"] == 1
        assert first["feed_summaries"]["DE"]["errors"] == 1
        assert "firewall-sophos" not in harness.files()
        ledger = {k.split("/")[-1]: v for k, v in harness.ledger().items()}
        assert ledger["firewall-sophos"]["status"] == "error"
        assert ledger["firewall-sophos"]["groups"] == {}
        assert ledger["firewall-sophos"]["errors"] == 1
        assert "TimeoutError" in ledger["firewall-sophos"]["last_error"]

        # Plattform wieder erreichbar: nur dieser eine Eintrag wird nachgeholt
        harness.failing_pages = set()
        harness.page_calls.clear()
        second = harness.run()
        assert harness.page_calls == ["firewall-sophos"]
        assert second["total_errors"] == 0 and second["total_projects_saved"] == 1
        assert harness.files()["firewall-sophos"]["search_groups"] == [B]
        ledger = {k.split("/")[-1]: v for k, v in harness.ledger().items()}
        assert ledger["firewall-sophos"]["status"] == "captured"
        assert ledger["firewall-sophos"]["last_error"] is None

    def test_repeated_failure_is_retried_each_run_and_counted(self, harness):
        harness.failing_pages = {"firewall-sophos"}
        harness.run()
        harness.run()
        ledger = {k.split("/")[-1]: v for k, v in harness.ledger().items()}
        assert ledger["firewall-sophos"]["errors"] == 2
        assert harness.page_calls.count("firewall-sophos") == 2
        assert harness.page_calls.count("power-bi-berater") == 1   # der Rest nur einmal

    def test_failed_feed_marks_nothing_and_other_feeds_continue(self, harness):
        harness.failing_feeds = {"de"}
        first = harness.run()
        assert first["total_errors"] == 1
        assert first["feed_summaries"]["DE"]["errors"] == 1
        assert first["feed_summaries"]["DE"]["entries"] == 0
        assert sorted(harness.files()) == ["wien-netzwerk", "zuerich-crm"]
        assert len(harness.ledger()) == 2            # kein DE-Eintrag vermerkt

        harness.failing_feeds = set()
        second = harness.run()
        assert second["total_errors"] == 0
        assert second["feed_summaries"]["DE"]["new"] == 4
        assert len(harness.files()) == 6

    def test_failure_while_writing_is_retried(self, harness):
        calls = {"n": 0}
        original = Harness._render

        def flaky(schema, meta):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("Datenträger voll")
            return original(schema, meta)

        with patch.object(Harness, "_render", staticmethod(flaky)):
            first = harness.run()
            assert first["total_errors"] == 1
            assert len(harness.files()) == 5
            second = harness.run()
        assert second["total_errors"] == 0 and second["total_projects_saved"] == 1
        assert len(harness.files()) == 6
        assert len(list(harness.out.glob("*.md"))) == 6      # keine Dublette

    def test_interrupted_run_can_be_repeated_without_duplicates(self, harness):
        """Abbruch mitten im Lauf: der nächste Lauf holt nach, ohne Dubletten."""
        seen = {"n": 0}
        original = harness._parse

        def crash_after_three(url):
            seen["n"] += 1
            if seen["n"] == 4:
                raise KeyboardInterrupt()
            return original(url)

        harness._parse = crash_after_three
        with pytest.raises(KeyboardInterrupt):
            harness.run()
        partial = len(harness.files())
        assert 0 < partial < 6
        assert (harness.out / LEDGER_FILENAME).exists()      # Zwischenstand gesichert

        harness._parse = original
        harness.run()
        assert len(harness.files()) == 6
        assert len(list(harness.out.glob("*.md"))) == 6


# ══════════════════════════════════════════════════════════════════════════════
# 7 — RSS-Vorfilter
# ══════════════════════════════════════════════════════════════════════════════

class TestRssPrefilter:

    def test_clearly_unrelated_title_is_filtered_without_page_request(self, harness):
        harness.run()
        assert "assistenzarzt" not in harness.page_calls
        log = [json.loads(line) for line in
               (harness.out / "filter_rejected.jsonl").read_text("utf-8").splitlines()]
        prefiltered = [e for e in log if e["provider_url"].endswith("assistenzarzt?ref=rss")]
        assert {e["search_group_id"] for e in prefiltered} == {A, B}
        assert all(e["filter_result"]["failed_criteria"] == ["rss_title_prefilter"]
                   for e in prefiltered)

    def test_topic_term_in_rss_overrides_title_exclusion(self, harness):
        harness.feeds["de"].append({
            "link": f"{BASE}recruiter-power-bi?ref=rss",
            "title": "Recruiter (m/w/d) mit Power BI Reporting", "summary": "", "id": "x"})
        ALL["recruiter-power-bi"] = (
            "Recruiter (m/w/d) mit Power BI Reporting", "",
            "Aufbau eines Recruiting-Reportings in Power BI.")
        try:
            harness.run()
        finally:
            ALL.pop("recruiter-power-bi")
        assert "recruiter-power-bi" in harness.page_calls
        assert harness.files()["recruiter-power-bi"]["search_groups"] == [A]

    def test_empty_prefilter_fetches_every_new_page_once(self, harness):
        harness.config["rss_prefilter"] = {"title_exclude_terms": []}
        summary = harness.run()
        assert summary["page_requests"] == 8
        assert "assistenzarzt" in harness.page_calls

    def test_title_match_only_applies_to_the_title(self, harness):
        """Der Berufsbegriff im Kurztext allein reicht nicht für den Vorfilter."""
        harness.feeds["de"].append({
            "link": f"{BASE}java-entwickler-2?ref=rss", "title": "Softwareentwickler (m/w/d)",
            "summary": "Zusammenarbeit mit dem Recruiter des Kunden", "id": "y"})
        ALL["java-entwickler-2"] = ("Softwareentwickler (m/w/d)", "", "Java und Spring Boot.")
        try:
            harness.run()
        finally:
            ALL.pop("java-entwickler-2")
        assert "java-entwickler-2" in harness.page_calls


# ══════════════════════════════════════════════════════════════════════════════
# 8 — Geänderte Suchbegriffe: kontrollierte Neubewertung
# ══════════════════════════════════════════════════════════════════════════════

class TestReevaluation:

    def _add_java_term(self, harness):
        cfg = copy.deepcopy(harness.config)
        from search_group_config import apply_default_topic_filters
        merged = apply_default_topic_filters(cfg)
        terms = merged["search_groups"][A]["filters"]["include_terms"] + ["Spring Boot"]
        harness.config["search_groups"][A]["filters"] = {"include_terms": terms}

    def test_filtered_entry_in_feed_is_reevaluated_after_term_change(self, harness):
        harness.run()
        assert "java-entwickler" not in harness.files()
        harness.page_calls.clear()

        self._add_java_term(harness)
        summary = harness.run()

        # nur die zuvor für Gruppe A abgelehnten Einträge werden neu geladen;
        # erfasste Projekte und der RSS-gefilterte Eintrag kosten keinen Abruf
        assert sorted(harness.page_calls) == [
            "firewall-sophos", "java-entwickler", "wien-netzwerk"]
        assert harness.files()["java-entwickler"]["search_groups"] == [A]
        assert summary["total_projects_saved"] == 1
        assert len(list(harness.out.glob("*.md"))) == 7

    def test_unchanged_terms_trigger_no_reevaluation(self, harness):
        harness.run()
        harness.page_calls.clear()
        harness.run(reevaluate_filtered=True)
        assert harness.page_calls == []

    def test_entry_gone_from_feed_needs_explicit_reevaluation(self, harness):
        harness.run()
        harness.feeds["de"] = [e for e in harness.feeds["de"] if "java" not in e["link"]]
        self._add_java_term(harness)
        harness.page_calls.clear()

        harness.run()                                   # ohne Schalter: nicht angefasst
        assert "java-entwickler" not in harness.page_calls
        assert "java-entwickler" not in harness.files()

        summary = harness.run(reevaluate_filtered=True)  # mit Schalter: nachgeholt
        assert "java-entwickler" in harness.page_calls
        assert harness.files()["java-entwickler"]["search_groups"] == [A]
        assert summary["feed_summaries"]["NEUBEWERTUNG"]["new"] == 1

    def test_reevaluation_respects_page_request_cap(self, harness):
        harness.run()
        self._add_java_term(harness)
        harness.page_calls.clear()
        summary = harness.run(max_page_requests=1)
        assert len(harness.page_calls) == 1
        assert summary["page_requests_deferred"] == 2
