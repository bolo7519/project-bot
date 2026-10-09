#!/usr/bin/env python3
"""
Application Generator Module for Project Bot

This module integrates the legacy bewerbung_generator_app functionality
into the main project-bot workflow with automated application generation.
"""

import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

import yaml
from anthropic import Anthropic, APIStatusError as AnthropicAPIStatusError
from openai import OpenAI, RateLimitError, AuthenticationError
import google.generativeai as genai

from state_manager import ProjectStateManager

# Import LangChain components
# Phase 0 fix: langchain.prompts, langchain.chains, langchain.chat_models were
# removed in LangChain 1.x. Use langchain_core and langchain_openai instead.
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

try:
    from langchain_anthropic import ChatAnthropic
except ImportError:
    ChatAnthropic = None

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    ChatGoogleGenerativeAI = None

# Import tiktoken for token counting
try:
    import tiktoken
except ImportError:
    tiktoken = None


class ApplicationGenerator:
    """
    Integrated Application Generator for Project Bot

    This class handles the generation of professional German job applications
    using the proven template from the legacy bewerbung_generator_app.
    """

    # Model configurations (migrated from legacy app)
    SUPPORTED_MODELS = {
        'openai': {
            'gpt-4.1': {'max_tokens': 16384, 'context_window': 1000000, 'cost_per_1k_input': 0.005, 'cost_per_1k_output': 0.015},
            'gpt-4.1-mini': {'max_tokens': 16384, 'context_window': 1000000, 'cost_per_1k_input': 0.00015, 'cost_per_1k_output': 0.0006},
            'gpt-4.1-nano': {'max_tokens': 16384, 'context_window': 1000000, 'cost_per_1k_input': 0.0001, 'cost_per_1k_output': 0.0004},
            'gpt-4o': {'max_tokens': 16384, 'context_window': 128000, 'cost_per_1k_input': 0.01, 'cost_per_1k_output': 0.03},
            'gpt-4o-mini': {'max_tokens': 16384, 'context_window': 128000, 'cost_per_1k_input': 0.00015, 'cost_per_1k_output': 0.0006},
            'gpt-4-turbo': {'max_tokens': 4096, 'context_window': 128000, 'cost_per_1k_input': 0.01, 'cost_per_1k_output': 0.03},
            'gpt-4': {'max_tokens': 4096, 'context_window': 8192, 'cost_per_1k_input': 0.03, 'cost_per_1k_output': 0.06},
        },
        'anthropic': {
            'claude-sonnet-4-20250514': {'max_tokens': 8192, 'context_window': 200000, 'cost_per_1k_input': 0.003, 'cost_per_1k_output': 0.015},
            'claude-opus-4-0': {'max_tokens': 32000, 'context_window': 200000, 'cost_per_1k_input': 0.015, 'cost_per_1k_output': 0.075},
            'claude-4-opus': {'max_tokens': 32000, 'context_window': 200000, 'cost_per_1k_input': 0.015, 'cost_per_1k_output': 0.075},
            'claude-4-sonnet': {'max_tokens': 64000, 'context_window': 200000, 'cost_per_1k_input': 0.003, 'cost_per_1k_output': 0.015},
            'claude-3.7-sonnet': {'max_tokens': 8192, 'context_window': 200000, 'cost_per_1k_input': 0.003, 'cost_per_1k_output': 0.015},
            'claude-3-5-sonnet-20241022': {'max_tokens': 8192, 'context_window': 200000, 'cost_per_1k_input': 0.003, 'cost_per_1k_output': 0.015},
            'claude-3-5-haiku-20241022': {'max_tokens': 8192, 'context_window': 200000, 'cost_per_1k_input': 0.00025, 'cost_per_1k_output': 0.00125},
            'claude-3-opus-20240229': {'max_tokens': 4096, 'context_window': 200000, 'cost_per_1k_input': 0.015, 'cost_per_1k_output': 0.075},
            'claude-3-sonnet-20240229': {'max_tokens': 4096, 'context_window': 200000, 'cost_per_1k_input': 0.003, 'cost_per_1k_output': 0.015},
        },
        'google': {
            'gemini-1.5-pro': {'max_tokens': 8192, 'context_window': 2000000, 'cost_per_1k_input': 0.00125, 'cost_per_1k_output': 0.005},
            'gemini-1.5-flash': {'max_tokens': 8192, 'context_window': 1000000, 'cost_per_1k_input': 0.000075, 'cost_per_1k_output': 0.0003},
            'gemini-pro': {'max_tokens': 4096, 'context_window': 32768, 'cost_per_1k_input': 0.0005, 'cost_per_1k_output': 0.0015},
        }
    }

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the Application Generator.

        Args:
            config: Application generator configuration from config.yaml
        """
        self.config = config
        self.app_config = config.get('application_generator', {})

        # Validate configuration
        self._validate_config()

        # Set up LLM configuration
        self.model_provider = self.app_config['llm']['provider'].lower()
        self.model_name = self.app_config['llm']['model']
        self.api_key = self._resolve_api_key(self.app_config['llm']['api_key'])

        # Validate model configuration
        self._validate_model_config()

        # Initialize the German prompt template (exactly as-is from legacy app)
        self.template = self._get_german_template()

        # Create prompt template
        self.prompt = PromptTemplate(
            input_variables=["skills", "project_requirements"],
            template=self.template,
        )

        # Initialize LLM
        self.chat = self._initialize_llm()

        # Create LLM chain using LCEL (Phase 0: LLMChain removed in LangChain 1.x)
        self.chain = self.prompt | self.chat

    def _validate_config(self) -> None:
        """
        Validate the application generator configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        if not self.app_config:
            raise ValueError("application_generator section not found in config")

        required_keys = ['enabled', 'llm', 'template']
        for key in required_keys:
            if key not in self.app_config:
                raise ValueError(f"Required configuration key missing: {key}")

        # Validate LLM configuration
        llm_config = self.app_config['llm']
        required_llm_keys = ['provider', 'model', 'api_key']
        for key in required_llm_keys:
            if key not in llm_config:
                raise ValueError(f"Required LLM configuration key missing: {key}")

        # Validate provider
        provider = llm_config['provider'].lower()
        if provider not in self.SUPPORTED_MODELS:
            raise ValueError(f"Unsupported LLM provider: {provider}")

        # Validate model
        model = llm_config['model']
        if model not in self.SUPPORTED_MODELS[provider]:
            raise ValueError(f"Unsupported model {model} for provider {provider}")

    def _validate_model_config(self) -> None:
        """
        Validate model configuration exists.

        Raises:
            ValueError: If model configuration is invalid
        """
        if self.model_provider not in self.SUPPORTED_MODELS:
            raise ValueError(f"Unsupported provider: {self.model_provider}")

        if self.model_name not in self.SUPPORTED_MODELS[self.model_provider]:
            raise ValueError(f"Unsupported model {self.model_name} for provider {self.model_provider}")

        self.model_config = self.SUPPORTED_MODELS[self.model_provider][self.model_name]

    def _resolve_api_key(self, api_key_template: str) -> str:
        """
        Resolve API key from template (support environment variables).

        Args:
            api_key_template: API key template (may contain ${VAR_NAME})

        Returns:
            Resolved API key

        Raises:
            ValueError: If API key cannot be resolved
        """
        # Check for environment variable pattern ${VAR_NAME}
        env_var_pattern = r'\$\{([^}]+)\}'
        match = re.search(env_var_pattern, api_key_template)

        if match:
            env_var = match.group(1)
            resolved_key = os.environ.get(env_var)
            if not resolved_key:
                raise ValueError(f"Environment variable {env_var} not found or empty")
            return resolved_key

        # Return as-is if no environment variable pattern
        return api_key_template

    def _get_german_template(self) -> str:
        """
        Get the German application template (exactly as-is from legacy app).

        Returns:
            German prompt template string
        """
        # This is the exact template from bewerbung_generator_app/bewerbung_generator.py lines 112-162
        # DO NOT MODIFY - this is tested and proven
        return '''
Ich bin ein Freelancer mit den folgenden Profil:

{skills}

Ich bewerbe mich für ein Projekt mit der folgenden Ausschreibung:

{project_requirements}

Schreibe eine Bewerbung, mit der folgenden Struktur:

1. <Einleitung>:
2. <Qualifikationen>:
3. <Schlusswort>:

Benutze die diese Struktur nur als Gliederung der Abschnitte. Du sollst die Abschnitte nicht als Überschriften schreiben.

Dein Schreibstile sollte professionell, formell, bescheiden und exakt sein. Vermeide Marketing-Sprache. Vermeide Übertreibungen und Superlative.

Hier ist die Anleitung für die einzelnen Abschnitte:

<Einleitung>:
- Schreibe keine Überschrift für diesen Abschnitt.
- Beginne mit Position und Referenznummer des Projekts.
Beispiel:
Position: Solution Architect (m/w/d)
Referenznummer: 123456
Dann fahre fort mit:
- Schreibe eine kurze allgemeine Einleitung zur Bewerbung auf das Projekt.
- Weise auf meine Zertifizierungen hin.

<Qualifikationen>:
- Zähle die Anforderungen aus project_requirements und merke dir die Anzahl.
- Anforderungen werden oft mit den Worten wie "Qualifikationen", "Muss-Anforderungen", "Must-Have", "Nice to Have" oder "Profil" eingeleitet.
- Erstelle für Jede Anforderung einen Unterabschnitt. Die Anzahl der Unterabschnitte muss der Anzahl der Anforderungen entsprechen.
- Anforderungen sind oft in zwei Kategorien wie Anforderungen und zusätzliche Qualifikationen unterteilt. Beispiel 1: "Qualifikationen: Must-Have" und "Zusätzliche Qualifikationen: Nice to Have". Beispiel 2: "Anforderungen:" und "Anforderungen nice-to-have:".
- Liste für jede Anforderung entsprechenden Fähigkeiten und Erfahrungen auf, die ich habe.
- Erfinde keine Erfahrungen oder Fähigkeiten, die du nicht in meinen <skills> finden kannst.
- Finde für die Qualifikation Nachweise in meinem CV und füge sie als Auflistung mit Jahreszahl und Projektname in Klammern hinzu. Beispiel 1: (2021/2022: Planung, Aufbau und Konfiguration von Oracle Service Bus Umgebungen). Beispiel 2 (2024: Weiterbildung – HashiCorp Zertifizierung)
- Falls du keine Erfahrung findest, führe eine Gap-Analyse durch und schreibe und welche Fähigkeiten fehlen, wie ich sie erwerben kann und auf welche meiner Fähigkeiten ich zurückgreifen kann, um die Lücke zu schließen.

<Schlusswort>:
- Schreibe keine Überschrift für diesen Abschnitt.
- Weise hier auch auf meine Soft Skills hin die ich aus dem CV habe.
- Schreibe eine kurze Zusammenfassung, warum ich für das Projekt geeignet bin.
- Erstelle ein Rating von 1 bis 100%, wie gut ich für das Projekt geeignet bin und liefere die Gap-Analyse.
- Gebe meine Verfügbarkeit und meine Gehaltsvorstellung an.
Beispiel:
Verfügbarkeit: sofort, vollzeit, remote und vor Ort
Gehaltsvorstellung: 120,- € pro Stunde
'''

    def _initialize_llm(self):
        """
        Initialize the LLM based on provider and model.

        Returns:
            Initialized LLM instance

        Raises:
            ValueError: If provider is unsupported
            ImportError: If required packages are not installed
        """
        if self.model_provider == 'openai':
            return ChatOpenAI(
                model_name=self.model_name,
                temperature=0,
                max_tokens=self.model_config['max_tokens'],
                openai_api_key=self.api_key
            )

        elif self.model_provider == 'anthropic':
            if ChatAnthropic is None:
                raise ImportError("langchain_anthropic not installed. Run: pip install langchain-anthropic")
            return ChatAnthropic(
                model_name=self.model_name,
                temperature=0,
                max_tokens=self.model_config['max_tokens'],
                anthropic_api_key=self.api_key
            )

        elif self.model_provider == 'google':
            if ChatGoogleGenerativeAI is None:
                raise ImportError("langchain_google_genai not installed. Run: pip install langchain-google-genai")
            return ChatGoogleGenerativeAI(
                model=self.model_name,
                temperature=0,
                max_output_tokens=self.model_config['max_tokens'],
                google_api_key=self.api_key
            )

        else:
            raise ValueError(f"Unsupported provider: {self.model_provider}")

    def get_model_info(self) -> Dict[str, Any]:
        """
        Get information about the current model configuration.

        Returns:
            Dictionary with model information
        """
        return {
            'provider': self.model_provider,
            'model': self.model_name,
            'max_tokens': self.model_config['max_tokens'],
            'context_window': self.model_config['context_window']
        }

    def calculate_cost(self, input_tokens: int, output_tokens: Optional[int] = None) -> float:
        """
        Calculate estimated cost for the given token count.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens (optional)

        Returns:
            Estimated cost in USD
        """
        if output_tokens is None:
            # Legacy mode: assume input_tokens is total tokens, estimate 80/20 split
            total_tokens = input_tokens
            input_tokens = int(total_tokens * 0.8)
            output_tokens = int(total_tokens * 0.2)

        input_cost_per_1k = self.model_config.get('cost_per_1k_input', 0.002)
        output_cost_per_1k = self.model_config.get('cost_per_1k_output', 0.006)

        input_cost = (input_tokens / 1000) * input_cost_per_1k
        output_cost = (output_tokens / 1000) * output_cost_per_1k

        return input_cost + output_cost

    def calculate_tokens_and_cost(self, text: str, model: str = "gpt-4") -> Tuple[int, float]:
        """
        Calculate token count and estimated cost for the given text.

        Args:
            text: Text to calculate tokens for
            model: Model name for token encoding

        Returns:
            Tuple of (token_count, cost)
        """
        if tiktoken is None:
            # Fallback estimation if tiktoken not available
            token_count = len(text.split()) * 1.3  # rough estimation
        else:
            encoding = tiktoken.encoding_for_model(model)
            token_count = len(encoding.encode(text))

        cost = self.calculate_cost(token_count)
        return token_count, cost

    def visual_separator(self, text: str) -> str:
        """
        Create a visual separator with text in the middle.

        Args:
            text: Text to display in separator

        Returns:
            Formatted separator string
        """
        separator_line = "*" * 80
        text_line = f"* {text.center(76)} *"
        return f"\n{separator_line}\n{text_line}\n{separator_line}\n"

    def extract_project_metadata(self, project_content: str) -> Dict[str, str]:
        """
        Extract project metadata from markdown content.

        Args:
            project_content: Project markdown content

        Returns:
            Dictionary with extracted metadata
        """
        metadata = {
            'title': '',
            'position': '',
            'reference_number': '',
            'company': '',
            'location': ''
        }

        # Extract title from first heading
        title_match = re.search(r'^#\s+(.+)$', project_content, re.MULTILINE)
        if title_match:
            metadata['title'] = title_match.group(1).strip()

        # Try to extract position and reference number from content
        # Look for common patterns
        position_patterns = [
            r'Position:\s*([^\n]+)',
            r'Stelle:\s*([^\n]+)',
            r'Job:\s*([^\n]+)',
            r'Rolle:\s*([^\n]+)'
        ]

        for pattern in position_patterns:
            match = re.search(pattern, project_content, re.IGNORECASE)
            if match:
                metadata['position'] = match.group(1).strip()
                break

        # Look for reference number
        ref_patterns = [
            r'Referenznummer:\s*([^\n]+)',
            r'Reference:\s*([^\n]+)',
            r'Ref\.?\s*:\s*([^\n]+)',
            r'ID:\s*([^\n]+)'
        ]

        for pattern in ref_patterns:
            match = re.search(pattern, project_content, re.IGNORECASE)
            if match:
                metadata['reference_number'] = match.group(1).strip()
                break

        return metadata

    def generate_application(self, skills: str, project_requirements: str) -> Tuple[str, int, float]:
        """
        Generate job application using the LLM chain.

        Args:
            skills: CV/skills content
            project_requirements: Project requirements content

        Returns:
            Tuple of (application_text, tokens_used, cost)
        """
        # Calculate input tokens and cost
        input_text = self.prompt.template.format(
            skills=skills,
            project_requirements=project_requirements
        )
        input_tokens, input_cost = self.calculate_tokens_and_cost(input_text)

        print(f"Content length of the chain in tokens: {input_tokens}")
        print(f"Cost for {input_tokens} tokens: ${input_cost:.4f}")

        # Generate the application (Phase 0: chain.generate() → chain.invoke() via LCEL)
        response = self.chain.invoke({
            "skills": skills,
            "project_requirements": project_requirements
        })

        # Extract the generated text (LCEL invoke returns AIMessage)
        application_text = response.content

        # Extract token usage from response metadata
        tokens_used = 0
        usage_meta = getattr(response, "usage_metadata", None)
        if usage_meta:
            # langchain_core AIMessage carries usage_metadata dict
            tokens_used = usage_meta.get("total_tokens",
                          usage_meta.get("input_tokens", 0) + usage_meta.get("output_tokens", 0))
        else:
            # Fallback: estimate from text length
            tokens_used = int(len(application_text.split()) * 1.3)

        cost = self.calculate_cost(tokens_used)

        return application_text, tokens_used, cost

    def append_application_to_markdown(self, project_file: str, application_text: str,
                                      tokens_used: int, cost: float, metadata: Dict[str, str]) -> None:
        """
        Append generated application to project markdown file.

        Args:
            project_file: Path to project markdown file
            application_text: Generated application text
            tokens_used: Number of tokens used
            cost: Estimated cost
            metadata: Project metadata
        """
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Create application section
        application_section = f"\n## Generated Application\n"
        application_section += f"**Generation Date**: {current_time}  \n"
        application_section += f"**AI Provider**: {self.model_provider.upper()}  \n"
        application_section += f"**Model**: {self.model_name}  \n"
        application_section += f"**Tokens Used**: {tokens_used}  \n"
        application_section += f"**Estimated Cost**: ${cost:.4f}  \n\n"

        # Wrap the generated application text with markers
        application_section += "MARKER_APPLICATION_START\n"
        application_section += application_text
        application_section += "\nMARKER_APPLICATION_END\n"

        # Add footer
        application_section += "\n\n---\n"
        application_section += "*Application generated automatically by Project Bot*"

        # Read existing file
        with open(project_file, 'r', encoding='utf-8') as f:
            existing_content = f.read()

        # Create backup if configured
        if self.app_config.get('output', {}).get('backup_original', True):
            backup_file = f"{project_file}.backup"
            with open(backup_file, 'w', encoding='utf-8') as f:
                f.write(existing_content)

        # Append application section
        new_content = existing_content + application_section

        # Write back to file
        with open(project_file, 'w', encoding='utf-8') as f:
            f.write(new_content)

    def update_project_state(self, project_file: str, new_state: str, note: Optional[str] = None) -> bool:
        """
        Update project state using the state manager.

        Args:
            project_file: Path to project file
            new_state: New state to set
            note: Optional note for the state change

        Returns:
            True if update was successful, False otherwise
        """
        try:
            projects_dir = os.path.dirname(project_file)
            state_manager = ProjectStateManager(projects_dir)
            return state_manager.update_state(project_file, new_state, note)
        except Exception as e:
            print(f"Error updating project state: {e}")
            return False

    def process_project(self, project_file: str, cv_content: str, fit_score: int) -> Dict[str, Any]:
        """
        Process a single project file and generate application if eligible.

        Args:
            project_file: Path to project markdown file
            cv_content: CV content for matching
            fit_score: Project fit score from evaluation

        Returns:
            Dictionary with processing results
        """
        result = {
            'project_file': project_file,
            'processed': False,
            'application_generated': False,
            'error': None,
            'tokens_used': 0,
            'cost': 0.0,
            'state_updated': False
        }

        try:
            # Check if application generation is enabled
            if not self.app_config.get('enabled', False):
                result['error'] = "Application generation is disabled"
                return result

            # Check fit score threshold
            threshold = self.app_config.get('auto_generation_threshold', 90)
            if fit_score < threshold:
                result['error'] = f"Fit score {fit_score} below threshold {threshold}"
                return result

            # Initialize state manager
            projects_dir = os.path.dirname(project_file)
            state_manager = ProjectStateManager(projects_dir)

            # Update state to applied
            state_manager.update_state(project_file, 'applied', 'Starting application generation')
            result['state_updated'] = True

            # Load project content
            with open(project_file, 'r', encoding='utf-8') as f:
                project_content = f.read()

            # Extract project metadata
            metadata = self.extract_project_metadata(project_content)

            # Generate application
            print(f"Generating application for: {os.path.basename(project_file)}")
            application_text, tokens_used, cost = self.generate_application(
                cv_content, project_content
            )

            # Append to markdown file
            self.append_application_to_markdown(
                project_file, application_text, tokens_used, cost, metadata
            )

            # Update state to sent
            state_manager.update_state(project_file, 'applied', f'Application generated successfully')

            # Update result
            result.update({
                'processed': True,
                'application_generated': True,
                'tokens_used': tokens_used,
                'cost': cost,
                'metadata': metadata
            })

            print(f"✅ Application generated successfully for {os.path.basename(project_file)}")
            print(f"   Tokens used: {tokens_used}, Cost: ${cost:.4f}")

        except Exception as e:
            result['error'] = str(e)
            print(f"❌ Error processing {os.path.basename(project_file)}: {e}")

        return result

    def process_projects_batch(self, project_files: List[str], cv_content: str,
                              fit_scores: Dict[str, int]) -> List[Dict[str, Any]]:
        """
        Process multiple project files in batch.

        Args:
            project_files: List of project file paths
            cv_content: CV content for matching
            fit_scores: Dictionary mapping file paths to fit scores

        Returns:
            List of processing results
        """
        results = []
        batch_size = self.app_config.get('processing', {}).get('batch_size', 5)

        for i in range(0, len(project_files), batch_size):
            batch = project_files[i:i + batch_size]

            for project_file in batch:
                fit_score = fit_scores.get(project_file, 0)
                result = self.process_project(project_file, cv_content, fit_score)
                results.append(result)

                # Add small delay between projects
                time.sleep(1)

        return results


def load_application_config(config_path: str = 'config.yaml') -> Dict[str, Any]:
    """
    Load and validate application generator configuration.

    Args:
        config_path: Path to configuration file

    Returns:
        Application generator configuration

    Raises:
        ValueError: If configuration is invalid
        FileNotFoundError: If config file not found
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        app_config = config.get('application_generator', {})
        if not app_config:
            raise ValueError("application_generator section not found in config")

        return config

    except FileNotFoundError:
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing YAML configuration: {e}")


def create_application_generator(config_path: str = 'config.yaml') -> ApplicationGenerator:
    """
    Create and initialize ApplicationGenerator instance.

    Args:
        config_path: Path to configuration file

    Returns:
        Initialized ApplicationGenerator instance

    Raises:
        ValueError: If configuration is invalid
        ImportError: If required packages are missing
    """
    config = load_application_config(config_path)
    return ApplicationGenerator(config)


# CLI functionality for manual application generation
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate job applications using AI")
    parser.add_argument('project_files', nargs='+', help='Project files to process')
    parser.add_argument('--config', default='config.yaml', help='Configuration file path')
    parser.add_argument('--cv', default='data/cv.md', help='CV file path')
    parser.add_argument('--threshold', type=int, help='Override fit score threshold')

    args = parser.parse_args()

    try:
        # Load configuration
        config = load_application_config(args.config)

        # Override threshold if specified
        if args.threshold is not None:
            config['application_generator']['auto_generation_threshold'] = args.threshold

        # Create generator
        generator = ApplicationGenerator(config)

        # Load CV content
        with open(args.cv, 'r', encoding='utf-8') as f:
            cv_content = f.read()

        # Process each project file (assume high fit score for manual processing)
        for project_file in args.project_files:
            if not os.path.exists(project_file):
                print(f"❌ Project file not found: {project_file}")
                continue

            print(f"Processing: {project_file}")
            result = generator.process_project(project_file, cv_content, fit_score=95)

            if result['application_generated']:
                print(f"✅ Application generated successfully")
                if result.get('state_updated'):
                    print(f"   State updated to: sent")
            else:
                print(f"❌ Failed to generate application: {result.get('error', 'Unknown error')}")

    except Exception as e:
        print(f"❌ Error: {e}")
        exit(1)