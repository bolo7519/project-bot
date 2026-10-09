"""
Tests für DedupeService (dedupe_service.py)

Prüft: URL-basierte Deduplizierung, Persistenz des Index,
Erkennung bekannter URLs und korrekte Schlüsselbildung.
API: DedupeService(state_dir), already_processed(provider_id, canonical_url),
     mark_processed(provider_id, canonical_url), canonicalize_url(url, provider_id)
"""

import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from dedupe_service import DedupeService


@pytest.fixture
def dedupe(tmp_path):
    """DedupeService mit temporärem Verzeichnis als State-Store."""
    return DedupeService(state_dir=str(tmp_path))


def _canonical(svc: DedupeService, provider: str, url: str) -> str:
    return svc.canonicalize_url(url, provider)


class TestDedupeService:
    def test_new_url_is_not_duplicate(self, dedupe):
        url = "https://www.freelancermap.de/projekt/12345"
        canonical = _canonical(dedupe, "freelancermap", url)
        assert dedupe.already_processed("freelancermap", canonical) is False

    def test_registered_url_is_duplicate(self, dedupe):
        url = "https://www.freelancermap.de/projekt/12345"
        canonical = _canonical(dedupe, "freelancermap", url)
        dedupe.mark_processed("freelancermap", canonical)
        assert dedupe.already_processed("freelancermap", canonical) is True

    def test_same_url_different_provider_not_duplicate(self, dedupe):
        """URL ist pro Provider getrennt indexiert."""
        url = "https://example.com/projekt/99"
        can_a = _canonical(dedupe, "freelancermap", url)
        dedupe.mark_processed("freelancermap", can_a)
        # Für den anderen Provider: canonicalize + check
        can_b = _canonical(dedupe, "freelance", url)
        assert dedupe.already_processed("freelance", can_b) is False

    def test_index_persists_to_disk(self, tmp_path):
        """Nach einem Neustart des Service soll der Index noch vorhanden sein."""
        url = "https://www.freelancermap.de/projekt/99999"

        svc1 = DedupeService(state_dir=str(tmp_path))
        canonical = svc1.canonicalize_url(url, "freelancermap")
        svc1.mark_processed("freelancermap", canonical)

        # Neues Objekt, selbes Verzeichnis
        svc2 = DedupeService(state_dir=str(tmp_path))
        assert svc2.already_processed("freelancermap", canonical) is True

    def test_empty_url_handled_gracefully(self, dedupe):
        """Leere URL soll keinen unkontrollierten Crash verursachen."""
        try:
            canonical = _canonical(dedupe, "freelancermap", "")
            result = dedupe.already_processed("freelancermap", canonical)
            assert isinstance(result, bool)
        except (ValueError, KeyError, AttributeError):
            pass  # Explizite Ablehnung ist auch akzeptabel
