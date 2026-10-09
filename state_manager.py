#!/usr/bin/env python3
"""
Project State Manager for Bewerbungs-Bot

This module provides state management for project files using YAML frontmatter.
Replaces directory-based state management with self-contained project files.

Phase 1: Integriert ProjectRecord (Schema v2).
- read_project() gibt weiterhin (Dict, str) zurück (v1-Kompatibilität)
- read_project_record() gibt ein ProjectRecord-Objekt zurück (neu)
- write_project() schreibt immer Schema v2 wenn ein ProjectRecord übergeben wird
- Bestehende v1-Dateien werden beim nächsten Schreibzugriff automatisch auf v2 upgradet
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union
import yaml

from project_record import ProjectRecord, CURRENT_SCHEMA_VERSION


class ProjectStateManager:
    """
    Manages project states through YAML frontmatter in markdown files.

    States: scraped -> rejected/accepted -> applied -> sent -> open -> archived
    """

    VALID_STATES = {
        'scraped', 'rejected', 'accepted',
        'applied', 'sent', 'open', 'archived'
    }

    VALID_TRANSITIONS = {
        'scraped': ['rejected', 'accepted'],  # Direct evaluation result
        'rejected': ['accepted', 'applied', 'archived'],  # Allow re-application
        'accepted': ['applied', 'rejected'],  # Can still reject
        'applied': ['sent', 'archived', 'open'],  # More flexible
        'sent': ['open', 'archived'],
        'open': ['archived', 'applied'],  # Can re-apply
        'archived': ['scraped', 'accepted', 'rejected']  # Allow reopening
    }

    def __init__(self, projects_dir: str = "projects"):
        """
        Initialize the state manager.

        Args:
            projects_dir: Directory containing project files
        """
        self.projects_dir = Path(projects_dir)
        self.projects_dir.mkdir(exist_ok=True)

    def _parse_frontmatter(self, content: str) -> Tuple[Dict[str, Any], str]:
        """
        Parse YAML frontmatter from markdown content.

        Args:
            content: Full markdown content

        Returns:
            Tuple of (frontmatter_dict, body_content)
        """
        if not content.startswith('---'):
            return {}, content

        # Find the end of frontmatter
        lines = content.split('\n')
        end_idx = -1
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == '---':
                end_idx = i
                break

        if end_idx == -1:
            return {}, content

        # Parse YAML frontmatter
        frontmatter_text = '\n'.join(lines[1:end_idx])
        try:
            frontmatter = yaml.safe_load(frontmatter_text) or {}
        except yaml.YAMLError:
            frontmatter = {}

        # Get body content
        body_content = '\n'.join(lines[end_idx + 1:]) if end_idx + 1 < len(lines) else ''

        return frontmatter, body_content

    def _format_frontmatter(self, frontmatter: Dict[str, Any]) -> str:
        """
        Format frontmatter dictionary as YAML string.

        Args:
            frontmatter: Frontmatter dictionary

        Returns:
            YAML frontmatter string
        """
        if not frontmatter:
            return ''

        yaml_content = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
        return f"---\n{yaml_content}---\n\n"

    def read_project(self, project_path: str) -> Tuple[Dict[str, Any], str]:
        """
        Read project file and extract frontmatter and body.

        Args:
            project_path: Path to project file

        Returns:
            Tuple of (frontmatter_dict, body_content)

        Note: Rückgabetyp bleibt Dict für v1-Kompatibilität.
              Verwende read_project_record() für den typisierten Zugriff.
        """
        try:
            with open(project_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return self._parse_frontmatter(content)
        except FileNotFoundError:
            return {}, ''
        except Exception as e:
            print(f"Error reading project {project_path}: {e}")
            return {}, ''

    def read_project_record(self, project_path: str) -> Tuple[Optional[ProjectRecord], str]:
        """
        Read project file and return a typed ProjectRecord object.

        Supports Schema v1 and v2. v1-files are converted on-the-fly
        but NOT written back (use write_project_record for that).

        Args:
            project_path: Path to project file

        Returns:
            Tuple of (ProjectRecord or None on error, body_content)
        """
        fm, body = self.read_project(project_path)
        if not fm:
            return None, body
        try:
            record = ProjectRecord.from_frontmatter_dict(fm)
            return record, body
        except Exception as e:
            print(f"Error converting to ProjectRecord {project_path}: {e}")
            return None, body

    def write_project_record(self, project_path: str, record: ProjectRecord, body: str) -> bool:
        """
        Write a ProjectRecord to disk as Schema v2 frontmatter + body.

        Always writes Schema v2, even if the file was Schema v1 before.
        This is the safe upgrade path: explicit write = explicit upgrade.

        Args:
            project_path: Path to project file
            record: ProjectRecord to write
            body: Body content (unchanged)

        Returns:
            True if successful
        """
        try:
            content = record.to_frontmatter_text() + body
            with open(project_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"Error writing ProjectRecord {project_path}: {e}")
            return False

    def write_project(self, project_path: str, frontmatter: Dict[str, Any], body: str) -> bool:
        """
        Write project file with updated frontmatter.

        Args:
            project_path: Path to project file
            frontmatter: Frontmatter dictionary
            body: Body content

        Returns:
            True if successful, False otherwise
        """
        try:
            frontmatter_text = self._format_frontmatter(frontmatter)
            full_content = frontmatter_text + body

            with open(project_path, 'w', encoding='utf-8') as f:
                f.write(full_content)
            return True
        except Exception as e:
            print(f"Error writing project {project_path}: {e}")
            return False

    def get_current_state(self, project_path: str) -> Optional[str]:
        """
        Get current state of a project.

        Args:
            project_path: Path to project file

        Returns:
            Current state string or None if not found
        """
        frontmatter, _ = self.read_project(project_path)
        return frontmatter.get('state')

    def validate_transition(self, current_state: str, new_state: str) -> bool:
        """
        Validate if a state transition is allowed.

        Args:
            current_state: Current state
            new_state: Proposed new state

        Returns:
            True if transition is valid, False otherwise
        """
        if new_state not in self.VALID_STATES:
            return False

        if current_state not in self.VALID_TRANSITIONS:
            return False

        return new_state in self.VALID_TRANSITIONS[current_state]

    def update_state(self, project_path: str, new_state: str, note: Optional[str] = None, force: bool = False, ui_context: bool = False) -> bool:
        """
        Update project state with relaxed validation for UI interactions.
        
        Args:
            project_path: Path to project file
            new_state: New state to set
            note: Optional note for the state change
            force: If True, bypass validation for manual state changes
            ui_context: If True, allow all transitions for UI with logging
            
        Returns:
            True if successful, False otherwise
        """
        frontmatter, body = self.read_project(project_path)
        
        current_state = frontmatter.get('state')
        
        # Determine if this is an override (either forced or UI context)
        is_override = force or ui_context
        is_valid_transition = not current_state or self.validate_transition(current_state, new_state)
        
        # For UI context, always allow but log as override if invalid
        if ui_context and current_state and not is_valid_transition:
            print(f"UI override transition: {current_state} -> {new_state}")
            is_override = True
        
        # For non-UI calls, maintain strict validation unless forced
        elif not is_override and current_state and not is_valid_transition:
            print(f"Invalid state transition: {current_state} -> {new_state}")
            return False
        
        # Update state
        frontmatter['state'] = new_state
        
        # Update state history
        if 'state_history' not in frontmatter:
            frontmatter['state_history'] = []
        
        history_entry = {
            'state': new_state,
            'timestamp': datetime.now().isoformat()
        }
        
        # Handle notes - always optional, auto-generate for overrides if empty
        if note and note.strip():
            history_entry['note'] = note.strip()
        elif is_override:
            auto_note = f"UI state change: {current_state or 'none'} → {new_state}"
            if force:
                auto_note = f"Manual override: {current_state or 'none'} → {new_state}"
            history_entry['note'] = auto_note
        
        # Add override flag
        if is_override:
            history_entry['override'] = True
            if ui_context:
                history_entry['ui_context'] = True
        
        frontmatter['state_history'].append(history_entry)
        
        # Write back to file
        return self.write_project(project_path, frontmatter, body)

    def initialize_project(self, project_path: str, metadata: Dict[str, Any]) -> bool:
        """
        Initialize a new project with frontmatter.

        Args:
            project_path: Path to project file
            metadata: Initial project metadata

        Returns:
            True if successful, False otherwise
        """
        # Read existing content
        frontmatter, body = self.read_project(project_path)

        # Merge metadata
        frontmatter.update(metadata)

        # Set initial state if not present
        if 'state' not in frontmatter:
            frontmatter['state'] = 'scraped'
            frontmatter['state_history'] = [{
                'state': 'scraped',
                'timestamp': datetime.now().isoformat()
            }]

        return self.write_project(project_path, frontmatter, body)

    def query_projects(self, state: Optional[str] = None, company: Optional[str] = None) -> List[str]:
        """
        Query projects by state and/or company.

        Args:
            state: Filter by state (optional)
            company: Filter by company (optional)

        Returns:
            List of matching project file paths
        """
        matching_projects = []

        for project_file in self.projects_dir.glob('*.md'):
            frontmatter, _ = self.read_project(str(project_file))

            # Check state filter
            if state and frontmatter.get('state') != state:
                continue

            # Check company filter
            if company and frontmatter.get('company') != company:
                continue

            matching_projects.append(str(project_file))

        return matching_projects

    def get_state_summary(self) -> Dict[str, int]:
        """
        Get summary of projects by state.

        Returns:
            Dictionary with state counts
        """
        summary = {state: 0 for state in self.VALID_STATES}

        for project_file in self.projects_dir.glob('*.md'):
            state = self.get_current_state(str(project_file))
            if state in summary:
                summary[state] += 1

        return summary

    def get_projects_by_state(self, state: str) -> List[Dict[str, Any]]:
        """
        Get detailed information about projects in a specific state.

        Args:
            state: State to filter by

        Returns:
            List of project info dictionaries
        """
        projects = []

        for project_file in self.projects_dir.glob('*.md'):
            frontmatter, _ = self.read_project(str(project_file))

            if frontmatter.get('state') == state:
                project_info = {
                    'path': str(project_file),
                    'filename': project_file.name,
                    **frontmatter
                }
                projects.append(project_info)

        return projects