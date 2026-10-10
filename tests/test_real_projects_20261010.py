"""
Regressionstests mit realen Ausschreibungen vom 10.10.2026 (DE/AT/CH).

Grundlage sind die 15 an diesem Tag neu erfassten FreelancerMap-Projekte im
Wortlaut (tests/fixtures/real_projects_20261010.json, ohne Ansprechpartner)
sowie sieben früher erfasste, fachlich passende Projekte als Gegenprobe.

Geprüft wird:
  1. Entwicklerrollen mit zwingenden, nicht belegten Technologien bleiben
     unter der Schwelle "Prüfen" — Power BI, CRM und Prozessautomatisierung
     sind davon nicht pauschal betroffen.
  2. Nice-to-have zählt schwach, der Senior-Bonus nur bei fachlichem Treffer,
     eine beiläufige Einzelerwähnung ergibt keine mittlere Eignung.
  3. Das Kernthema im Titel zählt mehr; "PowerBI Experte" wird neu bewertet,
     aber wegen nicht bestätigter Kenntnis nicht auf 50 angehoben.
  4. Schwache Einzelbegriffe erfassen nur im Titel oder im Zusammenhang;
     geeignete Infrastruktur- und CRM-Projekte bleiben erfasst.
  5. Rahmenbedingungen: Vertragsart, Auslastung, hybrid/remote, Start/Ende.

Kein Netzwerkzugriff, keine LLM-Aufrufe.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from filter_engine import FilterConfig, FilterEngine
from search_group_config import load_default_topic_filters
from suitability_scorer import (
    DECISION_HIGH, DECISION_LOW, DECISION_MEDIUM,
    RECOMMEND_APPLY, RECOMMEND_REVIEW, RECOMMEND_SKIP,
    STATUS_NOT_CHECKED, STATUS_NOT_OK, STATUS_OK, STATUS_PARTIAL, STATUS_UNKNOWN,
    SuitabilityScorer, _split_sections,
)

TODAY = date(2026, 10, 10)
A, B = "automation_bi", "infra_security"
FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "real_projects_20261010.json").read_text("utf-8"))
IMPORTED = FIXTURES["imported"]
REFERENCE = FIXTURES["reference"]


def _data(entry: dict) -> dict:
    return {"title": entry["title"], "description": entry["description"]}


@pytest.fixture(scope="module")
def scorer() -> SuitabilityScorer:
    return SuitabilityScorer(today=TODAY)


@pytest.fixture(scope="module")
def engines() -> dict:
    return {
        group_id: FilterEngine(FilterConfig.from_dict(cfg))
        for group_id, cfg in load_default_topic_filters().items()
    }


@pytest.fixture(scope="module")
def results(scorer) -> dict:
    return {k: scorer.score_project(_data(v)) for k, v in {**IMPORTED, **REFERENCE}.items()}


def _groups(engines: dict, entry: dict) -> set:
    return {g for g, e in engines.items() if e.apply(_data(entry), g).passed}


# Stand vor dieser Änderung: (Suchgruppen, Eignung, Empfehlung) — wie in den
# Projektdateien vom 10.10.2026 gespeichert.
BEFORE = {
    "lotus_excel_entwickler": ({A, B}, 10, RECOMMEND_SKIP),
    "pilotkunden_werbung": ({A}, 15, RECOMMEND_SKIP),
    "powerbi_experte": ({A}, 25, RECOMMEND_REVIEW),
    "angular_java_fullstack": ({A}, 33, RECOMMEND_REVIEW),
    "business_analyst_finance": ({A}, 10, RECOMMEND_SKIP),
    "ai_platform_engineer": ({A}, 0, RECOMMEND_SKIP),
    "business_analyst_ai_sdlc": ({B}, 18, RECOMMEND_SKIP),
    "mainframe_developer": ({B}, 25, RECOMMEND_REVIEW),
    "servicenow_developer": ({A}, 0, RECOMMEND_SKIP),
    "data_scientist_plattform": ({B}, 0, RECOMMEND_SKIP),
    "dynamics_entwickler_crm": ({A}, 15, RECOMMEND_SKIP),
    "data_engineer_snowflake": ({B}, 0, RECOMMEND_SKIP),
    "data_scientist_azure_snowflake": ({B}, 0, RECOMMEND_SKIP),
    "dynamics_entwickler_27611": ({A}, 15, RECOMMEND_SKIP),
    "pc_rollout_field_service": ({A, B}, 15, RECOMMEND_SKIP),
}

# Stand nach dieser Änderung: (Suchgruppen, fachliche Eignung, Gesamtwert, Empfehlung)
AFTER = {
    "lotus_excel_entwickler": (set(), 0, 0, RECOMMEND_SKIP),
    "pilotkunden_werbung": ({A}, 15, 0, RECOMMEND_SKIP),
    "powerbi_experte": ({A}, 49, 49, RECOMMEND_REVIEW),
    "angular_java_fullstack": (set(), 20, 20, RECOMMEND_SKIP),
    "business_analyst_finance": (set(), 0, 0, RECOMMEND_SKIP),
    "ai_platform_engineer": (set(), 0, 0, RECOMMEND_SKIP),
    "business_analyst_ai_sdlc": (set(), 2, 2, RECOMMEND_SKIP),
    "mainframe_developer": (set(), 4, 0, RECOMMEND_SKIP),
    "servicenow_developer": (set(), 0, 0, RECOMMEND_SKIP),
    "data_scientist_plattform": (set(), 0, 0, RECOMMEND_SKIP),
    "dynamics_entwickler_crm": ({A}, 20, 20, RECOMMEND_SKIP),
    "data_engineer_snowflake": (set(), 0, 0, RECOMMEND_SKIP),
    "data_scientist_azure_snowflake": (set(), 0, 0, RECOMMEND_SKIP),
    "dynamics_entwickler_27611": ({A}, 15, 15, RECOMMEND_SKIP),
    "pc_rollout_field_service": (set(), 0, 0, RECOMMEND_SKIP),
}


class TestAllFifteenProjects:

    def test_fixture_is_complete(self):
        assert set(IMPORTED) == set(BEFORE) == set(AFTER) and len(IMPORTED) == 15
        assert {v["feed"] for v in IMPORTED.values()} == {"DE", "AT", "CH"}

    @pytest.mark.parametrize("key", sorted(AFTER))
    def test_capture_and_suitability(self, key, engines, results):
        groups, technical, score, recommendation = AFTER[key]
        r = results[key]
        assert _groups(engines, IMPORTED[key]) == groups
        assert (r.technical_score, r.score, r.recommendation) == (
            technical, score, recommendation)

    def test_only_power_bi_remains_worth_a_look(self, results):
        review = {k for k in IMPORTED if results[k].recommendation != RECOMMEND_SKIP}
        assert review == {"powerbi_experte"}

    def test_capture_drops_from_fifteen_to_four(self, engines):
        captured = {k for k, v in IMPORTED.items() if _groups(engines, v)}
        assert captured == {
            "pilotkunden_werbung", "powerbi_experte",
            "dynamics_entwickler_crm", "dynamics_entwickler_27611",
        }
        assert sum(1 for groups, _s, _r in BEFORE.values() if groups) == 15


# ══════════════════════════════════════════════════════════════════════════════
# 1 — Entwickler-Spezialisierungen
# ══════════════════════════════════════════════════════════════════════════════

class TestDeveloperRoles:

    @pytest.mark.parametrize("key, missing", [
        ("angular_java_fullstack", {"Angular", "Java", "Microservices", "Camunda"}),
        ("mainframe_developer", {"Cobol", "CICS", "JCL", "ADABAS"}),
        ("servicenow_developer", {"JavaScript", "ServiceNow-Entwicklung"}),
        ("lotus_excel_entwickler", {"VBA", "Lotus Notes"}),
        ("dynamics_entwickler_crm", {"Plugin*", "Erfahrung in der Entwicklung"}),
        ("dynamics_entwickler_27611", {"Plugin*", "Erfahrung in der Entwicklung"}),
    ])
    def test_stays_below_review_and_names_missing_must_haves(self, key, missing, results):
        r = results[key]
        assert r.technical_score < 25 and r.decision == DECISION_LOW
        assert r.recommendation == RECOMMEND_SKIP
        assert missing <= set(r.missing_requirements)

    def test_angular_java_was_review_before(self, results):
        assert BEFORE["angular_java_fullstack"][1:] == (33, RECOMMEND_REVIEW)
        r = results["angular_java_fullstack"]
        assert r.technical_score == 20
        assert any("Reine Softwareentwicklerrolle" in x and "auf 20 begrenzt" in x
                   for x in r.reasons)

    def test_dynamics_developer_is_capped_despite_crm_in_title(self, results):
        r = results["dynamics_entwickler_crm"]
        assert any("Kernthema im Titel" in x and "CRM" in x for x in r.reasons)
        assert any("Reine Softwareentwicklerrolle" in x for x in r.reasons)
        assert r.technical_score == 20

    @pytest.mark.parametrize("key", [
        "data_scientist_plattform", "data_engineer_snowflake", "data_scientist_azure_snowflake"])
    def test_pure_data_engineering_roles(self, key, engines, results):
        r = results[key]
        assert _groups(engines, IMPORTED[key]) == set()      # "Entra ID" allein erfasst nicht
        assert r.recommendation == RECOMMEND_SKIP
        assert {"Snowflake", "Python"} <= set(r.missing_requirements)
        assert any("Data-Engineering" in x for x in r.reasons)

    # Ausdrücklich nicht pauschal ungeeignet ───────────────────────────────────

    def test_power_bi_developer_is_not_capped(self, scorer):
        r = scorer.score_project({
            "title": "Power BI Entwickler (m/w/d)",
            "description": (
                "Berichte und Dashboards in Power BI. Freiberuflich, remote.\n"
                "Anforderungen:\n"
                "- Erfahrung in der Entwicklung von Berichten mit Power BI\n"
                "- DAX und Power Query"
            ),
        })
        assert r.decision == DECISION_HIGH and r.recommendation == RECOMMEND_APPLY
        assert not any("Softwareentwicklerrolle" in x for x in r.reasons)

    def test_power_bi_developer_with_one_script_language_is_not_capped(self, scorer):
        r = scorer.score_project({
            "title": "Power BI Developer (m/w/d)",
            "description": (
                "Reporting mit Power BI, DAX und Power Query. Remote.\n"
                "Anforderungen:\n- Power BI, DAX\n- Grundkenntnisse in Python"
            ),
        })
        assert r.decision == DECISION_HIGH
        assert not any("Softwareentwicklerrolle" in x and "begrenzt" in x for x in r.reasons)

    def test_crm_integration_is_not_a_developer_role(self, scorer):
        r = scorer.score_project({
            "title": "CRM-Berater Salesforce-Integration (m/w/d)",
            "description": (
                "Anbindung von Salesforce an das ERP über Make.com, Beratung des "
                "Vertriebs. Freelance, remote.\n"
                "Anforderungen:\n- Erfahrung mit Salesforce und API-Integration\n"
                "- Erfahrung mit Make.com oder Zapier"
            ),
        })
        assert r.decision == DECISION_HIGH and r.recommendation == RECOMMEND_APPLY
        assert r.missing_requirements == []

    def test_process_automation_developer_title_needs_two_unproven_requirements(self, scorer):
        base = {
            "title": "Entwickler Prozessautomatisierung Make.com (m/w/d)",
            "description": (
                "Aufbau von Workflows in Make.com und Zapier, Prozessautomatisierung im "
                "Vertrieb. Remote.\nAnforderungen:\n- Erfahrung mit Make.com\n{extra}"
            ),
        }
        one = scorer.score_project(dict(
            base, description=base["description"].format(extra="- JavaScript für eigene Module")))
        two = scorer.score_project(dict(
            base, description=base["description"].format(
                extra="- JavaScript für eigene Module\n- Mehrjährige Erfahrung mit Node.js")))
        assert one.decision == DECISION_HIGH
        assert two.technical_score == 20 and two.recommendation == RECOMMEND_SKIP

    def test_programming_language_as_nice_to_have_does_not_cap(self, scorer):
        r = scorer.score_project({
            "title": "Entwickler SharePoint Online / Microsoft 365 (m/w/d)",
            "description": (
                "Aufbau von SharePoint-Seiten und Power Automate Flows in Microsoft 365.\n"
                "Anforderungen:\n- SharePoint Online\n"
                "Nice-to-have:\n- TypeScript\n- React"
            ),
        })
        assert not any("Softwareentwicklerrolle" in x for x in r.reasons)
        assert r.missing_requirements == []

    def test_infrastructure_consulting_is_not_a_developer_role(self, results):
        for key in ("infra_architect_remote", "infra_security_architect",
                    "technischer_projektleiter", "firewall_admin_interim"):
            assert not any("Softwareentwicklerrolle" in x for x in results[key].reasons), key


# ══════════════════════════════════════════════════════════════════════════════
# 2 — Bewertung: Nice-to-have, Senior-Bonus, Einzelerwähnung
# ══════════════════════════════════════════════════════════════════════════════

class TestWeighting:

    def test_mainframe_infrastructure_is_only_nice_to_have(self, results):
        r = results["mainframe_developer"]
        assert BEFORE["mainframe_developer"][1:] == (25, RECOMMEND_REVIEW)
        assert any("nur Nice-to-have" in x and "IT-Infrastruktur*" in x and "(+4)" in x
                   for x in r.reasons)
        assert r.technical_score == 4

    def test_nice_to_have_sections_and_lines_are_detected(self):
        sections = _split_sections(
            "Aufgaben:\n- Betrieb der Firewalls\n"
            "Ihr Profil:\n- Erfahrung mit Sophos\n- Kenntnisse in VMware von Vorteil\n"
            "Nice-To-Have:\n- Erfahrung mit IT-Infrastrukturen\n"
            "Rahmendaten:\n- Start: sofort"
        )
        assert "Sophos" in sections.must and "VMware" not in sections.must
        assert "VMware" in sections.nice and "IT-Infrastrukturen" in sections.nice
        assert "IT-Infrastrukturen" not in sections.main and "Firewalls" in sections.main
        assert "Start: sofort" in sections.main and "Start" not in sections.nice

    def test_firm_requirement_with_idealerweise_stays_a_requirement(self):
        sections = _split_sections(
            "Anforderungen:\n- Erfahrung mit Firewalls, idealerweise Sophos")
        assert "Sophos" in sections.must and sections.nice == ""

    def test_requirement_headings_of_the_real_postings_are_detected(self):
        for key, term in [
            ("mainframe_developer", "Cobol"),             # "Ihre Qualifikationen:"
            ("data_engineer_snowflake", "Snowflake"),     # "**Ihre Kenntnisse:**"
            ("servicenow_developer", "JavaScript"),       # "Must-Have-Skills:"
            ("data_scientist_plattform", "Snowflake"),    # "**Anforderungen (Muss-Kriterien)** • …"
            ("powerbi_experte", "PowerBI Premium"),       # "… mit folgenden Skillset:"
            ("dynamics_entwickler_27611", "Plugin"),      # "**Anforderungen** :"
        ]:
            assert term in _split_sections(IMPORTED[key]["description"]).must, key

    def test_senior_bonus_needs_a_topical_match(self, scorer, results):
        for key in ("lotus_excel_entwickler", "business_analyst_finance"):
            r = results[key]
            assert BEFORE[key][1] == 10                    # vorher 10 Punkte nur für die Rolle
            assert r.technical_score == 0
            assert any("ohne fachliche Übereinstimmung — kein Bonus" in x for x in r.reasons)
        with_match = scorer.score_project({
            "title": "Senior Berater Sophos Firewall (m/w/d)", "description": "Remote."})
        assert any("Senior-/Beratungsrolle" in x and "(+10)" in x for x in with_match.reasons)

    def test_single_incidental_mention_cannot_reach_medium(self, scorer):
        r = scorer.score_project({
            "title": "Senior Consultant Organisationsentwicklung (m/w/d)",
            "description": "Begleitung der Reorganisation; Ablage der Unterlagen in SharePoint.",
        })
        # 15 (SharePoint) + 10 (Senior) = 25 → auf 24 begrenzt
        assert r.technical_score == 24 and r.decision == DECISION_LOW
        assert any("Nur ein fachlicher Treffer" in x for x in r.reasons)

    def test_single_topic_in_title_is_not_incidental(self, scorer):
        r = scorer.score_project({
            "title": "Senior Consultant SharePoint (m/w/d)",
            "description": "Begleitung der Einführung. Remote.",
        })
        assert r.technical_score == 40 and r.decision == DECISION_MEDIUM
        assert not any("Nur ein fachlicher Treffer" in x for x in r.reasons)

    def test_low_priority_role_in_title_counts_across_groups(self, results):
        r = results["pc_rollout_field_service"]
        assert r.best_group == A                            # wegen "Microsoft 365"
        assert any("Rolle niedriger Priorität im Titel" in x for x in r.reasons)
        assert r.technical_score == 0


# ══════════════════════════════════════════════════════════════════════════════
# 3 — Titelgewichtung und "PowerBI Experte"
# ══════════════════════════════════════════════════════════════════════════════

class TestTitleWeightAndPowerBi:

    def test_power_bi_expert_is_reassessed(self, results):
        r = results["powerbi_experte"]
        assert BEFORE["powerbi_experte"][1] == 25
        assert r.reasons[:4] == [
            "Hohe Priorität [automation_bi]: Power BI (+15)",
            "Kernthema im Titel [automation_bi]: Power BI (+15)",
            "Senior-/Beratungsrolle: Beratung (+10)",
            "Kompetenzbonus Power BI mit nachgewiesener Praxis (DAX, Power Query, Reporting): "
            "Reporting, Reports, Datenanalyse* (+10)",
        ]

    def test_power_bi_expert_is_not_lifted_to_50_while_premium_is_unconfirmed(self, results):
        r = results["powerbi_experte"]
        assert r.technical_score == 49 and r.decision == DECISION_MEDIUM
        assert r.recommendation == RECOMMEND_REVIEW
        assert r.missing_requirements == ["Power BI Premium"]
        assert any("Power BI Premium" in x and "bleibt unter 50" in x for x in r.reasons)

    def test_same_posting_without_premium_reaches_high(self, scorer):
        entry = dict(IMPORTED["powerbi_experte"])
        entry["description"] = entry["description"].replace(
            "Expertise in PowerBI Premium und PowerBI Reporting System",
            "Expertise im PowerBI Reporting System")
        r = scorer.score_project(_data(entry))
        assert r.technical_score == 50 and r.decision == DECISION_HIGH
        assert r.missing_requirements == []

    def test_premium_as_nice_to_have_does_not_hold_back(self, scorer):
        r = scorer.score_project({
            "title": "Power BI Berater (m/w/d)",
            "description": (
                "Reporting und Dashboards in Power BI mit DAX. Remote, freiberuflich.\n"
                "Nice-to-have:\n- Power BI Premium"
            ),
        })
        assert r.decision == DECISION_HIGH and r.missing_requirements == []

    def test_title_bonus_is_given_once(self, scorer):
        r = scorer.score_project({
            "title": "Power BI / SharePoint / CRM Berater (m/w/d)",
            "description": "Remote.",
        })
        assert len([x for x in r.reasons if x.startswith("Kernthema im Titel")]) == 1

    def test_title_bonus_does_not_lift_a_specialist_cap(self, scorer):
        r = scorer.score_project({
            "title": "Senior Data Engineer Power BI / Databricks (m/w/d)",
            "description": (
                "Berichte in Power BI mit DAX und Power Query. Remote.\n"
                "Must-Have\n- Mehrjährige Erfahrung mit Databricks und PySpark"
            ),
        })
        assert any(x.startswith("Kernthema im Titel") for x in r.reasons)
        assert r.technical_score == 40 and r.recommendation == RECOMMEND_REVIEW


# ══════════════════════════════════════════════════════════════════════════════
# 4 — Erfassung
# ══════════════════════════════════════════════════════════════════════════════

def _passes(engines: dict, group: str, title: str, description: str = "") -> bool:
    return engines[group].apply({"title": title, "description": description}, group).passed


class TestCapture:

    @pytest.mark.parametrize("key, group, term", [
        ("lotus_excel_entwickler", A, "Systemintegration"),
        ("angular_java_fullstack", A, "API-Integration*"),
        ("business_analyst_finance", A, "ChatGPT"),
        ("ai_platform_engineer", A, "GenAI"),
        ("servicenow_developer", A, "KI-Agent*"),
        ("business_analyst_ai_sdlc", B, "Informationssicherheit*"),
        ("mainframe_developer", B, "IT-Infrastruktur*"),
        ("data_engineer_snowflake", B, "Entra ID"),
        ("pc_rollout_field_service", B, "Microsoft 365"),
    ])
    def test_single_weak_term_no_longer_captures(self, key, group, term, engines):
        check = engines[group].apply(_data(IMPORTED[key]), group).checks[0]
        assert not check.passed and check.value_found == [term]
        assert "Nur beiläufig erwähnt" in check.reason

    @pytest.mark.parametrize("group, title", [
        (A, "Microsoft 365 Consultant (m/w/d)"),
        (B, "Microsoft 365 Administrator (m/w/d)"),
        (A, "Berater API-Integration (m/w/d)"),
        (A, "Entwicklung eines KI-Agenten für den Kundenservice"),
        (B, "Senior Berater IT-Infrastruktur (m/w/d)"),
        (B, "IT Infrastructure Architect"),
        (B, "Entra ID Migration (m/w/d)"),
        (B, "IT-Projektleiter (m/w/d)"),
        (B, "IT Projektleiter :in"),
        (B, "Technischer Projektleiter Rechenzentrum (m/w/d)"),
        (B, "Interim IT Manager (m/w/d)"),
        (B, "Interim CIO für Mittelständler"),
        (B, "Projektleiter Infrastrukturprojekte (m/w/d)"),
        (B, "IT-Leiter (m/w/d) interim"),
    ])
    def test_weak_or_role_term_in_title_captures(self, group, title, engines):
        assert _passes(engines, group, title, "Details auf Anfrage.")

    @pytest.mark.parametrize("group, description", [
        (B, "Modernisierung der IT-Infrastruktur an drei Standorten, Betrieb in Microsoft 365."),
        (B, "Erfahrung mit IT-Infrastrukturen und Entra ID."),
        (A, "API-Integration zwischen Shop und ERP, Auswertung mit ChatGPT."),
        (A, "Einführung von Microsoft 365 und Aufbau einer Systemintegration."),
        (B, "IT-Projektleitung für die Ablösung der IT-Infrastruktur."),
    ])
    def test_two_weak_terms_in_context_capture(self, group, description, engines):
        assert _passes(engines, group, "Berater (m/w/d)", description)

    @pytest.mark.parametrize("group, description", [
        (B, "Modernisierung der Firewalls an drei Standorten."),
        (B, "Aufbau eines VPN zwischen den Standorten."),
        (A, "Einführung eines CRM im Vertrieb."),
        (A, "Automatisierung mit Make.com."),
        (A, "Berichte in Power BI."),
        (A, "Migration nach SharePoint."),
    ])
    def test_one_strong_term_still_captures(self, group, description, engines):
        assert _passes(engines, group, "Berater (m/w/d)", description)

    def test_no_global_exclusion_of_m365_api_integration_or_it_infrastructure(self):
        filters = load_default_topic_filters()
        for group, terms in [
            (A, {"Microsoft 365", "M365", "API-Integration*"}),
            (B, {"Microsoft 365", "M365", "IT-Infrastruktur*"}),
        ]:
            assert terms <= set(filters[group]["weak_include_terms"])
            assert not terms & set(filters[group]["include_terms"])

    def test_groups_a_and_b_are_kept(self):
        assert set(load_default_topic_filters()) == {A, B}

    def test_project_lead_in_running_text_alone_does_not_capture(self, engines):
        assert not _passes(
            engines, B, "Bauingenieur (m/w/d)",
            "Abstimmung mit der IT-Projektleitung des Auftraggebers.")

    @pytest.mark.parametrize("key, groups", [
        ("infra_architect_remote", {B}),
        ("infra_security_architect", {B}),
        ("technischer_projektleiter", {B}),
        ("firewall_admin_interim", {B}),
        ("it_administrator_m365", {A, B}),
        ("n8n_workflow", {A}),
        ("netsec_consultant", {B}),
    ])
    def test_suitable_real_projects_stay_captured(self, key, groups, engines):
        assert _groups(engines, REFERENCE[key]) == groups

    @pytest.mark.parametrize("key, minimum, recommendation", [
        ("infra_architect_remote", 50, RECOMMEND_APPLY),
        ("infra_security_architect", 50, RECOMMEND_APPLY),
        ("technischer_projektleiter", 50, RECOMMEND_APPLY),
        ("firewall_admin_interim", 50, RECOMMEND_APPLY),
        ("n8n_workflow", 25, RECOMMEND_REVIEW),
        ("netsec_consultant", 40, RECOMMEND_REVIEW),
    ])
    def test_suitable_real_projects_are_not_downgraded(self, key, minimum, recommendation, results):
        r = results[key]
        assert r.technical_score >= minimum and r.recommendation == recommendation

    @pytest.mark.parametrize("title, description, group", [
        ("Senior Berater IT-Infrastruktur und Sophos (m/w/d)",
         "Standortvernetzung, Firewall-Migration auf Sophos, VLAN-Konzept. Freelance, remote.", B),
        ("Interim IT Manager (m/w/d)",
         "Leitung der IT-Transformation, Ablösung der Server und Firewalls. Freiberuflich.", B),
        ("CRM-Berater Bitrix24 (m/w/d)",
         "Einführung von Bitrix24 im Vertrieb, Anbindung per Make.com. Freelance, remote.", A),
        ("Salesforce Consultant Vertriebsprozesse (m/w/d)",
         "Beratung zu CRM-Prozessen und API-Integration. Remote, freiberuflich.", A),
    ])
    def test_infrastructure_and_crm_consulting_is_captured_and_recommended(
            self, title, description, group, engines, scorer):
        assert _passes(engines, group, title, description)
        r = scorer.score_project({"title": title, "description": description})
        assert r.decision == DECISION_HIGH and r.recommendation == RECOMMEND_APPLY


# ══════════════════════════════════════════════════════════════════════════════
# 5 — Rahmenbedingungen
# ══════════════════════════════════════════════════════════════════════════════

def _conditions(results: dict, key: str) -> dict:
    return {k: (c.status, c.value) for k, c in results[key].conditions.items()}


class TestConditionsOfRealPostings:

    def test_contract_types(self, results):
        got = {k: results[k].conditions["contract_type"].value for k in IMPORTED}
        assert got["mainframe_developer"] == "Festanstellung"
        assert got["pilotkunden_werbung"] == "Anbieterwerbung"
        assert got["data_scientist_plattform"] == "Arbeitnehmerüberlassung/Personalverleih"
        assert got["powerbi_experte"] == "Freelance"                 # "freiberuflicher"
        assert got["business_analyst_ai_sdlc"] == "Freelance"        # "Vertragsart: Freelance"
        assert got["pc_rollout_field_service"] is None               # keine Angabe
        assert results["pc_rollout_field_service"].conditions["contract_type"].status \
            == STATUS_UNKNOWN

    def test_platform_name_in_url_is_not_a_contract_type(self, scorer):
        r = scorer.score_project({
            "title": "Berater (m/w/d)",
            "description": "Siehe https://www.freelancermap.de/projekt/berater",
        })
        assert r.conditions["contract_type"].status == STATUS_UNKNOWN

    def test_permanent_position_and_advertisement_are_not_recommended(self, scorer, results):
        assert results["mainframe_developer"].conditions["contract_type"].status == STATUS_NOT_OK
        assert results["pilotkunden_werbung"].conditions["contract_type"].status == STATUS_NOT_OK
        # Auch ein fachlich hoch passender Text wird als Festanstellung nicht empfohlen.
        r = scorer.score_project({
            "title": "Senior Berater Sophos Firewall und IT-Infrastruktur (m/w/d)",
            "description": (
                "Firewall-Migration, VPN, Netzwerksegmentierung. Remote.\n"
                "Es handelt sich um eine Festanstellung mit unbefristetem Arbeitsvertrag."),
        })
        assert r.decision == DECISION_HIGH and r.recommendation == RECOMMEND_SKIP
        assert r.score == r.technical_score - 20
        assert r.recommendation_reasons[0].startswith("Vertragsart: Festanstellung")

    def test_negated_or_mixed_contract_type(self, scorer):
        negated = scorer.score_project({
            "title": "Berater (m/w/d)",
            "description": "Freelance-Projekt, keine Festanstellung."})
        mixed = scorer.score_project({
            "title": "Berater (m/w/d)",
            "description": "Möglich als Freelancer oder in Festanstellung."})
        assert negated.conditions["contract_type"].value == "Freelance"
        assert mixed.conditions["contract_type"].status == STATUS_PARTIAL

    def test_workload_percent_ranges_pensum_and_workload(self, results):
        got = {k: results[k].conditions["workload"].value for k in IMPORTED}
        assert got["powerbi_experte"] == "80–100 % (32–40 h/Woche)"       # "ab 80% bis zu 100%"
        assert got["angular_java_fullstack"] == "100 % (40 h/Woche)"      # "**Workload:**100 %"
        assert got["dynamics_entwickler_27611"] == "80 % (32 h/Woche)"    # "Pensum: 80%"
        assert got["data_scientist_plattform"] == "100 % (40 h/Woche)"    # "Pensum: 100%"
        assert got["business_analyst_finance"] == "5 Tage pro Woche (40 h/Woche)"
        assert got["business_analyst_ai_sdlc"] == "Vollzeit (40 h/Woche)"
        for key in ("powerbi_experte", "dynamics_entwickler_27611"):
            assert results[key].conditions["workload"].status == STATUS_OK

    def test_onsite_days_are_not_a_workload(self, results):
        # "2-4 Tage/Woche on-site in Wien" ist der Arbeitsort, nicht die Auslastung
        check = results["mainframe_developer"].conditions["workload"]
        assert (check.status, check.value) == (STATUS_UNKNOWN, None)

    def test_workload_percent_in_title(self, scorer):
        r = scorer.score_project({
            "title": "Senior Engineer Kafka (80-100%)", "description": "Zürich."})
        assert r.conditions["workload"].value == "80–100 % (32–40 h/Woche)"
        r = scorer.score_project({
            "title": "100% remote - Infrastructure Architect", "description": "Details folgen."})
        assert r.conditions["workload"].status == STATUS_UNKNOWN
        assert r.conditions["work_mode"].value == "100 % remote"

    def test_hybrid_is_distinguished_from_fully_remote(self, results):
        got = {k: results[k].conditions["work_mode"] for k in IMPORTED}
        hybrid = [
            "powerbi_experte",              # "Remote + vor Ort in Oberösterreich"
            "angular_java_fullstack",       # remote, einmal pro Woche Büro Budapest
            "mainframe_developer",          # 2-4 Tage on-site, 1-3 Tage remote
            "data_scientist_plattform",     # "mit Remote-Anteil"
            "dynamics_entwickler_27611",    # "Zürich und remote"
            "dynamics_entwickler_crm",      # "Arbeitsmodell: Hybrid"
        ]
        for key in hybrid:
            assert got[key].status == STATUS_PARTIAL and got[key].value.startswith("hybrid"), key
        assert got["business_analyst_finance"].value == "hybrid (60 % vor Ort)"
        # vollständig remote bleibt "erfüllt"
        assert (got["lotus_excel_entwickler"].status, got["lotus_excel_entwickler"].value) == (
            STATUS_OK, "remote")                # "Remote/On-Site: Remote" ist nur das Feld
        assert got["pc_rollout_field_service"].status == STATUS_UNKNOWN

    def test_remote_with_negated_onsite_stays_remote(self, scorer):
        r = scorer.score_project({
            "title": "Berater (m/w/d)",
            "description": "Einsatz: Remote, keine Präsenz vor Ort erforderlich."})
        assert (r.conditions["work_mode"].status, r.conditions["work_mode"].value) == (
            STATUS_OK, "remote")

    def test_start_and_end_are_kept_apart(self, results):
        got = {k: (results[k].conditions["start"].value, results[k].conditions["duration"].value)
               for k in IMPORTED}
        # "Start: 1. November 2026 Ende: 30. September 2027" — vorher Start "01.09.2027"
        assert got["dynamics_entwickler_27611"] == ("01.11.2026", "bis 30.09.2027")
        assert got["data_scientist_plattform"] == ("01.11.2026", "bis 30.04.2027")
        assert got["data_scientist_azure_snowflake"] == ("01.11.2026", "bis 30.04.2027")
        assert got["angular_java_fullstack"] == ("sofort", "bis 31.12.2027")
        assert got["powerbi_experte"] == ("sofort", "5-6 Monate")
        assert got["lotus_excel_entwickler"][0] == "26.10.2026"
        assert got["data_engineer_snowflake"][0] == "01.01.2027"
        assert got["business_analyst_finance"][0] == "sofort"     # "Zum nächstmöglichen Zeitpunkt"

    def test_duration_is_shown_but_never_scored(self, results):
        check = results["powerbi_experte"].conditions["duration"]
        assert check.status == STATUS_NOT_CHECKED
        assert not any("Laufzeit" in x for x in results["powerbi_experte"].recommendation_reasons)

    def test_unknown_stays_unknown(self, results):
        assert _conditions(results, "pc_rollout_field_service") == {
            "contract_type": (STATUS_UNKNOWN, None),
            "work_mode": (STATUS_UNKNOWN, None),
            "workload": (STATUS_UNKNOWN, None),
            "start": (STATUS_UNKNOWN, None),
            "duration": (STATUS_UNKNOWN, None),
        }
        r = results["ai_platform_engineer"]
        assert r.conditions["workload"].status == STATUS_UNKNOWN
        assert r.conditions["start"].status == STATUS_UNKNOWN
