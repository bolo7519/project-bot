"""
Tests für Phase-0-Sicherheits-Fixes in server_enhanced.py

Prüft, dass:
- debug=True nicht mehr hartcodiert ist
- host nicht mehr hartcodiert 0.0.0.0 ist
- CORS nicht mehr Wildcard '*' erlaubt
"""

import ast
import re
import pytest
from pathlib import Path


SERVER_FILE = Path(__file__).parent.parent / "server_enhanced.py"


def _read_server() -> str:
    return SERVER_FILE.read_text(encoding="utf-8")


class TestServerSecurityFixes:
    def test_debug_not_hardcoded_true(self):
        """debug=True darf nicht mehr als Literal im app.run()-Aufruf stehen."""
        src = _read_server()
        # Suche nach app.run(...) Block
        run_block_match = re.search(r'app\.run\(.*?\)', src, re.DOTALL)
        assert run_block_match, "app.run() nicht gefunden"
        run_block = run_block_match.group(0)
        assert "debug=True" not in run_block, (
            "debug=True ist noch im app.run()-Aufruf vorhanden – Phase-0-Fix fehlt"
        )

    def test_host_not_hardcoded_0000(self):
        """host='0.0.0.0' darf nicht mehr hartcodiert im app.run()-Aufruf stehen."""
        src = _read_server()
        run_block_match = re.search(r'app\.run\(.*?\)', src, re.DOTALL)
        assert run_block_match, "app.run() nicht gefunden"
        run_block = run_block_match.group(0)
        assert "host='0.0.0.0'" not in run_block, (
            "host='0.0.0.0' ist noch hartcodiert – Phase-0-Fix fehlt"
        )
        assert 'host="0.0.0.0"' not in run_block, (
            "host=\"0.0.0.0\" ist noch hartcodiert – Phase-0-Fix fehlt"
        )

    def test_cors_not_wildcard(self):
        """CORS origins darf nicht mehr ['*'] oder '*' sein."""
        src = _read_server()
        # Suche nach CORS(...) Aufruf
        cors_match = re.search(r'CORS\s*\(.*?\)', src, re.DOTALL)
        assert cors_match, "CORS()-Aufruf nicht gefunden"
        cors_call = cors_match.group(0)
        assert '"*"' not in cors_call and "'*'" not in cors_call, (
            "CORS erlaubt noch Wildcard-Origin '*' – Phase-0-Fix fehlt"
        )

    def test_flask_host_env_var_present(self):
        """FLASK_HOST-Umgebungsvariable soll als Konfigurationspunkt vorhanden sein."""
        src = _read_server()
        assert "FLASK_HOST" in src, (
            "FLASK_HOST Umgebungsvariable fehlt – Server-Host muss konfigurierbar sein"
        )

    def test_flask_debug_env_var_present(self):
        """FLASK_DEBUG-Umgebungsvariable soll als Konfigurationspunkt vorhanden sein."""
        src = _read_server()
        assert "FLASK_DEBUG" in src, (
            "FLASK_DEBUG Umgebungsvariable fehlt – Debug-Modus muss über Env konfigurierbar sein"
        )
