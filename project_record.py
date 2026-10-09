"""
project_record.py — Einheitliches Datenmodell für Project Bot (Phase 1).

Schema v2 erweitert Schema v1 um:
- schema_version: 2          (stabile Versionierung)
- project_id:  <prefix>-<12hex>  (stabiler, quellenunabhängiger Identifier)
- search_groups: [str, …]    (mehrere Suchgruppen pro Projekt möglich)
- sources: [{provider, url, discovered_at}, …]   (alle Quellen die dieses Projekt melden)

Rückwärtskompatibilität: v1-Dateien (kein schema_version oder schema_version=1)
werden beim Lesen automatisch nach v2 hochkonvertiert; das Original bleibt auf Disk
unverändert bis migrate_to_v2.py explizit ausgeführt wird.

SICHERHEIT: Verschiedene Kundenprojekte werden NIEMALS automatisch zusammengeführt.
Nur URL-identische Datensätze desselben Projekts werden als Duplikat erkannt.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import yaml


# ──────────────────────────────────────────────────────────────────────────────
# Konstanten
# ──────────────────────────────────────────────────────────────────────────────

CURRENT_SCHEMA_VERSION = 2

# Präfixe für project_id — codiert die primäre Quelle
_ID_PREFIX_URL = "url"   # Aus kanonischer Anbieter-URL erzeugt (bevorzugt)
_ID_PREFIX_TITLE = "ttl"  # Fallback: aus Titel-Slug erzeugt (nur wenn keine URL)


# ──────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ──────────────────────────────────────────────────────────────────────────────

def _canonical_url(url: str) -> str:
    """Normalisiert eine URL für die ID-Erzeugung (scheme+host+path, kein Fragment/Query)."""
    from urllib.parse import urlparse
    p = urlparse(url.strip())
    return f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/')}"


def _make_project_id(provider_url: Optional[str], title: Optional[str]) -> str:
    """
    Erzeugt einen stabilen, quellenübergreifenden Identifier für ein Projekt.

    Priorität:
    1. Kanonische Anbieter-URL  → Präfix "url-"
    2. Titel-Slug               → Präfix "ttl-"  (Fallback, weniger stabil)

    Der Identifier ist deterministisch: dasselbe Projekt erzeugt immer dieselbe ID.

    Sicherheitshinweis: Unterschiedliche URLs erzeugen IMMER unterschiedliche IDs,
    auch wenn Titel ähnlich sind. Es gibt keine automatische Fuzzy-Zusammenführung.
    """
    if provider_url and provider_url.strip():
        canonical = _canonical_url(provider_url)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
        return f"{_ID_PREFIX_URL}-{digest}"

    if title and title.strip():
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
        digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()[:12]
        return f"{_ID_PREFIX_TITLE}-{digest}"

    # Letzter Ausweg: zufälliger Zeitstempel-Hash (sollte nie vorkommen)
    digest = hashlib.sha256(datetime.utcnow().isoformat().encode()).hexdigest()[:12]
    return f"unk-{digest}"


# ──────────────────────────────────────────────────────────────────────────────
# Datenklassen
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ProjectSource:
    """Eine Quelle, die dieses Projekt gemeldet hat."""
    provider: str                      # z.B. "freelancermap", "gulp", "email"
    url: str                           # Original-URL bei dieser Quelle
    discovered_at: str                 # ISO-8601-Timestamp
    channel: Optional[str] = None      # z.B. "rss", "scraper", "email"

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "provider": self.provider,
            "url": self.url,
            "discovered_at": self.discovered_at,
        }
        if self.channel:
            d["channel"] = self.channel
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ProjectSource":
        return ProjectSource(
            provider=d.get("provider", ""),
            url=d.get("url", ""),
            discovered_at=d.get("discovered_at", ""),
            channel=d.get("channel"),
        )


@dataclass
class ProjectRecord:
    """
    Einheitliches Datenmodell für ein Freelance-Projekt (Schema v2).

    Dieses Objekt ist die Single Source of Truth für alle Projektdaten.
    Es kann aus YAML-Frontmatter gelesen und wieder in Frontmatter serialisiert werden.

    Sicherheitsregel: Zwei ProjectRecord-Objekte mit verschiedenen project_ids
    dürfen NIEMALS zusammengeführt werden, auch wenn Titel oder Firma ähnlich sind.
    """

    # ── Pflichtfelder ──────────────────────────────────────────────────────────
    project_id: str                        # Stabiler Identifier (url-<12hex> oder ttl-<12hex>)
    title: str
    state: str

    # ── Herkunft & Schema ─────────────────────────────────────────────────────
    schema_version: int = CURRENT_SCHEMA_VERSION
    sources: List[ProjectSource] = field(default_factory=list)

    # ── Optionale Metadaten ───────────────────────────────────────────────────
    company: Optional[str] = None
    location: Optional[str] = None
    provider: Optional[str] = None          # Primärer Anbieter (v1-Kompatibilität)
    provider_url: Optional[str] = None      # Primäre URL (v1-Kompatibilität)

    # ── Suchgruppen (NEU in v2) ───────────────────────────────────────────────
    search_groups: List[str] = field(default_factory=list)
    # Ermöglicht: ein Projekt passt zu mehreren Kompetenzprofilen
    # Beispiel: ["automation-bi", "infra-security"]
    # Sicherheit: Verschiedene Projekte werden NICHT wegen überlappender
    # Suchgruppen zusammengeführt — nur project_id entscheidet über Identität.

    # ── Zeitstempel ───────────────────────────────────────────────────────────
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    # ── Bewertung ─────────────────────────────────────────────────────────────
    pre_eval_score: Optional[int] = None
    llm_score: Optional[int] = None
    fit_score: Optional[int] = None         # Alias für llm_score (v1-Kompatibilität)

    # ── State-History ─────────────────────────────────────────────────────────
    state_history: List[Dict[str, Any]] = field(default_factory=list)

    # ── Sonstige v1-Felder (durchgeleitet, damit kein Datenverlust) ───────────
    extra: Dict[str, Any] = field(default_factory=dict)

    # ──────────────────────────────────────────────────────────────────────────
    # Serialisierung
    # ──────────────────────────────────────────────────────────────────────────

    def to_frontmatter_dict(self) -> Dict[str, Any]:
        """Gibt ein Dict zurück, das als YAML-Frontmatter in eine Markdown-Datei geschrieben wird."""
        d: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "title": self.title,
            "state": self.state,
        }

        # Optionale skalare Felder
        for attr in ("company", "location", "provider", "provider_url",
                     "created_at", "updated_at"):
            v = getattr(self, attr)
            if v is not None:
                d[attr] = v

        # Score-Felder
        for attr in ("pre_eval_score", "llm_score", "fit_score"):
            v = getattr(self, attr)
            if v is not None:
                d[attr] = v

        # Listen
        if self.search_groups:
            d["search_groups"] = list(self.search_groups)

        if self.sources:
            d["sources"] = [s.to_dict() for s in self.sources]

        if self.state_history:
            d["state_history"] = list(self.state_history)

        # Durchgeleitete v1-Felder
        for k, v in self.extra.items():
            if k not in d:
                d[k] = v

        return d

    def to_frontmatter_text(self) -> str:
        """Gibt den vollständigen YAML-Frontmatter-Block als String zurück."""
        return "---\n" + yaml.dump(
            self.to_frontmatter_dict(),
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ) + "---\n\n"

    # ──────────────────────────────────────────────────────────────────────────
    # Deserialisierung
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def from_frontmatter_dict(d: Dict[str, Any]) -> "ProjectRecord":
        """
        Erzeugt einen ProjectRecord aus einem YAML-Frontmatter-Dict.

        Unterstützt Schema v1 (kein schema_version / schema_version=1) und v2.
        Rückwärtskompatibilität: Fehlende v2-Felder werden aus v1-Feldern abgeleitet.
        """
        schema_version = int(d.get("schema_version", 1))

        # ── project_id ────────────────────────────────────────────────────────
        project_id = d.get("project_id")
        if not project_id:
            # v1-Migration: ID aus URL oder Titel ableiten
            project_id = _make_project_id(
                provider_url=d.get("provider_url"),
                title=d.get("title"),
            )

        # ── sources ───────────────────────────────────────────────────────────
        sources_raw = d.get("sources", [])
        if sources_raw and isinstance(sources_raw, list):
            sources = [
                ProjectSource.from_dict(s) if isinstance(s, dict) else
                ProjectSource(provider="unknown", url=str(s), discovered_at="")
                for s in sources_raw
            ]
        else:
            # v1-Migration: provider + provider_url → erste Quelle
            sources = []
            if d.get("provider") and d.get("provider_url"):
                sources.append(ProjectSource(
                    provider=d["provider"],
                    url=d["provider_url"],
                    discovered_at=d.get("created_at", ""),
                    channel=None,
                ))

        # ── search_groups ─────────────────────────────────────────────────────
        sg = d.get("search_groups", [])
        if isinstance(sg, str):
            sg = [sg]
        search_groups = list(sg)

        # ── Bekannte Felder extrahieren ───────────────────────────────────────
        known_keys = {
            "schema_version", "project_id", "title", "state",
            "company", "location", "provider", "provider_url",
            "created_at", "updated_at",
            "pre_eval_score", "llm_score", "fit_score",
            "search_groups", "sources", "state_history",
        }
        extra = {k: v for k, v in d.items() if k not in known_keys}

        return ProjectRecord(
            schema_version=CURRENT_SCHEMA_VERSION,   # immer auf aktuell setzen
            project_id=project_id,
            title=d.get("title", ""),
            state=d.get("state", "scraped"),
            sources=sources,
            company=d.get("company"),
            location=d.get("location"),
            provider=d.get("provider"),
            provider_url=d.get("provider_url"),
            search_groups=search_groups,
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
            pre_eval_score=d.get("pre_eval_score"),
            llm_score=d.get("llm_score"),
            fit_score=d.get("fit_score"),
            state_history=d.get("state_history", []),
            extra=extra,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Hilfsmethoden
    # ──────────────────────────────────────────────────────────────────────────

    def effective_score(self) -> int:
        """Gibt den besten verfügbaren Score zurück (llm > fit > pre_eval > 0)."""
        return self.llm_score or self.fit_score or self.pre_eval_score or 0

    def add_source(self, source: ProjectSource) -> None:
        """
        Fügt eine Quelle hinzu, sofern die URL noch nicht vorhanden ist.

        Sicherheitsregel: Es wird NUR geprüft ob die URL identisch ist.
        Keine inhaltliche/semantische Zusammenführung.
        """
        existing_urls = {s.url for s in self.sources}
        if source.url not in existing_urls:
            self.sources.append(source)

    def add_search_group(self, group: str) -> None:
        """Fügt eine Suchgruppe hinzu (nur wenn noch nicht vorhanden)."""
        if group not in self.search_groups:
            self.search_groups.append(group)

    def is_v1(self) -> bool:
        """True wenn das zugrundeliegende File Schema v1 hatte (project_id war abgeleitet)."""
        return self.schema_version < CURRENT_SCHEMA_VERSION

    def same_project_as(self, other: "ProjectRecord") -> bool:
        """
        Prüft ob two Records dasselbe Projekt beschreiben.

        AUSSCHLIESSLICH anhand project_id — keine Fuzzy-Logik, kein Titel-Vergleich.
        Unterschiedliche project_ids = unterschiedliche Projekte, immer.
        """
        return self.project_id == other.project_id
