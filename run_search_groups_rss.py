#!/usr/bin/env python3
"""
run_search_groups_rss.py — RSS-Ingest über die Search-Group-Pipeline (Phase 2/3).

Verwendet run_rss_ingestion_for_search_groups() aus email_agent.py:
  - Deduplizierung via DedupeService
  - TF-IDF-Vorbewertung via PreScorer
  - Kein LLM, keine Bewerbungen, keine SMTP

Aufruf:
    python run_search_groups_rss.py [--config config.yaml] [--dry-run] [--groups automation_bi infra_security]

Sicherheitsnote:
  - Nur RSS, kein E-Mail-Abruf
  - Keine LLM-Aufrufe
  - Keine Bewerbungsgenerierung
  - Bindet ausschließlich lokal (kein Netzwerk-Binding)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from main import load_application_config
from email_agent import run_rss_ingestion_for_search_groups
from pre_scorer import PreScorer

# Regex: prüft ob eine Score-Zeile bereits im Body steht
_SCORE_LINE_RE = re.compile(r"- \*\*Score:\*\*\s*\d+/100", re.MULTILINE)


SCORE_KIND_SUITABILITY = "Eignung (regelbasiert)"
SCORE_KIND_TFIDF = "Textähnlichkeit (TF-IDF)"


def _read_frontmatter(content: str) -> dict:
    """Liest das YAML-Frontmatter; leeres Dict, wenn keines vorhanden oder lesbar ist."""
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    try:
        import yaml
        frontmatter = yaml.safe_load(content[3:end]) or {}
        return frontmatter if isinstance(frontmatter, dict) else {}
    except Exception:
        return {}


def _suitability_score_from_frontmatter(content: str):
    """Liest suitability.score aus dem YAML-Frontmatter; None, wenn nicht vorhanden."""
    try:
        score = (_read_frontmatter(content).get("suitability") or {}).get("score")
        return int(score) if score is not None else None
    except Exception:
        return None


def _score_section(frontmatter: dict, tfidf_100, tfidf_profile) -> str:
    """
    Baut den Abschnitt "Vorbewertung" mit eindeutig benannten Werten.

    Die Zeile "- **Score:** N/100" liest das Dashboard. "Score-Art" sagt, welcher
    Wert dort steht; Eignung und Textähnlichkeit stehen zusätzlich getrennt.
    """
    suitability = frontmatter.get("suitability") or {}
    lines = []
    if suitability.get("score") is not None:
        technical = suitability.get("technical") or {}
        lines.append(f"- **Score:** {int(suitability['score'])}/100")
        lines.append(f"- **Score-Art:** {SCORE_KIND_SUITABILITY}")
        detail = f"- **Eignung:** {int(suitability['score'])}/100"
        if technical.get("score") is not None:
            detail += f" — fachlich {int(technical['score'])}/100 ({technical.get('decision')})"
        if suitability.get("recommendation"):
            detail += f", Empfehlung: {suitability['recommendation']}"
        lines.append(detail)
    elif tfidf_100 is not None:
        lines.append(f"- **Score:** {tfidf_100}/100")
        lines.append(f"- **Score-Art:** {SCORE_KIND_TFIDF}")
    if tfidf_100 is not None:
        similarity = f"- **Textähnlichkeit (TF-IDF):** {tfidf_100}/100"
        if tfidf_profile:
            similarity += f" — bestes Profil: {tfidf_profile}"
        lines.append(similarity)
    return "\n## Vorbewertung\n\n" + "\n".join(lines) + "\n"


def _backfill_scores_for_new_files(output_dir: str, new_count: int) -> int:
    """
    Schreibt den Abschnitt "Vorbewertung" in neu angelegte Projektdateien, die
    noch keinen Score-Eintrag im Body haben.
    Gibt die Anzahl der beschriebenen Dateien zurück.
    """
    if new_count == 0:
        return 0

    projects_dir = Path(output_dir)
    md_files = sorted(projects_dir.glob("*.md"))
    if not md_files:
        return 0

    try:
        scorer = PreScorer()
    except Exception as exc:
        print(f"  ⚠️  PreScorer nicht verfügbar, Score-Backfill übersprungen: {exc}")
        return 0

    scored = 0
    for md_path in md_files:
        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception:
            continue

        if _SCORE_LINE_RE.search(content):
            continue  # Bereits gescoret

        frontmatter = _read_frontmatter(content)

        # Textähnlichkeit: gespeicherter TF-IDF-Wert, sonst wie bisher neu berechnet
        pre_scores = frontmatter.get("pre_scores") or {}
        tfidf_100 = None
        tfidf_profile = pre_scores.get("best_profile")
        if pre_scores.get("best_score") is not None:
            tfidf_100 = round(float(pre_scores["best_score"]) * 100)
        else:
            title_match = re.search(r"^#\s+(.*)", content, re.MULTILINE)
            title = title_match.group(1).strip() if title_match else md_path.stem
            try:
                result = scorer.score_project({"title": title, "description": content})
                tfidf_100 = round((result.best_score or 0.0) * 100)
                tfidf_profile = result.best_profile
            except Exception:
                tfidf_100 = None

        has_suitability = (frontmatter.get("suitability") or {}).get("score") is not None
        if tfidf_100 is None and not has_suitability:
            continue

        if not content.endswith("\n"):
            content += "\n"
        content += _score_section(frontmatter, tfidf_100, tfidf_profile)
        md_path.write_text(content, encoding="utf-8")
        scored += 1

    return scored


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RSS-Ingest für Search Groups (mit PreScoring)"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Pfad zur config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulation ohne Dateiänderungen",
    )
    parser.add_argument(
        "--groups",
        nargs="*",
        default=None,
        help="Nur diese Suchgruppen ausführen, z.B. --groups automation_bi infra_security",
    )
    parser.add_argument(
        "--output-dir",
        default="projects",
        help="Projektverzeichnis (default: projects)",
    )
    parser.add_argument(
        "--page-delay",
        type=float,
        default=0.5,
        help="Pause in Sekunden zwischen zwei Projektseiten-Abrufen (default: 0.5)",
    )
    parser.add_argument(
        "--max-page-requests",
        type=int,
        default=None,
        help="Obergrenze der Projektseiten-Abrufe je Lauf (default: 60)",
    )
    parser.add_argument(
        "--reevaluate-filtered",
        action="store_true",
        help="Nach geänderten Suchbegriffen auch früher gefilterte Einträge neu "
             "bewerten, die nicht mehr im Feed stehen",
    )
    args = parser.parse_args()

    config = load_application_config(args.config)

    if "search_groups" not in config or not config["search_groups"]:
        print(
            "ERROR: Keine search_groups in config.yaml gefunden.\n"
            "Bitte search_groups_patch.yaml in config.yaml einfügen:\n"
            "  cat search_groups_patch.yaml >> config.yaml",
            file=sys.stderr,
        )
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    groups_hint = ", ".join(args.groups) if args.groups else "alle aktivierten"
    print(f"📰 RSS-Ingest (Search Groups) [{mode}] — Gruppen: {groups_hint}")

    summary = run_rss_ingestion_for_search_groups(
        config=config,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
        group_ids=args.groups,
        page_delay=args.page_delay,
        max_page_requests=args.max_page_requests,
        reevaluate_filtered=args.reevaluate_filtered,
    )

    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))

    # Score-Backfill für neu angelegte Dateien (Body-Regex für server_enhanced.py)
    new_projects = summary['total_projects_saved']
    scored_count = 0
    if not args.dry_run and new_projects > 0:
        print(f"\n⚙️  Score-Backfill für {new_projects} neue Projekte …")
        scored_count = _backfill_scores_for_new_files(args.output_dir, new_projects)
        print(f"   → {scored_count} Dateien mit Score versehen")

    print()
    print("─" * 60)
    print(f"  Gruppen verarbeitet      : {summary['groups_processed']}")
    print(f"  RSS-Einträge gefunden    : {summary['total_entries_found']}")
    print(f"  Neue Projekte gespeichert: {new_projects}")
    print(f"  Duplikate übersprungen   : {summary['total_urls_skipped_dedupe']}")
    print(f"  Fachlich gefiltert       : {summary.get('total_projects_filtered', 0)}")
    for group_id, group_summary in summary.get('group_summaries', {}).items():
        print(
            f"    {group_id:<22}: {group_summary.get('projects_saved', 0)} neu, "
            f"{group_summary.get('urls_skipped_dedupe', 0)} bekannt, "
            f"{group_summary.get('projects_filtered', 0)} fachfremd/gefiltert, "
            f"{group_summary.get('projects_unsuitable', 0)} wegen Muss-Anforderung abgelehnt"
        )
    print(f"  Scores geschrieben       : {scored_count}")
    print(f"  HTTP: Feed-Abrufe        : {summary.get('feed_requests', 0)}")
    print(f"  HTTP: Projektseiten      : {summary.get('page_requests', 0)}"
          f" (zurückgestellt: {summary.get('page_requests_deferred', 0)})")
    for label, feed in summary.get('feed_summaries', {}).items():
        print(
            f"    Feed {label:<13}: {feed.get('entries', 0)} Einträge — "
            f"{feed.get('new', 0)} neu, {feed.get('known', 0)} bekannt, "
            f"{feed.get('filtered', 0)} fachlich gefiltert, "
            f"{feed.get('errors', 0)} fehlerhaft, "
            f"{feed.get('deferred', 0)} zurückgestellt, "
            f"{feed.get('page_requests', 0)} Seitenabrufe"
        )
    print(f"  Fehler                   : {summary['total_errors']}")
    if args.dry_run:
        print("  (DRY-RUN — keine Dateien geändert)")


if __name__ == "__main__":
    main()
