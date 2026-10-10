"""
rss_feed_pipeline.py — Gemeinsamer RSS-Abruf für alle Suchgruppen.

Ablauf je Lauf:
  1. Jeder Feed wird genau einmal geladen, auch wenn mehrere Suchgruppen ihn
     abonniert haben.
  2. Je Eintrag entscheidet zuerst das Seen-Ledger: Ist für alle Gruppen schon
     eine gültige Entscheidung gespeichert, wird nichts abgerufen.
  3. Sonst reichen manchmal die RSS-Daten (klar fachfremder Titel ohne einen
     einzigen Fachbegriff); nur wenn nicht, wird die Projektseite EINMAL
     abgerufen und gegen alle offenen Gruppen geprüft.
  4. Passende Projekte gehen wie bisher über den SearchGroupDispatcher
     (URL-Deduplizierung, eine Datei, mehrere Gruppen).
  5. Fehlgeschlagene Abrufe werden nicht als verarbeitet vermerkt und beim
     nächsten Lauf wiederholt.

Nicht verändert werden: Projekte, die schon vor dem Ledger bekannt waren
(sie gelten als "bekannt" und werden weder neu geladen noch umgeschrieben),
FilterEngine, Kompetenzprofile und Eignungsbewertung.

Kein LLM, kein SMTP, keine Bewerbungen.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from filter_engine import FilterEngine, _compile_include_term, filter_config_from_search_group
from project_record import _canonical_url, _make_project_id
from search_group_config import load_rss_prefilter
from seen_ledger import (
    BASIS_EXISTING, BASIS_FILE, BASIS_PAGE, BASIS_RSS_TITLE,
    DECISION_CAPTURED, DECISION_EXISTING, DECISION_FILTERED,
    SeenLedger,
)

logger = logging.getLogger(__name__)

# Obergrenze der Projektseiten-Abrufe je Lauf (Schutz der Plattform). Was
# darüber liegt, bleibt unentschieden und kommt beim nächsten Lauf dran.
DEFAULT_MAX_PAGE_REQUESTS = 60

# Klassen, in die jeder Feed-Eintrag genau einmal fällt
CLASS_NEW = "new"
CLASS_KNOWN = "known"
CLASS_FILTERED = "filtered"
CLASS_ERROR = "errors"
CLASS_DEFERRED = "deferred"


def feed_label(provider_id: str, feed_url: str) -> str:
    """Kurzname eines Feeds für die Auswertung, z.B. ".../de.xml" → "DE"."""
    name = urlparse(feed_url).path.rstrip("/").rsplit("/", 1)[-1]
    name = name.rsplit(".", 1)[0] if "." in name else name
    label = (name or provider_id).upper()
    return label if provider_id == "freelancermap" else f"{provider_id}:{label}"


def _terms_hash(group_cfg: Any, prefilter_terms: List[str]) -> str:
    """Fingerabdruck der Regeln, unter denen eine Filterentscheidung gilt."""
    payload = json.dumps(
        {"filters": getattr(group_cfg, "filters", None) or {}, "prefilter": prefilter_terms},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _any_term(terms: List[str], text: str) -> Optional[str]:
    for term in terms or []:
        pattern = _compile_include_term(term)
        if pattern is not None and pattern.search(text):
            return term
    return None


def _empty_counts() -> Dict[str, int]:
    return {
        "entries_found": 0, "projects_saved": 0, "urls_skipped_dedupe": 0,
        "projects_filtered": 0, "projects_unsuitable": 0, "errors": 0,
    }


class SharedFeedRun:
    """Zustand und Zähler eines Laufs."""

    def __init__(self, agent: Any, config: Dict[str, Any], groups: Dict[str, Any],
                 output_dir: str, page_delay: float, max_page_requests: Optional[int]) -> None:
        import email_agent as ea  # zur Laufzeit, damit Tests die Namen ersetzen können

        self.agent = agent
        self.config = config
        self.groups = groups
        self.output_dir = output_dir
        self.page_delay = page_delay
        self.max_page_requests = max_page_requests

        self.ledger = SeenLedger(output_dir)
        self.dedupe = ea.DedupeService(output_dir)
        self.dispatcher = ea.SearchGroupDispatcher(output_dir, self.dedupe)
        self.renderer = ea.MarkdownRenderer()
        self._state_manager_cls = ea.ProjectStateManager
        self._adapters: Dict[str, Any] = {}

        prefilter = config.get("rss_prefilter")
        if prefilter is None:
            prefilter = load_rss_prefilter()
        self.title_exclude_terms: List[str] = list(prefilter.get("title_exclude_terms") or [])

        self.engines: Dict[str, FilterEngine] = {}
        self.include_terms: Dict[str, List[str]] = {}
        self.terms: Dict[str, str] = {}
        for group_id, group_cfg in groups.items():
            filter_cfg = filter_config_from_search_group(group_cfg)
            self.engines[group_id] = FilterEngine(filter_cfg)
            self.include_terms[group_id] = list(filter_cfg.include_terms)
            self.terms[group_id] = _terms_hash(group_cfg, self.title_exclude_terms)

        self.pre_scorer = None
        try:
            self.pre_scorer = ea.PreScorer()
        except Exception as exc:
            logger.warning("PreScorer nicht verfügbar — Pre-Scoring deaktiviert: %s", exc)
        self.suitability_scorer = None
        try:
            applicant_cfg = config.get("applicant") or {}
            if "available_from" in applicant_cfg:
                self.suitability_scorer = ea.SuitabilityScorer(
                    available_from=applicant_cfg.get("available_from"))
            else:
                self.suitability_scorer = ea.SuitabilityScorer()
        except Exception as exc:
            logger.warning("Eignungsbewertung nicht verfügbar: %s", exc)

        self.group_counts: Dict[str, Dict[str, int]] = {g: _empty_counts() for g in groups}
        self.provider_counts: Dict[str, Dict[str, Dict[str, int]]] = {
            g: {p: _empty_counts() for p in cfg.get_rss_providers()} for g, cfg in groups.items()
        }
        self.feed_counts: Dict[str, Dict[str, int]] = {}
        self.feed_requests = 0
        self.page_requests = 0
        self.feed_errors = 0
        self.entry_errors = 0
        self.seen_keys: set = set()

    # ── Zähler ─────────────────────────────────────────────────────────────────

    def _count(self, group_id: str, provider_id: str, field: str, n: int = 1) -> None:
        self.group_counts[group_id][field] += n
        provider = self.provider_counts[group_id].setdefault(provider_id, _empty_counts())
        provider[field] += n

    def _feed(self, label: str) -> Dict[str, int]:
        return self.feed_counts.setdefault(label, {
            "entries": 0, CLASS_NEW: 0, CLASS_KNOWN: 0, CLASS_FILTERED: 0,
            CLASS_ERROR: 0, CLASS_DEFERRED: 0, "page_requests": 0,
        })

    def _adapter(self, provider_id: str) -> Any:
        if provider_id not in self._adapters:
            self._adapters[provider_id] = self.agent.load_adapter(
                provider_id, {"provider_id": provider_id})
        return self._adapters[provider_id]

    # ── Ein Feed ───────────────────────────────────────────────────────────────

    def process_feed(self, provider_id: str, feed_url: str, group_ids: List[str],
                     limit: int, max_age_days: int) -> None:
        label = feed_label(provider_id, feed_url)
        counts = self._feed(label)
        try:
            self.feed_requests += 1
            entries = self.agent.fetch_rss_feed(feed_url, limit, max_age_days)
        except Exception as exc:
            # Feed nicht abrufbar: nichts wird als gesehen vermerkt.
            self.feed_errors += 1
            counts[CLASS_ERROR] += 1
            for group_id in group_ids:
                self._count(group_id, provider_id, "errors")
            logger.error("RSS-Feed nicht abrufbar: %s (%s)", label, exc)
            return

        for group_id in group_ids:
            self._count(group_id, provider_id, "entries_found", len(entries))
        counts["entries"] += len(entries)

        try:
            for entry in entries:
                url = (entry.get("link") or "").strip()
                if not url:
                    continue
                outcome = self.process_entry(
                    url=url,
                    title=str(entry.get("title") or ""),
                    summary=str(entry.get("summary") or ""),
                    provider_id=provider_id,
                    label=label,
                    group_ids=group_ids,
                )
                counts[outcome] += 1
        finally:
            # Zwischenstand sichern: ein Abbruch kostet höchstens diesen Feed.
            self.ledger.save()

    # ── Ein Eintrag ────────────────────────────────────────────────────────────

    def process_entry(self, *, url: str, title: str, summary: str, provider_id: str,
                      label: str, group_ids: List[str], touch: bool = True) -> str:
        key = _canonical_url(url)
        self.seen_keys.add(key)
        if touch or key not in self.ledger:
            self.ledger.touch(key, url=url, title=title, summary=summary,
                              provider=provider_id, feed=label)
        record = self.ledger.get(key)

        # 1. Was ist schon entschieden?
        pending: List[str] = []
        any_captured = False
        for group_id in group_ids:
            decision = self.ledger.decision_for(key, group_id, self.terms[group_id])
            if decision in (DECISION_CAPTURED, DECISION_EXISTING):
                self._count(group_id, provider_id, "urls_skipped_dedupe")
                any_captured = True
            elif decision == DECISION_FILTERED:
                self._count(group_id, provider_id, "projects_filtered")
            else:
                pending.append(group_id)
        if not pending:
            return CLASS_KNOWN if any_captured else CLASS_FILTERED

        # 2. Bekannte URL? Dann wird die Projektseite nie erneut abgerufen.
        project_id = _make_project_id(provider_url=url, title=title or None)
        known = self.dedupe.already_processed_by_id(project_id)
        discovered_at = datetime.now().isoformat()
        schema: Optional[Dict[str, Any]] = None
        basis = BASIS_PAGE

        if known and not record.get("groups"):
            # Schon vor dem Ledger bekannt → nicht anfassen.
            self.ledger.record_decisions(
                key, {g: DECISION_EXISTING for g in pending}, self.terms, BASIS_EXISTING)
            for group_id in pending:
                self._count(group_id, provider_id, "urls_skipped_dedupe")
            return CLASS_KNOWN
        if known:
            # Von dieser Pipeline angelegt, aber für eine Gruppe noch offen
            # (z.B. nach geänderten Suchbegriffen): gespeicherten Text prüfen.
            schema = self._schema_from_existing_file(project_id, title)
            basis = BASIS_FILE

        # 3. Reichen die RSS-Daten? Nur für ein sicheres Nein: klar fachfremder
        #    Titel und kein einziger Fachbegriff einer offenen Gruppe im RSS-Text.
        rss_text = f"{title} ; {summary}"
        excluded_by = None if known else _any_term(self.title_exclude_terms, title)
        if excluded_by and not any(
            _any_term(self.include_terms[g], rss_text) for g in pending
        ):
            for group_id in pending:
                self._count(group_id, provider_id, "projects_filtered")
                self.dispatcher.log_rejected(
                    search_group_id=group_id, provider_id=provider_id, provider_url=url,
                    title=title, channel="rss", discovered_at=discovered_at,
                    filter_result_dict={
                        "search_group_id": group_id, "passed": False,
                        "failed_criteria": ["rss_title_prefilter"], "unknown_criteria": [],
                        "checks": [{
                            "criterion": "rss_title_prefilter", "passed": False,
                            "reason": f"Fachfremder Titel laut RSS: '{excluded_by}' "
                                      f"(Projektseite nicht abgerufen)",
                            "value_found": excluded_by, "was_unknown": False,
                        }],
                    },
                )
            self.ledger.record_decisions(
                key, {g: DECISION_FILTERED for g in pending}, self.terms, BASIS_RSS_TITLE)
            return CLASS_KNOWN if any_captured else CLASS_FILTERED

        # 4. Projektseite abrufen — nur für unbekannte URLs, höchstens einmal je
        #    Eintrag und gedeckelt je Lauf.
        if schema is None:
            if known:
                # Bekannt, aber Datei nicht lesbar: lieber offen lassen als laden.
                return self._entry_failed(
                    key, url, provider_id, pending,
                    FileNotFoundError("Projektdatei zur bekannten URL nicht gefunden"))
            if (self.max_page_requests is not None
                    and self.page_requests >= self.max_page_requests):
                return CLASS_DEFERRED
            if self.page_delay and self.page_requests:
                time.sleep(self.page_delay)
            self.page_requests += 1
            self._feed(label)["page_requests"] += 1
            try:
                parse_result = self._adapter(provider_id).parse(url)
                schema = (
                    parse_result["schema"]
                    if isinstance(parse_result, dict) and "schema" in parse_result
                    else parse_result
                )
                if not isinstance(schema, dict):
                    raise ValueError("Projektseite lieferte keine auswertbaren Daten")
            except Exception as exc:
                return self._entry_failed(key, url, provider_id, pending, exc)

        # 5. Gegen alle offenen Gruppen prüfen
        decisions: Dict[str, str] = {}
        passing: List[Tuple[str, Dict[str, Any]]] = []
        for group_id in pending:
            result = self.engines[group_id].apply(schema, group_id)
            if result.passed:
                passing.append((group_id, result.to_dict()))
                continue
            decisions[group_id] = DECISION_FILTERED
            self._count(group_id, provider_id, "projects_filtered")
            self.dispatcher.log_rejected(
                search_group_id=group_id, provider_id=provider_id, provider_url=url,
                title=schema.get("title") or title, channel="rss",
                discovered_at=discovered_at, filter_result_dict=result.to_dict(),
            )

        created = False
        if passing:
            try:
                created = self._dispatch(
                    url, title, schema, provider_id, passing, decisions, discovered_at)
            except Exception as exc:
                # Schon gelungene Entscheidungen behalten, der Rest wird wiederholt.
                if decisions:
                    self.ledger.record_decisions(key, decisions, self.terms, basis)
                return self._entry_failed(key, url, provider_id,
                                          [g for g in pending if g not in decisions], exc)

        self.ledger.record_decisions(key, decisions, self.terms, basis)
        if created:
            return CLASS_NEW
        if any_captured or DECISION_CAPTURED in decisions.values():
            return CLASS_KNOWN
        return CLASS_FILTERED

    def _schema_from_existing_file(self, project_id: str, title: str) -> Optional[Dict[str, Any]]:
        """
        Liest Titel und Ausschreibungstext aus der vorhandenen Projektdatei.

        Angehängte Bewertungsabschnitte werden abgeschnitten, damit z.B. der
        Profilname "power_bi_sharepoint" keinen Fachtreffer auslöst.
        """
        try:
            path = self.dispatcher._find_existing_file(project_id)
            if not path:
                return None
            frontmatter, body = self._state_manager_cls(self.output_dir).read_project(path)
            for marker in ("## Vorbewertung", "## 🤖 AI Evaluation Results"):
                body = body.split(marker, 1)[0]
            return {"title": frontmatter.get("title") or title, "description": body}
        except Exception as exc:
            logger.warning("Projektdatei zu %s nicht lesbar: %s", project_id, exc)
            return None

    def _dispatch(self, url: str, title: str, schema: Dict[str, Any], provider_id: str,
                  passing: List[Tuple[str, Dict[str, Any]]], decisions: Dict[str, str],
                  discovered_at: str) -> bool:
        """Legt die Projektdatei an bzw. ergänzt Suchgruppen. True, wenn neu angelegt."""
        import email_agent as ea

        adapter = self._adapter(provider_id)
        markdown_content = self.renderer.render(schema, {
            "provider_id": provider_id,
            "provider_name": adapter.get_provider_name(),
            "collection_channel": "rss",
            "collected_at": datetime.now().isoformat(),
        })
        project_title = schema.get("title") or title or "project"

        pre_score_dict: Dict[str, Any] = {}
        if self.pre_scorer is not None:
            try:
                pre_score_dict = self.pre_scorer.score_project(schema).to_dict()
            except Exception as exc:
                logger.warning("Pre-Scoring fehlgeschlagen für %s: %s", url, exc)
        suitability = None
        if self.suitability_scorer is not None:
            try:
                suitability = self.suitability_scorer.score_project(schema)
            except Exception as exc:
                logger.warning("Eignungsbewertung fehlgeschlagen für %s: %s", url, exc)

        created = False
        for group_id, filter_result_dict in passing:
            extra: Dict[str, Any] = {"filter_results": {group_id: filter_result_dict}}
            if pre_score_dict:
                extra["pre_scores"] = pre_score_dict
            if suitability is not None:
                extra["suitability"] = suitability.to_dict()
            result = self.dispatcher.dispatch(
                search_group_id=group_id, provider_id=provider_id, provider_url=url,
                title=project_title, markdown_content=markdown_content, channel="rss",
                discovered_at=discovered_at, extra_metadata=extra,
            )
            decisions[group_id] = DECISION_CAPTURED
            if result.action == ea.DispatchResult.ACTION_CREATED:
                created = True
                self._count(group_id, provider_id, "projects_saved")
                if (suitability is not None and suitability.rejected
                        and isinstance(result.filepath, str)):
                    self._state_manager_cls(self.output_dir).update_state(
                        result.filepath, "rejected",
                        note=f"Eignungsbewertung: {suitability.reject_reason}")
                    self._count(group_id, provider_id, "projects_unsuitable")
            else:
                self._count(group_id, provider_id, "urls_skipped_dedupe")
        return created

    def _entry_failed(self, key: str, url: str, provider_id: str, groups: List[str],
                      exc: Exception) -> str:
        """Fehler vermerken, ohne den Eintrag als verarbeitet zu markieren."""
        self.entry_errors += 1
        for group_id in groups:
            self._count(group_id, provider_id, "errors")
        self.ledger.record_error(key, f"{type(exc).__name__}: {exc}")
        logger.error("RSS-Eintrag nicht verarbeitet, wird wiederholt: %s (%s)", url, exc)
        return CLASS_ERROR

    # ── Kontrollierte Neubewertung ─────────────────────────────────────────────

    def reevaluate_filtered(self) -> Dict[str, int]:
        """
        Bewertet früher gefilterte Einträge neu, deren Entscheidung unter anderen
        Suchbegriffen fiel und die nicht mehr im Feed stehen. Einträge im
        aktuellen Feed werden ohnehin automatisch neu bewertet.
        """
        counts = self._feed("NEUBEWERTUNG")
        try:
            for key, record in self.ledger.items():
                if key in self.seen_keys or record.get("status") != DECISION_FILTERED:
                    continue
                stale = [
                    g for g, decision in (record.get("groups") or {}).items()
                    if g in self.groups and decision.get("terms") != self.terms[g]
                ]
                if not stale:
                    continue
                counts["entries"] += 1
                outcome = self.process_entry(
                    url=record.get("url") or key, title=record.get("title") or "",
                    summary=record.get("summary") or "",
                    provider_id=record.get("provider") or "freelancermap",
                    label="NEUBEWERTUNG",
                    group_ids=[g for g in self.groups if g in stale], touch=False,
                )
                counts[outcome] += 1
        finally:
            self.ledger.save()
        return counts


def run_shared_feed_ingestion(
    agent: Any,
    config: Dict[str, Any],
    groups: Dict[str, Any],
    output_dir: str = "projects",
    dry_run: bool = False,
    group_ids: Optional[List[str]] = None,
    page_delay: float = 0.0,
    max_page_requests: Optional[int] = DEFAULT_MAX_PAGE_REQUESTS,
    reevaluate_filtered: bool = False,
) -> Dict[str, Any]:
    """
    Führt den RSS-Abruf für alle (oder ausgewählte) aktiven Suchgruppen aus.

    Args:
        agent: EmailAgent (liefert fetch_rss_feed und load_adapter).
        config: Konfiguration mit bereits ergänzten Fachbegriffen.
        groups: group_id → SearchGroupConfig, nach Priorität sortiert.
        page_delay: Pause in Sekunden zwischen zwei Projektseiten-Abrufen.
        max_page_requests: Obergrenze der Projektseiten-Abrufe je Lauf; None = keine.
        reevaluate_filtered: Auch nicht mehr im Feed stehende, unter alten
            Suchbegriffen gefilterte Einträge neu bewerten.

    Returns:
        Zusammenfassung; die bisherigen Schlüssel bleiben erhalten, neu sind
        feed_requests, page_requests, page_requests_deferred und feed_summaries.
    """
    active = {
        gid: cfg for gid, cfg in groups.items()
        if cfg.enabled and (group_ids is None or gid in group_ids)
    }
    summary: Dict[str, Any] = {
        "dry_run": dry_run, "groups_processed": len(active),
        "total_entries_found": 0, "total_projects_saved": 0,
        "total_urls_skipped_dedupe": 0, "total_projects_filtered": 0,
        "total_errors": 0, "feed_requests": 0, "page_requests": 0,
        "page_requests_deferred": 0, "feed_summaries": {}, "group_summaries": {},
    }

    # Jeder Feed nur einmal: (Provider, URL) → abonnierende Gruppen
    feeds: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for group_id, group_cfg in active.items():
        for provider_id in group_cfg.get_rss_providers():
            rss_cfg = group_cfg.providers[provider_id].rss
            for feed_url in rss_cfg.feed_urls:
                feed = feeds.setdefault((provider_id, feed_url), {
                    "groups": [], "limit": 0, "max_age_days": 0})
                feed["groups"].append(group_id)
                feed["limit"] = max(feed["limit"], rss_cfg.limit)
                feed["max_age_days"] = max(feed["max_age_days"], rss_cfg.max_age_days)

    if dry_run:
        for group_id in active:
            counts = _empty_counts()
            summary["group_summaries"][group_id] = {
                "group_id": group_id, "dry_run": True, **counts,
                "provider_summaries": {
                    p: _empty_counts() for p in active[group_id].get_rss_providers()},
            }
        logger.info("DRY RUN: würde %d Feed(s) abrufen: %s", len(feeds),
                    [feed_label(p, u) for p, u in feeds])
        return summary

    run = SharedFeedRun(agent, config, active, output_dir, page_delay, max_page_requests)
    try:
        for (provider_id, feed_url), feed in feeds.items():
            run.process_feed(provider_id, feed_url, feed["groups"],
                             feed["limit"], feed["max_age_days"])
        if reevaluate_filtered:
            run.reevaluate_filtered()
        run.ledger.prune()
    finally:
        run.ledger.save()

    for group_id in active:
        counts = run.group_counts[group_id]
        summary["group_summaries"][group_id] = {
            "group_id": group_id, "dry_run": False, **counts,
            "provider_summaries": run.provider_counts[group_id],
        }
        summary["total_entries_found"] += counts["entries_found"]
        summary["total_projects_saved"] += counts["projects_saved"]
        summary["total_urls_skipped_dedupe"] += counts["urls_skipped_dedupe"]
        summary["total_projects_filtered"] += counts["projects_filtered"]
    summary["total_errors"] = run.feed_errors + run.entry_errors
    summary["feed_requests"] = run.feed_requests
    summary["page_requests"] = run.page_requests
    summary["page_requests_deferred"] = sum(
        c[CLASS_DEFERRED] for c in run.feed_counts.values())
    summary["feed_summaries"] = run.feed_counts
    summary["ledger_entries"] = len(run.ledger)
    return summary
