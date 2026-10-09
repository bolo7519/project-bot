"""
Tests für quellenübergreifende Deduplizierung (Phase 1).

Geprüft:
- Dasselbe Projekt von zwei Anbietern → einmal verarbeitet
- Verschiedene Projekte → beide verarbeitet
- Persistenz der project_id über Instanzgrenzen
- v1-kompatible URL-Keys bleiben funktionsfähig
- Keine automatische Zusammenführung verschiedener Projekte
- DedupeService.already_processed_by_id() / mark_processed_by_id()
"""

import json
import pytest
from pathlib import Path

from dedupe_service import DedupeService
from project_record import ProjectRecord, ProjectSource, _make_project_id


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def dedupe(tmp_path):
    """Frische DedupeService-Instanz mit tmpdir."""
    return DedupeService(str(tmp_path))


@pytest.fixture
def dedupe_dir(tmp_path):
    """Gibt nur das tmp-Verzeichnis zurück (für Persistenz-Tests)."""
    return str(tmp_path)


# ──────────────────────────────────────────────────────────────────────────────
# project_id-basierte Deduplizierung (Phase 1)
# ──────────────────────────────────────────────────────────────────────────────

class TestProjectIdDedupe:
    def test_new_project_id_not_processed(self, dedupe):
        """Neue project_id → noch nicht verarbeitet."""
        assert not dedupe.already_processed_by_id("url-aabbcc112233")

    def test_marked_project_id_is_processed(self, dedupe):
        """Nach mark_processed_by_id → already_processed_by_id True."""
        pid = "url-aabbcc112233"
        dedupe.mark_processed_by_id(pid, "freelancermap", "https://example.com/1")
        assert dedupe.already_processed_by_id(pid)

    def test_cross_source_same_project_detected(self, dedupe):
        """
        QUELLENÜBERGREIFEND: Dasselbe Projekt von zwei Anbietern wird
        als Duplikat erkannt, sobald es per project_id gesehen wurde.
        """
        url = "https://www.freelancermap.de/projekt/99999"
        pid = _make_project_id(provider_url=url, title=None)

        # Beim ersten Mal von freelancermap gesehen
        canonical = f"https://www.freelancermap.de/projekt/99999"
        dedupe.mark_processed_by_id(pid, "freelancermap", canonical)

        # Wenn gulp dasselbe Projekt meldet (gleiche project_id, andere Quelle)
        assert dedupe.already_processed_by_id(pid), (
            "Dasselbe Projekt sollte als Duplikat erkannt werden, egal von welcher Quelle."
        )

    def test_different_projects_both_processed(self, dedupe):
        """Verschiedene Projekte werden unabhängig voneinander verarbeitet."""
        pid1 = _make_project_id(provider_url="https://example.com/1", title=None)
        pid2 = _make_project_id(provider_url="https://example.com/2", title=None)

        assert pid1 != pid2  # Voraussetzung

        dedupe.mark_processed_by_id(pid1, "provider_a", "https://example.com/1")

        assert dedupe.already_processed_by_id(pid1)
        assert not dedupe.already_processed_by_id(pid2), (
            "Verschiedene Projekte dürfen sich nicht gegenseitig als verarbeitet markieren."
        )

    def test_similar_titles_different_urls_not_confused(self, dedupe):
        """
        SICHERHEITSTEST: Ähnliche Titel, aber verschiedene URLs
        → werden NICHT als dasselbe Projekt erkannt.
        """
        url1 = "https://example.com/projekt/111"
        url2 = "https://example.com/projekt/222"
        pid1 = _make_project_id(provider_url=url1, title="IT Projektmanager")
        pid2 = _make_project_id(provider_url=url2, title="IT Projektmanager")

        assert pid1 != pid2  # Sicherheitsvoraussetzung

        dedupe.mark_processed_by_id(pid1, "provider_a", url1)

        assert dedupe.already_processed_by_id(pid1)
        assert not dedupe.already_processed_by_id(pid2), (
            "SICHERHEITSVERLETZUNG: Zwei verschiedene Projekte mit ähnlichem Titel "
            "wurden als identisch erkannt!"
        )

    def test_get_processed_project_ids(self, dedupe):
        """get_processed_project_ids gibt alle markierten IDs zurück."""
        pid1 = "url-aabbcc112233"
        pid2 = "url-ddeeff445566"
        dedupe.mark_processed_by_id(pid1, "provider_a", "https://a.com/1")
        dedupe.mark_processed_by_id(pid2, "provider_b", "https://b.com/2")
        ids = dedupe.get_processed_project_ids()
        assert pid1 in ids
        assert pid2 in ids


# ──────────────────────────────────────────────────────────────────────────────
# Persistenz über Instanzgrenzen
# ──────────────────────────────────────────────────────────────────────────────

class TestPersistence:
    def test_project_id_persists_across_instances(self, dedupe_dir):
        """project_id bleibt nach Neustart der Instanz bekannt."""
        pid = "url-aabbcc112233"

        # Instanz 1: markieren
        svc1 = DedupeService(dedupe_dir)
        svc1.mark_processed_by_id(pid, "freelancermap", "https://example.com/1")

        # Instanz 2: prüfen (simuliert Neustart)
        svc2 = DedupeService(dedupe_dir)
        assert svc2.already_processed_by_id(pid)

    def test_url_key_persists_across_instances(self, dedupe_dir):
        """v1 URL-Key bleibt nach Neustart der Instanz bekannt."""
        svc1 = DedupeService(dedupe_dir)
        svc1.mark_processed("freelancermap", "https://example.com/1")

        svc2 = DedupeService(dedupe_dir)
        assert svc2.already_processed("freelancermap", "https://example.com/1")

    def test_v2_format_stored_on_disk(self, dedupe_dir):
        """Die Datei auf Disk verwendet das v2-Format (Dict mit url_keys + project_ids)."""
        svc = DedupeService(dedupe_dir)
        svc.mark_processed_by_id("url-test123456ab", "provider", "https://example.com/1")

        state_file = Path(dedupe_dir) / ".processed_urls.json"
        data = json.loads(state_file.read_text(encoding="utf-8"))

        assert "url_keys" in data, "v2-Format erwartet 'url_keys'"
        assert "project_ids" in data, "v2-Format erwartet 'project_ids'"
        assert "url-test123456ab" in data["project_ids"]

    def test_v1_file_loaded_correctly(self, dedupe_dir):
        """v1-Datei (Liste) wird korrekt geladen (Rückwärtskompatibilität)."""
        state_file = Path(dedupe_dir) / ".processed_urls.json"
        # v1-Format: einfache Liste
        v1_data = ["freelancermap:https://example.com/old-projekt"]
        state_file.write_text(json.dumps(v1_data), encoding="utf-8")

        svc = DedupeService(dedupe_dir)
        # v1-URL noch erkannt
        assert svc.already_processed("freelancermap", "https://example.com/old-projekt")
        # project_ids leer (v1 hatte keine)
        assert len(svc.get_processed_project_ids()) == 0


# ──────────────────────────────────────────────────────────────────────────────
# v1-Kompatibilität
# ──────────────────────────────────────────────────────────────────────────────

class TestV1Compatibility:
    def test_already_processed_still_works(self, dedupe):
        """v1-Methode already_processed() funktioniert weiterhin."""
        dedupe.mark_processed("freelancermap", "https://example.com/1")
        assert dedupe.already_processed("freelancermap", "https://example.com/1")
        assert not dedupe.already_processed("freelancermap", "https://example.com/2")

    def test_get_processed_count_still_works(self, dedupe):
        """v1-Methode get_processed_count() zählt URL-Keys."""
        dedupe.mark_processed("provider_a", "https://a.com/1")
        dedupe.mark_processed("provider_a", "https://a.com/2")
        dedupe.mark_processed("provider_b", "https://b.com/1")

        assert dedupe.get_processed_count("provider_a") == 2
        assert dedupe.get_processed_count("provider_b") == 1
        assert dedupe.get_processed_count() == 3

    def test_mark_processed_by_id_also_sets_url_key(self, dedupe):
        """mark_processed_by_id() setzt auch den URL-Key (Dual-Write)."""
        pid = "url-aabbcc112233"
        dedupe.mark_processed_by_id(pid, "freelancermap", "https://example.com/1")

        # URL-Key ebenfalls bekannt
        assert dedupe.already_processed("freelancermap", "https://example.com/1")
