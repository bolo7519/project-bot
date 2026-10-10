"""
suitability_scorer.py — Regelbasierte Eignungsbewertung für erfasste Projekte.

Kostenlos und lokal (kein LLM, keine API). Bewertet ein bereits fachlich
erfasstes Projekt gegen suitability_rules.yaml und liefert drei getrennte
Ergebnisse:

1. Fachliche Eignung   — Wert 0–100 und Einstufung (hoch/mittel/niedrig/abgelehnt)
2. Rahmenbedingungen   — Vertragsart, Arbeitsort, Auslastung, Starttermin,
                         Laufzeit (nur Anzeige); je Angabe ein
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
- Reine Softwareentwicklerrollen mit zwingenden, nicht belegten Technologien
  bleiben unter der Schwelle "mittel". Power BI, CRM-Integration und
  Prozessautomatisierung sind davon nicht pauschal betroffen.
- Die Fundstelle zählt: Kernthema im Titel gibt einen Zuschlag, Begriffe nur
  unter Nice-to-have zählen schwach, eine einzelne beiläufige Erwähnung ergibt
  keine mittlere Eignung, der Senior-Bonus setzt einen fachlichen Treffer voraus.
- Verlangte, aber nicht bestätigte Kenntnisse verhindern die Einstufung "hoch"
  und werden als ``missing_requirements`` ausgewiesen.
- Festanstellung und Anbieterwerbung sind keine Freelance-Projekte und werden
  nicht empfohlen.
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

# Kennzeichnung des Eignungs-Scores im Frontmatter
SCORE_METHOD = "eignung_regelbasiert"

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

# ── Abschnitte einer Ausschreibung ───────────────────────────────────────────
# Erkannt werden Überschriftenzeilen ("Ihr Profil:", "**Ihre Kenntnisse:**",
# "Must-Have-Skills:", "Anforderungen (Muss)"), nie Wörter im Fließtext —
# "Anforderungsworkshops durchführen" ist keine Überschrift.
_MUST_WORDS = (
    r"must[ \t-]*haves?|muss[ \t-]*(?:kriterien|anforderungen)|zwingend erforderlich|"
    r"voraussetzungen|anforderungsprofil|anforderungen|profil|talente|requirements|"
    r"qualifikation(?:en)?|qualifications|kenntnisse|skillset|skills|know[ -]?how|"
    r"das bring(?:st du|en sie) mit|was (?:sie mitbringen|du mitbringst)|"
    r"your profile|your skills"
)
_HEADING_PREFIX = (
    r"(?:(?:ihr|ihre|dein|deine|unsere|unser|fachliche|technische|zwingende|"
    r"erforderliche|your|required)\s+)*"
)
# Überschrift am Zeilenanfang, optional mit Inhalt hinter dem Doppelpunkt
_MUST_HEAD_RE = re.compile(
    rf"^{_HEADING_PREFIX}(?:{_MUST_WORDS})\b\s*(?:\([^)]*\)?)?[\s-]*(?:skills?)?\s*(?::(.*)|$)",
    re.IGNORECASE,
)
# Einleitungszeile, die mit Doppelpunkt endet ("… mit folgendem Skillset:")
_MUST_ANY_RE = re.compile(rf"\b(?:{_MUST_WORDS})\b", re.IGNORECASE)
_NICE_WORDS = (
    r"nice[ \t-]*to[ \t-]*haves?|soll[ \t-]*kriterien|kann[ \t-]*kriterien|wünschenswert|"
    r"von vorteil|von nutzen|(?:ist |wäre )?ein plus\b|pluspunkt"
)
# Als Überschrift zusätzlich "Optional" / "Idealerweise"; im Fließtext nicht,
# denn "Erfahrung in X, idealerweise mit Y" bleibt eine feste Anforderung an X.
_NICE_HEAD_RE = re.compile(rf"(?:{_NICE_WORDS}|optional|idealerweise)", re.IGNORECASE)
_NICE_RE = re.compile(_NICE_WORDS, re.IGNORECASE)
# Zeilen, die einen Anforderungsabschnitt beenden
_SECTION_END_RE = re.compile(
    r"^(?:rahmenparameter|rahmendaten|rahmenbedingungen|eckdaten|quick facts|wir bieten|"
    r"konditionen|das zeichnet (?:sie|dich) aus|ihre aufgaben|deine aufgaben|aufgaben|"
    r"kontakt|bei interesse|ort|einsatzort|arbeitsort|standort|start|projektstart|"
    r"projektdauer|dauer|laufzeit|arbeitsmodell|sektor|branche|sprache)\b",
    re.IGNORECASE,
)
_LINE_DECOR_RE = re.compile(r"^[\s\-\*•·#>]+|[\s\*]+$")
_MUST_SECTION_MAX_CHARS = 2500
_NICE_SECTION_MAX_CHARS = 1200


@dataclass
class Sections:
    """Text einer Ausschreibung, nach Verbindlichkeit getrennt."""
    must: str = ""          # Muss-/Anforderungsabschnitte
    nice: str = ""          # Nice-to-have: Abschnitte und einzelne Zeilen
    main: str = ""          # alles außer Nice-to-have


def _split_sections(text: str) -> Sections:
    """
    Teilt den Ausschreibungstext in Muss-, Nice-to-have- und Haupttext.

    Nice-to-have sind Abschnitte unter einer entsprechenden Überschrift sowie
    einzelne Zeilen mit einem Hinweis wie "von Vorteil" oder "wünschenswert".
    """
    must: List[str] = []
    nice: List[str] = []
    main: List[str] = []
    state: Optional[str] = None
    budget = 0
    # Aufzählungen und fette Überschriften, die mitten in einer Zeile stehen
    # ("**Anforderungen** • Punkt 1 • Punkt 2"), auf eigene Zeilen bringen.
    text = re.sub(r"[ \t]*•[ \t]*", "\n• ", text)
    text = re.sub(r"\*\*([^*\n]{2,70})\*\*", r"\n**\1**\n", text)
    for raw in text.splitlines():
        clean = _LINE_DECOR_RE.sub("", raw).replace("**", "").strip()
        if not clean or not re.search(r"\w", clean):
            main.append(raw)            # Leerzeile oder nur Satzzeichen
            continue
        is_short = len(clean) <= 80
        nice_heading = is_short and len(clean) <= 60 and bool(_NICE_HEAD_RE.match(clean)) and (
            clean.endswith(":") or len(clean) <= 30)
        head = _MUST_HEAD_RE.match(clean) if is_short else None
        intro = (
            head is None and is_short and clean.endswith(":")
            and _MUST_ANY_RE.search(clean) is not None
        )
        if nice_heading:
            state, budget = "nice", _NICE_SECTION_MAX_CHARS
            continue
        if head is not None or intro:
            state, budget = "must", _MUST_SECTION_MAX_CHARS
            main.append(raw)
            rest = (head.group(1) or "").strip() if head is not None else ""
            if rest:
                must.append(rest)
            continue
        if state and (
            _SECTION_END_RE.match(clean)
            or (clean.endswith(":") and len(clean) <= 45)
        ):
            state = None
        if state:
            budget -= len(raw) + 1
            if budget < 0:
                state = None

        if state == "nice" or _NICE_RE.search(clean):
            nice.append(raw)            # zählt nur abgeschwächt
            continue
        main.append(raw)
        if state == "must":
            must.append(raw)
    return Sections(must="\n".join(must), nice="\n".join(nice), main="\n".join(main))


def _must_sections(text: str) -> str:
    """Gibt den Text aller Muss-/Anforderungsabschnitte zurück (kann leer sein)."""
    return _split_sections(text).must


def _matches(terms: List[Any], text: str) -> List[str]:
    """
    Liefert die Begriffe aus ``terms``, die in ``text`` vorkommen.

    Ein Eintrag ist ein Begriff oder ``{"term": ..., "with": [...]}``: Letzterer
    zählt nur, wenn zusätzlich mindestens einer der ``with``-Begriffe vorkommt
    (z.B. "DAX" nur im Power-BI-Zusammenhang, nicht bei "DAX-Konzern").
    """
    found: List[str] = []
    for entry in terms or []:
        context: List[str] = []
        term = entry
        if isinstance(entry, dict):
            term = entry.get("term")
            context = list(entry.get("with") or [])
        if not term:
            continue
        pattern = _compile_include_term(str(term))
        if pattern is None or not pattern.search(text):
            continue
        if context and not _matches(context, text):
            continue
        found.append(str(term))
    return found


# ──────────────────────────────────────────────────────────────────────────────
# Rahmenbedingungen aus dem Ausschreibungstext lesen
# ──────────────────────────────────────────────────────────────────────────────

_HOURS = r"(?:Stunden|Std\.?|h)"
_PER_WEEK = r"(?:pro|/|je|die|per)\s*Woche"
_HOURS_RANGE_RE = re.compile(
    rf"(\d{{1,2}})\s*(?:-|–|bis)\s*(\d{{1,2}})\s*{_HOURS}\s*{_PER_WEEK}", re.IGNORECASE)
_HOURS_RE = re.compile(rf"(\d{{1,2}})\s*{_HOURS}\s*{_PER_WEEK}", re.IGNORECASE)
# Tage pro Woche als Auslastung — nicht, wenn die Angabe den Arbeitsort meint
# ("2-4 Tage/Woche on-site", "4 Tage pro Woche vor Ort").
_PLACE_WORDS = r"(?:on-?site|vor[ -]Ort|remote|im Büro|Präsenz|Home[ -]?office)"
_DAYS_RE = re.compile(
    rf"(\d)(?:\s*(?:-|–|bis)\s*(\d))?\s*Tage\s*{_PER_WEEK}(?![^\n]{{0,15}}{_PLACE_WORDS})",
    re.IGNORECASE)
# "Auslastung: 100 %", "Pensum: 80%", "Workload: 100 %", "ab 80% bis zu 100%"
_PERCENT_RE = re.compile(
    r"(?:Auslastung|Arbeitspensum|Pensum|Workload|Arbeitsumfang|Kapazität)\W{0,12}"
    r"(?:ab\s*|ca\.?\s*|min\.?\s*|mind\.?\s*)?(\d{2,3})\s*%?\s*"
    r"(?:(?:-|–|bis(?:\s*zu)?)\s*(\d{2,3})\s*)?%",
    re.IGNORECASE)
# Prozentangabe im Titel ("[100%]", "(80-100%)") — nicht "100% remote"
_TITLE_PERCENT_RE = re.compile(
    r"(\d{2,3})\s*(?:(?:-|–)\s*(\d{2,3})\s*)?%(?!\s*(?:rem|vor|on|home))", re.IGNORECASE)
_FULL_TIME_RE = re.compile(r"\b(Vollzeit|full[ -]?time)\b", re.IGNORECASE)
_PART_TIME_RE = re.compile(r"\b(Teilzeit|part[ -]?time)\b", re.IGNORECASE)
_REMOTE_PERCENT_RE = re.compile(r"(\d{1,3})\s*%\s*Remote", re.IGNORECASE)
_ONSITE_PERCENT_RE = re.compile(
    r"(\d{1,3})\s*%\s*(?:vor[ -]Ort|on-?site|Präsenz|im Büro)", re.IGNORECASE)
_FULL_REMOTE_RE = re.compile(
    r"\b(?:full(?:y)?[ -]?remote|remote[ -]only|vollständig remote|komplett remote|"
    r"rein remote|ausschließlich remote)\b", re.IGNORECASE)
# Bloße Feldbezeichnung, keine Aussage über den Arbeitsort
_PLACE_LABEL_RE = re.compile(
    r"(?:remote\s*/\s*on-?site|on-?site\s*/\s*remote|remote\s*/\s*vor[ -]Ort|"
    r"vor[ -]Ort\s*/\s*remote)\s*:", re.IGNORECASE)
_ONSITE_NEGATED_RE = re.compile(
    r"\b(?:kein(?:e|en|er)?|ohne|nicht)\s+(?:\w+\s+)?(?:vor[ -]Ort\w*|on-?site\w*|"
    r"Präsenz\w*|Anwesenheit\w*|Reisetätigkeit)", re.IGNORECASE)
_ONSITE_HINT_RE = re.compile(
    r"(?<![\w])(vor[ -]Ort|on-?site|Präsenz\w*|im Büro|Büropräsenz|Anwesenheit\w*)", re.IGNORECASE)
_PLACE_LINE_RE = re.compile(
    r"(?:Arbeitsort|Einsatzort|Standort|Projektstandort|Ort|Location)\**\s*:?\**\s*([^\n]{0,90})",
    re.IGNORECASE)
_PLACE_MIX_RE = re.compile(
    r"\w{3,}\)?\s*(?:und|oder|sowie|\+|/|,)\s*remote|"
    r"remote\s*(?:und|oder|sowie|\+)\s*\w", re.IGNORECASE)

# Vertragsart
_NOT_EMPLOYMENT_RE = re.compile(
    r"\bkein(?:e|en|er)?\s+(?:Festanstellung|Arbeitnehmerüberlassung|ANÜ|Personalverleih)",
    re.IGNORECASE)

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
# Hier endet die Startangabe: Was danach steht, gehört zu Ende, Dauer oder
# einem anderen Feld derselben Zeile ("Start: 1.11.2026 Ende: 30.9.2027").
_START_SEGMENT_END_RE = re.compile(
    r"\b(?:Ende|Enddatum|Projektende|End date|Dauer|Projektdauer|Pensum|Auslastung|"
    r"Arbeitsort|Einsatzort)\b", re.IGNORECASE)
_NO_LETTER_BEFORE = r"(?<![A-Za-zÄÖÜäöüß])"
_END_LABEL_RE = re.compile(
    _NO_LETTER_BEFORE + r"(?:Projektende|Enddatum|Ende|End date)\**\s*:\**\s*([^\n]{0,40})",
    re.IGNORECASE)
_PERIOD_LABEL_RE = re.compile(
    _NO_LETTER_BEFORE + r"(Projektzeitraum|Einsatzzeitraum|Zeitraum|Projektlaufzeit|Vertragslaufzeit|Laufzeit|"
    r"Projektdauer|Einsatzdauer|Dauer)"
    r"\**\s*:?\**\s*([^\n]{0,80})", re.IGNORECASE)
_DURATION_RE = re.compile(
    r"(?:ca\.?\s*)?\d{1,2}(?:\s*(?:-|–|bis)\s*\d{1,2})?\s*\+?\s*"
    r"(?:Monate?n?|MM|Wochen|Jahre?|PT|Personentage)\b\+*", re.IGNORECASE)
_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_MONTH_RE = re.compile(
    r"(?:(Anfang|Mitte|Ende)\s+|(\d{1,2})\.\s*)?(" + "|".join(_MONTHS) + r")(?:\s+(\d{4}))?",
    re.IGNORECASE,
)
_IMMEDIATE_RE = re.compile(
    r"\b(asap|ab sofort|per sofort|sofort|schnellstmöglich\w*|nächstmöglich\w*|kurzfristig)\b",
    re.IGNORECASE)


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
    for part, day_text, month_name, year in _MONTH_RE.findall(segment):
        month = _MONTHS[month_name.lower()]
        day = {"mitte": 15, "ende": 28}.get(part.lower(), 1)
        if day_text:
            try:
                date(int(year) if year else today.year, month, int(day_text))
                day = int(day_text)
            except ValueError:
                pass
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
        is_period = label.lower() in ("zeitraum", "projektzeitraum", "laufzeit")
        if not is_period:
            segment = _START_SEGMENT_END_RE.split(segment, maxsplit=1)[0]
        dates = _dates_in_segment(segment, today)
        immediate = bool(_IMMEDIATE_RE.search(segment))
        if not dates and not immediate:
            continue
        if not dates:
            return today, "sofort"
        if is_period:
            start = dates[0]          # "von – bis": das erste Datum ist der Start
            return start, start.strftime("%d.%m.%Y")
        start = max(dates)            # Startfenster: der späteste genannte Termin
        prefix = "sofort bis spätestens " if immediate else (
            "spätestens " if len(dates) > 1 else "")
        return start, prefix + start.strftime("%d.%m.%Y")
    return None, None


def _parse_end(text: str, today: date) -> Optional[str]:
    """
    Liest Projektende bzw. Laufzeit — getrennt vom Starttermin.

    Returns:
        Anzeige wie "bis 30.09.2027" oder "5-6 Monate"; None ohne Angabe.
    """
    for segment in _END_LABEL_RE.findall(text):
        dates = _dates_in_segment(segment, today)
        if dates:
            return "bis " + dates[0].strftime("%d.%m.%Y")
    for label, segment in _PERIOD_LABEL_RE.findall(text):
        dates = _dates_in_segment(segment, today)
        if len(dates) >= 2:
            return "bis " + max(dates).strftime("%d.%m.%Y")
        if dates and re.search(r"\bbis\b", segment, re.IGNORECASE):
            return "bis " + dates[-1].strftime("%d.%m.%Y")
        duration = _DURATION_RE.search(segment)
        if duration:
            return duration.group(0).strip()
    return None


@dataclass
class GroupScore:
    """Zwischenergebnis der Bewertung einer Suchgruppe."""
    positive: int = 0
    negative: int = 0
    reasons: List[str] = field(default_factory=list)
    full_hits: int = 0                                   # Treffer mit vollem Gewicht
    title_hits: Dict[str, List[str]] = field(default_factory=dict)   # high/medium im Titel
    low_title_hits: List[str] = field(default_factory=list)          # low_priority im Titel


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
    # Zwingend verlangte, nicht belegte bzw. nicht bestätigte Kompetenzen
    missing_requirements: List[str] = field(default_factory=list)
    conditions: Dict[str, ConditionCheck] = field(default_factory=dict)
    recommendation_reasons: List[str] = field(default_factory=list)
    reject_reason: Optional[str] = None

    @property
    def rejected(self) -> bool:
        return self.decision == DECISION_REJECT

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            # Eignungs-Score (regelbasiert). Nicht zu verwechseln mit der
            # Textähnlichkeit unter pre_scores (TF-IDF, Skala 0–1).
            "method": SCORE_METHOD,
            "score": self.score,
            "recommendation": self.recommendation,
            "recommendation_reasons": list(self.recommendation_reasons),
            "technical": {
                "score": self.technical_score,
                "decision": self.decision,
                "best_group": self.best_group,
                "group_scores": dict(self.group_scores),
                "reasons": list(self.reasons),
                "missing_requirements": list(self.missing_requirements),
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

    def _score_group(self, group_id: str, sections: Sections, title: str) -> "GroupScore":
        """
        Bewertet eine Gruppe. Treffer zählen nach Fundstelle:
        - im Haupttext (Titel, Aufgaben, Anforderungen): volle Punkte
        - nur unter Nice-to-have: stark abgeschwächt (``nice_to_have_factor``)
        """
        cfg = self._groups.get(group_id) or {}
        factor = float(self._rules.get("nice_to_have_factor", 0.25))
        result = GroupScore()
        labels = {
            "high": "Hohe Priorität",
            "medium": "Mittlere Priorität",
            "transferable": "Nur übertragbare Kompetenz (keine eigene Praxis)",
            "low_priority": "Niedrige Priorität",
        }
        for category, label in labels.items():
            terms = cfg.get(category) or []
            found = _matches(terms, sections.main)
            if found:
                points = self._capped(category, len(found))
                if points >= 0:
                    result.positive += points
                    result.full_hits += len(found)
                else:
                    result.negative += points
                result.reasons.append(
                    f"{label} [{group_id}]: {', '.join(found)} ({points:+d})")
            if category == "low_priority":
                result.low_title_hits = _matches(terms, title)
                continue
            in_title = _matches(found, title)
            if in_title and category in ("high", "medium"):
                result.title_hits.setdefault(category, in_title)
            nice_only = [t for t in _matches(terms, sections.nice) if t not in found]
            if nice_only:
                weight = int(self._weights.get(category, 0))
                points = max(1, round(weight * factor)) * len(nice_only) if weight > 0 else 0
                if points:
                    result.positive += points
                    result.reasons.append(
                        f"{label} [{group_id}], nur Nice-to-have: "
                        f"{', '.join(nice_only)} ({points:+d})")
        return result

    def _specialist_caps(self, title: str, must_text: str) -> List[Tuple[int, str, List[str]]]:
        """
        Prüft, ob reine Spezialistenrollen vorliegen.

        Eine Regel greift, wenn im Anforderungsabschnitt nicht belegte
        Spezialerfahrung verlangt wird UND
          - der Titel die Spezialistenrolle nennt (``min_required_with_title``
            Anforderungen, Standard 1), oder
          - der Titel zusätzlich ein belegtes Kernthema nennt
            (``exempt_title_terms``): dann erst ab ``min_required_with_exempt_title``
            Anforderungen — ein "Power BI Entwickler" ist nicht pauschal eine
            ungeeignete Entwicklerrolle, oder
          - ohne passenden Titel mindestens ``min_required_without_title``
            (Standard 2) solcher Anforderungen verlangt werden.

        Returns:
            Liste von (Deckel, Grund, fehlende Anforderungen).
        """
        caps: List[Tuple[int, str, List[str]]] = []
        for rule in self._common.get("specialist_roles") or []:
            required = _matches(rule.get("required_experience_terms") or [], must_text)
            if not required:
                continue
            in_title = _matches(rule.get("title_terms") or [], title)
            exempt = _matches(rule.get("exempt_title_terms") or [], title)
            if in_title and exempt:
                needed = int(rule.get("min_required_with_exempt_title", 2))
            elif in_title:
                needed = int(rule.get("min_required_with_title", 1))
            else:
                needed = int(rule.get("min_required_without_title", 2))
            if len(required) < needed:
                continue
            cap = int(rule.get("score_cap", 40))
            label = rule.get("label") or "Spezialistenrolle"
            where = f"Titel: {', '.join(in_title)}; " if in_title else ""
            caps.append((cap, (
                f"{label}: zwingend geforderte Spezialerfahrung nicht belegt "
                f"({where}Anforderungen: {', '.join(required[:5])}) "
                f"— fachliche Eignung auf {cap} begrenzt"
            ), required))
        return caps

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
        percent = _ONSITE_PERCENT_RE.search(text)
        if percent:
            share = int(percent.group(1))
            if share >= 100:
                return ConditionCheck(STATUS_NOT_OK, "vollständig vor Ort", "Angabe: 100 % vor Ort")
            if share > 0:
                return ConditionCheck(STATUS_PARTIAL, f"hybrid ({share} % vor Ort)")
        if _FULL_REMOTE_RE.search(text):
            return ConditionCheck(STATUS_OK, "remote")
        if _matches(cfg.get("hybrid") or [], text):
            return ConditionCheck(STATUS_PARTIAL, "hybrid")
        if _matches(cfg.get("remote") or [], text):
            # "Remote" allein heißt nicht vollständig remote: Nennt die
            # Ausschreibung daneben Präsenz oder einen Einsatzort "X und remote",
            # ist es ein hybrides Modell.
            cleaned = _ONSITE_NEGATED_RE.sub(" ", _PLACE_LABEL_RE.sub(" ", text))
            hint = _ONSITE_HINT_RE.search(cleaned)
            if hint:
                return ConditionCheck(
                    STATUS_PARTIAL, "hybrid",
                    f"Remote mit Vor-Ort-Anteil (Angabe: {hint.group(1)})")
            for line in _PLACE_LINE_RE.findall(cleaned):
                if _PLACE_MIX_RE.search(line):
                    return ConditionCheck(
                        STATUS_PARTIAL, "hybrid", f"Einsatzort und remote: {line.strip()[:60]}")
            return ConditionCheck(STATUS_OK, "remote")
        return ConditionCheck(STATUS_UNKNOWN, None, "Kein Remote-Anteil angegeben")

    def _check_contract_type(self, title: str, text: str) -> ConditionCheck:
        """Unterscheidet Freelance, Festanstellung, Personalverleih und Anbieterwerbung."""
        cfg = self._conditions.get("contract_type") or {}
        advert = _matches(cfg.get("advertisement_title") or [], title)
        if advert:
            return ConditionCheck(
                STATUS_NOT_OK, "Anbieterwerbung",
                f"Kein Projektauftrag: Anbieter sucht selbst Kunden/Partner ({advert[0]})")
        cleaned = _NOT_EMPLOYMENT_RE.sub(" ", text)
        employment = _matches(cfg.get("employment") or [], cleaned)
        temp = _matches(cfg.get("temporary_employment") or [], cleaned)
        freelance = _matches(cfg.get("freelance") or [], cleaned)
        if employment and not freelance:
            return ConditionCheck(STATUS_NOT_OK, "Festanstellung", f"Angabe: {employment[0]}")
        if employment:
            return ConditionCheck(
                STATUS_PARTIAL, "Freelance oder Festanstellung",
                f"Beides genannt ({freelance[0]}, {employment[0]})")
        if temp:
            return ConditionCheck(
                STATUS_PARTIAL, "Arbeitnehmerüberlassung/Personalverleih",
                f"Angabe: {temp[0]} — kein klassischer Freelance-Vertrag")
        if freelance:
            return ConditionCheck(STATUS_OK, "Freelance")
        return ConditionCheck(STATUS_UNKNOWN, None, "Keine Vertragsart angegeben")

    def _check_workload(self, text: str, title: str = "") -> ConditionCheck:
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
            m = _PERCENT_RE.search(text) or (_TITLE_PERCENT_RE.search(title) if title else None)
            if m and 10 <= int(m.group(1)) <= 100:
                first = int(m.group(1))
                second = int(m.group(2)) if m.group(2) and int(m.group(2)) <= 100 else first
                first, second = min(first, second), max(first, second)
                hours = (first * full_time / 100, second * full_time / 100)
                label = f"{first} %" if first == second else f"{first}–{second} %"
        if hours is None:
            m = _DAYS_RE.search(text)
            if m:
                first = float(m.group(1)) * 8
                hours = (first, float(m.group(2)) * 8 if m.group(2) else first)
                label = m.group(0)
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
        body_sections = _split_sections(body)
        sections = Sections(
            must=body_sections.must,
            nice=body_sections.nice,
            main="\n".join(filter(None, [
                title, body_sections.main, _schlagworte_text(project_data)])),
        )
        must_text = sections.must
        missing: List[str] = []

        # Es zählt die Gruppe mit den meisten Pluspunkten, inklusive ihrer Abzüge.
        group_scores: Dict[str, int] = {}
        parts: Dict[str, GroupScore] = {}
        for group_id in self._groups:
            parts[group_id] = self._score_group(group_id, sections, title)
            group_scores[group_id] = parts[group_id].positive + parts[group_id].negative
        best_group: Optional[str] = None
        if parts:
            candidate = max(parts, key=lambda g: (parts[g].positive, -parts[g].negative))
            if parts[candidate].reasons:
                best_group = candidate
        best = parts[best_group] if best_group else GroupScore()
        technical = group_scores[best_group] if best_group else 0
        reasons.extend(best.reasons)

        # Zentrale Kompetenz im Titel: einmaliger Zuschlag
        title_cfg = self._rules.get("title_bonus") or {}
        for category in ("high", "medium"):
            hits = best.title_hits.get(category)
            points = int(title_cfg.get(category, 0))
            if hits and points:
                technical += points
                reasons.append(
                    f"Kernthema im Titel [{best_group}]: {', '.join(hits[:3])} ({points:+d})")
                break

        # Eine Rolle niedriger Priorität im Titel zählt immer — auch wenn eine
        # andere Gruppe wegen eines Einzelbegriffs die meisten Pluspunkte hat.
        low_title = sorted({
            term for group_id, part in parts.items()
            if group_id != best_group for term in part.low_title_hits
        })
        if low_title:
            points = self._capped("low_priority", len(low_title))
            technical += points
            reasons.append(
                f"Rolle niedriger Priorität im Titel: {', '.join(low_title)} ({points:+d})")

        # Senior-/Beratungsbonus nur bei fachlicher Übereinstimmung
        role_cfg = self._common.get("role") or {}
        role_hits = _matches(role_cfg.get("terms") or [], sections.main)
        if role_hits and best.full_hits > 0:
            points = int(role_cfg.get("points", 0))
            technical += points
            reasons.append(f"Senior-/Beratungsrolle: {', '.join(role_hits[:4])} ({points:+d})")
        elif role_hits:
            reasons.append(
                f"Senior-/Beratungsrolle ({', '.join(role_hits[:4])}) ohne fachliche "
                f"Übereinstimmung — kein Bonus")

        # Kompetenzbonus für nachgewiesene Praxis — je Regel höchstens einmal.
        # Steht vor Deckel und Muss-Prüfung und kann beide nicht umgehen.
        for rule in self._common.get("competency_bonuses") or []:
            if not _matches(rule.get("requires") or [], sections.main):
                continue
            skills = _matches(rule.get("skills") or [], sections.main)
            if not skills:
                continue
            points = min(
                int(rule.get("max_points", 10)),
                len(skills) * int(rule.get("points_per_skill", 5)),
            )
            technical += points
            reasons.append(
                f"Kompetenzbonus {rule.get('label', '')}: {', '.join(skills)} ({points:+d})"
            )

        # Nicht belegte Muss-Anforderungen
        reject_reason: Optional[str] = None
        for rule in self._common.get("unsupported_must_haves") or []:
            terms = rule.get("terms") or []
            label = rule.get("label") or ", ".join(terms)
            in_must = _matches(terms, must_text)
            if not in_must:
                continue  # bloße Erwähnung außerhalb der Anforderungen: keine Folge
            missing.extend(in_must)
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

        # Einzelne beiläufige Erwähnung: höchstens ein fachlicher Treffer und
        # kein Kernthema im Titel ergibt keine mittlere Eignung.
        single_cap = self._rules.get("single_mention_cap")
        if (single_cap is not None and best.full_hits <= 1 and not best.title_hits
                and technical > int(single_cap)):
            technical = int(single_cap)
            reasons.append(
                f"Nur ein fachlicher Treffer und kein Kernthema im Titel "
                f"— fachliche Eignung auf {int(single_cap)} begrenzt")

        # Reine Spezialistenrollen: Deckel statt Ablehnung (der niedrigste gilt)
        for cap, cap_reason, required in sorted(
                self._specialist_caps(title, must_text), key=lambda c: c[0]):
            missing.extend(required)
            if technical > cap:
                technical = cap
                reasons.append(cap_reason)
            else:
                reasons.append(cap_reason.replace(
                    f" — fachliche Eignung auf {cap} begrenzt", ""))

        # Nicht bestätigte Anforderungen: keine Anhebung auf "hoch", solange
        # eine verlangte Kenntnis nicht bestätigt ist — stattdessen prüfen.
        high_threshold = int(self._thresholds.get("high", 50))
        for rule in self._common.get("unconfirmed_requirements") or []:
            if not _matches(rule.get("requires") or [], sections.main) and rule.get("requires"):
                continue
            found = _matches(rule.get("terms") or [], sections.main)
            if not found:
                continue
            missing.extend(found)
            label = rule.get("label") or "Nicht bestätigte Anforderung"
            if technical >= high_threshold:
                technical = high_threshold - 1
                reasons.append(
                    f"{label}: {', '.join(found)} — bitte prüfen; fachliche Eignung "
                    f"bleibt unter {high_threshold}")
            else:
                reasons.append(f"{label}: {', '.join(found)} — bitte prüfen")

        missing = list(dict.fromkeys(missing))

        if reject_reason:
            technical = min(technical, int(self._rules.get("reject_score_cap", 10)))
            reasons.append(reject_reason)
            decision = DECISION_REJECT
        elif technical >= high_threshold:
            decision = DECISION_HIGH
        elif technical >= int(self._thresholds.get("medium", 25)):
            decision = DECISION_MEDIUM
        else:
            decision = DECISION_LOW

        # ── 2. Rahmenbedingungen ──────────────────────────────────────────────
        end_shown = _parse_end(conditions_text, self._today or date.today())
        conditions = {
            "contract_type": self._check_contract_type(title, conditions_text),
            "work_mode": self._check_work_mode(conditions_text),
            "workload": self._check_workload(conditions_text, title),
            "start": self._check_start(conditions_text),
            # Reine Anzeige, getrennt vom Starttermin; wird nicht bewertet.
            "duration": ConditionCheck(
                STATUS_NOT_CHECKED if end_shown else STATUS_UNKNOWN, end_shown,
                None if end_shown else "Keine Laufzeit angegeben"),
        }

        # ── 3. Gesamtwert und Empfehlung ──────────────────────────────────────
        adjustments = self._conditions.get("score_adjustments") or {}
        score = technical
        if conditions["work_mode"].status == STATUS_NOT_OK:
            score += int(adjustments.get("onsite_only", 0))
        if conditions["workload"].status == STATUS_NOT_OK:
            score += int(adjustments.get("workload_outside", 0))
        if conditions["contract_type"].status == STATUS_NOT_OK:
            score += int(adjustments.get("not_freelance", 0))
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
            missing_requirements=missing,
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
        names = {"contract_type": "Vertragsart", "work_mode": "Arbeitsort",
                 "workload": "Auslastung", "start": "Starttermin"}
        violated = [
            k for k, c in conditions.items()
            if k in names and c.status in (STATUS_NOT_OK, STATUS_CONFLICT)
        ]
        unknown = [
            names[k] for k, c in conditions.items()
            if k in names and c.status == STATUS_UNKNOWN
        ]
        why: List[str] = []

        if decision == DECISION_REJECT:
            return RECOMMEND_SKIP, [reject_reason or "Fachlich abgelehnt"]
        if decision == DECISION_LOW:
            return RECOMMEND_SKIP, ["Fachliche Eignung niedrig"]

        contract = conditions.get("contract_type")
        if contract is not None and contract.status == STATUS_NOT_OK:
            # Festanstellung oder Anbieterwerbung: kein Freelance-Projekt
            return RECOMMEND_SKIP, [
                f"Vertragsart: {contract.value} ({contract.note or contract.status})"]

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
