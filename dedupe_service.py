"""
Dedupe Service for Project Bot

Handles provider-aware deduplication of project URLs to prevent reprocessing.

Phase 1: Erweitert um project_id-basierte Deduplizierung (quellenübergreifend).

Schlüssel-Hierarchie:
1. project_id  (quellenübergreifend, stabil) — Primärschlüssel seit Phase 1
2. provider_id + canonical_url              — Sekundärschlüssel (v1-Kompatibilität)

Sicherheitsregel: Zwei Datensätze werden NUR dann als Duplikat erkannt, wenn ihre
project_id IDENTISCH ist. Keine Fuzzy-Logik, kein Titel-Vergleich.
"""

import os
import json
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlunparse


class DedupeService:
    """
    Service for deduplicating project URLs across providers.

    Primary key (Phase 1): project_id (source-independent, stable)
    Secondary key (v1-compat): provider_id + canonical_url
    """

    def __init__(self, state_dir: str = "projects"):
        """
        Initialize the dedupe service.

        Args:
            state_dir: Directory where processed URLs are tracked
        """
        self.state_dir = state_dir
        self.processed_file = os.path.join(state_dir, ".processed_urls.json")
        self._ensure_state_dir()
        self._load_processed()

    def _ensure_state_dir(self) -> None:
        """Ensure the state directory exists."""
        os.makedirs(self.state_dir, exist_ok=True)

    def _load_processed(self) -> None:
        """Load the set of processed keys from disk."""
        if os.path.exists(self.processed_file):
            try:
                with open(self.processed_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Phase 1: data kann List (v1) oder Dict (v2) sein
                    if isinstance(data, list):
                        # v1-Format: nur URL-Keys
                        self.processed = set(data)
                        self._project_ids: set = set()
                    elif isinstance(data, dict):
                        # v2-Format: {"url_keys": [...], "project_ids": [...]}
                        self.processed = set(data.get("url_keys", []))
                        self._project_ids = set(data.get("project_ids", []))
                    else:
                        self.processed = set()
                        self._project_ids = set()
            except (json.JSONDecodeError, FileNotFoundError):
                self.processed = set()
                self._project_ids: set = set()
        else:
            self.processed = set()
            self._project_ids: set = set()

    def _save_processed(self) -> None:
        """Save the processed keys to disk (v2-Format)."""
        data = {
            "url_keys": list(self.processed),
            "project_ids": list(self._project_ids),
        }
        with open(self.processed_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    def canonicalize_url(self, url: str, provider_id: str) -> str:
        """
        Canonicalize a URL for deduplication.

        Provider-specific rules:
        - Strip tracking parameters
        - Normalize host/path

        Args:
            url: The URL to canonicalize
            provider_id: Provider identifier for custom rules

        Returns:
            Canonicalized URL string
        """
        parsed = urlparse(url)

        # Remove common tracking parameters
        query_params = parse_qs(parsed.query)
        tracking_params = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
                          'fbclid', 'gclid', 'msclkid', 'ref', 'source'}

        # Provider-specific tracking params
        if provider_id == 'freelancermap':
            tracking_params.update({'ref', 'source'})

        # Remove tracking params
        filtered_query = {k: v for k, v in query_params.items() if k not in tracking_params}

        # Reconstruct query string
        query = '&'.join(f"{k}={v[0]}" for k, v in filtered_query.items()) if filtered_query else ''

        # Normalize path (remove trailing slashes, etc.)
        path = parsed.path.rstrip('/')

        # Reconstruct canonical URL
        canonical = urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.params,
            query,
            ''  # Remove fragment
        ))

        return canonical

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 1: project_id-basierte Methoden (quellenübergreifend)
    # ──────────────────────────────────────────────────────────────────────────

    def already_processed_by_id(self, project_id: str) -> bool:
        """
        Prüft ob ein Projekt anhand seiner stabilen project_id bereits verarbeitet wurde.

        Quellenübergreifend: dasselbe Projekt wird erkannt, egal von welchem
        Provider es erneut gemeldet wird.

        Sicherheitsregel: Nur exakte project_id-Übereinstimmung zählt.
        Kein Titel-Vergleich, keine Fuzzy-Logik.

        Args:
            project_id: Stabiler Projekt-Identifier (z.B. "url-a06de2aecc89")

        Returns:
            True wenn bereits verarbeitet
        """
        return project_id in self._project_ids

    def mark_processed_by_id(self, project_id: str, provider_id: str, canonical_url: str) -> None:
        """
        Markiert ein Projekt sowohl per project_id als auch per URL-Key als verarbeitet.

        Schreibt beide Schlüssel, damit:
        - quellenübergreifende Deduplizierung via project_id funktioniert
        - v1-Code weiterhin via provider_id + URL deduplizieren kann

        Args:
            project_id: Stabiler Projekt-Identifier
            provider_id: Provider-Identifier (z.B. "freelancermap")
            canonical_url: Kanonisierte URL
        """
        self._project_ids.add(project_id)
        url_key = f"{provider_id}:{canonical_url}"
        self.processed.add(url_key)
        self._save_processed()

    def get_processed_project_ids(self) -> set:
        """Gibt alle verarbeiteten project_ids zurück."""
        return set(self._project_ids)

    # ──────────────────────────────────────────────────────────────────────────
    # v1-kompatible URL-basierte Methoden (unverändert)
    # ──────────────────────────────────────────────────────────────────────────

    def already_processed(self, provider_id: str, canonical_url: str) -> bool:
        """
        Check if a URL has already been processed.

        Args:
            provider_id: Provider identifier
            canonical_url: Canonicalized URL

        Returns:
            True if already processed, False otherwise
        """
        key = f"{provider_id}:{canonical_url}"
        return key in self.processed

    def mark_processed(self, provider_id: str, canonical_url: str) -> None:
        """
        Mark a URL as processed (v1-kompatibel, ohne project_id).

        Bevorzuge mark_processed_by_id() wenn eine project_id verfügbar ist.

        Args:
            provider_id: Provider identifier
            canonical_url: Canonicalized URL
        """
        key = f"{provider_id}:{canonical_url}"
        self.processed.add(url_key := key)
        self._save_processed()

    def get_processed_count(self, provider_id: Optional[str] = None) -> int:
        """
        Get the count of processed URL keys.

        Args:
            provider_id: Optional provider filter

        Returns:
            Number of processed URL keys
        """
        if provider_id:
            return len([k for k in self.processed if k.startswith(f"{provider_id}:")])
        return len(self.processed)