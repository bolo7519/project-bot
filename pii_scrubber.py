"""
pii_scrubber.py — PII-Minimierung vor LLM-API-Übertragung (Phase 4).

Entfernt oder ersetzt personenbezogene Daten aus Projekttexten, bevor sie
an eine externe LLM-API gesendet werden. Arbeitet ausschließlich mit Regex
und strukturellen Regeln — kein ML, keine externen Bibliotheken.

Scope:
  - E-Mail-Adressen              → [EMAIL]
  - Telefonnummern (DE/EU)       → [PHONE]
  - IBAN / Kontonummern          → [IBAN]
  - Direkte Ansprechpartner      → [NAME] (Heuristik: Herr/Frau/Hr./Fr. + Name)
  - Vollständige E-Mail-Signaturen
  - E-Mail-Verläufe (Re:/Fwd:-Blöcke, ---- Forwarded message ----)
  - Unternehmensinterne Kontaktblöcke

Sicherheitsregel:
  Wenn der Text nach dem Scrubbing weniger als MIN_USEFUL_CHARS verbleibende
  Zeichen hat (ohne Platzhalter), ist er für eine LLM-Bewertung ungeeignet.
  In diesem Fall gibt is_safe_to_send() False zurück — kein API-Aufruf.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Tuple

import logging

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Schwellenwert
# ──────────────────────────────────────────────────────────────────────────────

MIN_USEFUL_CHARS = 150  # Mindestlänge nutzbarer Text nach Scrubbing

# ──────────────────────────────────────────────────────────────────────────────
# Regex-Muster
# ──────────────────────────────────────────────────────────────────────────────

# E-Mail-Adressen
_RE_EMAIL = re.compile(
    r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b',
    re.IGNORECASE,
)

# Telefonnummern: DE/AT/CH/EU-Formate
_RE_PHONE = re.compile(
    r'(?:'
    r'\+?\d{1,3}[\s\-.]?\(?\d{2,5}\)?[\s\-.]?\d{2,5}[\s\-.]?\d{2,5}'  # International
    r'|0\d{2,5}[\s\/\-]?\d{3,8}'   # DE-Vorwahl
    r')',
    re.IGNORECASE,
)

# IBAN (DE, AT, CH, …)
_RE_IBAN = re.compile(
    r'\b[A-Z]{2}\d{2}[\s]?[0-9A-Z]{4}[\s]?[0-9]{4}[\s]?[0-9]{4}'
    r'(?:[\s]?[0-9]{0,4}){0,4}\b',
    re.IGNORECASE,
)

# Ansprechpartner — "Herr/Frau/Hr./Fr. Nachname" (1–2 Wörter nach Anrede)
_RE_SALUTATION_NAME = re.compile(
    r'\b(?:Herr|Frau|Hr\.|Fr\.|Mr\.|Mrs\.|Ms\.)\s+[A-ZÄÖÜ][a-zäöüß\-]+'
    r'(?:\s+[A-ZÄÖÜ][a-zäöüß\-]+)?',
    re.UNICODE,
)

# E-Mail-Signaturen — typische Einleitungen
_RE_SIGNATURE_START = re.compile(
    r'^[\-_*]{2,}\s*$'            # --- Trennlinie
    r'|^Mit\s+freundlichen?\s+Grü'
    r'|^Mit\s+freundlichen?\s+Gru'
    r'|^Freundliche\s+Grü'
    r'|^Viele\s+Grü'
    r'|^Beste\s+Grü'
    r'|^Best\s+regards'
    r'|^Kind\s+regards'
    r'|^Yours\s+sincerely'
    r'|^Thanks\s+and\s+regards',
    re.IGNORECASE | re.UNICODE | re.MULTILINE,
)

# E-Mail-Verlauf / Forwarding-Block
_RE_EMAIL_THREAD = re.compile(
    r'(?m)^(?:'
    r'[-]{4,}\s*(?:Forwarded|Weitergeleitet|Original)\s+[Mm]essage'
    r'|Von:\s+.+\nGesendet:'
    r'|From:\s+.+\nSent:'
    r'|On\s+\d{1,2}\s+\w+\s+\d{4}.+wrote:'
    r'|Am\s+\d{1,2}\.\s+\w+\s+\d{4}.+schrieb:'
    r').*',
    re.IGNORECASE | re.DOTALL,
)

# Kontaktblock-Zeilen (Telefon/Fax/Mobil-Label gefolgt von Nummer)
_RE_CONTACT_LABEL = re.compile(
    r'(?m)^(?:Tel(?:efon)?|Fax|Mobil|Mobile|Phone|Handy|Direct)[.:]?\s*[\+\d].*$',
    re.IGNORECASE,
)


# ──────────────────────────────────────────────────────────────────────────────
# Ergebnis-Typ
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ScrubResult:
    """Ergebnis eines Scrubbing-Vorgangs."""
    text: str                              # Bereinigter Text
    replacements: Dict[str, int] = field(default_factory=dict)
    # Anzahl Ersetzungen pro Typ: {"email": 2, "phone": 1, ...}
    signature_removed: bool = False
    thread_removed: bool = False
    truncated: bool = False                # Reserviert für Prompt-Builder

    def total_replacements(self) -> int:
        return sum(self.replacements.values())

    def is_safe_to_send(self) -> bool:
        """
        Prüft ob genug nutzbarer Inhalt nach dem Scrubbing verbleibt
        UND ob keine erkennbaren PII-Muster übrig geblieben sind.

        Vier Bedingungen müssen erfüllt sein:
        1. Mindestens MIN_USEFUL_CHARS Zeichen außerhalb der Platzhalter-Tags
        2. Keine residualen E-Mail-Adressen (user@domain.tld)
        3. Keine residualen Telefonnummern (DE/EU-Formate)
        4. Keine residualen IBAN-Nummern (XX00 ...)

        Designentscheidung — Grenzen dieser Prüfung:
        - Nur strukturelle Muster werden erkannt; semantische PII (z. B. Personen-
          namen ohne Anrede, Steuer-IDs, Handelsregisternummern) ist nicht erkennbar.
        - Diese Prüfung ist ein Sicherheitsnetz als letzte Verteidigungslinie,
          kein vollständiger Datenschutz-Garant. Die primäre Schutzmaßnahme
          ist das vorgelagerte Scrubbing durch PIIScrubber.scrub().
        - Regex-Anonymisierung bietet keine vollständige Datenschutzgarantie
          gemäß DSGVO; sie reduziert das Risiko der Übertragung erkennbarer
          personenbezogener Daten an externe APIs erheblich, kann aber nicht
          ausschließen, dass kontextuelle PII (z. B. eingebettete Namen in
          Fließtext ohne Anrede) übersehen wird.
        """
        cleaned = re.sub(r'\[(?:EMAIL|PHONE|IBAN|NAME|SCRUBBED)\]', '', self.text)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        # Bedingung 1: Mindestlänge nutzbarer Inhalt
        if len(cleaned) < MIN_USEFUL_CHARS:
            return False

        # Bedingung 2: Keine residualen E-Mail-Adressen
        if re.search(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b', cleaned):
            return False

        # Bedingung 3: Keine residualen Telefonnummern (DE/EU-Formate)
        # Dieselbe Regex wie _RE_PHONE im Scrubber — erkennt Nummern die
        # der Scrubber hätte entfernen sollen aber möglicherweise übersehen hat.
        if re.search(
            r'(?:'
            r'\+?\d{1,3}[\s\-.]?\(?\d{2,5}\)?[\s\-.]?\d{2,5}[\s\-.]?\d{2,5}'
            r'|0\d{2,5}[\s\/\-]?\d{3,8}'
            r')',
            cleaned,
        ):
            return False

        # Bedingung 4: Keine residualen IBAN-Nummern
        # Prüft auf das typische Muster: 2 Buchstaben + 2 Ziffern + alphanumerische Blöcke
        if re.search(
            r'\b[A-Z]{2}\d{2}[\s]?[0-9A-Z]{4}[\s]?[0-9]{4}[\s]?[0-9]{4}',
            cleaned,
            re.IGNORECASE,
        ):
            return False

        return True


# ──────────────────────────────────────────────────────────────────────────────
# Haupt-Klasse
# ──────────────────────────────────────────────────────────────────────────────

class PIIScrubber:
    """
    Entfernt personenbezogene Daten aus Projekttexten.

    Reihenfolge:
    1. E-Mail-Verläufe (gesamter Abschnitt)
    2. E-Mail-Signaturen (ab Signaturzeile)
    3. IBAN
    4. E-Mail-Adressen
    5. Kontaktblock-Zeilen
    6. Telefonnummern
    7. Ansprechpartner-Namen
    """

    def scrub(self, text: str) -> ScrubResult:
        """
        Bereinigt den Text von PII.

        Args:
            text: Rohtext (Markdown oder Plaintext)

        Returns:
            ScrubResult mit bereinigtem Text und Statistik
        """
        replacements: Dict[str, int] = {}
        signature_removed = False
        thread_removed = False

        # 1. E-Mail-Verlauf / Forwarding-Block entfernen
        thread_match = _RE_EMAIL_THREAD.search(text)
        if thread_match:
            text = text[:thread_match.start()].rstrip()
            thread_removed = True
            replacements["thread"] = 1
            logger.debug("E-Mail-Verlauf entfernt (ab Zeichen %d)", thread_match.start())

        # 2. E-Mail-Signatur entfernen (ab erster Signaturzeile)
        sig_match = _RE_SIGNATURE_START.search(text)
        if sig_match:
            text = text[:sig_match.start()].rstrip()
            signature_removed = True
            replacements["signature"] = 1
            logger.debug("Signatur entfernt (ab Zeichen %d)", sig_match.start())

        # 3. IBAN
        n = len(_RE_IBAN.findall(text))
        if n:
            text = _RE_IBAN.sub("[IBAN]", text)
            replacements["iban"] = n

        # 4. E-Mail-Adressen
        n = len(_RE_EMAIL.findall(text))
        if n:
            text = _RE_EMAIL.sub("[EMAIL]", text)
            replacements["email"] = n

        # 5. Kontaktblock-Zeilen (Tel/Fax/Mobil + Nummer)
        contact_lines = _RE_CONTACT_LABEL.findall(text)
        if contact_lines:
            text = _RE_CONTACT_LABEL.sub("[SCRUBBED]", text)
            replacements["contact_label"] = len(contact_lines)

        # 6. Telefonnummern (nach Kontaktblock-Zeilen, um Doppelzählung zu vermeiden)
        n = len(_RE_PHONE.findall(text))
        if n:
            text = _RE_PHONE.sub("[PHONE]", text)
            replacements["phone"] = n

        # 7. Ansprechpartner-Namen
        n = len(_RE_SALUTATION_NAME.findall(text))
        if n:
            text = _RE_SALUTATION_NAME.sub("[NAME]", text)
            replacements["name"] = n

        result = ScrubResult(
            text=text.strip(),
            replacements=replacements,
            signature_removed=signature_removed,
            thread_removed=thread_removed,
        )

        if result.total_replacements() or signature_removed or thread_removed:
            logger.info(
                "PII-Scrubbing: %d Ersetzungen (%s)%s%s",
                result.total_replacements(),
                ", ".join(f"{k}={v}" for k, v in replacements.items()),
                " + Signatur" if signature_removed else "",
                " + Thread" if thread_removed else "",
            )

        if not result.is_safe_to_send():
            logger.warning(
                "Text nach Scrubbing zu kurz für API-Übertragung (%d nützliche Zeichen)",
                len(re.sub(r'\[(?:EMAIL|PHONE|IBAN|NAME|SCRUBBED)\]', '', result.text).strip()),
            )

        return result
