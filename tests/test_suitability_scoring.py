"""
Tests: regelbasierte Eignungsbewertung (suitability_scorer / suitability_rules.yaml).

Vergleichstests mit den acht Projekten aus dem RSS-Lauf vom 10.10.2026
(3 aus automation_bi, 5 aus infra_security). Die Texte sind gekürzte Fassungen
der gespeicherten Ausschreibungen: Titel, Eckdaten und Anforderungen im
Wortlaut der Schlüsselbegriffe, ohne Ansprechpartner. Dazu zwei konstruierte
Beispiele (Power BI, Sophos-Infrastruktur), weil im Lauf kein solches Projekt
vorkam.

Geprüft wird:
  - Erfassung und Eignung bleiben getrennt (alle acht bleiben erfasst)
  - Pega-/SAP-Spezialistenrollen mit Muss-Kriterien werden abgelehnt
  - eine bloße Erwähnung von SAP/Pega schließt nicht aus
  - reine Vor-Ort-Supportrollen und Spezial-Cybersecurity werden niedriger,
    Power BI und Senior-Infrastruktur höher bewertet
  - n8n zählt nur als übertragbare Kompetenz

Kein Netzwerkzugriff, keine LLM-Aufrufe.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from filter_engine import FilterConfig, FilterEngine
from pre_scorer import PROFILE_IDS
from search_group_config import load_default_topic_filters
from datetime import date

from suitability_scorer import (
    DECISION_HIGH, DECISION_LOW, DECISION_MEDIUM, DECISION_REJECT,
    RECOMMEND_APPLY, RECOMMEND_REVIEW, RECOMMEND_SKIP,
    STATUS_CONFLICT, STATUS_NOT_CHECKED, STATUS_NOT_OK, STATUS_OK,
    STATUS_PARTIAL, STATUS_UNKNOWN,
    SuitabilityScorer, _must_sections,
)

TODAY = date(2026, 10, 10)   # Bezugsdatum des Laufs

REPO_ROOT = Path(__file__).parent.parent
A, B = "automation_bi", "infra_security"

# TF-IDF-Wert (bestes Profil × 100), wie er vor dieser Änderung als
# "Score" in den gespeicherten Projektdateien stand.
N8N = {
    "before": 15, "group": A,
    "title": "Festpreis: n8n-Workflow für Voice, E-Mail & sevdesk API",
    "description": (
        "Für unseren Handwerksbetrieb suchen wir einen erfahrenen Entwickler für die "
        "schlüsselfertige Umsetzung einer Prozessautomatisierung auf n8n-Basis. "
        "Werkvertrag zum verbindlichen Festpreis.\n"
        "Zielsysteme: sevdesk über die offizielle REST-API sowie die Handwerker-App "
        "Benetics per API oder Webhook. n8n auf eigenem deutschen Cloud-Server.\n"
        "Abläufe: Voice-to-Quote, eingehende Kundenanfragen per E-Mail, optional ein "
        "KI-Telefonassistent.\n"
        "Bitte Festpreisangebot, Umsetzungsdauer sowie ein bis zwei Referenzen zu "
        "n8n- und API-Projekten angeben, idealerweise mit sevdesk-Bezug."
    ),
}
PEGA = {
    "before": 13, "group": A,
    "title": "Senior System Architect für Pega in Beratung und Design",
    "description": (
        "Pega Senior System Architect (SSA) für die Beratung und Umsetzung "
        "workflowbasierter Digitalisierungslösungen.\n"
        "- Start: asap - spätestens zu November 2026\n"
        "- Dauer: 12 bis 14 Monate in Vollzeit\n- Ort: Remote\n"
        "DEINE CHALLENGE\n"
        "- Beratung zu Workflow-Management, Prozessdigitalisierung und Automatisierung "
        "auf Basis der Pega Platform\n"
        "- Durchführung von Anforderungsworkshops\n"
        "DEINE TALENTE\n\nMust-Have\n\n"
        "- Zertifizierung als Certified Pega Senior System Architect\n"
        "- Mindestens 5 Jahre praktische Erfahrung in der Workflow-Implementierung mit Pega\n"
        "Nice-To-Have\n\n"
        "- Praxis in der Schnittstellenintegration"
    ),
}
SAP = {
    "before": 10, "group": A,
    "title": "SAP FI / Winshuttle Consultant   Skriptentwicklung S/4HANA (m/w/d) in Remote",
    "description": (
        "Erfahrener SAP FI / Winshuttle Consultant zur Entwicklung von zwei "
        "Winshuttle-Skripten im SAP-S/4HANA-Umfeld.\n"
        "Ihr Profil:\n\nMUSS-Kriterien:\n\n"
        "- Fundierte praktische Erfahrung in der Entwicklung von Winshuttle-Skripten\n"
        "- Sehr gute Kenntnisse in SAP FI und SAP S/4HANA\n\n"
        "SOLL-Kriterien:\n\n"
        "- Kenntnisse im Umfeld SAP-Datenmigration und Prozessautomatisierung\n\n"
        "Rahmenparameter:\n\n- Einsatzort: Remote aus Deutschland\n"
        "- Laufzeit: Start asap - 15PT\n- Auslastung: 15 PT"
    ),
}
VULN = {
    "before": 20, "group": B,
    "title": "Senior Berater IT-Sicherheit – Technisches Risiko- und "
             "Schwachstellenmanagement (m/w/d)",
    "description": (
        "**Ort:** Remote **Zeitraum:** 19.10.2026 – 30.06.2027 **Auslastung:** 100 %\n"
        "**Aufgaben**\n"
        "- Bewertung sicherheitsrelevanter Schwachstellen in komplexen IT-Infrastrukturen\n"
        "- Begleitung technischer Modernisierungsvorhaben unter Berücksichtigung von "
        "Informationssicherheit\n"
        "**Profil**\n"
        "- Fundierte Berufserfahrung in der technischen Informationssicherheit, idealerweise "
        "mit Schwerpunkt Schwachstellenmanagement, Cyber Risk Management, Attack Surface "
        "Management oder infrastruktureller IT-Sicherheit\n"
        "- Sicheres Verständnis von Netzwerkarchitekturen, Cloud-Infrastrukturen, "
        "Serverlandschaften"
    ),
}
NETSEC = {
    "before": 32, "group": B,
    "title": "Senior Cybersecurity Consultant – Network & Infrastructure Security (m/w/d)",
    "description": (
        "**Ort:** Remote **Zeitraum:** 19.10.2026 – 30.06.2027 **Auslastung:** 100 %\n"
        "**Aufgaben**\n"
        "- Analyse bestehender IT-Infrastrukturen hinsichtlich möglicher Schwachstellen\n"
        "- Weiterentwicklung von Sicherheitslösungen in den Bereichen Firewalls, "
        "Netzwerksegmentierung, sichere Zugriffsverfahren, DNS-Security und Web-Gateways\n"
        "- Fachliche Begleitung strategischer IT-Veränderungsprojekte mit Fokus auf "
        "Sicherheitsarchitektur\n"
        "**Anforderungen**\n"
        "- Fundierte Berufserfahrung in der technischen Informationssicherheit, idealerweise "
        "mit Schwerpunkt auf Netzwerkarchitekturen\n"
        "- Praktische Erfahrung mit Firewall-Infrastrukturen und Segmentierungskonzepten\n"
        "- Kenntnisse in der Begleitung von Infrastrukturmodernisierungen oder "
        "unternehmensweiten IT-Transformationsprogrammen"
    ),
}


def _supporter(ort: str) -> dict:
    return {
        "before": 9, "group": B,
        "title": f"Supporter (m/w/d) für Windows 11 Migration nähe {ort} gesucht",
        "description": (
            "Mehrere Freelancer für eine Windows 11 Migration im Onsite-Support-Umfeld.\n"
            "- Laufzeit: 3-6 Monate mit Option auf Verlängerung\n- Auslastung: Vollzeit\n"
            f"- Einsatz: Full-Onsite, kein Remote\n- Einsatzort: Nähe {ort}\n"
            "Schwerpunkt:\n- Windows 11 Migration\n- Client-Rollout\n- IT-Onsite-Support\n"
            "- Hard- und Software Support\n- Active Directory\n"
            "- Dokumentation im Ticketsystem"
        ),
    }


REAL_PROJECTS = {
    "n8n": N8N, "pega": PEGA, "sap": SAP, "vuln": VULN, "netsec": NETSEC,
    "supporter_bad_toelz": _supporter("Bad Tölz"),
    "supporter_freiburg": _supporter("Freiburg im Breisgau"),
    "supporter_rottweil": _supporter("Rottweil"),
}

# Konstruierte Beispiele (kein Projekt aus dem Lauf)
POWER_BI = {
    "title": "Senior Power BI Consultant – Management Reporting (m/w/d)",
    "description": (
        "Aufbau von KPI-Dashboards für die Geschäftsführung, Datenmodell und Berichte "
        "auf Basis von SharePoint-Listen. Einsatz: Remote, 3 Tage pro Woche."
    ),
}
SOPHOS_INFRA = {
    "title": "Technische Projektleitung IT-Infrastrukturmodernisierung (m/w/d)",
    "description": (
        "Ablösung der bestehenden Firewalls durch Sophos, Standortvernetzung per VPN, "
        "Netzwerksegmentierung und Migration des Active Directory. Senior-Berater, "
        "überwiegend Remote."
    ),
}


@pytest.fixture(scope="module")
def scorer():
    return SuitabilityScorer(today=TODAY)


@pytest.fixture(scope="module")
def results(scorer):
    return {k: scorer.score_project(v) for k, v in REAL_PROJECTS.items()}


# ══════════════════════════════════════════════════════════════════════════════
# 1 — Erfassung bleibt von der Eignung getrennt
# ══════════════════════════════════════════════════════════════════════════════

class TestCaptureIsUnchanged:

    @pytest.mark.parametrize("key", list(REAL_PROJECTS))
    def test_all_eight_projects_are_still_captured(self, key):
        """Auch abgelehnte oder niedrig bewertete Projekte bleiben fachlich erfasst."""
        project = REAL_PROJECTS[key]
        terms = load_default_topic_filters()[project["group"]]
        engine = FilterEngine(FilterConfig.from_dict(terms))
        assert engine.apply(project, project["group"]).passed is True

    def test_rules_file_does_not_touch_capture_or_profiles(self):
        rules = yaml.safe_load((REPO_ROOT / "suitability_rules.yaml").read_text("utf-8"))
        assert set(rules) == {"suitability"}
        assert set(rules["suitability"]["groups"]) == {A, B}
        assert len(PROFILE_IDS) == 4


# ══════════════════════════════════════════════════════════════════════════════
# 2 — Vorher/Nachher der acht echten Projekte
# ══════════════════════════════════════════════════════════════════════════════

class TestRealProjectsBeforeAfter:

    def test_pega_specialist_is_rejected(self, results):
        r = results["pega"]
        assert r.decision == DECISION_REJECT
        assert r.score <= 10
        assert "Pega" in r.reject_reason

    def test_sap_scripting_is_rejected(self, results):
        r = results["sap"]
        assert r.decision == DECISION_REJECT
        assert r.score <= 10
        assert "SAP" in r.reject_reason

    @pytest.mark.parametrize(
        "key", ["supporter_bad_toelz", "supporter_freiburg", "supporter_rottweil"]
    )
    def test_onsite_support_is_low_but_not_rejected(self, results, key):
        r = results[key]
        assert r.decision == DECISION_LOW
        assert r.score < REAL_PROJECTS[key]["before"]
        assert r.reject_reason is None
        assert r.conditions["work_mode"].status == STATUS_NOT_OK
        assert any("Niedrige Priorität" in reason for reason in r.reasons)

    def test_pure_cybersecurity_role_is_capped_not_rejected(self, results):
        """Viele Infrastruktur-Schlagworte überdecken die geforderte Spezialerfahrung nicht."""
        r = results["netsec"]
        assert r.technical_score == 40
        assert r.decision == DECISION_MEDIUM
        assert r.reject_reason is None
        assert r.recommendation == RECOMMEND_REVIEW
        assert any("Cybersecurity-Spezialistenrolle" in x and "begrenzt" in x for x in r.reasons)
        # ohne Deckel wären es 79 Punkte gewesen
        assert any("(+45)" in x for x in r.reasons) and any("(+24)" in x for x in r.reasons)

    def test_vulnerability_role_stays_below_network_security(self, results):
        assert results["vuln"].technical_score < results["netsec"].technical_score
        assert results["vuln"].decision == DECISION_MEDIUM
        assert any("Schwachstellenmanagement" in reason for reason in results["vuln"].reasons)

    def test_n8n_is_only_transferable(self, results):
        r = results["n8n"]
        assert r.decision == DECISION_MEDIUM
        assert r.reject_reason is None
        transferable = [x for x in r.reasons if "übertragbare Kompetenz" in x]
        assert len(transferable) == 1 and "n8n" in transferable[0] and "(+4)" in transferable[0]

    def test_ranking_after(self, results):
        order = sorted(results, key=lambda k: results[k].technical_score, reverse=True)
        assert set(order[:3]) == {"netsec", "vuln", "n8n"}
        assert set(order[3:]) == {
            "pega", "sap", "supporter_bad_toelz", "supporter_freiburg", "supporter_rottweil",
        }

    def test_recommendations(self, results):
        got = {k: r.recommendation for k, r in results.items()}
        assert got == {
            "n8n": RECOMMEND_REVIEW, "vuln": RECOMMEND_REVIEW, "netsec": RECOMMEND_REVIEW,
            "pega": RECOMMEND_SKIP, "sap": RECOMMEND_SKIP,
            "supporter_bad_toelz": RECOMMEND_SKIP, "supporter_freiburg": RECOMMEND_SKIP,
            "supporter_rottweil": RECOMMEND_SKIP,
        }

    def test_every_result_has_reasons(self, results):
        for key, r in results.items():
            assert r.reasons, key
            assert 0 <= r.score <= 100


# ══════════════════════════════════════════════════════════════════════════════
# 3 — Power BI und passende Senior-Infrastruktur werden hoch bewertet
# ══════════════════════════════════════════════════════════════════════════════

class TestPriorityExamples:

    def test_power_bi_reporting_scores_high(self, scorer):
        r = scorer.score_project(POWER_BI)
        assert r.decision == DECISION_HIGH and r.best_group == A
        assert any("Power BI" in reason for reason in r.reasons)
        assert r.recommendation == RECOMMEND_APPLY
        assert r.conditions["workload"].status == STATUS_OK

    def test_sophos_infrastructure_project_scores_high(self, scorer):
        r = scorer.score_project(SOPHOS_INFRA)
        assert r.decision == DECISION_HIGH and r.best_group == B
        assert any("Sophos" in reason for reason in r.reasons)

    def test_priority_examples_beat_all_low_rated_real_projects(self, scorer, results):
        best_low = max(
            r.technical_score for r in results.values()
            if r.decision in (DECISION_LOW, DECISION_REJECT)
        )
        assert scorer.score_project(POWER_BI).technical_score > best_low
        assert scorer.score_project(SOPHOS_INFRA).technical_score > best_low


# ══════════════════════════════════════════════════════════════════════════════
# 4 — Kein Ausschluss wegen eines einzelnen Begriffs
# ══════════════════════════════════════════════════════════════════════════════

class TestNoBlanketExclusion:

    def test_mere_mention_of_sap_has_no_effect(self, scorer):
        base = {
            "title": "IT-Projektleiter CRM-Einführung (m/w/d)",
            "description": "Einführung von Salesforce im Vertrieb. Remote.",
        }
        with_sap = dict(base, description=base["description"]
                        + " Die Buchhaltung arbeitet mit SAP FI; eine Schnittstelle folgt später.")
        r_base, r_sap = scorer.score_project(base), scorer.score_project(with_sap)
        assert r_sap.decision != DECISION_REJECT
        assert r_sap.technical_score == r_base.technical_score

    def test_must_have_without_title_is_penalised_not_rejected(self, scorer):
        project = {
            "title": "IT-Projektleiter CRM-Einführung (m/w/d)",
            "description": (
                "Einführung von Salesforce im Vertrieb. Remote.\n"
                "Anforderungen:\n- Erfahrung mit ABAP von Vorteil für die Schnittstelle"
            ),
        }
        r = scorer.score_project(project)
        assert r.decision != DECISION_REJECT
        assert any("Muss-Abschnitt" in reason and "(-25)" in reason for reason in r.reasons)

    def test_title_mention_without_must_section_is_not_rejected(self, scorer):
        project = {
            "title": "Projektleiter Pega-Ablösung durch Power Automate (m/w/d)",
            "description": "Migration bestehender Workflows nach Microsoft 365. Remote.",
        }
        assert scorer.score_project(project).decision != DECISION_REJECT

    def test_single_low_priority_term_does_not_reject(self, scorer):
        project = {
            "title": "Senior Berater IT-Infrastruktur (m/w/d)",
            "description": "Modernisierung der Firewalls, Abstimmung mit dem SOC. Remote.",
        }
        r = scorer.score_project(project)
        assert r.decision in (DECISION_HIGH, DECISION_MEDIUM)

    def test_must_section_detection_ignores_running_text(self):
        text = "Durchführung von Anforderungsworkshops und Erhebung technischer Anforderungen."
        assert _must_sections(text) == ""
        assert "Pega" in _must_sections("Must-Have\n- Pega Zertifizierung\nNice-To-Have\n- Scrum")
        assert "Scrum" not in _must_sections("Must-Have\n- Pega\nNice-To-Have\n- Scrum")


# ══════════════════════════════════════════════════════════════════════════════
# 5 — Konfiguration: nur belegte Angaben
# ══════════════════════════════════════════════════════════════════════════════

class TestRulesConfiguration:

    def test_no_certificates_are_claimed(self):
        for name in ("suitability_rules.yaml", "search_group_filters.yaml"):
            text = (REPO_ROOT / name).read_text("utf-8")
            for cert in ("CCNA", "CCNP", "MCSE"):
                assert cert not in text, f"{cert} in {name}"

    def test_n8n_is_not_listed_as_own_skill(self):
        rules = yaml.safe_load((REPO_ROOT / "suitability_rules.yaml").read_text("utf-8"))
        group = rules["suitability"]["groups"][A]
        assert group["transferable"] == ["n8n"]
        assert "n8n" not in [t for t in group["high"] + group["medium"] if isinstance(t, str)]

    def test_stated_priorities_are_present(self):
        rules = yaml.safe_load((REPO_ROOT / "suitability_rules.yaml").read_text("utf-8"))
        groups = rules["suitability"]["groups"]
        for term in ("Power BI", "Power Query", "Datenaufbereitung", "Management Reporting",
                     "KPI-Dashboard*", "Bitrix24", "Salesforce", "Make.com", "Zapier",
                     "SharePoint"):
            assert term in groups[A]["high"], term
        assert {"term": "DAX", "with": ["Power BI", "Power Query", "DAX-Formel*",
                                        "DAX-Measure*", "Measure*"]} in groups[A]["high"]
        assert "ActiveCampaign" in groups[A]["medium"]
        for term in ("Sophos", "Firewall*", "VPN", "WAN", "Routing", "Switching"):
            assert term in groups[B]["high"], term


# ══════════════════════════════════════════════════════════════════════════════
# 6 — Pipeline: Bewertung wird gespeichert, Ablehnung als Status
# ══════════════════════════════════════════════════════════════════════════════

URLS = {f"https://www.freelancermap.de/projekt/{k}": v for k, v in REAL_PROJECTS.items()}


def _run_pipeline(output_dir: str) -> dict:
    from email_agent import run_rss_ingestion_for_search_groups

    config = yaml.safe_load((REPO_ROOT / "search_groups_patch.yaml").read_text("utf-8"))
    config.setdefault("providers", {"freelancermap": {"enabled": True, "channels": {"rss": {}}}})
    entries = [{"link": url, "title": p["title"]} for url, p in URLS.items()]

    def fake_parse(url):
        p = URLS[url]
        return {"schema": {"title": p["title"], "url": url, "description": p["description"]}}

    def fake_render(schema, _meta):
        return (f"---\ntitle: \"{schema['title']}\"\n---\n\n# {schema['title']}\n\n"
                f"{schema['description']}\n")

    with (
        patch("email_agent.EmailAgent.fetch_rss_feed",
              side_effect=lambda url, *a, **k: entries if "/de.xml" in url else []),
        patch("email_agent.EmailAgent.load_adapter") as mock_load_adapter,
        patch("email_agent.MarkdownRenderer") as mock_renderer_cls,
    ):
        adapter = MagicMock()
        adapter.parse.side_effect = fake_parse
        adapter.get_provider_name.return_value = "freelancermap"
        mock_load_adapter.return_value = adapter
        mock_renderer_cls.return_value.render.side_effect = fake_render
        return run_rss_ingestion_for_search_groups(
            config, output_dir=output_dir, dry_run=False, group_ids=[A, B],
        )


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text[3:text.index("\n---", 3)])


class TestPipelineStoresSuitability:

    @pytest.fixture
    def run(self, tmp_path):
        out = tmp_path / "projects"
        out.mkdir()
        summary = _run_pipeline(str(out))
        files = {}
        for md in out.glob("*.md"):
            fm = _frontmatter(md)
            files[fm["sources"][0]["url"].rsplit("/", 1)[1]] = (md, fm)
        return out, summary, files

    def test_all_eight_are_saved_two_marked_rejected(self, run):
        _out, summary, files = run
        assert summary["total_projects_saved"] == 8
        assert summary["total_errors"] == 0
        assert set(files) == set(REAL_PROJECTS)
        states = {k: fm["state"] for k, (_md, fm) in files.items()}
        assert states["pega"] == "rejected" and states["sap"] == "rejected"
        assert all(s == "scraped" for k, s in states.items() if k not in ("pega", "sap"))
        assert summary["group_summaries"][A]["projects_unsuitable"] == 2
        assert summary["group_summaries"][B]["projects_unsuitable"] == 0

    def test_rejection_reason_is_in_state_history(self, run):
        _out, _summary, files = run
        note = files["pega"][1]["state_history"][-1]["note"]
        assert "Eignungsbewertung" in note and "Pega" in note

    def test_suitability_and_all_four_profiles_are_stored(self, run):
        _out, _summary, files = run
        for key, (_md, fm) in files.items():
            suit = fm["suitability"]
            assert suit["technical"]["reasons"], key
            assert set(suit["conditions"]) == {"work_mode", "workload", "start"}
            assert suit["recommendation"] in (RECOMMEND_APPLY, RECOMMEND_REVIEW, RECOMMEND_SKIP)
            assert {p["profile_id"] for p in fm["pre_scores"]["profiles"]} == set(PROFILE_IDS)
        assert files["netsec"][1]["suitability"]["technical"]["decision"] == DECISION_MEDIUM

    def test_dashboard_score_line_uses_suitability(self, run):
        """Die Score-Zeile im Text (vom Dashboard gelesen) zeigt die Eignungsbewertung."""
        import re
        from run_search_groups_rss import _backfill_scores_for_new_files

        out, _summary, files = run
        assert _backfill_scores_for_new_files(str(out), 8) == 8
        for key, (md, fm) in files.items():
            match = re.search(r"- \*\*Score:\*\*\s*(\d+)/100", md.read_text("utf-8"))
            assert match and int(match.group(1)) == fm["suitability"]["score"], key

    def test_score_line_falls_back_to_tfidf_without_suitability(self, tmp_path):
        from run_search_groups_rss import _suitability_score_from_frontmatter

        assert _suitability_score_from_frontmatter("---\ntitle: x\n---\n\n# x\n") is None
        assert _suitability_score_from_frontmatter("# ohne Frontmatter\n") is None
        assert _suitability_score_from_frontmatter(
            "---\nsuitability:\n  score: 42\n---\n\n# x\n") == 42


# ══════════════════════════════════════════════════════════════════════════════
# 7 — Spezialistenrolle vs. Infrastrukturberatung mit Security-Anteil
# ══════════════════════════════════════════════════════════════════════════════

class TestSpecialistRoles:

    def test_infrastructure_consulting_with_security_share_is_not_capped(self, scorer):
        project = {
            "title": "Senior Berater IT-Infrastruktur und Security (m/w/d)",
            "description": (
                "Modernisierung der Standortvernetzung. Remote.\n"
                "Anforderungen:\n"
                "- Erfahrung mit Firewalls, VPN und Netzwerksegmentierung\n"
                "- Erstellung von Sicherheitskonzepten für die IT-Infrastruktur"
            ),
        }
        r = scorer.score_project(project)
        assert r.decision == DECISION_HIGH
        assert not any("Spezialistenrolle" in x for x in r.reasons)
        assert r.recommendation == RECOMMEND_APPLY

    def test_security_project_is_not_rejected_across_the_board(self, scorer, results):
        for key in ("netsec", "vuln"):
            assert results[key].decision != DECISION_REJECT
            assert results[key].recommendation != RECOMMEND_SKIP

    def test_neutral_title_with_two_required_specialisations_is_capped(self, scorer):
        project = {
            "title": "Senior Consultant IT-Infrastruktur (m/w/d)",
            "description": (
                "Firewalls, VPN, Netzwerksegmentierung, Standortvernetzung, Sophos. Remote.\n"
                "Anforderungen:\n"
                "- Mehrjährige Erfahrung mit Penetrationstests und Incident Response"
            ),
        }
        r = scorer.score_project(project)
        assert r.technical_score <= 40 and r.decision != DECISION_HIGH
        assert r.reject_reason is None
        assert any("Spezialistenrolle" in x for x in r.reasons)

    def test_specialist_title_without_required_experience_is_not_capped(self, scorer):
        project = {
            "title": "Projektleiter Cybersecurity-Programm: Firewall-Erneuerung (m/w/d)",
            "description": (
                "Technische Projektleitung für die Ablösung der Firewalls durch Sophos, "
                "Netzwerksegmentierung und VPN. Remote.\n"
                "Anforderungen:\n- Erfahrung in der Leitung von IT-Infrastrukturprojekten"
            ),
        }
        r = scorer.score_project(project)
        assert r.decision == DECISION_HIGH
        assert not any("Spezialistenrolle" in x for x in r.reasons)

    def test_single_specialisation_mentioned_in_tasks_only_has_no_effect(self, scorer):
        project = {
            "title": "Senior Berater IT-Infrastruktur (m/w/d)",
            "description": (
                "Aufgaben: Modernisierung der Firewalls, Abstimmung mit dem SOC und dem "
                "Team für Penetrationstests. Remote.\n"
                "Anforderungen:\n- Erfahrung mit Firewalls und VPN"
            ),
        }
        assert not any("Spezialistenrolle" in x for x in scorer.score_project(project).reasons)


# ══════════════════════════════════════════════════════════════════════════════
# 8 — Rahmenbedingungen: getrennt, "unbekannt" statt "erfüllt"
# ══════════════════════════════════════════════════════════════════════════════

BASE = {
    "title": "Senior Power BI Consultant – Management Reporting (m/w/d)",
    "description": "Aufbau von KPI-Dashboards auf Basis von SharePoint.",
}


def _with(extra: str) -> dict:
    return dict(BASE, description=BASE["description"] + "\n" + extra)


class TestConditions:

    def test_missing_information_is_unknown_not_fulfilled(self, scorer):
        r = scorer.score_project(BASE)
        assert {k: c.status for k, c in r.conditions.items()} == {
            "work_mode": STATUS_UNKNOWN, "workload": STATUS_UNKNOWN, "start": STATUS_UNKNOWN,
        }
        assert all(c.value is None for c in r.conditions.values())
        assert any("Offen (unbekannt): Arbeitsort, Auslastung, Starttermin" in x
                   for x in r.recommendation_reasons)

    def test_conditions_do_not_change_technical_score(self, scorer):
        base = scorer.score_project(BASE).technical_score
        for extra in ("Einsatz: Remote", "Einsatz: Full-Onsite, kein Remote",
                      "Auslastung: 100 %", "10 Stunden pro Woche", "Start: asap"):
            assert scorer.score_project(_with(extra)).technical_score == base, extra

    @pytest.mark.parametrize("extra,status,value", [
        ("Ort: Remote", STATUS_OK, "remote"),
        ("Einsatzort: 100% Remote", STATUS_OK, "100 % remote"),
        ("Einsatz: 80% Remote, Rest in Köln", STATUS_PARTIAL, "80 % remote"),
        ("Einsatz: hybrid in München", STATUS_PARTIAL, "hybrid"),
        ("Einsatz: Full-Onsite, kein Remote", STATUS_NOT_OK, "vollständig vor Ort"),
    ])
    def test_work_mode(self, scorer, extra, status, value):
        check = scorer.score_project(_with(extra)).conditions["work_mode"]
        assert (check.status, check.value) == (status, value)

    @pytest.mark.parametrize("extra,status,value", [
        ("Auslastung: Vollzeit", STATUS_OK, "Vollzeit (40 h/Woche)"),
        ("**Auslastung:** 100 %", STATUS_OK, "100 % (40 h/Woche)"),
        ("Auslastung: 50 %", STATUS_OK, "50 % (20 h/Woche)"),
        ("Umfang: 20-30 Stunden pro Woche", STATUS_OK, "20–30 h/Woche"),
        ("Umfang: 3 Tage pro Woche", STATUS_OK, "3 Tage pro Woche (24 h/Woche)"),
        ("Umfang: 10 Stunden pro Woche", STATUS_NOT_OK, "10 h/Woche"),
        ("Umfang: 1 Tage pro Woche", STATUS_NOT_OK, "1 Tage pro Woche (8 h/Woche)"),
        ("In Teilzeit möglich", STATUS_UNKNOWN, "Teilzeit"),
        ("Laufzeit: 15 PT", STATUS_UNKNOWN, None),
    ])
    def test_workload(self, scorer, extra, status, value):
        check = scorer.score_project(_with(extra)).conditions["workload"]
        assert (check.status, check.value) == (status, value)

    def test_violated_condition_limits_recommendation_not_technical_rating(self, scorer):
        r = scorer.score_project(_with("Einsatz: Full-Onsite, kein Remote"))
        assert r.decision == DECISION_HIGH
        assert r.recommendation == RECOMMEND_REVIEW
        assert r.score == r.technical_score - 20

    def test_no_bonus_for_fulfilled_conditions(self, scorer):
        r = scorer.score_project(_with("Ort: Remote\nAuslastung: 50 %"))
        assert r.score == r.technical_score
        assert r.recommendation == RECOMMEND_APPLY

    def test_real_projects_conditions(self, results):
        got = {
            k: (r.conditions["work_mode"].status, r.conditions["workload"].status,
                r.conditions["start"].value)
            for k, r in results.items()
        }
        assert got["n8n"] == (STATUS_UNKNOWN, STATUS_UNKNOWN, None)
        assert got["pega"] == (STATUS_OK, STATUS_OK, "sofort bis spätestens 01.11.2026")
        assert got["sap"] == (STATUS_OK, STATUS_UNKNOWN, "sofort")
        assert got["vuln"] == (STATUS_OK, STATUS_OK, "19.10.2026")
        assert got["netsec"] == (STATUS_OK, STATUS_OK, "19.10.2026")
        assert got["supporter_rottweil"][:2] == (STATUS_NOT_OK, STATUS_OK)


# ══════════════════════════════════════════════════════════════════════════════
# 9 — Starttermin und Verfügbarkeit
# ══════════════════════════════════════════════════════════════════════════════

class TestAvailability:

    def test_rules_file_has_no_availability_configured(self):
        rules = yaml.safe_load((REPO_ROOT / "suitability_rules.yaml").read_text("utf-8"))
        assert rules["suitability"]["applicant"]["available_from"] is None

    def test_without_availability_start_is_shown_but_not_checked(self, results):
        for key in ("vuln", "netsec", "pega", "sap"):
            start = results[key].conditions["start"]
            assert start.status == STATUS_NOT_CHECKED, key
            assert "nicht konfiguriert" in start.note
        assert results["n8n"].conditions["start"].status == STATUS_UNKNOWN

    def test_without_availability_there_is_no_deduction(self, scorer):
        r = scorer.score_project(_with("Ort: Remote\nStart: asap"))
        assert r.score == r.technical_score
        assert r.recommendation == RECOMMEND_APPLY
        assert any("nicht gegen eine Verfügbarkeit geprüft" in x for x in r.recommendation_reasons)

    def test_clear_conflict_limits_recommendation(self):
        scorer = SuitabilityScorer(available_from="2026-12-01", today=TODAY)
        r = scorer.score_project(_with("Ort: Remote\nZeitraum: 19.10.2026 – 30.06.2027"))
        assert r.decision == DECISION_HIGH                 # fachlich unverändert
        assert r.conditions["start"].status == STATUS_CONFLICT
        assert r.recommendation == RECOMMEND_REVIEW        # statt Bewerben
        assert r.score == r.technical_score                # kein Punktabzug

    def test_start_after_availability_is_fulfilled(self):
        scorer = SuitabilityScorer(available_from="2026-12-01", today=TODAY)
        r = scorer.score_project(_with("Ort: Remote\nStart: Januar 2027"))
        assert r.conditions["start"].status == STATUS_OK
        assert r.recommendation == RECOMMEND_APPLY

    def test_start_window_uses_latest_possible_date(self):
        scorer = SuitabilityScorer(available_from="2026-12-01", today=TODAY)
        text = "Start: ab ca. Mitte November / ab Dezember möglich"
        start = scorer.score_project(_with(text)).conditions["start"]
        assert (start.status, start.value) == (STATUS_OK, "spätestens 01.12.2026")

    def test_immediate_start_conflicts_with_later_availability(self):
        scorer = SuitabilityScorer(available_from="2026-12-01", today=TODAY)
        start = scorer.score_project(_with("Start: asap")).conditions["start"]
        assert (start.status, start.value) == (STATUS_CONFLICT, "sofort")

    def test_unknown_start_is_never_a_conflict(self):
        scorer = SuitabilityScorer(available_from="2026-12-01", today=TODAY)
        assert scorer.score_project(BASE).conditions["start"].status == STATUS_UNKNOWN

    def test_invalid_availability_is_ignored(self):
        scorer = SuitabilityScorer(available_from="irgendwann", today=TODAY)
        start = scorer.score_project(_with("Start: asap")).conditions["start"]
        assert start.status == STATUS_NOT_CHECKED

    def test_config_yaml_availability_reaches_pipeline(self, tmp_path):
        """applicant.available_from aus config.yaml wird an die Bewertung übergeben."""
        from email_agent import EmailAgent
        captured = {}

        class FakeScorer:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def score_project(self, _schema):
                raise RuntimeError("nicht nötig")

        config = yaml.safe_load((REPO_ROOT / "search_groups_patch.yaml").read_text("utf-8"))
        config["providers"] = {"freelancermap": {"enabled": True, "channels": {"rss": {}}}}
        config["applicant"] = {"available_from": "2026-12-01"}
        from search_group_config import load_search_groups
        group = load_search_groups(config)[A]
        with (
            patch("email_agent.SuitabilityScorer", FakeScorer),
            patch("email_agent.EmailAgent.load_adapter", return_value=MagicMock()),
        ):
            EmailAgent(config).process_rss_entries(
                [], {"provider_id": "freelancermap"}, str(tmp_path),
                search_group_id=A, search_group_config=group,
            )
        assert captured == {"available_from": "2026-12-01"}


# ══════════════════════════════════════════════════════════════════════════════
# 10 — Kompetenzprofile: nur belegte Angaben
# ══════════════════════════════════════════════════════════════════════════════

def _profile_keywords(name: str) -> str:
    """Profiltext ohne HTML-Kommentare (so, wie PreScorer und LLM-Prompt ihn sehen)."""
    import re
    text = (REPO_ROOT / "competency_profiles" / f"{name}.md").read_text("utf-8")
    return re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)


class TestCompetencyProfiles:

    @pytest.mark.parametrize("term", [
        "HubSpot", "Zoho", "Pipedrive", "SAP CRM", "Dynamics",
        "n8n", "Pega", "ABAP", "Winshuttle",
        "CCNA", "CCNP", "MCSE", "Cisco",
        "Tableau", "Qlik", "MuleSoft", "Boomi", "Kubernetes", "Terraform",
        "Penetration", "SIEM", "SOC",
    ])
    def test_unverified_terms_are_not_profile_keywords(self, term):
        for profile in PROFILE_IDS:
            assert term.lower() not in _profile_keywords(profile).lower(), (term, profile)

    def test_stated_experience_is_present(self):
        crm = _profile_keywords("crm_sales_automation")
        for term in ("Bitrix24", "Salesforce", "Make.com", "Zapier", "ActiveCampaign"):
            assert term in crm, term
        assert "vorkonfigurierten, individuell angepassten App-Integrationen" in crm
        infra = _profile_keywords("it_infrastructure_security")
        for term in ("Sophos", "Firewall", "VPN", "WAN", "Routing", "Switching"):
            assert term in infra, term

    def test_power_bi_stays_highly_prioritised(self):
        from pre_scorer import PreScorer
        rules = yaml.safe_load((REPO_ROOT / "suitability_rules.yaml").read_text("utf-8"))
        assert rules["suitability"]["groups"][A]["high"][0] == "Power BI"
        result = PreScorer().score_project(POWER_BI)
        assert result.best_profile == "power_bi_sharepoint"

    def test_each_profile_still_ranks_its_own_topic_first(self):
        from pre_scorer import PreScorer
        scorer = PreScorer()
        assert scorer.loaded_profile_ids == PROFILE_IDS
        samples = {
            "crm_sales_automation": "CRM-Berater Bitrix24 und Salesforce, CRM-Integration",
            "ai_business_process_integration":
                "Geschäftsprozessautomatisierung, KI-Integration, Prozessdigitalisierung",
            "it_infrastructure_security":
                "Sophos Firewall, VPN, WAN, Netzwerksegmentierung, IT-Infrastrukturmodernisierung",
            "power_bi_sharepoint": "Power BI Management Reporting, KPI-Dashboard, SharePoint",
        }
        for profile, text in samples.items():
            assert scorer.score_text(text).best_profile == profile, profile

    def test_llm_profile_summary_contains_no_comment_text(self):
        """Mehrzeilige Kommentare dürfen nicht als Keywords in den LLM-Prompt gelangen."""
        import inspect
        import llm_evaluator
        builder_cls = next(
            cls for _n, cls in inspect.getmembers(llm_evaluator, inspect.isclass)
            if hasattr(cls, "_profile_summary")
        )
        builder = object.__new__(builder_cls)
        text = (REPO_ROOT / "competency_profiles" / "crm_sales_automation.md").read_text("utf-8")
        summary = builder._profile_summary(text, max_chars=5000)
        assert "Bitrix24" in summary
        for unwanted in ("n8n", "Pega", "Nicht als Keyword", "STAND", "-->"):
            assert unwanted not in summary, unwanted


# ══════════════════════════════════════════════════════════════════════════════
# 11 — Power BI: praktische Erfahrung vs. nicht belegte Spezialisierung
# ══════════════════════════════════════════════════════════════════════════════

PBI_DEVELOPER = {
    "title": "Power BI Entwickler (m/w/d) – Berichte und Datenmodell",
    "description": (
        "Erstellung von Berichten in Power BI, Kennzahlen mit DAX, Datenaufbereitung "
        "mit Power Query. Einsatz: Remote, Auslastung: 50 %.\n"
        "Anforderungen:\n- Praktische Erfahrung mit Power BI, DAX und Power Query"
    ),
}
PBI_MANAGEMENT_REPORTING = {
    "title": "Berater Management Reporting / KPI-Dashboards (m/w/d)",
    "description": (
        "Aufbau eines Management Reportings mit KPI-Dashboards in Power BI für die "
        "Geschäftsführung. Einsatz: 100% Remote, 3 Tage pro Woche."
    ),
}
PBI_SHAREPOINT = {
    "title": "Senior Consultant Power BI & SharePoint (m/w/d)",
    "description": (
        "Reporting auf Basis von SharePoint-Listen in Microsoft 365, DAX-Formeln für "
        "Kennzahlen, Power Query für die Datenaufbereitung. Ort: Remote."
    ),
}
DATA_ENGINEER = {
    "title": "Senior Data Engineer Power BI / Databricks (m/w/d)",
    "description": (
        "Aufbau einer Datenplattform, Berichte in Power BI mit DAX und Power Query. "
        "Ort: Remote.\n"
        "Must-Have\n- Mehrjährige Erfahrung mit Databricks und PySpark\n"
        "- Data Vault Modellierung\nNice-To-Have\n- Power BI"
    ),
}


class TestPowerBi:

    @pytest.mark.parametrize(
        "project", [PBI_DEVELOPER, PBI_MANAGEMENT_REPORTING, PBI_SHAREPOINT],
        ids=["entwickler_dax_power_query", "management_reporting", "power_bi_sharepoint"],
    )
    def test_fitting_power_bi_projects_score_high_and_recommend_apply(self, scorer, project):
        r = scorer.score_project(project)
        assert r.best_group == A
        assert r.decision == DECISION_HIGH
        assert r.recommendation == RECOMMEND_APPLY
        assert r.reject_reason is None
        assert not any("Spezialistenrolle" in x for x in r.reasons)
        assert any("Power BI" in x for x in r.reasons)

    def test_dax_and_power_query_count_as_high_priority(self, scorer):
        r = scorer.score_project(PBI_DEVELOPER)
        high = next(x for x in r.reasons if x.startswith("Hohe Priorität"))
        for term in ("Power BI", "Power Query", "DAX", "Datenaufbereitung"):
            assert term in high, term

    def test_power_bi_outranks_all_eight_real_projects(self, scorer, results):
        best_real = max(r.technical_score for r in results.values())
        for project in (PBI_DEVELOPER, PBI_MANAGEMENT_REPORTING, PBI_SHAREPOINT):
            assert scorer.score_project(project).technical_score > best_real

    def test_required_power_bi_practice_is_not_treated_as_specialisation(self, scorer):
        """DAX/Power Query als Anforderung ist belegte Praxis, kein Deckel."""
        r = scorer.score_project(PBI_DEVELOPER)
        assert r.technical_score >= 50

    def test_data_engineering_specialist_is_capped_not_rejected(self, scorer):
        r = scorer.score_project(DATA_ENGINEER)
        assert r.technical_score == 40 and r.decision == DECISION_MEDIUM
        assert r.reject_reason is None
        assert r.recommendation == RECOMMEND_REVIEW
        assert any("Data-Engineering" in x and "begrenzt" in x for x in r.reasons)

    def test_single_unverified_requirement_does_not_cap(self, scorer):
        project = dict(PBI_DEVELOPER, description=PBI_DEVELOPER["description"]
                       + "\n- Zertifizierung PL-300 erforderlich")
        r = scorer.score_project(project)
        assert r.decision == DECISION_HIGH
        assert not any("Spezialistenrolle" in x for x in r.reasons)

    def test_dax_without_power_bi_context_does_not_count(self, scorer):
        project = {
            "title": "Projektleiter CRM-Einführung bei einem DAX-Konzern (m/w/d)",
            "description": "Einführung von Salesforce im Vertrieb eines DAX-Konzerns. Remote.",
        }
        assert not any("DAX" in x.split(":")[1] for x in scorer.score_project(project).reasons
                       if x.startswith("Hohe Priorität"))

    def test_profile_contains_dax_and_power_query(self):
        text = _profile_keywords("power_bi_sharepoint")
        for term in ("Power BI", "DAX", "Power Query", "Datenaufbereitung",
                     "KPI-Dashboard", "Management Reporting"):
            assert term in text, term
        assert "praktische, selbst ausgeführte Arbeit mit DAX-Formeln" in text

    @pytest.mark.parametrize(
        "project", [PBI_DEVELOPER, PBI_MANAGEMENT_REPORTING, PBI_SHAREPOINT],
        ids=["entwickler_dax_power_query", "management_reporting", "power_bi_sharepoint"],
    )
    def test_tfidf_assigns_power_bi_profile(self, project):
        from pre_scorer import PreScorer
        assert PreScorer().score_project(project).best_profile == "power_bi_sharepoint"

    def test_power_bi_projects_are_captured_by_group_a(self):
        engine = FilterEngine(FilterConfig.from_dict(load_default_topic_filters()[A]))
        for project in (PBI_DEVELOPER, PBI_MANAGEMENT_REPORTING, PBI_SHAREPOINT, DATA_ENGINEER):
            assert engine.apply(project, A).passed is True


# ══════════════════════════════════════════════════════════════════════════════
# 12 — Schwelle 50 und einmaliger Power-BI-Kompetenzbonus
# ══════════════════════════════════════════════════════════════════════════════

def _bonus_reasons(result) -> list:
    return [x for x in result.reasons if x.startswith("Kompetenzbonus")]


class TestPowerBiBonus:

    def test_high_threshold_is_50(self):
        rules = yaml.safe_load((REPO_ROOT / "suitability_rules.yaml").read_text("utf-8"))
        assert rules["suitability"]["thresholds"] == {"high": 50, "medium": 25}

    def test_developer_project_reaches_high_through_bonus(self, scorer):
        r = scorer.score_project(PBI_DEVELOPER)
        assert r.technical_score == 55 and r.decision == DECISION_HIGH
        bonus = _bonus_reasons(r)
        assert len(bonus) == 1 and "DAX, Power Query" in bonus[0] and "(+10)" in bonus[0]

    def test_bonus_is_applied_once_and_capped_at_10(self, scorer):
        text = PBI_DEVELOPER["description"] + (
            "\nDAX, DAX-Formeln, DAX-Measures, Power Query, Power Query, Power BI, Power BI")
        r = scorer.score_project(dict(PBI_DEVELOPER, description=text))
        bonus = _bonus_reasons(r)
        assert len(bonus) == 1 and "(+10)" in bonus[0]
        assert r.technical_score == scorer.score_project(PBI_DEVELOPER).technical_score

    def test_one_skill_gives_half_bonus(self, scorer):
        project = {
            "title": "Power BI Berichte (m/w/d)",
            "description": "Berichte in Power BI, Datenquellen per Power Query anbinden.",
        }
        bonus = _bonus_reasons(scorer.score_project(project))
        assert len(bonus) == 1 and bonus[0].endswith("Power Query (+5)")

    def test_no_bonus_without_power_bi(self, scorer):
        project = {
            "title": "Excel-Spezialist (m/w/d)",
            "description": "Datenaufbereitung mit Power Query in Excel.",
        }
        assert _bonus_reasons(scorer.score_project(project)) == []

    def test_no_bonus_for_power_bi_alone(self, scorer):
        assert _bonus_reasons(scorer.score_project(PBI_MANAGEMENT_REPORTING)) == []

    def test_bonus_does_not_lift_specialist_cap(self, scorer):
        r = scorer.score_project(DATA_ENGINEER)
        assert len(_bonus_reasons(r)) == 1          # Bonus wurde gerechnet …
        assert r.technical_score == 40              # … der Deckel gilt trotzdem
        assert r.decision == DECISION_MEDIUM and r.recommendation == RECOMMEND_REVIEW

    def test_bonus_does_not_override_missing_must_have(self, scorer):
        project = {
            "title": "Pega System Architect mit Power BI Reporting (m/w/d)",
            "description": (
                "Berichte in Power BI mit DAX und Power Query. Remote.\n"
                "Must-Have\n- Zertifizierung als Certified Pega Senior System Architect"
            ),
        }
        r = scorer.score_project(project)
        assert len(_bonus_reasons(r)) == 1
        assert r.decision == DECISION_REJECT and r.technical_score <= 10
        assert r.recommendation == RECOMMEND_SKIP

    def test_bonus_does_not_cancel_must_have_penalty(self, scorer):
        base = scorer.score_project(PBI_DEVELOPER)
        with_must = scorer.score_project(dict(
            PBI_DEVELOPER,
            description=PBI_DEVELOPER["description"] + "\n- ABAP-Kenntnisse erforderlich"))
        assert with_must.technical_score == base.technical_score - 25

    def test_all_four_power_bi_examples(self, scorer):
        got = {
            name: (r.technical_score, r.decision, r.recommendation)
            for name, r in {
                "entwickler": scorer.score_project(PBI_DEVELOPER),
                "management_reporting": scorer.score_project(PBI_MANAGEMENT_REPORTING),
                "power_bi_sharepoint": scorer.score_project(PBI_SHAREPOINT),
                "data_engineer": scorer.score_project(DATA_ENGINEER),
            }.items()
        }
        assert got == {
            "entwickler": (55, DECISION_HIGH, RECOMMEND_APPLY),
            "management_reporting": (55, DECISION_HIGH, RECOMMEND_APPLY),
            "power_bi_sharepoint": (65, DECISION_HIGH, RECOMMEND_APPLY),
            "data_engineer": (40, DECISION_MEDIUM, RECOMMEND_REVIEW),
        }

    def test_eight_real_projects_are_unchanged(self, results):
        got = {k: (r.technical_score, r.decision, r.recommendation) for k, r in results.items()}
        low = (0, DECISION_LOW, RECOMMEND_SKIP)
        assert got == {
            "netsec": (40, DECISION_MEDIUM, RECOMMEND_REVIEW),
            "n8n": (35, DECISION_MEDIUM, RECOMMEND_REVIEW),
            "vuln": (26, DECISION_MEDIUM, RECOMMEND_REVIEW),
            "pega": (10, DECISION_REJECT, RECOMMEND_SKIP),
            "sap": (10, DECISION_REJECT, RECOMMEND_SKIP),
            "supporter_bad_toelz": low, "supporter_freiburg": low, "supporter_rottweil": low,
        }
        assert all(_bonus_reasons(r) == [] for r in results.values())


# ══════════════════════════════════════════════════════════════════════════════
# 13 — Eignungs-Score und Textähnlichkeit sind unterscheidbar
# ══════════════════════════════════════════════════════════════════════════════

class TestScoreKindsAreDistinguishable:

    def test_frontmatter_keeps_both_values_under_different_keys(self, tmp_path):
        out = tmp_path / "projects"
        out.mkdir()
        _run_pipeline(str(out))
        for md in out.glob("*.md"):
            fm = _frontmatter(md)
            assert fm["suitability"]["method"] == "eignung_regelbasiert"
            assert isinstance(fm["suitability"]["score"], int)          # 0–100
            assert 0.0 <= fm["pre_scores"]["best_score"] <= 1.0         # TF-IDF, 0–1
            assert "method" not in fm["pre_scores"]

    def test_project_text_labels_both_scores(self, tmp_path):
        import re
        from run_search_groups_rss import _backfill_scores_for_new_files

        out = tmp_path / "projects"
        out.mkdir()
        _run_pipeline(str(out))
        assert _backfill_scores_for_new_files(str(out), 8) == 8
        for md in out.glob("*.md"):
            fm = _frontmatter(md)
            section = md.read_text("utf-8").split("## Vorbewertung", 1)[1]
            suit = fm["suitability"]
            tfidf = round(fm["pre_scores"]["best_score"] * 100)
            assert f"- **Score:** {suit['score']}/100\n" in section
            assert "- **Score-Art:** Eignung (regelbasiert)\n" in section
            assert (f"- **Eignung:** {suit['score']}/100 — fachlich "
                    f"{suit['technical']['score']}/100 ({suit['technical']['decision']}), "
                    f"Empfehlung: {suit['recommendation']}\n") in section
            assert (f"- **Textähnlichkeit (TF-IDF):** {tfidf}/100 — bestes Profil: "
                    f"{fm['pre_scores']['best_profile']}\n") in section
            # genau eine Zeile, die das Dashboard als Score liest
            assert len(re.findall(r"- \*\*Score:\*\*\s*\d+/100", section)) == 1

    def test_file_without_suitability_is_labelled_as_text_similarity(self, tmp_path):
        from run_search_groups_rss import _backfill_scores_for_new_files

        out = tmp_path / "projects"
        out.mkdir()
        md = out / "alt.md"
        md.write_text(
            "---\ntitle: Power BI Berater\nstate: scraped\n---\n\n"
            "# Power BI Berater\n\nKPI-Dashboard und Management Reporting.\n", "utf-8")
        assert _backfill_scores_for_new_files(str(out), 1) == 1
        section = md.read_text("utf-8").split("## Vorbewertung", 1)[1]
        assert "- **Score-Art:** Textähnlichkeit (TF-IDF)\n" in section
        assert "**Eignung:**" not in section
        assert "- **Textähnlichkeit (TF-IDF):** " in section

    def test_existing_score_lines_are_not_rewritten(self, tmp_path):
        from run_search_groups_rss import _backfill_scores_for_new_files

        out = tmp_path / "projects"
        out.mkdir()
        md = out / "bestand.md"
        original = ("---\ntitle: x\n---\n\n# x\n\nText\n\n## Vorbewertung\n\n"
                    "- **Score:** 15/100\n")
        md.write_text(original, "utf-8")
        assert _backfill_scores_for_new_files(str(out), 1) == 0
        assert md.read_text("utf-8") == original
