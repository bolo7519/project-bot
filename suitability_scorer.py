"""
suitability_scorer.py — Regelbasierte Eignungsbewertung für erfasste Projekte.

Kostenlos und lokal (kein LLM, keine API). Bewertet ein bereits fachlich
erfasstes Projekt gegen suitability_rules.yaml und liefert drei getrennte
Ergebnisse:

1. Fachliche Eignung   — Wert 0–100 und Einstufung (hoch/mittel/niedrig/abgelehnt)
2. Rahmenbedingungen   — Arbeitsort, Auslastung, Starttermin; je Angabe ein
                         Status (erfüllt / teilweise / nicht erfüllt /
                         unbekannt / nicht geprüft / Konflikt)
3. Gesamtempfehlung    — Bewerben / Prüfen / Nicht bewerben

Abgrenzung zu den Nachbarmodulen:
- filter_engine.py / search_group_filters.yaml: Erfassung (gehört das Projekt
  fachlich zu einer Suchgruppe?). Wird hier nicht verändert.
- pre_scorer.py: TF-IDF-Ähnlichkeit gegen die vier Kompetenzprofile. Bleibt
  unverändert und unabhängig.

Grundsätze:
- Ein einzelner unerwünschter Begriff schließt nie aus. Abgelehnt wird nur,
  wenn eine nicht belegte Spezialisierung im Titel steht UND im Muss-Abschnitt
  verlangt wird.
- Viele passende Schlagworte überdecken keine zwingend geforderte
  Spezialerfahrung: reine Spezialistenrollen werden fachlich gedeckelt.
- Fehlende Angaben zu den Rahmenbedingungen sind "unbekannt", nie "erfüllt".
- Ohne konfigurierte Verfügbarkeit wird der Starttermin nur angezeigt.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from filter_engine import _compile_include_term, _schlagworte_text

logger = logging.getLogger(__name__)

DEFAULT_RULES_PATH = Path(__file__).parent / "suitability_rules.yaml"

# Fachliche Einstufung
DECISION_HIGH = "hoch"
DECISION_MEDIUM = "mittel"
DECISION_LOW = "niedrig"
DECISION_REJECT = "abgelehnt"

# Status einer Rahmenbedingung
STATUS_OK = "erfüllt"
STATUS_PARTIAL = "teilweise"
STATUS_NOT_OK = "nicht erfüllt"
STATUS_UNKNOWN = "unbekannt"
STATUS_NOT_CHECKED = "nicht geprüft"
STATUS_CONFLICT = "Konflikt"

# Gesamtempfehlung
RECOMMEND_APPLY = "Bewerben"
RECOMMEND_REVIEW = "Prüfen"
RECOMMEND_SKIP = "Nicht bewerben"

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


# ──────────────────────────────────────────────────────────────────────────────
# Rahmenbedingungen aus dem Ausschreibungstext lesen
# ──────────────────────────────────────────────────────────────────────────────

_HOURS = r"(?:Stunden|Std\.?|h)"
_PER_WEEK = r"(?:pro|/|je|die|per)\s*Woche"
_HOURS_RANGE_RE = re.compile(
    rf"(\d{{1,2}})\s*(?:-|–|bis)\s*(\d{{1,2}})\s*{_HOURS}\s*{_PER_WEEK}", re.IGNORECASE)
_HOURS_RE = re.compile(rf"(\d{{1,2}})\s*{_HOURS}\s*{_PER_WEEK}", re.IGNORECASE)
_DAYS_RE = re.compile(
    rf"(\d)(?:\s*(?:-|–|bis)\s*(\d))?\s*Tage\s*{_PER_WEEK}", re.IGNORECASE)
_PERCENT_RE = re.compile(r"Auslastung\W{0,8}(\d{2,3})\s*%", re.IGNORECASE)
_FULL_TIME_RE = re.compile(r"\b(Vollzeit|full[ -]?time)\b", re.IGNORECASE)
_PART_TIME_RE = re.compile(r"\b(Teilzeit|part[ -]?time)\b", re.IGNORECASE)
_REMOTE_PERCENT_RE = re.compile(r"(\d{1,3})\s*%\s*Remote", re.IGNORECASE)

_MONTHS = {
    "januar": 1, "january": 1, "februar": 2, "february": 2, "märz": 3, "maerz": 3,
    "march": 3, "april": 4, "mai": 5, "may": 5, "juni": 6, "june": 6, "juli": 7,
    "july": 7, "august": 8, "september": 9, "oktober": 10, "october": 10,
    "november": 11, "dezember": 12, "december": 12,
}
_START_LABEL_RE = re.compile(
    r"(Projektzeitraum|Zeitraum|Laufzeit|Projektstart|Einsatzbeginn|Starttermin|Start|Beginn)"
    r"\**\s*:?\**\s*([^\n]{0,80})",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_MONTH_RE = re.compile(
    r"(?:(Anfang|Mitte|Ende)\s+)?(" + "|".join(_MONTHS) + r")(?:\s+(\d{4}))?",
    re.IGNORECASE,
)
_IMMEDIATE_RE = re.compile(r"\b(asap|ab sofort|sofort|schnellstmöglich|kurzfristig)\b", re.IGNORECASE)


def _parse_iso_date(value: Any) -> Optional[date]:
    """Liest ein Datum (date, datetime oder 'YYYY-MM-DD'); None bei leer/ungültig."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        logger.warning("Ungültiges Datum für available_from: %r — wird ignoriert", value)
        return None


def _dates_in_segment(segment: str, today: date) -> List[date]:
    found: List[date] = []
    for day, month, year in _DATE_RE.findall(segment):
        try:
            found.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    for part, month_name, year in _MONTH_RE.findall(segment):
        month = _MONTHS[month_name.lower()]
        day = {"mitte": 15, "ende": 28}.get(part.lower(), 1)
        if year:
            found.append(date(int(year), month, day))
        else:
            # Ohne Jahr: der nächste solche Monat ab heute
            candidate = date(today.year, month, day)
            if candidate < today.replace(day=1):
                candidate = date(today.year + 1, month, day)
            found.append(candidate)
    return found


def _parse_start(text: str, today: date) -> Tuple[Optional[date], Optional[str]]:
    """
    Liest den spätestmöglichen Starttermin.

    Returns:
        (Datum, Anzeige) — (None, None), wenn kein Starttermin angegeben ist.
        "sofort"/"asap" ohne Datum wird als heute gewertet.
    """
    for label, segment in _START_LABEL_RE.findall(text):
        dates = _dates_in_segment(segment, today)
        immediate = bool(_IMMEDIATE_RE.search(segment))
        if not dates and not immediate:
            continue
        if not dates:
            return today, "sofort"
        if label.lower() in ("zeitraum", "projektzeitraum", "laufzeit"):
            start = dates[0]          # "von – bis": das erste Datum ist der Start
            return start, start.strftime("%d.%m.%Y")
        start = max(dates)            # Startfenster: der späteste genannte Termin
        prefix = "sofort bis spätestens " if immediate else (
            "spätestens " if len(dates) > 1 else "")
        return start, prefix + start.strftime("%d.%m.%Y")
    return None, None


@dataclass
class ConditionCheck:
    """Ergebnis einer einzelnen Rahmenbedingung."""
    status: str
    value: Optional[str] = None      # gefundene Angabe, None = nichts gefunden
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"status": self.status, "value": self.value}
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class SuitabilityResult:
    """Fachliche Eignung, Rahmenbedingungen und Gesamtempfehlung eines Projekts."""
    score: int                               # Gesamtwert 0–100 (fachlich inkl. Abzüge)
    technical_score: int                     # fachliche Eignung 0–100
    decision: str                            # fachlich: hoch | mittel | niedrig | abgelehnt
    recommendation: str                      # Bewerben | Prüfen | Nicht bewerben
    best_group: Optional[str] = None
    group_scores: Dict[str, int] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)              # fachliche Gründe
    conditions: Dict[str, ConditionCheck] = field(default_factory=dict)
    recommendation_reasons: List[str] = field(default_factory=list)
    reject_reason: Optional[str] = None

    @property
    def rejected(self) -> bool:
        return self.decision == DECISION_REJECT

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "score": self.score,
            "recommendation": self.recommendation,
            "recommendation_reasons": list(self.recommendation_reasons),
            "technical": {
                "score": self.technical_score,
                "decision": self.decision,
                "best_group": self.best_group,
                "group_scores": dict(self.group_scores),
                "reasons": list(self.reasons),
            },
            "conditions": {k: v.to_dict() for k, v in self.conditions.items()},
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

    _UNSET = object()

    def __init__(
        self,
        rules: Optional[Dict[str, Any]] = None,
        rules_path: Optional[Path] = None,
        available_from: Any = _UNSET,
        today: Optional[date] = None,
    ) -> None:
        """
        Args:
            rules: Regel-Dict (Inhalt von ``suitability``); Default: aus Datei.
            rules_path: Alternative Regeldatei.
            available_from: Verfügbarkeit des Bewerbers (ISO-Datum). Ohne Angabe
                gilt ``applicant.available_from`` aus den Regeln; None = nicht
                konfiguriert → Starttermin wird nicht geprüft.
            today: Bezugsdatum für "sofort" und Monatsangaben ohne Jahr.
        """
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
        self._conditions = rules.get("conditions") or {}
        if available_from is self._UNSET:
            available_from = (rules.get("applicant") or {}).get("available_from")
        self._available_from = _parse_iso_date(available_from)
        self._today = today

    # ── Fachliche Eignung ──────────────────────────────────────────────────────

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

    def _specialist_cap(self, title: str, must_text: str) -> Tuple[Optional[int], Optional[str]]:
        """
        Prüft, ob eine reine Spezialistenrolle vorliegt.

        Returns:
            (Deckel, Grund) oder (None, None).
        """
        for rule in self._common.get("specialist_roles") or []:
            required = _matches(rule.get("required_experience_terms") or [], must_text)
            if not required:
                continue
            in_title = _matches(rule.get("title_terms") or [], title)
            if in_title or len(required) >= 2:
                cap = int(rule.get("score_cap", 40))
                label = rule.get("label") or "Spezialistenrolle"
                where = f"Titel: {', '.join(in_title)}; " if in_title else ""
                return cap, (
                    f"{label}: zwingend geforderte Spezialerfahrung nicht belegt "
                    f"({where}Anforderungen: {', '.join(required[:5])}) "
                    f"— fachliche Eignung auf {cap} begrenzt"
                )
        return None, None

    # ── Rahmenbedingungen ──────────────────────────────────────────────────────

    def _check_work_mode(self, text: str) -> ConditionCheck:
        cfg = self._conditions.get("work_mode") or {}
        onsite = _matches(cfg.get("onsite_only") or [], text)
        if onsite:
            return ConditionCheck(STATUS_NOT_OK, "vollständig vor Ort", f"Angabe: {onsite[0]}")
        percent = _REMOTE_PERCENT_RE.search(text)
        if percent:
            share = int(percent.group(1))
            if share >= 100:
                return ConditionCheck(STATUS_OK, "100 % remote")
            if share <= 0:
                return ConditionCheck(STATUS_NOT_OK, "0 % remote")
            return ConditionCheck(STATUS_PARTIAL, f"{share} % remote")
        if _matches(cfg.get("hybrid") or [], text):
            return ConditionCheck(STATUS_PARTIAL, "hybrid")
        if _matches(cfg.get("remote") or [], text):
            return ConditionCheck(STATUS_OK, "remote")
        return ConditionCheck(STATUS_UNKNOWN, None, "Kein Arbeitsort angegeben")

    def _check_workload(self, text: str) -> ConditionCheck:
        cfg = self._conditions.get("workload") or {}
        low = float(cfg.get("preferred_hours_min", 20))
        high = float(cfg.get("preferred_hours_max", 40))
        full_time = float(cfg.get("full_time_hours", 40))

        hours: Optional[Tuple[float, float]] = None
        label: Optional[str] = None
        m = _HOURS_RANGE_RE.search(text)
        if m:
            hours = (float(m.group(1)), float(m.group(2)))
        if hours is None:
            m = _HOURS_RE.search(text)
            if m:
                hours = (float(m.group(1)), float(m.group(1)))
        if hours is None:
            m = _DAYS_RE.search(text)
            if m:
                first = float(m.group(1)) * 8
                hours = (first, float(m.group(2)) * 8 if m.group(2) else first)
                label = m.group(0)
        if hours is None:
            m = _PERCENT_RE.search(text)
            if m:
                value = int(m.group(1)) * full_time / 100
                hours = (value, value)
                label = f"{m.group(1)} %"
        if hours is None and _FULL_TIME_RE.search(text):
            hours = (full_time, full_time)
            label = "Vollzeit"

        if hours is None:
            if _PART_TIME_RE.search(text):
                return ConditionCheck(STATUS_UNKNOWN, "Teilzeit", "Teilzeit ohne Stundenangabe")
            return ConditionCheck(STATUS_UNKNOWN, None, "Keine Auslastung angegeben")

        lo, hi = hours
        shown = f"{lo:g} h/Woche" if lo == hi else f"{lo:g}–{hi:g} h/Woche"
        if label:
            shown = f"{label} ({shown})"
        in_range = hi >= low and lo <= high
        return ConditionCheck(
            STATUS_OK if in_range else STATUS_NOT_OK,
            shown,
            None if in_range else f"Außerhalb von {low:g}–{high:g} h/Woche",
        )

    def _check_start(self, text: str) -> ConditionCheck:
        today = self._today or date.today()
        start, shown = _parse_start(text, today)
        if start is None:
            return ConditionCheck(STATUS_UNKNOWN, None, "Kein Starttermin angegeben")
        if self._available_from is None:
            return ConditionCheck(
                STATUS_NOT_CHECKED, shown, "Verfügbarkeit des Bewerbers nicht konfiguriert")
        available = self._available_from.strftime("%d.%m.%Y")
        if start < self._available_from:
            return ConditionCheck(
                STATUS_CONFLICT, shown, f"Start vor der konfigurierten Verfügbarkeit ab {available}")
        return ConditionCheck(STATUS_OK, shown, f"Verfügbar ab {available}")

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
            " ; ".join(f"{k}: {v}" for k, v in details.items() if v)
            if isinstance(details, dict) else ""
        )
        body = "\n".join(filter(None, [
            str(project_data.get("description") or ""),
            str(project_data.get("summary") or ""),
            str(project_data.get("body") or ""),
            str(project_data.get("content") or ""),
        ]))
        text = "\n".join(filter(None, [title, body, _schlagworte_text(project_data)]))
        conditions_text = "\n".join(filter(None, [text, details_text]))

        reasons: List[str] = []

        # ── 1. Fachliche Eignung ──────────────────────────────────────────────
        # Es zählt die Gruppe mit den meisten Pluspunkten, inklusive ihrer Abzüge.
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
        technical = group_scores[best_group] if best_group else 0
        if best_group:
            reasons.extend(parts[best_group][2])

        role_cfg = self._common.get("role") or {}
        role_hits = _matches(role_cfg.get("terms") or [], text)
        if role_hits:
            points = int(role_cfg.get("points", 0))
            technical += points
            reasons.append(f"Senior-/Beratungsrolle: {', '.join(role_hits[:4])} ({points:+d})")

        must_text = _must_sections(body)

        # Nicht belegte Muss-Anforderungen
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
            technical += penalty
            reasons.append(
                f"Nicht belegte Anforderung im Muss-Abschnitt: {label} "
                f"({', '.join(in_must)}) ({penalty:+d})"
            )

        technical = max(0, min(100, technical))

        # Reine Spezialistenrolle: Deckel statt Ablehnung
        cap, cap_reason = self._specialist_cap(title, must_text)
        if cap is not None and technical > cap:
            technical = cap
            reasons.append(cap_reason)
        elif cap is not None:
            reasons.append(cap_reason.replace(
                f" — fachliche Eignung auf {cap} begrenzt", ""))

        if reject_reason:
            technical = min(technical, int(self._rules.get("reject_score_cap", 10)))
            reasons.append(reject_reason)
            decision = DECISION_REJECT
        elif technical >= int(self._thresholds.get("high", 50)):
            decision = DECISION_HIGH
        elif technical >= int(self._thresholds.get("medium", 25)):
            decision = DECISION_MEDIUM
        else:
            decision = DECISION_LOW

        # ── 2. Rahmenbedingungen ──────────────────────────────────────────────
        conditions = {
            "work_mode": self._check_work_mode(conditions_text),
            "workload": self._check_workload(conditions_text),
            "start": self._check_start(conditions_text),
        }

        # ── 3. Gesamtwert und Empfehlung ──────────────────────────────────────
        adjustments = self._conditions.get("score_adjustments") or {}
        score = technical
        if conditions["work_mode"].status == STATUS_NOT_OK:
            score += int(adjustments.get("onsite_only", 0))
        if conditions["workload"].status == STATUS_NOT_OK:
            score += int(adjustments.get("workload_outside", 0))
        score = max(0, min(100, score))

        recommendation, why = self._recommend(decision, conditions, reject_reason)

        return SuitabilityResult(
            score=score,
            technical_score=technical,
            decision=decision,
            recommendation=recommendation,
            best_group=best_group,
            group_scores=group_scores,
            reasons=reasons,
            conditions=conditions,
            recommendation_reasons=why,
            reject_reason=reject_reason,
        )

    @staticmethod
    def _recommend(
        decision: str,
        conditions: Dict[str, ConditionCheck],
        reject_reason: Optional[str],
    ) -> Tuple[str, List[str]]:
        names = {"work_mode": "Arbeitsort", "workload": "Auslastung", "start": "Starttermin"}
        violated = [
            k for k, c in conditions.items() if c.status in (STATUS_NOT_OK, STATUS_CONFLICT)
        ]
        unknown = [names[k] for k, c in conditions.items() if c.status == STATUS_UNKNOWN]
        why: List[str] = []

        if decision == DECISION_REJECT:
            return RECOMMEND_SKIP, [reject_reason or "Fachlich abgelehnt"]
        if decision == DECISION_LOW:
            return RECOMMEND_SKIP, ["Fachliche Eignung niedrig"]

        for key in violated:
            check = conditions[key]
            detail = check.note or check.value or check.status
            why.append(f"{names[key]}: {check.status} ({detail})")

        if decision == DECISION_MEDIUM:
            if conditions["work_mode"].status == STATUS_NOT_OK:
                recommendation = RECOMMEND_SKIP
                why.insert(0, "Fachliche Eignung nur mittel und vollständig vor Ort")
            else:
                recommendation = RECOMMEND_REVIEW
                why.insert(0, "Fachliche Eignung mittel")
        elif violated:
            recommendation = RECOMMEND_REVIEW
            why.insert(0, "Fachliche Eignung hoch, aber Rahmenbedingung verletzt")
        else:
            recommendation = RECOMMEND_APPLY
            why.insert(0, "Fachliche Eignung hoch, keine Rahmenbedingung verletzt")

        if unknown:
            why.append(f"Offen (unbekannt): {', '.join(unknown)}")
        if conditions["start"].status == STATUS_NOT_CHECKED:
            why.append("Starttermin nicht gegen eine Verfügbarkeit geprüft")
        return recommendation, why
