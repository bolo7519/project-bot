"""
Smoke-Tests: Alle Kernmodule müssen importierbar sein.

Diese Tests prüfen ausschließlich, ob die Module geladen werden können —
keine echten API-Aufrufe, keine Netzwerkverbindungen, keine Datenbankzugriffe.

Hintergrund (Phase 0): application_generator.py nutzte veraltete LangChain-APIs
(langchain.prompts.PromptTemplate, langchain.chains.LLMChain,
langchain.chat_models.ChatOpenAI), die in LangChain 1.x entfernt wurden.
Fix: Migration auf langchain_core.prompts und LCEL (prompt | llm).
"""

import importlib
import warnings
import pytest


# Module die ohne Konfiguration oder Credentials importierbar sein müssen
CORE_MODULES = [
    "state_manager",
    "dedupe_service",
    "scores",
    "application_generator",
    "email_agent",
    "evaluate_projects",
    "logging_config",
    "file_purger",
]


@pytest.mark.parametrize("module_name", CORE_MODULES)
def test_module_imports_without_error(module_name):
    """Jedes Kernmodul muss ohne ImportError ladbar sein."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        try:
            importlib.import_module(module_name)
        except ImportError as e:
            pytest.fail(f"Modul '{module_name}' nicht importierbar: {e}")


def test_application_generator_no_legacy_langchain():
    """application_generator.py darf keine veralteten LangChain-Imports enthalten."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "application_generator.py").read_text(encoding="utf-8")
    # Diese Pfade wurden in LangChain 1.x entfernt
    assert "from langchain.chains import LLMChain" not in src, (
        "langchain.chains.LLMChain ist in LangChain 1.x entfernt – Phase-0-Fix fehlt"
    )
    assert "from langchain.prompts import PromptTemplate" not in src, (
        "langchain.prompts.PromptTemplate ist in LangChain 1.x entfernt – Phase-0-Fix fehlt"
    )
    assert "from langchain.chat_models import ChatOpenAI" not in src, (
        "langchain.chat_models.ChatOpenAI ist in LangChain 1.x entfernt – Phase-0-Fix fehlt"
    )


def test_application_generator_uses_lcel():
    """application_generator.py soll LCEL (prompt | llm) statt LLMChain nutzen."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "application_generator.py").read_text(encoding="utf-8")
    assert "from langchain_core.prompts import PromptTemplate" in src, (
        "application_generator.py importiert PromptTemplate nicht aus langchain_core"
    )
    # LCEL-Komposition
    assert "self.prompt | self.chat" in src, (
        "application_generator.py nutzt nicht das LCEL-Muster (prompt | llm)"
    )
