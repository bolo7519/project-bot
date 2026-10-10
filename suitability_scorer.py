"""
suitability_scorer.py — Regelbasierte Eignungsbewertung für erfasste Projekte.

Kostenlos und lokal (kein LLM, keine API). Bewertet ein bereits fachlich
erfasstes Projekt gegen die Schwerpunkte und Einschränkungen aus
suitability_rules.yaml und liefert einen Wert 0–100 mit nachvollziehbaren
Gründen.

Abgrenzung zu den Nachbarmodulen:
- filter_engine.py / search_group_filters.yaml: Erfassung (gehört das Projekt
  fachlich zu einer Suchgruppe?). Wird hier nicht verändert.
- pre_scorer.py: TF-IDF-Ähnlichkeit gegen die vier Kompetenzprofile. Bleibt
  unverändert und unabhängig; diese Bewertung ersetzt sie nicht.

Ein einzelner unerwünschter Begriff schließt nie aus. Abgelehnt wird nur, wenn
eine nicht belegte Spezialisierung im Titel steht UND im Muss-/Anforderungs-
abschnitt verlangt wird.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from filter_engine import _compile_include_term, _schlagworte_text

logger = logging.getLogger(__name__)

DEFAULT_RULES_PATH = Path(__file__).parent / "suitability_rules.yaml"

DECISION_HIGH = "hoch"
DECISION_MEDIUM = "mittel"
DECISION_LOW = "niedrig"
DECISION_REJECT = "abgelehnt"

# Überschriften, die einen Muss-/Anforderungsabschnitt einleiten bzw. beenden.
# Nur als Überschrift (Zeilenanfang oder fett, gefolgt von ":" / "**" /
# Zeilenende), damit "Anforderungsworkshops" im Fließtext nicht zählt.
_MUST_START_RE = re.compile(
    r"(?:^|\n|\*\*)[ \t]*(?:ihr |dein |deine )?"
    r"(must[ \t-]*haves?|muss[ \t-]*(?:kriterien|anforderungen)|zwingend erforderlich|"
    r"voraussetzungen|anforderungen|profil|talente|requirements|qualifikation(?:en)?)"
    r"[ \t]*(?::|\*\*|\n|$)",
    re.IGNORECASE,
)
_MUST_END_RE = re.compile(
    r"(nice[ \t-]*to[ \t-]*have|soll[ \t-]*kriterien|kann[ \t-]*kriterien|wünschenswert|"
    r"von vorteil|rahmenparameter|rahmendaten|wir bieten|konditionen)",
    re.IGNORECASE,
)
_MUST_SECTION_MAX_CHARS = 2500


def _must_sections(text: str) -> str:
    """Gibt den Text aller Muss-/Anforderungsabschnitte zurück (kann leer sein)."""
    chunks: List[str] = []
    for start in _MUST_START_RE.finditer(text):
        rest = text[start.end():start.end() + _MUST_SECTION_MAX_CHARS]
        end = _MUST_END_RE.search(rest)
        chunks.append(rest[:end.start()] if end else rest)
    return "\n".join(chunks)


def _matches(terms: List[str], text: str) -> List[str]:
    """Liefert die Begriffe aus ``terms``, die in ``text`` vorkommen."""
    found: List[str] = []
    for term in terms or []:
        pattern = _compile_include_term(term)
        if pattern is not None and pattern.search(text):
            found.append(term)
    return found


@dataclass
class SuitabilityResult:
    """Ergebnis der Eignungsbewertung eines Projekts."""
    score: int                               # 0–100
    decision: str                            # hoch | mittel | niedrig | abgelehnt
    best_group: Optional[str] = None         # Gruppe mit dem höchsten Fachwert
    group_scores: Dict[str, int] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    reject_reason: Optional[str] = None

    @property
    def rejected(self) -> bool:
        return self.decision == DECISION_REJECT

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "score": self.score,
            "decision": self.decision,
            "best_group": self.best_group,
            "group_scores": dict(self.group_scores),
            "reasons": list(self.reasons),
        }
        if self.reject_reason:
            d["reject_reason"] = self.reject_reason
        return d


class SuitabilityScorer:
    """
    Bewertet Projekte anhand von suitability_rules.yaml.

    Usage:
        scorer = SuitabilityScorer()
        result = scorer.score_project({"title": ..., "description": ...})
    """

    def __init__(self, rules: Optional[Dict[str, Any]] = None,
                 rules_path: Optional[Path] = None) -> None:
        if rules is None:
            import yaml
            path = Path(rules_path) if rules_path is not None else DEFAULT_RULES_PATH
            with open(path, "r", encoding="utf-8") as fh:
                rules = (yaml.safe_load(fh) or {}).get("suitability") or {}
        self._rules = rules
        self._weights = rules.get("weights") or {}
        self._caps = rules.get("caps") or {}
        self._thresholds = rules.get("thresholds") or {}
        self._groups = rules.get("groups") or {}
        self._common = rules.get("common") or {}

    # ── Hilfsfunktionen ────────────────────────────────────────────────────────

    def _capped(self, category: str, hits: int) -> int:
        points = hits * int(self._weights.get(category, 0))
        cap = self._caps.get(category)
        if cap is None:
            return points
        return max(points, int(cap)) if int(cap) < 0 else min(points, int(cap))

    def _score_group(self, group_id: str, text: str) -> Tuple[int, int, List[str]]:
        """Gibt (Pluspunkte, Abzüge, Gründe) für eine Gruppe zurück."""
        cfg = self._groups.get(group_id) or {}
        positive = 0
        negative = 0
        reasons: List[str] = []
        labels = {
            "high": "Hohe Priorität",
            "medium": "Mittlere Priorität",
            "transferable": "Nur übertragbare Kompetenz (keine eigene Praxis)",
            "low_priority": "Niedrige Priorität",
        }
        for category, label in labels.items():
            found = _matches(cfg.get(category) or [], text)
            if not found:
                continue
            points = self._capped(category, len(found))
            if points >= 0:
                positive += points
            else:
                negative += points
            reasons.append(f"{label} [{group_id}]: {', '.join(found)} ({points:+d})")
        return positive, negative, reasons

    def _work_mode(self, text: str) -> Tuple[int, Optional[str]]:
        cfg = self._common.get("work_mode") or {}
        if _matches(cfg.get("onsite_only") or [], text):
            points = int(cfg.get("onsite_points", 0))
            return points, f"Arbeitsort: vollständig vor Ort ({points:+d})"
        if _matches(cfg.get("hybrid") or [], text):
            points = int(cfg.get("hybrid_points", 0))
            return points, f"Arbeitsort: hybrid ({points:+d})"
        if _matches(cfg.get("remote") or [], text):
            points = int(cfg.get("remote_points", 0))
            return points, f"Arbeitsort: remote ({points:+d})"
        return 0, None

    # ── Öffentliche API ────────────────────────────────────────────────────────

    def score_project(self, project_data: Dict[str, Any]) -> SuitabilityResult:
        """
        Bewertet ein Projekt gegen alle konfigurierten Gruppen.

        Args:
            project_data: Dict mit title, description/summary/body/content,
                optional schlagworte und details (z.B. einsatzart, auslastung).
        """
        title = str(project_data.get("title") or "")
        details = project_data.get("details")
        details_text = (
            " ; ".join(str(v) for v in details.values() if v)
            if isinstance(details, dict) else ""
        )
        body = "\n".join(filter(None, [
            str(project_data.get("description") or ""),
            str(project_data.get("summary") or ""),
            str(project_data.get("body") or ""),
            str(project_data.get("content") or ""),
        ]))
        text = "\n".join(filter(None, [
            title, body, _schlagworte_text(project_data), details_text,
        ]))

        reasons: List[str] = []

        # 1. Fachliche Schwerpunkte je Gruppe. Es zählt die Gruppe mit den meisten
        #    Pluspunkten (bei Gleichstand die mit den meisten Abzügen), inklusive
        #    ihrer Abzüge — so gehen Abzüge nicht verloren, wenn die andere Gruppe
        #    gar nicht trifft.
        group_scores: Dict[str, int] = {}
        parts: Dict[str, Tuple[int, int, List[str]]] = {}
        for group_id in self._groups:
            parts[group_id] = self._score_group(group_id, text)
            group_scores[group_id] = parts[group_id][0] + parts[group_id][1]
        best_group: Optional[str] = None
        if parts:
            candidate = max(parts, key=lambda g: (parts[g][0], -parts[g][1]))
            if parts[candidate][2]:
                best_group = candidate
        total = group_scores[best_group] if best_group else 0
        if best_group:
            reasons.extend(parts[best_group][2])

        # 2. Rolle / Seniorität (einmalig)
        role_cfg = self._common.get("role") or {}
        role_hits = _matches(role_cfg.get("terms") or [], text)
        if role_hits:
            points = int(role_cfg.get("points", 0))
            total += points
            reasons.append(f"Senior-/Beratungsrolle: {', '.join(role_hits[:4])} ({points:+d})")

        # 3. Arbeitsort
        points, reason = self._work_mode(text)
        total += points
        if reason:
            reasons.append(reason)

        # 4. Auslastung
        workload_cfg = self._common.get("workload") or {}
        workload_hits = _matches(workload_cfg.get("terms") or [], text)
        if workload_hits:
            points = int(workload_cfg.get("points", 0))
            total += points
            reasons.append(f"Auslastung in Teilzeit möglich: {', '.join(workload_hits)} ({points:+d})")

        # 5. Nicht belegte Muss-Anforderungen
        must_text = _must_sections(body)
        reject_reason: Optional[str] = None
        for rule in self._common.get("unsupported_must_haves") or []:
            terms = rule.get("terms") or []
            label = rule.get("label") or ", ".join(terms)
            in_must = _matches(terms, must_text)
            if not in_must:
                continue  # bloße Erwähnung außerhalb der Anforderungen: keine Folge
            if _matches(terms, title):
                reject_reason = (
                    f"Nicht belegte Muss-Anforderung im Titel und in den Anforderungen: "
                    f"{label} ({', '.join(in_must)})"
                )
                break
            penalty = int(self._rules.get("must_have_penalty", 0))
            total += penalty
            reasons.append(
                f"Nicht belegte Anforderung im Muss-Abschnitt: {label} "
                f"({', '.join(in_must)}) ({penalty:+d})"
            )

        score = max(0, min(100, total))
        if reject_reason:
            score = min(score, int(self._rules.get("reject_score_cap", 10)))
            reasons.append(reject_reason)
            decision = DECISION_REJECT
        elif score >= int(self._thresholds.get("high", 50)):
            decision = DECISION_HIGH
        elif score >= int(self._thresholds.get("medium", 25)):
            decision = DECISION_MEDIUM
        else:
            decision = DECISION_LOW

        return SuitabilityResult(
            score=score,
            decision=decision,
            best_group=best_group,
            group_scores=group_scores,
            reasons=reasons,
            reject_reason=reject_reason,
        )
