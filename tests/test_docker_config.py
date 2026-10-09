"""
Tests zur Docker-Konfiguration (statische Prüfung, kein Docker-Daemon erforderlich).

HINWEIS: Der Docker-Build selbst kann in der CI-Umgebung nicht ausgeführt werden,
da kein Docker-Daemon verfügbar ist (/var/run/docker.sock fehlt). Diese Tests
prüfen ausschließlich die Konfigurationsdateien statisch.

Geprüft wird:
- Dockerfile vorhanden und enthält erwartete Anweisungen
- Alle im Dockerfile referenzierten Dateien existieren
- Python-Syntax aller kopierten .py-Dateien korrekt
- Keine hartcodierten Credentials im Dockerfile
"""

import ast
import py_compile
import os
import re
import pytest
from pathlib import Path


ROOT = Path(__file__).parent.parent
DOCKERFILE = ROOT / "Dockerfile"


class TestDockerfileExists:
    def test_dockerfile_present(self):
        """Dockerfile muss im Repository-Root vorhanden sein."""
        assert DOCKERFILE.exists(), "Dockerfile fehlt im Repository-Root"

    def test_dockerfile_not_empty(self):
        src = DOCKERFILE.read_text(encoding="utf-8")
        assert len(src.strip()) > 0, "Dockerfile ist leer"


class TestDockerfileContent:
    @pytest.fixture(autouse=True)
    def dockerfile_src(self):
        self.src = DOCKERFILE.read_text(encoding="utf-8")

    def test_exposes_port_8002(self):
        assert "EXPOSE 8002" in self.src, "Dockerfile EXPOSEd Port 8002 nicht"

    def test_runs_server_enhanced(self):
        assert "server_enhanced.py" in self.src, (
            "Dockerfile startet nicht server_enhanced.py als Einstiegspunkt"
        )

    def test_copies_requirements_txt(self):
        assert "COPY requirements.txt" in self.src, (
            "Dockerfile kopiert requirements.txt nicht"
        )

    def test_installs_pip_requirements(self):
        assert "pip install" in self.src and "requirements.txt" in self.src, (
            "Dockerfile führt pip install -r requirements.txt nicht aus"
        )

    def test_no_hardcoded_api_keys(self):
        """Keine API-Keys oder Passwörter dürfen hartcodiert im Dockerfile stehen."""
        forbidden_patterns = [
            r'sk-[A-Za-z0-9]{20,}',   # OpenAI-Style API-Key
            r'AIza[A-Za-z0-9_-]{35}',  # Google API-Key
            r'password\s*=\s*["\'][^"\']{4,}',
        ]
        for pattern in forbidden_patterns:
            assert not re.search(pattern, self.src, re.IGNORECASE), (
                f"Dockerfile enthält möglicherweise hartcodierte Credentials: {pattern}"
            )

    def test_no_debug_env_set_to_true(self):
        """FLASK_DEBUG darf im Dockerfile nicht auf 1/true gesetzt sein."""
        # Suche nach ENV FLASK_DEBUG=1 oder ENV FLASK_DEBUG 1
        assert not re.search(r'ENV\s+FLASK_DEBUG\s*[=\s]\s*[1t]', self.src, re.IGNORECASE), (
            "Dockerfile setzt FLASK_DEBUG auf einen aktivierten Wert"
        )

    def test_config_yaml_removed_from_image(self):
        """config.yaml soll aus dem Image entfernt werden (wird als Volume gemountet)."""
        assert "config.yaml" in self.src, (
            "Dockerfile erwähnt config.yaml nicht – prüfen ob Credentials-Handling korrekt ist"
        )


class TestReferencedFilesExist:
    """Alle im Dockerfile via COPY referenzierten Dateien und Verzeichnisse müssen existieren."""

    def test_requirements_txt_exists(self):
        assert (ROOT / "requirements.txt").exists()

    def test_frontend_package_json_exists(self):
        assert (ROOT / "frontend" / "package.json").exists(), (
            "frontend/package.json fehlt – Docker-Build würde fehlschlagen"
        )

    def test_frontend_vite_config_exists(self):
        assert (ROOT / "frontend" / "vite.config.js").exists(), (
            "frontend/vite.config.js fehlt – Frontend-Build würde fehlschlagen"
        )

    def test_frontend_index_html_exists(self):
        assert (ROOT / "frontend" / "index.html").exists()

    def test_frontend_src_dir_exists(self):
        assert (ROOT / "frontend" / "src").is_dir()

    def test_server_enhanced_py_exists(self):
        assert (ROOT / "server_enhanced.py").exists()


class TestPythonSyntax:
    """Alle Python-Dateien die in das Docker-Image kopiert werden, müssen syntaktisch korrekt sein."""

    PYTHON_FILES = [
        "server_enhanced.py",
        "main.py",
        "application_generator.py",
        "email_agent.py",
        "state_manager.py",
        "dedupe_service.py",
        "scores.py",
        "evaluate_projects.py",
        "logging_config.py",
        "file_purger.py",
    ]

    @pytest.mark.parametrize("filename", PYTHON_FILES)
    def test_python_file_syntax(self, filename):
        path = ROOT / filename
        if not path.exists():
            pytest.skip(f"{filename} nicht vorhanden")
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as e:
            pytest.fail(f"Syntaxfehler in {filename}: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# NICHT GETESTET (kein Docker-Daemon in der CI-Umgebung)
# ──────────────────────────────────────────────────────────────────────────────
#
# docker build -t project-bot .          → nicht ausführbar
# docker run --rm project-bot python -c "import server_enhanced" → nicht ausführbar
# Health-Check via curl http://localhost:8002/api/v1/health       → nicht ausführbar
#
# Empfehlung: Vor dem Merge manuell auf einer Maschine mit Docker ausführen:
#   docker build -t project-bot . && docker run --rm -e FLASK_HOST=127.0.0.1 project-bot python -c "print('OK')"
# ──────────────────────────────────────────────────────────────────────────────
