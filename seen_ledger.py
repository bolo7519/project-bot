"""
seen_ledger.py — Persistentes Verzeichnis bereits gesehener RSS-Einträge.

Zweck: Ein Feed-Eintrag soll höchstens einmal zu einem Projektseiten-Abruf
führen. Das Ledger merkt sich je URL, was für welche Suchgruppe entschieden
wurde — auch für fachfremde Einträge, die keine Projektdatei bekommen.

Datei: ``{output_dir}/rss_seen_ledger.json``

    {
      "version": 1,
      "entries": {
        "<kanonische URL>": {
          "url": "...", "title": "...", "summary": "...",   # RSS-Daten
          "provider": "freelancermap", "feed": "DE",
          "first_seen": "...", "last_seen": "...",
          "status": "captured" | "filtered" | "existing" | "error",
          "basis":  "page" | "rss_title" | "existing" | "file",
          "groups": {"<group_id>": {"decision": "captured" | "filtered" | "existing",
                                    "terms": "<Hash der Filterregeln>"}},
          "errors": 0, "last_error": null
        }
      }
    }

Regeln:
- Eine Entscheidung gilt nur für den Filterstand (``terms``), unter dem sie
  getroffen wurde. Ändern sich die Suchbegriffe, wird der Eintrag neu bewertet.
- Schlägt Abruf oder Verarbeitung fehl, wird KEINE Entscheidung gespeichert
  (Status "error") — der Eintrag wird beim nächsten Lauf erneut versucht.
- Geschrieben wird atomar (temporäre Datei + Umbenennen), damit ein Abbruch
  mitten im Schreiben das Ledger nicht zerstört.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

LEDGER_FILENAME = "rss_seen_ledger.json"
LEDGER_VERSION = 1

DECISION_CAPTURED = "captured"   # fachlich passend, Projektdatei vorhanden
DECISION_FILTERED = "filtered"   # fachlich nicht passend
DECISION_EXISTING = "existing"   # Projekt war schon vor dem Ledger bekannt

STATUS_ERROR = "error"

BASIS_PAGE = "page"
BASIS_RSS_TITLE = "rss_title"
BASIS_EXISTING = "existing"
BASIS_FILE = "file"              # anhand der gespeicherten Projektdatei entschieden

_SUMMARY_MAX_CHARS = 600


class SeenLedger:
    """Liest und schreibt das Seen-Ledger eines Projektverzeichnisses."""

    def __init__(self, output_dir: str) -> None:
        self.path = Path(output_dir) / LEDGER_FILENAME
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self._changed: set = set()     # in diesem Lauf geänderte Schlüssel
        self._removed: set = set()     # in diesem Lauf entfernte Schlüssel
        self._load()

    # ── Laden / Speichern ──────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            entries = data.get("entries") if isinstance(data, dict) else None
            if not isinstance(entries, dict):
                raise ValueError("Feld 'entries' fehlt")
            self._entries = entries
        except (OSError, ValueError) as exc:
            # Beschädigtes Ledger: sichern und leer starten. Folge ist nur, dass
            # Einträge neu bewertet werden; Dubletten verhindert der DedupeService.
            backup = self.path.with_suffix(".json.corrupt")
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            logger.warning(
                "Seen-Ledger nicht lesbar (%s) — starte leer, Kopie: %s", exc, backup)
            self._entries = {}

    def _read_disk(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                entries = json.load(fh).get("entries")
            return entries if isinstance(entries, dict) else {}
        except (OSError, ValueError, AttributeError):
            return {}

    def save(self) -> None:
        """
        Schreibt das Ledger atomar; ohne Änderungen passiert nichts.

        Vor dem Schreiben wird der Stand auf der Platte übernommen und nur um die
        eigenen Änderungen ergänzt. Läuft ausnahmsweise ein zweiter Abruf
        gleichzeitig, gehen dessen Einträge so nicht verloren.
        """
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        merged = self._read_disk()
        for key in self._changed:
            if key in self._entries:
                merged[key] = self._entries[key]
        for key in self._removed:
            merged.pop(key, None)
        self._entries = merged

        # eigene temporäre Datei je Prozess und Thread, dann atomar umbenennen
        tmp = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(
                    {"version": LEDGER_VERSION, "entries": merged},
                    fh, ensure_ascii=False, indent=1, sort_keys=True,
                )
            os.replace(tmp, self.path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        self._dirty = False
        self._changed.clear()
        self._removed.clear()

    # ── Lesen ──────────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self._entries.get(key)

    def items(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        return iter(list(self._entries.items()))

    def decision_for(self, key: str, group_id: str, terms: str) -> Optional[str]:
        """
        Gültige Entscheidung für (URL, Gruppe) unter dem aktuellen Filterstand.

        None, wenn der Eintrag unbekannt ist, zuletzt fehlschlug oder unter
        anderen Suchbegriffen entschieden wurde.
        """
        record = self._entries.get(key)
        if not record or record.get("status") == STATUS_ERROR:
            return None
        group = (record.get("groups") or {}).get(group_id)
        if not group:
            return None
        # Bestandsprojekte bleiben unangetastet; eine bereits erfasste Zuordnung
        # wird durch neue Suchbegriffe nicht zurückgenommen. Neu bewertet werden
        # nur Ablehnungen, die unter anderen Suchbegriffen fielen.
        if group.get("decision") in (DECISION_EXISTING, DECISION_CAPTURED):
            return group.get("decision")
        if group.get("terms") != terms:
            return None
        return group.get("decision")

    # ── Schreiben ──────────────────────────────────────────────────────────────

    def touch(self, key: str, *, url: str, title: str, summary: str,
              provider: str, feed: str) -> Dict[str, Any]:
        """Legt den Eintrag an bzw. aktualisiert die RSS-Daten und ``last_seen``."""
        now = datetime.now().isoformat(timespec="seconds")
        record = self._entries.get(key)
        if record is None:
            record = {
                "url": url, "provider": provider, "feed": feed,
                "first_seen": now, "status": None, "basis": None,
                "groups": {}, "errors": 0, "last_error": None,
            }
            self._entries[key] = record
        record["title"] = title
        record["summary"] = (summary or "")[:_SUMMARY_MAX_CHARS]
        record["last_seen"] = now
        self._mark(key)
        return record

    def record_decisions(self, key: str, decisions: Dict[str, str], terms: Dict[str, str],
                         basis: str) -> None:
        """Speichert Entscheidungen je Gruppe und setzt den Gesamtstatus."""
        record = self._entries[key]
        groups = record.setdefault("groups", {})
        for group_id, decision in decisions.items():
            groups[group_id] = {"decision": decision, "terms": terms.get(group_id)}
        all_decisions = {g["decision"] for g in groups.values()}
        if DECISION_CAPTURED in all_decisions:
            record["status"] = DECISION_CAPTURED
        elif DECISION_EXISTING in all_decisions:
            record["status"] = DECISION_EXISTING
        else:
            record["status"] = DECISION_FILTERED
        record["basis"] = basis
        record["last_error"] = None
        self._mark(key)

    def record_error(self, key: str, error: str) -> None:
        """
        Vermerkt einen fehlgeschlagenen Abruf. Es wird keine Entscheidung
        gespeichert; bereits vorhandene Entscheidungen bleiben erhalten.
        """
        record = self._entries[key]
        record["errors"] = int(record.get("errors") or 0) + 1
        record["last_error"] = str(error)[:300]
        if not record.get("groups"):
            record["status"] = STATUS_ERROR
        self._mark(key)

    def prune(self, max_age_days: int = 120) -> int:
        """Entfernt Einträge, die seit ``max_age_days`` nicht mehr im Feed standen."""
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat(timespec="seconds")
        old = [k for k, r in self._entries.items() if str(r.get("last_seen") or "") < cutoff]
        for key in old:
            del self._entries[key]
            self._changed.discard(key)
            self._removed.add(key)
        if old:
            self._dirty = True
        return len(old)

    def _mark(self, key: str) -> None:
        self._changed.add(key)
        self._removed.discard(key)
        self._dirty = True
