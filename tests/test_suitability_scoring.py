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
from suitability_scorer import (
    DECISION_HIGH, DECISION_LOW, DECISION_MEDIUM, DECISION_REJECT,
    SuitabilityScorer, _must_sections,
)

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
        "Rahmenparameter:\n\n- Einsatzort: Remote aus Deutschland\n- Laufzeit: 15PT"
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
    return SuitabilityScorer()


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
        assert any("vollständig vor Ort" in reason for reason in r.reasons)
        assert any("Niedrige Priorität" in reason for reason in r.reasons)

    def test_senior_network_security_is_rated_higher(self, results):
        r = results["netsec"]
        assert r.decision == DECISION_HIGH
        assert r.score > REAL_PROJECTS["netsec"]["before"]
        assert r.best_group == B

    def test_specialised_vulnerability_role_ranks_below_network_security(self, results):
        assert results["vuln"].score < results["netsec"].score
        assert results["vuln"].decision == DECISION_MEDIUM
        assert any("Schwachstellenmanagement" in reason for reason in results["vuln"].reasons)

    def test_n8n_is_only_transferable(self, results):
        r = results["n8n"]
        assert r.decision == DECISION_MEDIUM
        assert r.reject_reason is None
        transferable = [x for x in r.reasons if "übertragbare Kompetenz" in x]
        assert len(transferable) == 1 and "n8n" in transferable[0] and "(+4)" in transferable[0]

    def test_ranking_after(self, results):
        order = sorted(results, key=lambda k: results[k].score, reverse=True)
        assert order[0] == "netsec"
        assert set(order[1:3]) == {"vuln", "n8n"}
        assert set(order[3:]) == {
            "pega", "sap", "supporter_bad_toelz", "supporter_freiburg", "supporter_rottweil",
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
        assert any("Teilzeit" in reason for reason in r.reasons)

    def test_sophos_infrastructure_project_scores_high(self, scorer):
        r = scorer.score_project(SOPHOS_INFRA)
        assert r.decision == DECISION_HIGH and r.best_group == B
        assert any("Sophos" in reason for reason in r.reasons)

    def test_priority_examples_beat_all_low_rated_real_projects(self, scorer, results):
        best_low = max(
            r.score for r in results.values()
            if r.decision in (DECISION_LOW, DECISION_REJECT)
        )
        assert scorer.score_project(POWER_BI).score > best_low
        assert scorer.score_project(SOPHOS_INFRA).score > best_low


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
        assert r_sap.score == r_base.score

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
        assert "n8n" not in group["high"] + group["medium"]
        for profile in (REPO_ROOT / "competency_profiles").glob("*.md"):
            assert "n8n" not in profile.read_text("utf-8"), profile.name

    def test_stated_priorities_are_present(self):
        rules = yaml.safe_load((REPO_ROOT / "suitability_rules.yaml").read_text("utf-8"))
        groups = rules["suitability"]["groups"]
        for term in ("Power BI", "Bitrix24", "Salesforce", "Make.com", "Zapier", "SharePoint"):
            assert term in groups[A]["high"], term
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
        patch("email_agent.EmailAgent.fetch_rss_feed", return_value=entries),
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
            assert fm["suitability"]["reasons"], key
            assert {p["profile_id"] for p in fm["pre_scores"]["profiles"]} == set(PROFILE_IDS)
        assert files["netsec"][1]["suitability"]["decision"] == DECISION_HIGH

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
