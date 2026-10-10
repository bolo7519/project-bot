"""
Tests: gruppenspezifische fachliche Filterung (include_terms).

Hintergrund: Beide Suchgruppen lesen denselben ungefilterten FreelancerMap-Feed.
Vor dieser Änderung kannte die FilterEngine kein fachliches Kriterium und die
Suchgruppen hatten keinen `filters`-Block — jeder Feed-Eintrag galt deshalb als
Treffer jeder Gruppe (Live-Lauf: 40 Einträge, 0 gefiltert).

Abgedeckt:
  1. Begriffsabgleich (Wortgrenzen, Schreibvarianten, keine Allerweltswörter)
  2. Standardfilter aus search_group_filters.yaml und Vorrang von config.yaml
  3. Zuordnung von Beispielprojekten: nur A, nur B, beide, keine
  4. Ende-zu-Ende über die produktive Pipeline mit search_groups_patch.yaml:
     fachfremde Projekte erzeugen keine Datei, gruppenübergreifende genau eine,
     Wiederholungsläufe keine Dubletten, Pre-Scoring gegen alle vier Profile

Kein Netzwerkzugriff, keine LLM-Aufrufe, kein SMTP.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from filter_engine import FilterConfig, FilterEngine, _compile_include_term
from pre_scorer import PROFILE_IDS
from search_group_config import (
    apply_default_topic_filters,
    load_default_topic_filters,
    load_search_groups,
)

REPO_ROOT = Path(__file__).parent.parent
PATCH_CONFIG_PATH = REPO_ROOT / "search_groups_patch.yaml"

GROUP_A = "automation_bi"
GROUP_B = "infra_security"


# ── Beispielprojekte ──────────────────────────────────────────────────────────
# (slug, erwartete Gruppen, Titel, Beschreibung, Schlagworte)
# Die „keine"-Beispiele enthalten bewusst Allerweltswörter, die früher als
# Suchbegriffe dienten (make, API, Workflow, Netzwerk, Infrastruktur, Security,
# Reporting) und jetzt keinen Treffer mehr auslösen dürfen.

SAMPLE_PROJECTS = [
    # ── nur Gruppe A ──────────────────────────────────────────────────────────
    (
        "crm-berater-hubspot", {GROUP_A},
        "CRM-Berater HubSpot Einführung (m/w/d)",
        "Analyse der Vertriebsprozesse, Aufbau des Lead-Managements und "
        "Schulung des Vertriebsteams.",
        [],
    ),
    (
        "automatisierung-make", {GROUP_A},
        "Automatisierungsexperte Make.com / Integromat (m/w/d)",
        "Aufbau und Wartung von Szenarien, Anbindung von REST-Schnittstellen "
        "an das Ticketsystem.",
        [],
    ),
    (
        "bi-entwickler", {GROUP_A},
        "BI Entwickler Finanzberichte (m/w/d)",
        "Aufbau eines Datenmodells und von Berichten für das Controlling.",
        ["Power BI", "Datenmodellierung"],  # Treffer nur über Schlagworte
    ),
    (
        "ki-prozessintegration", {GROUP_A},
        "KI-Integration in Geschäftsprozesse",
        "Einbindung von Azure OpenAI in die Angebotserstellung, "
        "Prozessautomatisierung im Innendienst.",
        [],
    ),
    # ── nur Gruppe B ──────────────────────────────────────────────────────────
    (
        "firewall-spezialist", {GROUP_B},
        "Firewall Spezialist (m/w/d) Sophos/Fortinet",
        "Migration der Firewalls an drei Standorten, Regelwerk und VPN-Tunnel.",
        [],
    ),
    (
        "netzwerkadministrator", {GROUP_B},
        "Netzwerkadministrator (m/w/d) VLAN-Segmentierung",
        "Neuaufbau der Switching-Landschaft und Segmentierung der Produktion.",
        [],
    ),
    (
        "spezialist-iso27001", {GROUP_B},
        "Spezialist ISO27001",
        "Mitwirkung bei Zertifizierungsangeboten und technische Audit-Reviews "
        "des ISMS.",
        [],
    ),
    (
        "it-projektleiter-infrastruktur", {GROUP_B},
        "IT-Projektleiter IT-Infrastruktur (m/w/d)",
        "Leitung eines Rechenzentrumsumzugs inkl. Server- und Storage-Migration.",
        [],
    ),
    # ── beide Gruppen ─────────────────────────────────────────────────────────
    (
        "m365-sharepoint-consultant", {GROUP_A, GROUP_B},
        "Microsoft 365 Consultant – SharePoint Migration",
        "Migration der Fileserver nach SharePoint Online, Intune-Rollout und "
        "Berechtigungskonzept.",
        [],
    ),
    (
        "it-transformation-crm", {GROUP_A, GROUP_B},
        "Programmleiter IT-Transformation (m/w/d)",
        "Ablösung des Alt-CRM durch Dynamics 365 und Erneuerung der "
        "Netzwerkinfrastruktur.",
        [],
    ),
    # ── keine Gruppe (fachfremd) ──────────────────────────────────────────────
    (
        "interim-accountant", set(),
        "Interim Accountant (m/w/d)",
        "Unterstützung im Monatsabschluss und Reporting an die kaufmännische "
        "Leitung.",
        [],
    ),
    (
        "interim-recruiter", set(),
        "Interim Recruiter (m/w/d)",
        "Wir verfügen über ein großes Netzwerk an Kandidaten und suchen "
        "Unterstützung im Active Sourcing.",
        [],
    ),
    (
        "baumanager-budgetcontrolling", set(),
        "Baumanager Risiko- und Budgetcontrolling (w/m/d)",
        "Verantwortung im Infrastrukturbereich Wasser und Abwasser, "
        "Anlagenplanung und Security-Einweisung auf der Baustelle.",
        [],
    ),
    (
        "frontend-developer-angular", set(),
        "Frontend Developer (m/w/d) – Angular, TypeScript",
        "We make great software. Anbindung an REST APIs, agiler Workflow im "
        "Scrum-Team, KI-Tools sind willkommen.",
        ["API", "Workflow"],
    ),
    (
        "sap-abap-payment-engine", set(),
        "Spezialist für SAP-Payment-Engine / ABAP-Entwickler",
        "Weiterentwicklung der Zahlungsverkehrsplattform einer Bank, hohes "
        "Kapitalmarkt-Know-how erwünscht.",
        [],
    ),
    (
        "produktionsplaner", set(),
        "Freelance technischer Produktionsplaner (m/w/d)",
        "Planung der Fertigungslinien, Abstimmung mit Einkauf und Qualität.",
        [],
    ),
]

SAMPLE_BY_URL = {
    f"https://www.freelancermap.de/projekt/{slug}": (groups, title, description, tags)
    for slug, groups, title, description, tags in SAMPLE_PROJECTS
}


def _schema(title: str, description: str, tags: list) -> dict:
    return {"title": title, "description": description, "schlagworte": tags}


@pytest.fixture(scope="module")
def default_filters():
    return load_default_topic_filters()


@pytest.fixture(scope="module")
def engines(default_filters):
    return {
        gid: FilterEngine(FilterConfig.from_dict(default_filters[gid]))
        for gid in (GROUP_A, GROUP_B)
    }


def _groups_for(engines, title, description, tags) -> set:
    schema = _schema(title, description, tags)
    return {gid for gid, eng in engines.items() if eng.apply(schema, gid).passed}


# ══════════════════════════════════════════════════════════════════════════════
# 1 — Begriffsabgleich
# ══════════════════════════════════════════════════════════════════════════════

class TestIncludeTermMatching:

    @pytest.mark.parametrize("term,text", [
        ("Power BI", "Erfahrung mit PowerBI"),
        ("Power BI", "Power-BI Berichte"),
        ("Power BI", "power bi"),
        ("ISO 27001", "Zertifizierung nach ISO27001"),
        ("Check Point", "Checkpoint Firewall"),
        ("Make.com", "Szenarien in Make.com, Zapier"),
        ("Firewall*", "Betrieb der Firewalls"),
        ("Firewall*", "Next-Gen-Firewall"),
        ("IT-Infrastruktur*", "Leitung von IT-Infrastrukturprojekten"),
        ("API-Integration*", "REST-API-Integrationen"),
        ("CRM", "Einführung eines CRM-Systems"),
    ])
    def test_term_matches(self, term, text):
        assert _compile_include_term(term).search(text)

    @pytest.mark.parametrize("term,text", [
        ("Make.com", "We make great software"),
        ("Make.com", "Makecom"),
        ("CRM", "Scrummaster"),
        ("RPA", "Corporate"),
        ("VPN", "MVPN-Light"),
        ("Firewall", "Firewallregeln"),          # ohne * keine Wortfortsetzung
        ("API-Integration*", "Kapitalintegration"),
        ("IT-Infrastruktur*", "Kapazität Infrastruktur"),
    ])
    def test_term_does_not_match(self, term, text):
        assert not _compile_include_term(term).search(text)

    def test_empty_term_is_ignored(self):
        assert _compile_include_term("  ") is None
        assert _compile_include_term("*") is None

    def test_no_match_rejects_and_is_not_unknown(self):
        """Kein Fachbegriff → abgelehnt; kein „unbekannt → pass" wie bei den anderen Filtern."""
        engine = FilterEngine(FilterConfig.from_dict({"include_terms": ["Sophos"]}))
        result = engine.apply({"title": "Interim Accountant"}, GROUP_B)
        assert result.passed is False
        assert [c.criterion for c in result.failed_checks] == ["include_terms"]
        assert result.unknown_checks == []

    def test_matched_terms_are_recorded(self):
        engine = FilterEngine(FilterConfig.from_dict({"include_terms": ["Sophos", "VLAN*"]}))
        result = engine.apply({"title": "Sophos Rollout", "description": "VLANs"}, GROUP_B)
        assert result.passed is True
        assert result.to_dict()["checks"][0]["value_found"] == ["Sophos", "VLAN*"]

    def test_include_min_matches(self):
        cfg = FilterConfig.from_dict(
            {"include_terms": ["Sophos", "Firewall*"], "include_min_matches": 2}
        )
        engine = FilterEngine(cfg)
        assert engine.apply({"title": "Sophos Firewall"}, GROUP_B).passed is True
        assert engine.apply({"title": "Sophos Endpoint"}, GROUP_B).passed is False

    def test_include_combines_with_exclude_terms(self):
        cfg = FilterConfig.from_dict(
            {"include_terms": ["Sophos"], "exclude_terms": ["Festanstellung"]}
        )
        result = FilterEngine(cfg).apply(
            {"title": "Sophos Admin", "description": "Festanstellung"}, GROUP_B
        )
        assert result.passed is False
        assert [c.criterion for c in result.failed_checks] == ["exclude_terms"]


# ══════════════════════════════════════════════════════════════════════════════
# 2 — Standardfilter und Konfiguration
# ══════════════════════════════════════════════════════════════════════════════

class TestDefaultTopicFilters:

    def test_root_cause_group_without_filters_lets_everything_pass(self):
        """Dokumentiert die Ursache: ohne include_terms gibt es keinen fachlichen Check."""
        config = yaml.safe_load(PATCH_CONFIG_PATH.read_text(encoding="utf-8"))
        groups = load_search_groups(config)
        assert groups[GROUP_A].filters is None
        engine = FilterEngine(FilterConfig.empty())
        result = engine.apply({"title": "Interim Accountant (m/w/d)"}, GROUP_A)
        assert result.passed is True and result.checks == []

    def test_defaults_exist_for_both_groups(self, default_filters):
        assert set(default_filters) == {GROUP_A, GROUP_B}
        for gid in (GROUP_A, GROUP_B):
            assert len(default_filters[gid]["include_terms"]) >= 20

    def test_groups_have_distinct_terms(self, default_filters):
        terms_a = set(default_filters[GROUP_A]["include_terms"])
        terms_b = set(default_filters[GROUP_B]["include_terms"])
        assert terms_a != terms_b
        # Überschneidung nur dort, wo sie fachlich gewollt ist
        assert terms_a & terms_b == {"Systemintegration", "Microsoft 365", "M365"}

    def test_make_replaced_by_makecom_and_integromat(self, default_filters):
        terms = {t.lower() for t in default_filters[GROUP_A]["include_terms"]}
        assert "make" not in terms
        assert {"make.com", "integromat"} <= terms

    def test_no_generic_single_words(self, default_filters):
        generic = {
            "make", "api", "workflow", "netzwerk", "infrastruktur", "security",
            "reporting", "dashboard", "ki", "ai", "automation", "automatisierung",
            "integration", "cloud", "projekt",
        }
        for gid in (GROUP_A, GROUP_B):
            terms = {t.lower().rstrip("*") for t in default_filters[gid]["include_terms"]}
            assert not (terms & generic), f"{gid}: zu allgemein: {terms & generic}"

    def test_all_terms_compile(self, default_filters):
        for gid in (GROUP_A, GROUP_B):
            for term in default_filters[gid]["include_terms"]:
                assert _compile_include_term(term) is not None, term

    def test_patch_config_gets_defaults_without_being_mutated(self):
        config = yaml.safe_load(PATCH_CONFIG_PATH.read_text(encoding="utf-8"))
        merged = apply_default_topic_filters(config)
        assert "filters" not in config["search_groups"][GROUP_A]
        groups = load_search_groups(merged)
        assert "Make.com" in groups[GROUP_A].filters["include_terms"]
        assert "Sophos" in groups[GROUP_B].filters["include_terms"]
        # Feed-Konfiguration bleibt unverändert
        assert (
            groups[GROUP_A].providers["freelancermap"].rss.feed_urls
            == config["search_groups"][GROUP_A]["providers"]["freelancermap"]
            ["channels"]["rss"]["feed_urls"]
        )

    def test_config_include_terms_take_precedence(self):
        config = {"search_groups": {GROUP_A: {"filters": {"include_terms": ["Nur Dies"]}}}}
        merged = apply_default_topic_filters(config)
        assert merged["search_groups"][GROUP_A]["filters"]["include_terms"] == ["Nur Dies"]

    def test_empty_list_in_config_disables_topic_filter(self):
        config = {"search_groups": {GROUP_A: {"filters": {"include_terms": []}}}}
        merged = apply_default_topic_filters(config)
        assert merged["search_groups"][GROUP_A]["filters"]["include_terms"] == []

    def test_other_filters_are_preserved(self):
        config = {"search_groups": {GROUP_B: {"filters": {"exclude_terms": ["ANÜ"]}}}}
        filters = apply_default_topic_filters(config)["search_groups"][GROUP_B]["filters"]
        assert filters["exclude_terms"] == ["ANÜ"]
        assert "Sophos" in filters["include_terms"]

    def test_unknown_group_gets_no_terms(self):
        config = {"search_groups": {"andere_gruppe": {"display_name": "X"}}}
        merged = apply_default_topic_filters(config)
        assert "filters" not in merged["search_groups"]["andere_gruppe"]

    def test_missing_defaults_file_is_tolerated(self, tmp_path):
        config = {"search_groups": {GROUP_A: {}}}
        merged = apply_default_topic_filters(config, tmp_path / "fehlt.yaml")
        assert "filters" not in merged["search_groups"][GROUP_A]


# ══════════════════════════════════════════════════════════════════════════════
# 3 — Zuordnung der Beispielprojekte
# ══════════════════════════════════════════════════════════════════════════════

class TestSampleClassification:

    @pytest.mark.parametrize(
        "slug,expected,title,description,tags",
        SAMPLE_PROJECTS,
        ids=[p[0] for p in SAMPLE_PROJECTS],
    )
    def test_project_is_assigned_to_expected_groups(
        self, engines, slug, expected, title, description, tags
    ):
        assert _groups_for(engines, title, description, tags) == expected

    def test_distribution(self, engines):
        counts = {"nur_a": 0, "nur_b": 0, "beide": 0, "keine": 0}
        for _slug, _expected, title, description, tags in SAMPLE_PROJECTS:
            groups = _groups_for(engines, title, description, tags)
            if groups == {GROUP_A, GROUP_B}:
                counts["beide"] += 1
            elif groups == {GROUP_A}:
                counts["nur_a"] += 1
            elif groups == {GROUP_B}:
                counts["nur_b"] += 1
            else:
                counts["keine"] += 1
        assert counts == {"nur_a": 4, "nur_b": 4, "beide": 2, "keine": 6}


# ══════════════════════════════════════════════════════════════════════════════
# 4 — Ende-zu-Ende über die produktive Pipeline
# ══════════════════════════════════════════════════════════════════════════════

def _fake_parse(url: str) -> dict:
    _groups, title, description, tags = SAMPLE_BY_URL[url]
    return {"schema": {"title": title, "url": url, "description": description,
                       "schlagworte": tags}}


def _fake_render(schema: dict, _meta: dict) -> str:
    return (
        f"---\ntitle: \"{schema['title']}\"\nurl: {schema['url']}\n---\n\n"
        f"# {schema['title']}\n\n{schema['description']}\n"
    )


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text[3:text.index("---", 3)])


def _run_pipeline(output_dir: str) -> dict:
    """Ein Lauf beider Gruppen über denselben simulierten Feed (wie produktiv)."""
    from email_agent import run_rss_ingestion_for_search_groups

    config = yaml.safe_load(PATCH_CONFIG_PATH.read_text(encoding="utf-8"))
    config.setdefault("providers", {"freelancermap": {"enabled": True, "channels": {"rss": {}}}})
    entries = [{"link": url, "title": v[1]} for url, v in SAMPLE_BY_URL.items()]

    with (
        patch("email_agent.EmailAgent.fetch_rss_feed", return_value=entries),
        patch("email_agent.EmailAgent.load_adapter") as mock_load_adapter,
        patch("email_agent.MarkdownRenderer") as mock_renderer_cls,
    ):
        adapter = MagicMock()
        adapter.parse.side_effect = _fake_parse
        adapter.get_provider_name.return_value = "freelancermap"
        mock_load_adapter.return_value = adapter
        mock_renderer_cls.return_value.render.side_effect = _fake_render

        return run_rss_ingestion_for_search_groups(
            config, output_dir=output_dir, dry_run=False,
            group_ids=[GROUP_A, GROUP_B],
        )


@pytest.fixture
def pipeline_run(tmp_path):
    out = tmp_path / "projects"
    out.mkdir()
    summary = _run_pipeline(str(out))
    return out, summary


def _files_by_url(out: Path) -> dict:
    result = {}
    for md in out.glob("*.md"):
        fm = _frontmatter(md)
        for src in fm.get("sources", []):
            result.setdefault(src["url"], []).append((md, fm))
    return result


class TestPipelineEndToEnd:

    def test_summary_counts(self, pipeline_run):
        _out, summary = pipeline_run
        n = len(SAMPLE_PROJECTS)
        # Jede Gruppe liest denselben Feed mit 16 Einträgen
        assert summary["total_entries_found"] == 2 * n
        assert summary["total_errors"] == 0
        # 10 fachlich passende Projekte → 10 Dateien
        assert summary["total_projects_saved"] == 10
        # Gruppe A lehnt 10 ab (4 nur-B + 6 fachfremd), Gruppe B ebenfalls 10
        assert summary["group_summaries"][GROUP_A]["projects_filtered"] == 10
        assert summary["group_summaries"][GROUP_B]["projects_filtered"] == 10
        assert summary["total_projects_filtered"] == 20
        # Die 2 gruppenübergreifenden Projekte werden bei B nur ergänzt
        assert summary["group_summaries"][GROUP_A]["projects_saved"] == 6
        assert summary["group_summaries"][GROUP_B]["projects_saved"] == 4
        assert summary["total_urls_skipped_dedupe"] == 2

    def test_off_topic_projects_create_no_file(self, pipeline_run):
        out, _summary = pipeline_run
        files = _files_by_url(out)
        for url, (expected, title, _d, _t) in SAMPLE_BY_URL.items():
            if not expected:
                assert url not in files, f"Fachfremdes Projekt gespeichert: {title}"
        assert len(list(out.glob("*.md"))) == 10

    def test_each_project_has_exactly_its_groups_and_one_file(self, pipeline_run):
        out, _summary = pipeline_run
        files = _files_by_url(out)
        for url, (expected, title, _d, _t) in SAMPLE_BY_URL.items():
            if not expected:
                continue
            assert len(files[url]) == 1, f"Dublette für: {title}"
            _md, fm = files[url][0]
            assert set(fm["search_groups"]) == expected, title

    def test_rejections_are_logged_per_group(self, pipeline_run):
        out, _summary = pipeline_run
        lines = [
            json.loads(line)
            for line in (out / "filter_rejected.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert len(lines) == 20
        for entry in lines:
            assert entry["filter_result"]["failed_criteria"] == ["include_terms"]
        rejected = {(e["search_group_id"], e["provider_url"]) for e in lines}
        for url, (expected, _title, _d, _t) in SAMPLE_BY_URL.items():
            for gid in (GROUP_A, GROUP_B):
                assert ((gid, url) in rejected) == (gid not in expected)

    def test_all_four_profiles_scored_regardless_of_group(self, pipeline_run):
        """Auch ein reines Gruppe-B-Projekt wird gegen alle vier Profile bewertet."""
        out, _summary = pipeline_run
        files = _files_by_url(out)
        for url, (expected, title, _d, _t) in SAMPLE_BY_URL.items():
            if not expected:
                continue
            _md, fm = files[url][0]
            scored = {p["profile_id"] for p in fm["pre_scores"]["profiles"]}
            assert scored == set(PROFILE_IDS), title

    def test_second_run_creates_no_duplicates(self, pipeline_run):
        out, _first = pipeline_run
        log_before = (out / "filter_rejected.jsonl").read_text(encoding="utf-8")
        groups_before = {
            md.name: _frontmatter(md)["search_groups"] for md in out.glob("*.md")
        }

        second = _run_pipeline(str(out))

        assert second["total_projects_saved"] == 0
        assert second["total_errors"] == 0
        assert second["total_urls_skipped_dedupe"] == 12   # 6 bei A + 6 bei B
        assert second["total_projects_filtered"] == 20
        assert len(list(out.glob("*.md"))) == 10
        assert {
            md.name: _frontmatter(md)["search_groups"] for md in out.glob("*.md")
        } == groups_before
        # Ablehnungen werden nicht bei jedem Lauf erneut protokolliert
        assert (out / "filter_rejected.jsonl").read_text(encoding="utf-8") == log_before
