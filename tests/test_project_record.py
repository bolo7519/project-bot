"""
Tests für project_record.py — Datenmodell Schema v2 (Phase 1).

Geprüft:
- ProjectRecord-Erzeugung und Serialisierung
- Stabile project_id (URL-basiert und Titel-basiert)
- Rückwärtskompatibilität: v1-Dict → ProjectRecord
- search_groups: Mehrere Suchgruppen pro Projekt
- sources: Mehrere Quellen pro Projekt
- same_project_as(): Identitätsprüfung NUR per project_id
- Sicherheitstest: Verschiedene URLs → immer verschiedene IDs (kein Merge)
"""

import pytest
from project_record import (
    ProjectRecord,
    ProjectSource,
    CURRENT_SCHEMA_VERSION,
    _make_project_id,
    _canonical_url,
)


# ──────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ──────────────────────────────────────────────────────────────────────────────

def _minimal_v1_dict(**kwargs):
    """Erzeugt ein minimales v1-Frontmatter-Dict."""
    d = {
        "title": "Test Projekt",
        "company": "Acme GmbH",
        "state": "scraped",
        "provider": "freelancermap",
        "provider_url": "https://www.freelancermap.de/projekt/12345",
        "created_at": "2026-10-01T10:00:00",
    }
    d.update(kwargs)
    return d


def _minimal_v2_dict(**kwargs):
    """Erzeugt ein minimales v2-Frontmatter-Dict."""
    d = _minimal_v1_dict()
    d.update({
        "schema_version": 2,
        "project_id": "url-testid123456",
        "search_groups": ["automation-bi"],
        "sources": [{
            "provider": "freelancermap",
            "url": "https://www.freelancermap.de/projekt/12345",
            "discovered_at": "2026-10-01T10:00:00",
        }],
    })
    d.update(kwargs)
    return d


# ──────────────────────────────────────────────────────────────────────────────
# Stabile project_id
# ──────────────────────────────────────────────────────────────────────────────

class TestProjectIdGeneration:
    def test_url_based_id_is_stable(self):
        """Dieselbe URL erzeugt immer dieselbe project_id."""
        url = "https://www.freelancermap.de/projekt/99999"
        id1 = _make_project_id(provider_url=url, title=None)
        id2 = _make_project_id(provider_url=url, title=None)
        assert id1 == id2

    def test_url_based_id_has_correct_prefix(self):
        id_ = _make_project_id(provider_url="https://example.com/projekt/1", title=None)
        assert id_.startswith("url-")

    def test_title_based_id_has_correct_prefix(self):
        id_ = _make_project_id(provider_url=None, title="Senior Python Developer")
        assert id_.startswith("ttl-")

    def test_url_takes_priority_over_title(self):
        """URL hat Vorrang vor Titel bei der ID-Erzeugung."""
        id_url = _make_project_id(provider_url="https://example.com/1", title="Titel A")
        id_ttl = _make_project_id(provider_url=None, title="Titel A")
        assert id_url.startswith("url-")
        assert id_ttl.startswith("ttl-")
        assert id_url != id_ttl

    def test_different_urls_produce_different_ids(self):
        """SICHERHEITSTEST: Verschiedene URLs → IMMER verschiedene IDs."""
        id1 = _make_project_id(provider_url="https://example.com/projekt/1", title="Gleicher Titel")
        id2 = _make_project_id(provider_url="https://example.com/projekt/2", title="Gleicher Titel")
        assert id1 != id2, (
            "SICHERHEITSVERLETZUNG: Verschiedene URLs haben dieselbe project_id erzeugt! "
            "Das würde zu ungewollter Projektzusammenführung führen."
        )

    def test_similar_titles_different_companies_different_ids(self):
        """SICHERHEITSTEST: Ähnliche Titel ohne URL → verschiedene IDs wenn leicht verschieden."""
        # Wenn zwei Kunden exakt denselben Titel verwenden UND keine URL da ist,
        # würden sie dieselbe ID bekommen — das ist dokumentiertes Verhalten (Fallback).
        # Unterschiedliche Unternehmen mit leicht verschiedenen Titeln → verschiedene IDs.
        id1 = _make_project_id(provider_url=None, title="IT Projektmanager (m/w/d) - Acme GmbH")
        id2 = _make_project_id(provider_url=None, title="IT Projektmanager (m/w/d) - Beta Corp")
        assert id1 != id2

    def test_url_canonical_normalization(self):
        """Trailing Slash und Query-Parameter ändern die URL-Basis nicht."""
        url1 = "https://www.freelancermap.de/projekt/99999"
        url2 = "https://www.freelancermap.de/projekt/99999/"
        # Beide sollten auf dieselbe Basis normalisiert werden
        assert _canonical_url(url1) == _canonical_url(url2)

    def test_id_length_is_fixed(self):
        """project_id hat immer die Form '<prefix>-<12hex>'."""
        id_ = _make_project_id(provider_url="https://example.com/1", title=None)
        parts = id_.split("-", 1)
        assert len(parts) == 2
        assert len(parts[1]) == 12
        assert all(c in "0123456789abcdef" for c in parts[1])


# ──────────────────────────────────────────────────────────────────────────────
# ProjectRecord — Erzeugung
# ──────────────────────────────────────────────────────────────────────────────

class TestProjectRecordCreation:
    def test_from_v1_dict_assigns_project_id(self):
        """v1-Dict ohne project_id bekommt automatisch eine ID."""
        d = _minimal_v1_dict()
        assert "project_id" not in d
        record = ProjectRecord.from_frontmatter_dict(d)
        assert record.project_id
        assert record.project_id.startswith("url-")

    def test_from_v1_dict_schema_version_upgraded(self):
        """v1-Dict wird beim Lesen auf schema_version=2 hochgesetzt."""
        d = _minimal_v1_dict()
        record = ProjectRecord.from_frontmatter_dict(d)
        assert record.schema_version == CURRENT_SCHEMA_VERSION

    def test_from_v2_dict_preserves_project_id(self):
        """v2-Dict mit vorhandener project_id behält diese."""
        d = _minimal_v2_dict(project_id="url-aabbcc112233")
        record = ProjectRecord.from_frontmatter_dict(d)
        assert record.project_id == "url-aabbcc112233"

    def test_from_v1_dict_sources_extracted(self):
        """provider+provider_url aus v1 wird als erster sources-Eintrag übernommen."""
        d = _minimal_v1_dict()
        record = ProjectRecord.from_frontmatter_dict(d)
        assert len(record.sources) == 1
        assert record.sources[0].provider == "freelancermap"
        assert record.sources[0].url == "https://www.freelancermap.de/projekt/12345"

    def test_from_v2_dict_multiple_sources(self):
        """v2-Dict mit mehreren sources wird korrekt deserialisiert."""
        d = _minimal_v2_dict(sources=[
            {"provider": "freelancermap", "url": "https://freelancermap.de/p/1", "discovered_at": "2026-10-01T10:00:00"},
            {"provider": "gulp", "url": "https://gulp.de/p/abc", "discovered_at": "2026-10-02T08:00:00", "channel": "rss"},
        ])
        record = ProjectRecord.from_frontmatter_dict(d)
        assert len(record.sources) == 2
        assert record.sources[1].provider == "gulp"
        assert record.sources[1].channel == "rss"

    def test_search_groups_from_v2(self):
        """search_groups werden aus v2-Dict übernommen."""
        d = _minimal_v2_dict(search_groups=["automation-bi", "infra-security"])
        record = ProjectRecord.from_frontmatter_dict(d)
        assert "automation-bi" in record.search_groups
        assert "infra-security" in record.search_groups

    def test_search_groups_empty_for_v1(self):
        """v1-Dict ohne search_groups → leere Liste."""
        record = ProjectRecord.from_frontmatter_dict(_minimal_v1_dict())
        assert record.search_groups == []

    def test_extra_fields_preserved(self):
        """Unbekannte v1-Felder gehen nicht verloren."""
        d = _minimal_v1_dict()
        d["custom_field"] = "custom_value"
        d["another_field"] = 42
        record = ProjectRecord.from_frontmatter_dict(d)
        assert record.extra.get("custom_field") == "custom_value"
        assert record.extra.get("another_field") == 42


# ──────────────────────────────────────────────────────────────────────────────
# Serialisierung: to_frontmatter_dict / to_frontmatter_text
# ──────────────────────────────────────────────────────────────────────────────

class TestProjectRecordSerialization:
    def test_roundtrip_v1_to_v2(self):
        """v1 lesen → serialisieren → v2 lesen: Daten bleiben erhalten."""
        d_v1 = _minimal_v1_dict()
        record_v1 = ProjectRecord.from_frontmatter_dict(d_v1)
        fm_v2 = record_v1.to_frontmatter_dict()

        record_v2 = ProjectRecord.from_frontmatter_dict(fm_v2)
        assert record_v2.project_id == record_v1.project_id
        assert record_v2.title == record_v1.title
        assert record_v2.state == record_v1.state
        assert record_v2.schema_version == CURRENT_SCHEMA_VERSION

    def test_serialized_dict_has_schema_version(self):
        record = ProjectRecord.from_frontmatter_dict(_minimal_v1_dict())
        d = record.to_frontmatter_dict()
        assert d["schema_version"] == CURRENT_SCHEMA_VERSION

    def test_serialized_dict_has_project_id(self):
        record = ProjectRecord.from_frontmatter_dict(_minimal_v1_dict())
        d = record.to_frontmatter_dict()
        assert "project_id" in d
        assert d["project_id"]

    def test_frontmatter_text_starts_with_dashes(self):
        record = ProjectRecord.from_frontmatter_dict(_minimal_v1_dict())
        text = record.to_frontmatter_text()
        assert text.startswith("---\n")
        assert "---\n\n" in text  # Abschluss-Separator + Leerzeile

    def test_search_groups_in_serialized_output(self):
        d = _minimal_v2_dict(search_groups=["automation-bi"])
        record = ProjectRecord.from_frontmatter_dict(d)
        out = record.to_frontmatter_dict()
        assert out.get("search_groups") == ["automation-bi"]

    def test_sources_in_serialized_output(self):
        d = _minimal_v1_dict()
        record = ProjectRecord.from_frontmatter_dict(d)
        out = record.to_frontmatter_dict()
        assert "sources" in out
        assert len(out["sources"]) == 1

    def test_extra_fields_in_serialized_output(self):
        d = _minimal_v1_dict()
        d["custom_tag"] = "wichtig"
        record = ProjectRecord.from_frontmatter_dict(d)
        out = record.to_frontmatter_dict()
        assert out.get("custom_tag") == "wichtig"


# ──────────────────────────────────────────────────────────────────────────────
# Identitätsprüfung: same_project_as()
# ──────────────────────────────────────────────────────────────────────────────

class TestProjectIdentity:
    def test_same_url_same_project(self):
        """Zwei Records mit gleicher URL → same_project_as() == True."""
        url = "https://www.freelancermap.de/projekt/99999"
        r1 = ProjectRecord.from_frontmatter_dict(_minimal_v1_dict(provider_url=url))
        r2 = ProjectRecord.from_frontmatter_dict(_minimal_v1_dict(provider_url=url))
        assert r1.same_project_as(r2)

    def test_different_url_different_project(self):
        """SICHERHEITSTEST: Verschiedene URLs → same_project_as() == False."""
        r1 = ProjectRecord.from_frontmatter_dict(
            _minimal_v1_dict(provider_url="https://example.com/projekt/1")
        )
        r2 = ProjectRecord.from_frontmatter_dict(
            _minimal_v1_dict(provider_url="https://example.com/projekt/2")
        )
        assert not r1.same_project_as(r2), (
            "SICHERHEITSVERLETZUNG: Verschiedene Projekte wurden fälschlicherweise "
            "als identisch erkannt. Das könnte zu Datenverlust führen."
        )

    def test_same_title_different_url_different_project(self):
        """SICHERHEITSTEST: Gleicher Titel, verschiedene URLs → verschiedene Projekte."""
        r1 = ProjectRecord.from_frontmatter_dict(_minimal_v1_dict(
            title="IT Projektmanager",
            provider_url="https://example.com/projekt/111",
        ))
        r2 = ProjectRecord.from_frontmatter_dict(_minimal_v1_dict(
            title="IT Projektmanager",
            provider_url="https://example.com/projekt/222",
        ))
        assert not r1.same_project_as(r2), (
            "SICHERHEITSVERLETZUNG: Gleicher Titel reicht nicht für Projektidentität — "
            "nur project_id (aus URL) entscheidet."
        )

    def test_identity_only_by_project_id(self):
        """same_project_as() prüft AUSSCHLIESSLICH project_id."""
        r1 = ProjectRecord.from_frontmatter_dict(_minimal_v2_dict(
            project_id="url-aabbcc112233",
            title="Projekt A",
            company="Firma X",
        ))
        r2 = ProjectRecord.from_frontmatter_dict(_minimal_v2_dict(
            project_id="url-aabbcc112233",
            title="Projekt B (andere Firma)",
            company="Firma Y",
        ))
        # Gleiche project_id → gleiches Projekt (auch wenn Titel/Firma verschieden)
        assert r1.same_project_as(r2)


# ──────────────────────────────────────────────────────────────────────────────
# Hilfsmethoden
# ──────────────────────────────────────────────────────────────────────────────

class TestProjectRecordHelpers:
    def test_add_source_prevents_duplicates(self):
        """Dieselbe URL wird nicht zweimal als Quelle hinzugefügt."""
        record = ProjectRecord.from_frontmatter_dict(_minimal_v1_dict())
        assert len(record.sources) == 1
        # Dieselbe URL nochmal hinzufügen
        record.add_source(ProjectSource(
            provider="freelancermap",
            url="https://www.freelancermap.de/projekt/12345",
            discovered_at="2026-10-05T12:00:00",
        ))
        assert len(record.sources) == 1  # Immer noch nur eine

    def test_add_source_new_url_added(self):
        """Neue URL von anderem Provider wird hinzugefügt."""
        record = ProjectRecord.from_frontmatter_dict(_minimal_v1_dict())
        record.add_source(ProjectSource(
            provider="gulp",
            url="https://www.gulp.de/projekt/xyz",
            discovered_at="2026-10-03T08:00:00",
        ))
        assert len(record.sources) == 2

    def test_add_search_group_prevents_duplicates(self):
        record = ProjectRecord.from_frontmatter_dict(_minimal_v1_dict())
        record.add_search_group("automation-bi")
        record.add_search_group("automation-bi")
        assert record.search_groups.count("automation-bi") == 1

    def test_add_multiple_search_groups(self):
        record = ProjectRecord.from_frontmatter_dict(_minimal_v1_dict())
        record.add_search_group("automation-bi")
        record.add_search_group("infra-security")
        assert len(record.search_groups) == 2

    def test_effective_score_prefers_llm_score(self):
        d = _minimal_v1_dict()
        d["pre_eval_score"] = 40
        d["llm_score"] = 88
        record = ProjectRecord.from_frontmatter_dict(d)
        assert record.effective_score() == 88

    def test_effective_score_fallback_to_pre_eval(self):
        d = _minimal_v1_dict()
        d["pre_eval_score"] = 55
        record = ProjectRecord.from_frontmatter_dict(d)
        assert record.effective_score() == 55

    def test_effective_score_zero_if_no_scores(self):
        record = ProjectRecord.from_frontmatter_dict(_minimal_v1_dict())
        assert record.effective_score() == 0
