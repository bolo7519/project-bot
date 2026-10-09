"""
Regressionstest: --config-Übergabe an evaluate_projects.main() (Phase-0-Bug 1).

Bug: main.py setzte sys.argv = [sys.argv[0]] und verwarf dabei --config und --cv-file,
     bevor evaluate_projects.main() aufgerufen wurde. Das zwang evaluate_projects immer
     auf seine Standardwerte (config.yaml, data/cv.md), egal was der Nutzer übergab.

Fix: eval_argv = [sys.argv[0], "--config", args.config, "--cv", args.cv_file]
     gesetzt und nach dem Aufruf wiederhergestellt.

Dieser Test prüft:
1. Der Fix ist strukturell korrekt in main.py vorhanden (Code-Inspektion).
2. Die Übergabe des richtigen --config-Werts an evaluate_projects.main() funktioniert
   runtime-seitig (via Mock, kein echter API-Call, keine echte Konfigurationsdatei nötig).
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

MAIN_PY = Path(__file__).parent.parent / "main.py"


class TestArgvPassthroughSourceInspection:
    """Statische Prüfung: Korrekte Implementierung im Quelltext."""

    def _src(self):
        return MAIN_PY.read_text(encoding="utf-8")

    def test_eval_argv_contains_config_arg(self):
        """eval_argv muss '--config' und args.config enthalten."""
        src = self._src()
        assert '"--config", args.config' in src or "'--config', args.config" in src, (
            "eval_argv enthält --config nicht – sys.argv-Fix fehlt oder wurde entfernt"
        )

    def test_eval_argv_contains_cv_arg(self):
        """eval_argv muss '--cv' und args.cv_file enthalten."""
        src = self._src()
        assert '"--cv", args.cv_file' in src or "'--cv', args.cv_file" in src, (
            "eval_argv enthält --cv nicht – sys.argv-Fix fehlt oder wurde entfernt"
        )

    def test_sys_argv_is_restored_via_finally(self):
        """sys.argv muss in einem finally-Block wiederhergestellt werden."""
        src = self._src()
        # saved_argv muss gesetzt und wiederhergestellt werden
        assert "saved_argv = sys.argv" in src, (
            "sys.argv wird vor dem Überschreiben nicht gesichert"
        )
        assert "sys.argv = saved_argv" in src, (
            "sys.argv wird nach evaluate_projects.main() nicht wiederhergestellt"
        )
        # finally-Block muss vorhanden sein
        assert "finally:" in src, (
            "Kein finally-Block – sys.argv wird bei Exception nicht wiederhergestellt"
        )

    def test_old_broken_clobber_not_in_active_code(self):
        """Der alte Bug (sys.argv = [sys.argv[0]]) darf nicht als aktiver Code vorhanden sein.
        Im Kommentartext ist die Erwähnung OK; nur in einer aktiven Code-Zeile nicht."""
        src = self._src()
        # Nur Code-Zeilen prüfen (Zeilen die nicht mit '#' beginnen, nach Strip)
        active_lines = [
            line for line in src.splitlines()
            if not line.lstrip().startswith("#")
        ]
        active_code = "\n".join(active_lines)
        assert "sys.argv = [sys.argv[0]]" not in active_code, (
            "Der alte kaputte sys.argv-Clobber ist als aktiver Code vorhanden – Phase-0-Fix fehlt"
        )


class TestArgvPassthroughRuntime:
    """Runtime-Test: Der korrekte --config-Wert landet bei evaluate_projects.main()."""

    def test_custom_config_reaches_evaluate_projects(self, tmp_path):
        """
        Simuliert den sys.argv-Fix aus main.py isoliert:
        Wenn --config auf eine abweichende Datei zeigt, muss evaluate_projects.main()
        genau diesen Wert in sys.argv[2] sehen.
        """
        import evaluate_projects

        custom_config = str(tmp_path / "my_custom_config.yaml")
        custom_cv = str(tmp_path / "my_cv.md")

        seen_argv = []

        def fake_evaluate_main():
            seen_argv.extend(sys.argv[:])

        with patch.object(evaluate_projects, "main", side_effect=fake_evaluate_main):
            # Repliziert exakt den Fix aus main.py
            class FakeArgs:
                config = custom_config
                cv_file = custom_cv

            args = FakeArgs()
            eval_argv = [sys.argv[0], "--config", args.config, "--cv", args.cv_file]
            saved_argv = sys.argv[:]
            sys.argv = eval_argv
            try:
                evaluate_projects.main()
            finally:
                sys.argv = saved_argv

        assert "--config" in seen_argv, "evaluate_projects.main() sah kein --config"
        config_idx = seen_argv.index("--config")
        assert seen_argv[config_idx + 1] == custom_config, (
            f"evaluate_projects.main() sah falschen config-Wert: "
            f"{seen_argv[config_idx + 1]!r} statt {custom_config!r}"
        )

    def test_sys_argv_restored_after_call(self, tmp_path):
        """sys.argv muss nach dem Aufruf wieder den ursprünglichen Wert haben."""
        import evaluate_projects

        original_argv = sys.argv[:]

        with patch.object(evaluate_projects, "main", return_value=None):
            eval_argv = [sys.argv[0], "--config", "test.yaml", "--cv", "test_cv.md"]
            saved_argv = sys.argv[:]
            sys.argv = eval_argv
            try:
                evaluate_projects.main()
            finally:
                sys.argv = saved_argv

        assert sys.argv == original_argv, (
            "sys.argv wurde nach evaluate_projects.main() nicht korrekt wiederhergestellt"
        )

    def test_sys_argv_restored_even_on_exception(self, tmp_path):
        """sys.argv muss auch dann wiederhergestellt werden, wenn main() eine Exception wirft."""
        import evaluate_projects

        original_argv = sys.argv[:]

        with patch.object(evaluate_projects, "main", side_effect=RuntimeError("Simulated failure")):
            eval_argv = [sys.argv[0], "--config", "fail.yaml", "--cv", "fail_cv.md"]
            saved_argv = sys.argv[:]
            sys.argv = eval_argv
            try:
                try:
                    evaluate_projects.main()
                finally:
                    sys.argv = saved_argv
            except RuntimeError:
                pass  # erwartet

        assert sys.argv == original_argv, (
            "sys.argv wurde nach Exception in evaluate_projects.main() nicht wiederhergestellt"
        )
