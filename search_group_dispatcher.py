"""
search_group_dispatcher.py — Routing-Logik für Suchgruppen (Phase 2).

Der SearchGroupDispatcher entscheidet, was mit einem neu gefundenen Projekt
passiert:

1. Ist das Projekt (per project_id) bereits bekannt?
   → Suchgruppe am vorhandenen ProjectRecord ergänzen; keine neue Datei.

2. Ist das Projekt neu?
   → Neue Projektdatei mit Schema v2 anlegen, Suchgruppe eintragen,
     project_id im DedupeService registrieren.

Sicherheitsgarantie:
- Identitätsprüfung ausschließlich per project_id (kein Fuzzy-Matching).
- Zwei verschiedene Kundenprojekte mit unterschiedlicher URL → immer
  verschiedene project_ids → niemals zusammengeführt.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dedupe_service import DedupeService
from project_record import ProjectRecord, ProjectSource, _make_project_id, _canonical_url
from state_manager import ProjectStateManager
from utils.filename import create_safe_filename

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Ergebnis-Typ
# ──────────────────────────────────────────────────────────────────────────────

class DispatchResult:
    """Beschreibt, was der Dispatcher mit einem Projekt gemacht hat."""

    ACTION_CREATED = "created"      # Neue Projektdatei angelegt
    ACTION_MERGED = "merged"        # Suchgruppe an bestehendem Projekt ergänzt
    ACTION_SKIPPED = "skipped"      # Duplikat (Suchgruppe war bereits eingetragen)

    def __init__(
        self,
        action: str,
        project_id: str,
        filepath: Optional[str],
        search_group_id: str,
        provider_id: str,
        url: str,
    ) -> None:
        self.action = action
        self.project_id = project_id
        self.filepath = filepath
        self.search_group_id = search_group_id
        self.provider_id = provider_id
        self.url = url

    def __repr__(self) -> str:
        return (
            f"DispatchResult(action={self.action!r}, project_id={self.project_id!r},"
            f" filepath={self.filepath!r})"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ──────────────────────────────────────────────────────────────────────────────

class SearchGroupDispatcher:
    """
    Verarbeitet ein gescraptes Projekt und routet es in die richtige Suchgruppe.

    Kernverantwortlichkeiten:
    - Berechnet project_id aus provider_url oder Titel
    - Prüft per DedupeService, ob das Projekt bereits bekannt ist
    - Legt bei Bedarf eine neue Projektdatei an (Schema v2)
    - Ergänzt bei Duplikat nur die Suchgruppe im bestehenden ProjectRecord
    - Aktualisiert DedupeService und StateManager konsistent
    """

    def __init__(self, output_dir: str, dedupe_service: Optional[DedupeService] = None) -> None:
        """
        Args:
            output_dir: Verzeichnis, in dem Projektdateien gespeichert werden.
            dedupe_service: Optionaler DedupeService (für Tests injizierbar).
        """
        self.output_dir = output_dir
        self._dedupe = dedupe_service or DedupeService(output_dir)
        self._state_manager = ProjectStateManager(output_dir)

    # ── öffentliche API ────────────────────────────────────────────────────────

    def dispatch(
        self,
        *,
        search_group_id: str,
        provider_id: str,
        provider_url: str,
        title: str,
        markdown_content: str,
        channel: str = "rss",
        discovered_at: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> DispatchResult:
        """
        Routet ein gescraptes Projekt in die Suchgruppe.

        Args:
            search_group_id: Bezeichner der Suchgruppe (z.B. "automation_bi").
            provider_id: Quellenbezeichner (z.B. "freelancermap").
            provider_url: Direkte URL des Projekts beim Anbieter.
            title: Projekttitel (Fallback für project_id wenn URL leer).
            markdown_content: Vollständiger Markdown-Inhalt (Frontmatter + Body).
            channel: "rss" oder "email".
            discovered_at: ISO-8601-Zeitstempel; default: jetzt (UTC).
            extra_metadata: Zusätzliche Felder für den StateManager.

        Returns:
            DispatchResult mit Angabe, ob created / merged / skipped.
        """
        if discovered_at is None:
            discovered_at = datetime.now(timezone.utc).isoformat()

        canonical = _canonical_url(provider_url) if provider_url else ""
        project_id = _make_project_id(provider_url=provider_url or None, title=title or None)

        logger.debug(
            "dispatch called",
            extra={
                "search_group_id": search_group_id,
                "provider_id": provider_id,
                "project_id": project_id,
                "url": provider_url,
            },
        )

        # ── Duplikat-Prüfung per project_id ───────────────────────────────────
        if self._dedupe.already_processed_by_id(project_id):
            existing_path = self._find_existing_file(project_id)
            if existing_path:
                action = self._add_search_group_to_existing(
                    filepath=existing_path,
                    search_group_id=search_group_id,
                    provider_id=provider_id,
                    provider_url=provider_url,
                    discovered_at=discovered_at,
                    channel=channel,
                )
                logger.info(
                    "Duplicate project — search group merged",
                    extra={
                        "project_id": project_id,
                        "filepath": existing_path,
                        "search_group_id": search_group_id,
                        "action": action,
                    },
                )
                return DispatchResult(
                    action=action,
                    project_id=project_id,
                    filepath=existing_path,
                    search_group_id=search_group_id,
                    provider_id=provider_id,
                    url=provider_url,
                )
            # Bekannte ID, aber Datei nicht gefunden → trotzdem neu anlegen
            logger.warning(
                "project_id known but file not found — creating new file",
                extra={"project_id": project_id},
            )

        # ── Neue Projektdatei anlegen ──────────────────────────────────────────
        filepath = self._create_project_file(
            search_group_id=search_group_id,
            provider_id=provider_id,
            provider_url=provider_url,
            title=title,
            project_id=project_id,
            markdown_content=markdown_content,
            channel=channel,
            discovered_at=discovered_at,
            extra_metadata=extra_metadata or {},
        )

        # DedupeService aktualisieren (dual-write: project_id + URL-Key)
        self._dedupe.mark_processed_by_id(project_id, provider_id, canonical)

        logger.info(
            "New project file created",
            extra={
                "project_id": project_id,
                "filepath": filepath,
                "search_group_id": search_group_id,
            },
        )
        return DispatchResult(
            action=DispatchResult.ACTION_CREATED,
            project_id=project_id,
            filepath=filepath,
            search_group_id=search_group_id,
            provider_id=provider_id,
            url=provider_url,
        )

    def log_rejected(
        self,
        *,
        search_group_id: str,
        provider_id: str,
        provider_url: str,
        title: str,
        channel: str = "rss",
        discovered_at: Optional[str] = None,
        filter_result_dict: Dict[str, Any],
    ) -> None:
        """
        Protokolliert ein durch den Hard-Filter abgelehntes Projekt.

        Das Projekt wird NICHT als Datei angelegt und nicht an den Dispatcher
        übergeben. Stattdessen wird der Ablehnungseintrag an
        ``{output_dir}/filter_rejected.jsonl`` angehängt (eine JSON-Zeile pro
        Ablehnung). So bleiben Filterentscheid und Ablehnungsgründe persistent
        nachvollziehbar.

        Args:
            search_group_id: Bezeichner der Suchgruppe.
            provider_id: Quellenbezeichner.
            provider_url: URL des abgelehnten Projekts.
            title: Projekttitel.
            channel: "rss" oder "email".
            discovered_at: ISO-8601-Zeitstempel; default: jetzt (UTC).
            filter_result_dict: Serialisiertes FilterResult (aus FilterResult.to_dict()).
        """
        import json as _json

        if discovered_at is None:
            discovered_at = datetime.now(timezone.utc).isoformat()

        entry = {
            "search_group_id": search_group_id,
            "provider_id": provider_id,
            "provider_url": provider_url,
            "title": title,
            "channel": channel,
            "discovered_at": discovered_at,
            "filter_result": filter_result_dict,
        }

        log_path = os.path.join(self.output_dir, "filter_rejected.jsonl")
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(_json.dumps(entry, ensure_ascii=False) + "\n")
            logger.info(
                "Abgelehntes Projekt protokolliert",
                extra={
                    "search_group_id": search_group_id,
                    "provider_url": provider_url,
                    "log_path": log_path,
                },
            )
        except OSError as exc:
            logger.warning(
                "Konnte filter_rejected.jsonl nicht schreiben",
                extra={"log_path": log_path, "error": str(exc)},
            )

    # ── private Hilfsmethoden ──────────────────────────────────────────────────

    def _find_existing_file(self, project_id: str) -> Optional[str]:
        """
        Sucht die Projektdatei mit der gegebenen project_id im output_dir.

        Scannt alle .md-Dateien und liest den project_id-Wert aus dem Frontmatter.
        Gibt den Pfad der ersten übereinstimmenden Datei zurück oder None.
        """
        projects_dir = Path(self.output_dir)
        if not projects_dir.exists():
            return None

        for md_file in projects_dir.glob("*.md"):
            try:
                record, _ = self._state_manager.read_project_record(str(md_file))
                if record and record.project_id == project_id:
                    return str(md_file)
            except Exception:
                continue
        return None

    def _add_search_group_to_existing(
        self,
        filepath: str,
        search_group_id: str,
        provider_id: str,
        provider_url: str,
        discovered_at: str,
        channel: str,
    ) -> str:
        """
        Ergänzt search_group_id (und optional eine neue source) im bestehenden Record.

        Returns:
            ACTION_MERGED wenn geändert, ACTION_SKIPPED wenn Gruppe bereits vorhanden.
        """
        record, body = self._state_manager.read_project_record(filepath)
        if record is None:
            logger.warning("Could not read existing record", extra={"filepath": filepath})
            return DispatchResult.ACTION_SKIPPED

        already_had_group = search_group_id in record.search_groups

        record.add_search_group(search_group_id)
        record.add_source(
            ProjectSource(
                provider=provider_id,
                url=provider_url,
                discovered_at=discovered_at,
                channel=channel,
            )
        )

        self._state_manager.write_project_record(filepath, record, body)

        return DispatchResult.ACTION_SKIPPED if already_had_group else DispatchResult.ACTION_MERGED

    def _create_project_file(
        self,
        *,
        search_group_id: str,
        provider_id: str,
        provider_url: str,
        title: str,
        project_id: str,
        markdown_content: str,
        channel: str,
        discovered_at: str,
        extra_metadata: Dict[str, Any],
    ) -> str:
        """
        Legt eine neue Projektdatei mit Schema v2 an.

        Schreibt zuerst den übergebenen markdown_content (aus dem Scraper),
        dann aktualisiert es das Frontmatter per ProjectRecord, damit
        project_id, search_groups und sources korrekt gesetzt sind.

        Returns:
            Absoluter Pfad zur neu angelegten Datei.
        """
        os.makedirs(self.output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = create_safe_filename(title or "project", timestamp)
        filepath = os.path.join(self.output_dir, filename)

        # Zuerst Rohinhalt schreiben (kommt vom Scraper/MarkdownRenderer)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        # StateManager initialisieren (setzt state=scraped etc.)
        metadata = {
            "scraped_date": discovered_at,
            "source_url": provider_url,
            **extra_metadata,
        }
        self._state_manager.initialize_project(filepath, metadata)

        # Frontmatter auf Schema v2 upgraden und search_group + source eintragen
        record, body = self._state_manager.read_project_record(filepath)
        if record is None:
            # Fallback: minimalen Record bauen
            record = ProjectRecord(
                project_id=project_id,
                title=title,
                state="scraped",
            )
            body = ""

        # Sicherstellen, dass project_id aus URL gesetzt ist
        record.project_id = project_id
        record.add_search_group(search_group_id)
        record.add_source(
            ProjectSource(
                provider=provider_id,
                url=provider_url,
                discovered_at=discovered_at,
                channel=channel,
            )
        )

        self._state_manager.write_project_record(filepath, record, body)

        return filepath
