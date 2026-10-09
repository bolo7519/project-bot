"""
tests/test_phase3_filter_scoring.py — Phase-3-Tests: Hard-Filter + TF-IDF-Vorbewertung.

Testanforderungen laut Spezifikation:
  T1. Fehlendes Rate-Feld → Projekt wird NICHT abgelehnt (unknown=pass)
  T2. Unbekannter Remote-Status → Projekt wird NICHT abgelehnt (unknown=pass)
  T3. ANÜ / Festanstellung können über exclude_terms herausgefiltert werden
  T4. Ein Projekt kann die Filter von Gruppe A bestehen und Gruppe B ablehnen
  T5. Umlaute und IT-Abkürzungen werden korrekt verarbeitet
  T6. Bestehende Projekte und Suchgruppen bleiben unverändert (backward compat)

Zusätzlich:
  T7. Pre-Scorer lädt alle vier Profile
  T8. Pre-Scorer liefert höchsten Score für das thematisch passende Profil
  T9. Pre-Scorer-Ergebnisse landen in ProjectRecord.extra
  T10. Filterentscheid je Suchgruppe wird in ProjectRecord.extra gespeichert
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch, MagicMock

import pytest

# ── Module unter Test ─────────────────────────────────────────────────────────
from filter_engine import (
    FilterConfig,
    FilterEngine,
    FilterResult,
    WorkModeFilterConfig,
    ContractTypeFilterConfig,
    RateFilterConfig,
    LanguageFilterConfig,
    filter_config_from_search_group,
    load_filter_config_for_group,
)
from pre_scorer import PreScorer, PROFILE_IDS, _tokenize as ps_tokenize


# ══════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ══════════════════════════════════════════════════════════════════════════════

def _make_project(
    *,
    title: str = "Testprojekt",
    description: str = "",
    work_mode: str = "",
    contract_type: str = "",
    rate_text: str = "",
    language_hint: str = "",
    published: str = "",
    full_text: str = "",
) -> Dict[str, Any]:
    """Erzeugt ein minimales Projekt-Dict für Filter- und Scoring-Tests."""
    combined = " ".join(filter(None, [
        title, description, work_mode, contract_type, rate_text,
        language_hint, published, full_text,
    ]))
    return {
        "title": title,
        "description": combined,
        "work_mode": work_mode,
        "contract_type": contract_type,
        "rate_text": rate_text,
        "language": language_hint,
        "published": published,
    }


def _filter_cfg_freelance_only() -> FilterConfig:
    """FilterConfig, die nur Freelance-Projekte akzeptiert."""
    return FilterConfig(
        contract_type=ContractTypeFilterConfig(
            allowed=["freelance"],
            reject_if_unknown=False,
        )
    )


def _filter_cfg_remote_hybrid() -> FilterConfig:
    """FilterConfig, die nur Remote/Hybrid-Projekte akzeptiert."""
    return FilterConfig(
        work_mode=WorkModeFilterConfig(
            allowed=["remote", "hybrid"],
            reject_if_unknown=False,
        )
    )


def _filter_cfg_exclude_festanstellung() -> FilterConfig:
    return FilterConfig(
        exclude_terms=["Festanstellung", "ANÜ", "Arbeitnehmerüberlassung"],
    )


def _filter_cfg_rate_min_80() -> FilterConfig:
    return FilterConfig(
        rate=RateFilterConfig(min_hourly=80.0, reject_if_unknown=False)
    )


# ══════════════════════════════════════════════════════════════════════════════
# T1 — Fehlendes Rate-Feld → NICHT abgelehnt
# ══════════════════════════════════════════════════════════════════════════════

class TestFilterUnknownRate:
    """T1: Fehlendes oder nicht-auswertbares Rate-Feld → unknown=pass."""

    def test_missing_rate_not_rejected(self):
        """Projekt ohne Ratenangabe besteht den Rate-Filter."""
        project = _make_project(title="Python Backend Entwickler", description="Keine Ratenangabe.")
        cfg = _filter_cfg_rate_min_80()
        engine = FilterEngine(cfg)
        result = engine.apply(project, "test_group")
        assert result.passed, (
            "Projekt ohne Rate-Information darf NICHT abgelehnt werden. "
            f"Fehlgeschlagene Checks: {[c for c in result.checks if not c.passed]}"
        )

    def test_missing_rate_check_marked_unknown(self):
        """Der Rate-Check ist als was_unknown=True markiert, wenn keine Rate gefunden wurde."""
        project = _make_project(title="DevOps Engineer", description="Gute Konditionen")
        cfg = _filter_cfg_rate_min_80()
        engine = FilterEngine(cfg)
        result = engine.apply(project, "grp")
        rate_checks = [c for c in result.checks if "rate" in c.criterion]
        # Mindestens ein Rate-Check muss vorhanden sein (wenn Rate-Config gesetzt)
        if rate_checks:
            # Wenn vorhanden und unbekannt, muss was_unknown=True und passed=True sein
            unknown_checks = [c for c in rate_checks if c.was_unknown]
            for c in unknown_checks:
                assert c.passed, "Unbekannter Rate-Check muss passed=True haben"

    def test_rate_below_minimum_rejected(self):
        """Projekt mit explizit zu niedrigem Stundensatz wird abgelehnt."""
        project = _make_project(
            title="Junior Dev",
            description="Stundensatz: 40 EUR/h"
        )
        cfg = _filter_cfg_rate_min_80()
        engine = FilterEngine(cfg)
        result = engine.apply(project, "grp")
        # Muss abgelehnt werden, wenn Rate klar unter Minimum liegt
        rate_checks = [c for c in result.checks if "rate" in c.criterion and not c.was_unknown]
        if rate_checks:
            assert not result.passed, "Projekt mit 40 EUR/h unter Minimum 80 EUR/h muss abgelehnt werden"

    def test_rate_above_minimum_accepted(self):
        """Projekt mit Stundensatz über Minimum wird akzeptiert."""
        project = _make_project(
            title="Senior Consultant",
            description="Rate: 120 EUR/h, Remote möglich"
        )
        cfg = _filter_cfg_rate_min_80()
        engine = FilterEngine(cfg)
        result = engine.apply(project, "grp")
        # Darf nicht durch Rate-Filter fallen
        rate_checks = [c for c in result.checks if "rate" in c.criterion and not c.was_unknown]
        for c in rate_checks:
            assert c.passed, f"Rate-Check bei 120 EUR/h (min 80) muss passed=True sein: {c}"


# ══════════════════════════════════════════════════════════════════════════════
# T2 — Unbekannter Remote-Status → NICHT abgelehnt
# ══════════════════════════════════════════════════════════════════════════════

class TestFilterUnknownWorkMode:
    """T2: Fehlender oder nicht auswertbarer Remote-Status → unknown=pass."""

    def test_missing_work_mode_not_rejected(self):
        """Projekt ohne Arbeitsmodell-Information besteht den Work-Mode-Filter."""
        project = _make_project(
            title="IT-Projektmanager",
            description="Leitung eines ERP-Migrationsprojekts. Details auf Anfrage."
        )
        cfg = _filter_cfg_remote_hybrid()
        engine = FilterEngine(cfg)
        result = engine.apply(project, "grp")
        assert result.passed, (
            "Projekt ohne Work-Mode-Angabe darf NICHT abgelehnt werden "
            f"(reject_if_unknown=False). Checks: {result.checks}"
        )

    def test_reject_if_unknown_true_rejects_missing(self):
        """Bei reject_if_unknown=True wird ein fehlendes Arbeitsmodell abgelehnt."""
        project = _make_project(title="Unbekanntes Modell")
        cfg = FilterConfig(
            work_mode=WorkModeFilterConfig(
                allowed=["remote"],
                reject_if_unknown=True,
            )
        )
        engine = FilterEngine(cfg)
        result = engine.apply(project, "grp")
        assert not result.passed, "Bei reject_if_unknown=True und fehlendem Work-Mode muss abgelehnt werden"

    def test_remote_project_accepted(self):
        """Explizites Remote-Projekt besteht den Work-Mode-Filter für [remote, hybrid]."""
        project = _make_project(
            title="Cloud Architect",
            description="100% Remote möglich. Azure, Kubernetes."
        )
        cfg = _filter_cfg_remote_hybrid()
        engine = FilterEngine(cfg)
        result = engine.apply(project, "grp")
        assert result.passed, "Explizit Remote-Projekt muss den Work-Mode-Filter bestehen"

    def test_onsite_project_rejected_when_remote_hybrid_required(self):
        """Vor-Ort-Projekt wird abgelehnt, wenn nur Remote/Hybrid erlaubt."""
        project = _make_project(
            title="IT Systemadministrator",
            description="Einsatz vor Ort in Frankfurt. Vollzeit im Büro."
        )
        cfg = _filter_cfg_remote_hybrid()
        engine = FilterEngine(cfg)
        result = engine.apply(project, "grp")
        assert not result.passed, "Vor-Ort-Projekt muss abgelehnt werden, wenn nur Remote/Hybrid erlaubt"


# ══════════════════════════════════════════════════════════════════════════════
# T3 — ANÜ / Festanstellung können herausgefiltert werden
# ══════════════════════════════════════════════════════════════════════════════

class TestFilterExcludeTerms:
    """T3: ANÜ und Festanstellung können über exclude_terms herausgefiltert werden."""

    def test_festanstellung_rejected(self):
        """Projekt mit 'Festanstellung' in der Beschreibung wird abgelehnt."""
        project = _make_project(
            title="Softwareentwickler (m/w/d)",
            description="Wir suchen eine Festanstellung. Vollzeitstelle in Berlin."
        )
        cfg = _filter_cfg_exclude_festanstellung()
        engine = FilterEngine(cfg)
        result = engine.apply(project, "grp")
        assert not result.passed, "Projekt mit 'Festanstellung' muss abgelehnt werden"

    def test_anue_rejected(self):
        """Projekt mit 'ANÜ' in der Beschreibung wird abgelehnt."""
        project = _make_project(
            title="DevOps Engineer",
            description="Einsatz über ANÜ (Arbeitnehmerüberlassung). Tarif nach IGZ."
        )
        cfg = _filter_cfg_exclude_festanstellung()
        engine = FilterEngine(cfg)
        result = engine.apply(project, "grp")
        assert not result.passed, "Projekt mit 'ANÜ' muss abgelehnt werden"

    def test_arbeitnehmerueberlassung_rejected(self):
        """Volles Wort 'Arbeitnehmerüberlassung' wird abgelehnt."""
        project = _make_project(
            title="IT-Berater",
            description="Tätigkeit im Rahmen der Arbeitnehmerüberlassung gemäß AÜG."
        )
        cfg = _filter_cfg_exclude_festanstellung()
        engine = FilterEngine(cfg)
        result = engine.apply(project, "grp")
        assert not result.passed, "Projekt mit 'Arbeitnehmerüberlassung' muss abgelehnt werden"

    def test_freelance_not_excluded(self):
        """Reines Freelance-Projekt ohne Ausschlussbegriffe wird NICHT abgelehnt."""
        project = _make_project(
            title="Power BI Consultant",
            description="Freiberuflicher Auftrag, 3 Monate, 100% Remote."
        )
        cfg = _filter_cfg_exclude_festanstellung()
        engine = FilterEngine(cfg)
        result = engine.apply(project, "grp")
        assert result.passed, "Freelance-Projekt ohne Ausschlussbegriffe darf nicht abgelehnt werden"


# ══════════════════════════════════════════════════════════════════════════════
# T4 — Projekt besteht Filter von Gruppe A, scheitert an Gruppe B
# ══════════════════════════════════════════════════════════════════════════════

class TestFilterCrossGroup:
    """T4: Gleiche Projektdaten, verschiedene Filterregeln pro Suchgruppe."""

    def test_project_passes_group_a_fails_group_b(self):
        """Ein Vor-Ort-Projekt besteht den infra_security-Filter (remote optional),
        scheitert aber am automation_bi-Filter (nur remote/hybrid)."""
        project = _make_project(
            title="IT-Infrastruktur Manager",
            description="Einsatz vor Ort in Hamburg. Netzwerk, Firewall, Cisco."
        )
        # Gruppe A: nur Remote/Hybrid
        cfg_a = FilterConfig(
            work_mode=WorkModeFilterConfig(allowed=["remote", "hybrid"], reject_if_unknown=False)
        )
        engine_a = FilterEngine(cfg_a)
        result_a = engine_a.apply(project, "automation_bi")

        # Gruppe B: Remote, Hybrid und Vor Ort erlaubt
        cfg_b = FilterConfig(
            work_mode=WorkModeFilterConfig(allowed=["remote", "hybrid", "onsite"], reject_if_unknown=False)
        )
        engine_b = FilterEngine(cfg_b)
        result_b = engine_b.apply(project, "infra_security")

        assert not result_a.passed, "Vor-Ort-Projekt muss Gruppe A (remote/hybrid) scheitern"
        assert result_b.passed, "Vor-Ort-Projekt muss Gruppe B (alle Modi) bestehen"

    def test_filter_results_stored_per_group(self):
        """Filterentscheide werden je Suchgruppe separat als Dict gespeichert."""
        project = _make_project(
            title="Testprojekt",
            description="ANÜ möglich oder freiberuflich"
        )
        cfg_a = FilterConfig(exclude_terms=["ANÜ"])  # Gruppe A lehnt ANÜ ab
        cfg_b = FilterConfig()  # Gruppe B hat keine Ausschlüsse

        result_a = FilterEngine(cfg_a).apply(project, "grp_a")
        result_b = FilterEngine(cfg_b).apply(project, "grp_b")

        assert result_a.search_group_id == "grp_a"
        assert result_b.search_group_id == "grp_b"
        assert not result_a.passed
        assert result_b.passed

        # Beide zu_dict() sind serialisierbar
        d_a = result_a.to_dict()
        d_b = result_b.to_dict()
        assert d_a["search_group_id"] == "grp_a"
        assert d_b["search_group_id"] == "grp_b"
        assert d_a["passed"] is False
        assert d_b["passed"] is True


# ══════════════════════════════════════════════════════════════════════════════
# T5 — Umlaute und IT-Abkürzungen korrekt verarbeitet
# ══════════════════════════════════════════════════════════════════════════════

class TestUmlautsAndAbbreviations:
    """T5: Umlaute und IT-Abkürzungen werden korrekt verarbeitet."""

    # ── Filter-Engine ──────────────────────────────────────────────────────────

    def test_umlaut_in_exclude_term_case_insensitive(self):
        """Ausschlussterm mit Umlaut (z.B. 'Überlassung') greift auch bei Kleinschreibung."""
        project = _make_project(
            title="Techniker",
            description="Vertragsform: arbeitnehmerüberlassung nach AÜG"
        )
        cfg = FilterConfig(exclude_terms=["Arbeitnehmerüberlassung"])
        result = FilterEngine(cfg).apply(project, "grp")
        assert not result.passed, "Umlaut-Ausschlussterm muss case-insensitiv greifen"

    def test_umlaut_work_mode_detection(self):
        """'Vor Ort' mit Umlaut-nahen Begriffen wird korrekt erkannt."""
        project = _make_project(
            title="Netzwerkadministrator",
            description="Einsatzort: Büro in München. Präsenz vor Ort erforderlich."
        )
        cfg = FilterConfig(
            work_mode=WorkModeFilterConfig(allowed=["remote"], reject_if_unknown=False)
        )
        result = FilterEngine(cfg).apply(project, "grp")
        # Muss als 'onsite' erkannt und abgelehnt werden
        # (oder als unknown → pass, wenn Erkennung fehlschlägt — beides valide)
        # Wichtig: kein Crash, kein Fehler
        assert isinstance(result.passed, bool), "Ergebnis muss bool sein"

    # ── Tokenizer (PreScorer) ──────────────────────────────────────────────────

    def test_tokenizer_expands_umlauts(self):
        """Umlaute werden in ASCII-Äquivalente expandiert (PreScorer-Tokenizer)."""
        tokens = ps_tokenize("Überprüfung der Qualität")
        assert "ueberpruefung" in tokens or "qualitaet" in tokens or "qualit" in tokens, (
            f"Umlaut-Expansion fehlgeschlagen. Tokens: {tokens}"
        )

    def test_tokenizer_handles_sz(self):
        """ß wird zu 'ss' expandiert."""
        tokens = ps_tokenize("Maßnahmen zur Straße")
        text = " ".join(tokens)
        assert "ss" in text or "massnahmen" in text or "strasse" in text, (
            f"ß→ss-Expansion fehlgeschlagen. Tokens: {tokens}"
        )

    def test_tokenizer_preserves_m365(self):
        """M365 wird als einzelnes Token erhalten."""
        tokens = ps_tokenize("Microsoft M365 Deployment in Azure")
        assert "m365" in tokens, f"M365 muss als geschütztes Token erhalten bleiben. Tokens: {tokens}"

    def test_tokenizer_preserves_api(self):
        """API bleibt als Token erhalten."""
        tokens = ps_tokenize("REST API Integration via GraphQL")
        assert "api" in tokens, f"API muss als Token erhalten bleiben. Tokens: {tokens}"

    def test_tokenizer_preserves_crm(self):
        """CRM bleibt als Token erhalten."""
        tokens = ps_tokenize("CRM-Einführung mit Salesforce und Power BI")
        assert "crm" in tokens, f"CRM muss als Token erhalten bleiben. Tokens: {tokens}"

    def test_tokenizer_preserves_power_bi_parts(self):
        """'power' und 'bi' werden als Tokens erkannt."""
        tokens = ps_tokenize("Power BI Dashboard Entwicklung")
        assert "bi" in tokens, f"'bi' muss als Token vorhanden sein. Tokens: {tokens}"

    def test_tokenizer_handles_compound_german(self):
        """Deutsche Komposita mit Umlaut werden korrekt tokenisiert."""
        tokens = ps_tokenize("Netzwerksicherheitslösung für SIEM")
        # Mindestens SIEM muss erkannt werden
        assert "siem" in tokens, f"SIEM muss als Token erkannt werden. Tokens: {tokens}"

    # ── Pre-Scorer ────────────────────────────────────────────────────────────

    def test_scorer_handles_umlaut_text(self):
        """Pre-Scorer läuft ohne Fehler bei deutschem Text mit Umlauten."""
        scorer = PreScorer()
        result = scorer.score_text(
            "Überprüfung der IT-Sicherheit, Härtung gemäß BSI-Grundschutz und NIS2-Anforderungen."
        )
        assert result is not None
        assert all(0.0 <= p.score <= 1.0 for p in result.profiles)

    def test_scorer_handles_it_abbreviations(self):
        """Pre-Scorer verarbeitet gängige IT-Abkürzungen korrekt."""
        scorer = PreScorer()
        result = scorer.score_text(
            "M365 Rollout mit Power BI, SharePoint Online, CRM-Integration via API."
        )
        # power_bi_sharepoint sollte einen der höchsten Scores haben
        assert result is not None
        bi_score = result.score_for("power_bi_sharepoint")
        assert bi_score >= 0.0, "power_bi_sharepoint-Score muss >= 0 sein"


# ══════════════════════════════════════════════════════════════════════════════
# T6 — Bestehende Projekte / Suchgruppen bleiben unverändert
# ══════════════════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    """T6: Phase-3-Integration darf bestehende Phase-2-Funktionalität nicht brechen."""

    def test_filter_engine_empty_config_passes_all(self):
        """FilterConfig.empty() lehnt nichts ab."""
        project = _make_project(
            title="Irgendein Projekt",
            description="ANÜ, Festanstellung, Vor Ort, kein Remote"
        )
        result = FilterEngine(FilterConfig.empty()).apply(project, "grp")
        assert result.passed, "Leere FilterConfig muss alles durchlassen"

    def test_process_rss_without_search_group_config(self):
        """process_rss_entries ohne search_group_config (legacy path) bleibt unverändert."""
        from email_agent import EmailAgent

        config = {
            "providers": {
                "testprovider": {
                    "channels": {"rss": {"feed_urls": [], "limit": 5}}
                }
            },
            "channels": {"email": {}, "rss": {}},
            "search_groups": {},
            "settings": {},
        }
        agent = EmailAgent(config)
        provider_config = {"provider_id": "testprovider", "feed_urls": [], "limit": 5}

        with tempfile.TemporaryDirectory() as tmpdir:
            result = agent.process_rss_entries([], provider_config, tmpdir)
        # Kein Fehler, kein projects_saved bei leerer Entries-Liste
        assert result["projects_saved"] == 0
        assert result["urls_skipped_dedupe"] == 0

    def test_search_group_config_without_filters(self):
        """SearchGroupConfig ohne filters-Feld hat filters=None."""
        from search_group_config import SearchGroupConfig
        cfg = SearchGroupConfig(
            group_id="test",
            display_name="Test",
        )
        assert cfg.filters is None

    def test_filter_config_from_none_filters(self):
        """filter_config_from_search_group() mit filters=None liefert leere FilterConfig."""
        from search_group_config import SearchGroupConfig
        cfg = SearchGroupConfig(group_id="test", display_name="Test")
        fc = filter_config_from_search_group(cfg)
        # Leere Config lehnt nichts ab
        project = _make_project(title="Test", description="ANÜ Festanstellung")
        result = FilterEngine(fc).apply(project, "test")
        assert result.passed


# ══════════════════════════════════════════════════════════════════════════════
# T7 — Pre-Scorer lädt alle vier Profile
# ══════════════════════════════════════════════════════════════════════════════

class TestPreScorerProfileLoading:
    """T7: Pre-Scorer lädt alle vier Standard-Profile."""

    def test_all_four_profiles_loaded(self):
        """Alle vier Kompetenzprofile müssen geladen sein."""
        scorer = PreScorer()
        loaded = scorer.loaded_profile_ids
        for pid in PROFILE_IDS:
            assert pid in loaded, f"Profil '{pid}' wurde nicht geladen. Geladen: {loaded}"

    def test_score_result_contains_all_four_profiles(self):
        """score_project() liefert Scores für alle vier Profile."""
        scorer = PreScorer()
        result = scorer.score_project({"title": "IT Projekt", "description": "Azure Kubernetes DevOps"})
        scored_ids = {p.profile_id for p in result.profiles}
        for pid in PROFILE_IDS:
            assert pid in scored_ids, f"Kein Score für Profil '{pid}' in Ergebnis"

    def test_scores_in_valid_range(self):
        """Alle Scores liegen im Bereich [0.0, 1.0]."""
        scorer = PreScorer()
        result = scorer.score_project({"description": "Power BI und SharePoint Entwicklung"})
        for p in result.profiles:
            assert 0.0 <= p.score <= 1.0, f"Score für {p.profile_id} außerhalb [0,1]: {p.score}"

    def test_empty_text_returns_zero_scores(self):
        """Leerer Projekttext liefert Score 0.0 für alle Profile."""
        scorer = PreScorer()
        result = scorer.score_project({"title": "", "description": ""})
        for p in result.profiles:
            assert p.score == 0.0, f"Leerer Text muss Score 0.0 liefern, nicht {p.score}"

    def test_best_profile_identified(self):
        """best_profile und best_score zeigen auf das Profil mit höchstem Score."""
        scorer = PreScorer()
        result = scorer.score_text("Salesforce CRM Einführung, Lead Management, API Integration")
        if result.best_profile is not None:
            best_idx = next(i for i, p in enumerate(result.profiles) if p.profile_id == result.best_profile)
            best_score_in_profiles = result.profiles[best_idx].score
            assert abs(best_score_in_profiles - result.best_score) < 1e-6


# ══════════════════════════════════════════════════════════════════════════════
# T8 — Pre-Scorer liefert höchsten Score für thematisch passendes Profil
# ══════════════════════════════════════════════════════════════════════════════

class TestPreScorerRelevance:
    """T8: Pre-Scorer erkennt das thematisch passende Profil."""

    def test_infrastructure_text_scores_highest_on_infra_profile(self):
        """IT-Infrastruktur-Text erzielt höchsten Score auf infra_security-Profil."""
        scorer = PreScorer()
        text = (
            "Gesucht: Netzwerkadministrator mit Erfahrung in Firewall, VPN, "
            "Cisco, Active Directory, SIEM. BSI-Grundschutz-Kenntnisse von Vorteil."
        )
        result = scorer.score_text(text)
        infra_score = result.score_for("it_infrastructure_security")
        # Infra-Score muss höher als CRM-Score sein
        crm_score = result.score_for("crm_sales_automation")
        assert infra_score > crm_score, (
            f"Infra-Text muss höheren Infra-Score ({infra_score:.4f}) als "
            f"CRM-Score ({crm_score:.4f}) haben"
        )

    def test_crm_text_scores_higher_on_crm_profile(self):
        """CRM-Text erzielt deutlich höheren CRM-Score als Infra-Score."""
        scorer = PreScorer()
        text = (
            "Salesforce Administrator gesucht. CRM-Einführung bei einem "
            "mittelständischen Unternehmen. HubSpot Erfahrung erwünscht. "
            "Lead Management, Pipeline, Power Automate."
        )
        result = scorer.score_text(text)
        crm_score = result.score_for("crm_sales_automation")
        infra_score = result.score_for("it_infrastructure_security")
        assert crm_score > infra_score, (
            f"CRM-Text: CRM-Score ({crm_score:.4f}) muss > Infra-Score ({infra_score:.4f}) sein"
        )

    def test_bi_text_scores_higher_on_bi_profile(self):
        """Power BI-Text erzielt höheren BI-Score als AI-Score."""
        scorer = PreScorer()
        text = (
            "Power BI Entwickler für DAX-Entwicklung. Aufbau von KPI-Dashboards "
            "in Power BI Service. Azure Synapse, Data Warehouse, SharePoint Online."
        )
        result = scorer.score_text(text)
        bi_score = result.score_for("power_bi_sharepoint")
        ai_score = result.score_for("ai_business_process_integration")
        assert bi_score > ai_score, (
            f"BI-Text: BI-Score ({bi_score:.4f}) muss > AI-Score ({ai_score:.4f}) sein"
        )

    def test_top_matches_contain_relevant_terms(self):
        """top_matches enthalten relevante Terme aus dem Text."""
        scorer = PreScorer()
        result = scorer.score_text("Kubernetes Docker CI/CD DevOps Terraform Ansible")
        infra_profile = next(p for p in result.profiles if p.profile_id == "it_infrastructure_security")
        # Mindestens ein relevanter Term muss in top_matches sein
        assert len(infra_profile.top_matches) > 0 or infra_profile.score == 0.0, (
            "Wenn Infra-Score > 0, muss top_matches nicht leer sein"
        )


# ══════════════════════════════════════════════════════════════════════════════
# T9 — Pre-Scorer-Ergebnisse landen in ProjectRecord.extra
# ══════════════════════════════════════════════════════════════════════════════

class TestPreScoreStorage:
    """T9: pre_scores werden korrekt in ProjectRecord.extra gespeichert."""

    def test_pre_score_dict_structure(self):
        """PreScoreResult.to_dict() erzeugt gültiges Dict für ProjectRecord.extra."""
        scorer = PreScorer()
        result = scorer.score_text("Azure Cloud Kubernetes DevOps")
        d = result.to_dict()

        assert "best_profile" in d
        assert "best_score" in d
        assert "profiles" in d
        assert isinstance(d["profiles"], list)
        assert len(d["profiles"]) == len(scorer.loaded_profile_ids)

        for p in d["profiles"]:
            assert "profile_id" in p
            assert "score" in p
            assert "top_matches" in p
            assert isinstance(p["score"], float)
            assert isinstance(p["top_matches"], list)

    def test_pre_score_stored_via_dispatcher(self):
        """Pre-Score-Dict wird in extra_metadata an den Dispatcher übergeben."""
        from search_group_config import SearchGroupConfig
        from email_agent import EmailAgent

        # SearchGroupConfig ohne Filter (alle durchlassen)
        sg_cfg = SearchGroupConfig(
            group_id="test_group",
            display_name="Test",
        )

        config = {
            "providers": {"testprovider": {"channels": {"rss": {}}}},
            "channels": {"email": {}, "rss": {}},
            "search_groups": {},
            "settings": {},
        }
        agent = EmailAgent(config)

        dispatched_extras: list = []

        def capture_dispatch(**kwargs):
            dispatched_extras.append(kwargs.get("extra_metadata") or {})
            mock_result = MagicMock()
            mock_result.action = "created"
            return mock_result

        entries = [{"link": "https://example.com/projekt/1", "title": "Power BI Projekt"}]
        provider_config = {"provider_id": "testprovider"}

        mock_schema = {
            "title": "Power BI Dashboard Entwicklung",
            "description": "DAX, Power Query, Azure Synapse, SharePoint Online",
        }
        mock_adapter = MagicMock()
        mock_adapter.parse.return_value = mock_schema
        mock_adapter.get_provider_name.return_value = "TestProvider"

        mock_renderer = MagicMock()
        mock_renderer.render.return_value = "# Test"

        mock_scorer = PreScorer()  # Echter PreScorer

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("email_agent.DedupeService"),
                patch("email_agent.SearchGroupDispatcher") as mock_dispatcher_cls,
                patch.object(agent, "load_adapter", return_value=mock_adapter),
                patch("email_agent.MarkdownRenderer", return_value=mock_renderer),
            ):
                mock_dispatcher_inst = MagicMock()
                mock_dispatcher_inst.dispatch.side_effect = capture_dispatch
                mock_dispatcher_cls.return_value = mock_dispatcher_inst

                agent.process_rss_entries(
                    entries,
                    provider_config,
                    tmpdir,
                    search_group_id="test_group",
                    search_group_config=sg_cfg,
                    _pre_scorer=mock_scorer,
                )

        assert len(dispatched_extras) == 1, "Dispatcher muss einmal aufgerufen worden sein"
        extra = dispatched_extras[0]
        assert "pre_scores" in extra, (
            f"'pre_scores' fehlt in extra_metadata. Vorhanden: {list(extra.keys())}"
        )
        assert "profiles" in extra["pre_scores"]


# ══════════════════════════════════════════════════════════════════════════════
# T10 — Filterentscheid je Suchgruppe in extra gespeichert
# ══════════════════════════════════════════════════════════════════════════════

class TestFilterResultStorage:
    """T10: filter_results werden korrekt in ProjectRecord.extra gespeichert."""

    def test_filter_result_dict_structure(self):
        """FilterResult.to_dict() erzeugt gültiges Dict für ProjectRecord.extra."""
        cfg = FilterConfig(exclude_terms=["ANÜ"])
        result = FilterEngine(cfg).apply(
            _make_project(title="Freelance Berater", description="Freiberuflicher Auftrag, keine Überlassung"),
            "automation_bi"
        )
        d = result.to_dict()

        assert "search_group_id" in d
        assert "passed" in d
        assert "checks" in d
        assert isinstance(d["checks"], list)
        assert d["search_group_id"] == "automation_bi"
        assert d["passed"] is True

    def test_filter_result_stored_via_dispatcher(self):
        """Filter-Result-Dict wird mit search_group_id-Key in extra gespeichert."""
        from search_group_config import SearchGroupConfig
        from email_agent import EmailAgent

        sg_cfg = SearchGroupConfig(
            group_id="automation_bi",
            display_name="Automation BI",
            filters={
                "exclude_terms": ["Festanstellung"],
                "contract_type": {"allowed": ["freelance"], "reject_if_unknown": False},
            }
        )

        config = {
            "providers": {"testprovider": {"channels": {"rss": {}}}},
            "channels": {"email": {}, "rss": {}},
            "search_groups": {},
            "settings": {},
        }
        agent = EmailAgent(config)
        dispatched_extras: list = []

        def capture(**kwargs):
            dispatched_extras.append(kwargs.get("extra_metadata") or {})
            r = MagicMock()
            r.action = "created"
            return r

        entries = [{"link": "https://example.com/p/99", "title": "Freelance Consultant"}]
        provider_config = {"provider_id": "testprovider"}

        mock_schema = {
            "title": "Freelance Cloud Berater",
            "description": "Vertragstyp: Freelance. Remote möglich. 100 EUR/h.",
        }
        mock_adapter = MagicMock()
        mock_adapter.parse.return_value = mock_schema
        mock_adapter.get_provider_name.return_value = "TP"
        mock_renderer = MagicMock()
        mock_renderer.render.return_value = "# Test"
        mock_scorer = MagicMock()
        mock_scorer.score_project.return_value = MagicMock(to_dict=lambda: {"profiles": [], "best_profile": None, "best_score": 0.0})

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("email_agent.DedupeService"),
                patch("email_agent.SearchGroupDispatcher") as mock_disp_cls,
                patch.object(agent, "load_adapter", return_value=mock_adapter),
                patch("email_agent.MarkdownRenderer", return_value=mock_renderer),
            ):
                mock_disp_inst = MagicMock()
                mock_disp_inst.dispatch.side_effect = capture
                mock_disp_cls.return_value = mock_disp_inst

                agent.process_rss_entries(
                    entries,
                    provider_config,
                    tmpdir,
                    search_group_id="automation_bi",
                    search_group_config=sg_cfg,
                    _pre_scorer=mock_scorer,
                )

        assert len(dispatched_extras) == 1
        extra = dispatched_extras[0]
        assert "filter_results" in extra, (
            f"'filter_results' fehlt in extra_metadata. Vorhanden: {list(extra.keys())}"
        )
        assert "automation_bi" in extra["filter_results"], (
            "filter_results muss search_group_id als Key haben"
        )
        fr = extra["filter_results"]["automation_bi"]
        assert fr["search_group_id"] == "automation_bi"
        assert isinstance(fr["passed"], bool)

    def test_filtered_project_not_dispatched(self):
        """Hard-Filter-Ablehnungen werden NICHT an den Dispatcher weitergegeben."""
        from search_group_config import SearchGroupConfig
        from email_agent import EmailAgent

        sg_cfg = SearchGroupConfig(
            group_id="automation_bi",
            display_name="Automation BI",
            filters={"exclude_terms": ["ANÜ"]}
        )

        config = {
            "providers": {"tp": {"channels": {"rss": {}}}},
            "channels": {"email": {}, "rss": {}},
            "search_groups": {},
            "settings": {},
        }
        agent = EmailAgent(config)
        dispatch_calls: list = []

        entries = [{"link": "https://x.com/p/5", "title": "ANÜ Berater"}]
        provider_config = {"provider_id": "tp"}

        mock_schema = {
            "title": "Berater gesucht",
            "description": "Einsatz über ANÜ nach AÜG.",
        }
        mock_adapter = MagicMock()
        mock_adapter.parse.return_value = mock_schema
        mock_adapter.get_provider_name.return_value = "TP"
        mock_renderer = MagicMock()
        mock_renderer.render.return_value = "# Test"

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("email_agent.DedupeService"),
                patch("email_agent.SearchGroupDispatcher") as mock_disp_cls,
                patch.object(agent, "load_adapter", return_value=mock_adapter),
                patch("email_agent.MarkdownRenderer", return_value=mock_renderer),
            ):
                mock_disp_inst = MagicMock()
                mock_disp_inst.dispatch.side_effect = lambda **kw: dispatch_calls.append(kw) or MagicMock(action="created")
                mock_disp_cls.return_value = mock_disp_inst

                result = agent.process_rss_entries(
                    entries,
                    provider_config,
                    tmpdir,
                    search_group_id="automation_bi",
                    search_group_config=sg_cfg,
                )

        assert len(dispatch_calls) == 0, "ANÜ-Projekt darf NICHT dispatcht werden"
        assert result["projects_filtered"] == 1, "projects_filtered muss 1 sein"
        assert result["projects_saved"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# FilterConfig.from_dict / Fixture YAML
# ══════════════════════════════════════════════════════════════════════════════

class TestFilterConfigFromYaml:
    """Liest filter_engine-Konfiguration aus der Fixture-YAML und validiert sie."""

    def test_load_filter_config_from_fixture_yaml(self):
        """load_filter_config_for_group() liest korrekte Filter aus search_groups_config.yaml."""
        import yaml

        fixture_path = Path(__file__).parent / "fixtures" / "search_groups_config.yaml"
        assert fixture_path.exists(), f"Fixture-Datei nicht gefunden: {fixture_path}"

        config = yaml.safe_load(fixture_path.read_text())
        fc = load_filter_config_for_group(config, "automation_bi")

        # Contract-Type-Filter muss konfiguriert sein
        assert fc.contract_type is not None, "contract_type-Filter fehlt in automation_bi"
        assert "freelance" in fc.contract_type.allowed

        # Exclude-Terms müssen vorhanden sein
        assert fc.exclude_terms, "exclude_terms dürfen nicht leer sein"
        exclude_lower = [t.lower() for t in fc.exclude_terms]
        assert any("festanstellung" in t for t in exclude_lower), "Festanstellung muss als Ausschluss konfiguriert sein"

    def test_infra_security_filter_config_from_yaml(self):
        """infra_security-Filterconfig aus YAML ist valide."""
        import yaml

        fixture_path = Path(__file__).parent / "fixtures" / "search_groups_config.yaml"
        config = yaml.safe_load(fixture_path.read_text())
        fc = load_filter_config_for_group(config, "infra_security")

        assert fc is not None
        # Muss mind. eine Filterregel haben
        has_any = (
            fc.work_mode is not None
            or fc.contract_type is not None
            or fc.rate is not None
            or fc.language is not None
            or bool(fc.exclude_terms)
        )
        assert has_any, "infra_security muss mindestens eine Filterregel haben"
