#!/usr/bin/env python3
"""
Enhanced Flask Backend for Vue3 Frontend
Backend-for-Frontend Pattern with REST API
"""

from flask import Flask, jsonify, request, send_from_directory, Response, send_file
from flask_cors import CORS
import subprocess
import os
import shutil
import sys
from pathlib import Path
import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from pydantic import BaseModel, ValidationError
from functools import wraps
import uuid
from datetime import timedelta
from dataclasses import asdict

# Import existing modules
from state_manager import ProjectStateManager

# Import scheduler manager
from scheduler_manager import get_scheduler_manager

# Import centralized logging
from logging_config import setup_logging

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

app = Flask(__name__)

# SECURITY (Phase 0): CORS is restricted to localhost only.
# The previous wildcard origins=["*"] combined with host='0.0.0.0' and debug=True
# exposed the API and the Flask debugger to the entire local network without
# authentication.  Only loopback origins are allowed until authentication is added.
# TODO Phase 9: Add proper authentication (API key or session) before any public deployment.
CORS(app, origins=["http://localhost:8002", "http://127.0.0.1:8002"])

# Disable caching globally
@app.after_request
def add_no_cache_headers(response):
    try:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Access-Control-Expose-Headers"] = "Cache-Control, Pragma, Expires"
    except Exception as e:
        logger.warning(f"Failed to set no-cache headers: {e}")
    return response

# Initialize state manager
state_manager = ProjectStateManager()

# Initialize scheduler manager
scheduler_manager = get_scheduler_manager()

# Pydantic Models for API validation

class ProjectFilters(BaseModel):
    search: Optional[str] = None
    statuses: List[str] = []
    companies: List[str] = []
    providers: List[str] = []
    channels: List[str] = []
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    pre_eval_score_min: Optional[int] = None
    pre_eval_score_max: Optional[int] = None
    llm_score_min: Optional[int] = None
    llm_score_max: Optional[int] = None
    page: int = 1
    page_size: int = 50

class ProjectStateUpdateRequest(BaseModel):
    from_state: str
    to_state: str
    note: Optional[str] = None
    force: bool = False
    ui_context: bool = True  # Default to True for UI calls

class ProjectResponse(BaseModel):
    id: str
    title: str
    company: Optional[str]
    url: Optional[str]
    retrieval_date: Optional[str]
    posted_date: Optional[str]
    pre_eval_score: Optional[int]
    llm_score: Optional[int]
    status: str
    state_history: List[Dict[str, Any]]
    file_path: str
    metadata: Dict[str, Any]

class ProjectListResponse(BaseModel):
    projects: List[ProjectResponse]
    total: int
    page: int
    page_size: int
    has_next: bool
    has_prev: bool

class DashboardStats(BaseModel):
    total_projects: int
    by_status: Dict[str, int]
    recent_activity: List[Dict[str, Any]]
    last_updated: str

class APIErrorResponse(BaseModel):
    error: str
    message: str
    code: int
    details: Optional[Dict[str, Any]] = None
    timestamp: str

class MarkdownContentResponse(BaseModel):
    content: str
    filename: str
    last_modified: str
    file_size: int

class MarkdownUpdateRequest(BaseModel):
    content: str

class CreateManualProjectRequest(BaseModel):
    title: str
    company: Optional[str] = None
    description: Optional[str] = None

# Pydantic Models for Scheduling API

class ScheduleCreateRequest(BaseModel):
    name: str
    description: str
    workflow_type: str  # cli_sequence
    cli_commands: List[Dict[str, Any]]
    cron_schedule: str
    timezone: str = "Europe/Berlin"
    metadata: Optional[Dict[str, Any]] = None

    def __init__(self, **data):
        super().__init__(**data)
        # Validate workflow_type
        if self.workflow_type != "cli_sequence":
            raise ValueError("Invalid workflow_type. Only 'cli_sequence' is supported")

class ScheduleUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    workflow_type: Optional[str] = None
    cli_commands: Optional[List[Dict[str, Any]]] = None
    cron_schedule: Optional[str] = None
    timezone: Optional[str] = None

class ScheduleResponse(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    workflow_type: str
    cli_commands: List[Dict[str, Any]]
    cron_schedule: str
    timezone: str
    created_at: str
    updated_at: str
    last_run: Optional[str]
    last_status: Optional[str]
    next_run: Optional[str]
    execution_history: List[Dict[str, Any]]

class ExecutionHistoryResponse(BaseModel):
    run_id: str
    started_at: str
    completed_at: Optional[str]
    status: str
    output: Optional[str]
    error: Optional[str]
    exit_code: Optional[int]

class SchedulerStatusResponse(BaseModel):
    running: bool
    total_schedules: int
    enabled_schedules: int
    active_jobs: int
    next_job: Optional[Dict[str, Any]]
    timestamp: str

# Pydantic Models for Log API

class LogFileInfo(BaseModel):
    name: str
    size: int
    modified: str
    is_current: bool

class LogFilesResponse(BaseModel):
    files: List[LogFileInfo]

# Error handling decorator
def handle_api_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ValidationError as e:
            return jsonify(APIErrorResponse(
                error="ValidationError",
                message="Invalid request data",
                code=400,
                details={"validation_errors": e.errors()},
                timestamp=datetime.now().isoformat()
            ).model_dump()), 400
        except Exception as e:
            logger.error(f"API Error in {f.__name__}: {e}")
            return jsonify(APIErrorResponse(
                error="InternalServerError",
                message=str(e),
                code=500,
                timestamp=datetime.now().isoformat()
            ).model_dump()), 500
    return decorated_function

# Quick Filters Configuration
QUICK_FILTERS_FILE = Path("data/quick_filters.json")

# Pydantic Models for Quick Filters
class QuickFilterItem(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    filters: Dict[str, Any]
    isDynamic: bool = False
    originalRange: Optional[str] = None
    created_at: str
    updated_at: str

class QuickFilterList(BaseModel):
    filters: List[QuickFilterItem]

class QuickFilterCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    filters: Dict[str, Any]
    isDynamic: Optional[bool] = False
    originalRange: Optional[str] = None

class QuickFilterUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    filters: Optional[Dict[str, Any]] = None
    isDynamic: Optional[bool] = None
    originalRange: Optional[str] = None


# Helper functions for Quick Filters
def load_quick_filters() -> QuickFilterList:
    """Load quick filters from JSON file"""
    if not QUICK_FILTERS_FILE.exists():
        return QuickFilterList(filters=[])
    with open(QUICK_FILTERS_FILE, 'r') as f:
        data = json.load(f)
        # Check if the loaded data is a dictionary and has the 'filters' key
        if isinstance(data, dict) and 'filters' in data:
            return QuickFilterList(**data)
        # Handle the case where the JSON file contains a list directly
        elif isinstance(data, list):
            return QuickFilterList(filters=data)
        else:
            # If the structure is unexpected, return an empty list
            logger.warning(f"Unexpected structure in {QUICK_FILTERS_FILE}. Returning empty list.")
            return QuickFilterList(filters=[])


def save_quick_filters(filters: QuickFilterList):
    """Save quick filters to JSON file"""
    with open(QUICK_FILTERS_FILE, 'w') as f:
        # The root of the JSON should be an object with a 'filters' key
        json.dump({"filters": [f.dict() for f in filters.filters]}, f, indent=2)


# Utility functions

def extract_latest_scores(content: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Extract the latest pre-evaluation and LLM scores from markdown content.
    Handles multiple evaluation sections by finding the most recent one based on timestamp.

    Args:
        content: The markdown content to parse

    Returns:
        Tuple of (pre_eval_score, llm_score) - both can be None if not found
    """
    # Find all evaluation sections with their timestamps
    evaluation_pattern = r'## 🤖 AI Evaluation Results\s*\n\s*\*\*Evaluation Timestamp:\*\*\s*([^\n]+)(.*?)(?=## 🤖 AI Evaluation Results|$)'
    evaluations = re.findall(evaluation_pattern, content, re.DOTALL)

    if not evaluations:
        # Fallback to old method for backward compatibility
        pre_eval_match = re.search(r"- \*\*Score:\*\*\s*(\d+)/100", content)
        llm_match = re.search(r"- \*\*Fit Score:\*\*\s*(\d+)/100", content)

        pre_eval_score = int(pre_eval_match.group(1)) if pre_eval_match else None
        llm_score = int(llm_match.group(1)) if llm_match else None
        return pre_eval_score, llm_score

    # Parse each evaluation and find the latest one
    latest_timestamp = None
    latest_pre_eval = None
    latest_llm = None

    for timestamp_str, section_content in evaluations:
        try:
            # Parse timestamp
            current_timestamp = datetime.fromisoformat(timestamp_str.strip())

            # Extract scores from this section
            pre_eval_match = re.search(r"- \*\*Score:\*\*\s*(\d+)/100", section_content)
            llm_match = re.search(r"- \*\*Fit Score:\*\*\s*(\d+)/100", section_content)

            current_pre_eval = int(pre_eval_match.group(1)) if pre_eval_match else None
            current_llm = int(llm_match.group(1)) if llm_match else None

            # Update latest if this is newer or first one
            if latest_timestamp is None or current_timestamp > latest_timestamp:
                latest_timestamp = current_timestamp
                latest_pre_eval = current_pre_eval
                latest_llm = current_llm

        except (ValueError, AttributeError) as e:
            # Skip invalid timestamps or parsing errors
            logger.warning(f"Failed to parse evaluation timestamp '{timestamp_str}': {e}")
            continue

    return latest_pre_eval, latest_llm

def parse_project_file(file_path: str) -> Dict[str, Any]:
    """Parse project file and extract metadata"""
    try:
        frontmatter, content = state_manager.read_project(file_path)

        # Extract basic metadata with defaults for required fields
        metadata = {
            "id": Path(file_path).stem,
            "title": frontmatter.get("title", Path(file_path).stem),
            "company": frontmatter.get("company"),
            "url": frontmatter.get("source_url"),
            "status": frontmatter.get("state", "scraped"),
            "state_history": frontmatter.get("state_history", []),
            "file_path": file_path,
            "retrieval_date": None,  # Required field
            "posted_date": None,     # Required field
            "pre_eval_score": None,
            "llm_score": None,
            "metadata": {}
        }

        # Extract title from H1 heading if not in frontmatter
        if not metadata["title"] or metadata["title"] == Path(file_path).stem:
            title_match = re.search(r"^#\s*(.*)", content, re.MULTILINE)
            if title_match:
                metadata["title"] = title_match.group(1).strip()

        # Extract company from content if not in frontmatter
        if not metadata["company"]:
            company_match = re.search(r"- \*\*Von:\*\*\s*(.*?)(?:\n|$)", content)
            if company_match:
                metadata["company"] = company_match.group(1).strip()

        # Extract URL from content if not in frontmatter
        if not metadata["url"]:
            url_match = re.search(r"\*\*URL:\*\*\s*\[(.*?)\]\((.*?)\)", content)
            if url_match:
                metadata["url"] = url_match.group(2).strip()

        # Extract dates
        if "scraped_date" in frontmatter:
            metadata["retrieval_date"] = frontmatter["scraped_date"]
        else:
            # Try to extract from filename timestamp
            filename_match = re.search(r"(\d{8})_(\d{6})", Path(file_path).name)
            if filename_match:
                date_str = filename_match.group(1)
                time_str = filename_match.group(2)
                metadata["retrieval_date"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}T{time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"

        # Extract posted date from content if available
        posted_match = re.search(r"- \*\*Eingestellt:\*\*\s*(.*?)(?:\n|$)", content)
        if posted_match:
            metadata["posted_date"] = posted_match.group(1).strip()

        # Extract latest scores from content (handle multiple evaluations)
        metadata["pre_eval_score"], metadata["llm_score"] = extract_latest_scores(content)

        # Extract provider and channel metadata for UI display
        metadata["metadata"] = {
            "provider_id": frontmatter.get("provider_id"),
            "provider_name": frontmatter.get("provider_name"),
            "collection_channel": frontmatter.get("collection_channel"),
            "collected_at": frontmatter.get("collected_at"),
            "reference_id": frontmatter.get("reference_id")
        }

        return metadata

    except Exception as e:
        logger.error(f"Error parsing project file {file_path}: {e}")
        return {
            "id": Path(file_path).stem,
            "title": Path(file_path).stem,
            "company": None,
            "url": None,
            "status": "error",
            "state_history": [],
            "file_path": file_path,
            "retrieval_date": None,
            "posted_date": None,
            "pre_eval_score": None,
            "llm_score": None,
            "metadata": {"error": str(e)}
        }

def get_projects_with_filters(filters: ProjectFilters) -> List[Dict[str, Any]]:
    """Get projects with filtering and pagination"""

    filters = handle_relative_dates(filters)

    projects_dir = Path("projects")
    if not projects_dir.exists():
        return []

    all_projects = []

    # Get all project files
    for project_file in projects_dir.glob("*.md"):
        project_data = parse_project_file(str(project_file))
        all_projects.append(project_data)

    logger.info(f"📊 Total projects loaded: {len(all_projects)}")
    logger.info(f"🔍 Applied filters: search={filters.search}, statuses={filters.statuses}, companies={filters.companies}, providers={filters.providers}, channels={filters.channels}, date_from={filters.date_from}, date_to={filters.date_to}, pre_eval_score_min={filters.pre_eval_score_min}, pre_eval_score_max={filters.pre_eval_score_max}, llm_score_min={filters.llm_score_min}, llm_score_max={filters.llm_score_max}")

    # Apply filters
    filtered_projects = []
    for project in all_projects:
        project_id = project.get("id", "unknown")
        project_date = project.get("retrieval_date")
        logger.debug(f"🔍 Checking project {project_id}: date={project_date}, status={project.get('status')}, company={project.get('company')}")

        # Search filter
        if filters.search:
            search_term = filters.search.lower()
            title = (project.get("title") or "").lower()
            company = (project.get("company") or "").lower()
            if not (search_term in title or search_term in company):
                logger.debug(f"❌ Project {project_id} filtered out by search")
                continue

        # Status filter
        if filters.statuses and project.get("status") not in filters.statuses:
            logger.debug(f"❌ Project {project_id} filtered out by status")
            continue

        # Company filter
        if filters.companies and project.get("company") not in filters.companies:
            logger.debug(f"❌ Project {project_id} filtered out by company")
            continue

        # Provider filter (case-insensitive)
        # Default to 'freelancermap' for projects without provider metadata (backward compatibility)
        project_provider = project.get("metadata", {}).get("provider_name") or "freelancermap"
        if filters.providers:
            # Convert both to lowercase for comparison
            project_provider_lower = project_provider.lower()
            filter_providers_lower = [p.lower() for p in filters.providers]
            logger.debug(f"Provider filter check: project={project_provider} ({project_provider_lower}), filters={filters.providers} ({filter_providers_lower})")
            if project_provider_lower not in filter_providers_lower:
                logger.debug(f"Project {project_id} filtered out by provider")
                continue
            else:
                logger.debug(f"Project {project_id} passed provider filter")

        # Channel filter
        if filters.channels and project.get("metadata", {}).get("collection_channel") not in filters.channels:
            logger.debug(f"❌ Project {project_id} filtered out by channel")
            continue

        # Date filters
        if filters.date_from or filters.date_to:
            if not project_date:
                logger.debug(f"❌ Project {project_id} filtered out - no date")
                continue

            try:
                # Parse project date - handle both date-only and datetime formats
                if 'T' in project_date:
                    project_date_obj = datetime.fromisoformat(project_date.replace('Z', '+00:00'))
                else:
                    project_date_obj = datetime.strptime(project_date, '%Y-%m-%d')
                logger.debug(f"📅 Project {project_id} date: {project_date_obj}")

                if filters.date_from:
                    # Parse date_from as date (YYYY-MM-DD)
                    from_date = datetime.strptime(filters.date_from, '%Y-%m-%d').date()
                    project_date_only = project_date_obj.date()
                    if project_date_only < from_date:
                        logger.debug(f"❌ Project {project_id} filtered out - before date_from ({project_date_only} < {from_date})")
                        continue

                if filters.date_to:
                    # Parse date_to as date (YYYY-MM-DD)
                    to_date = datetime.strptime(filters.date_to, '%Y-%m-%d').date()
                    project_date_only = project_date_obj.date()
                    if project_date_only > to_date:
                        logger.debug(f"❌ Project {project_id} filtered out - after date_to ({project_date_only} > {to_date})")
                        continue

            except Exception as e:
                logger.warning(f"⚠️ Error parsing date for project {project_id}: {e}, project_date='{project_date}', date_from='{filters.date_from}', date_to='{filters.date_to}'")
                continue

        # Pre-Eval Score filters
        pre_score = project.get("pre_eval_score")
        if filters.pre_eval_score_min is not None and (pre_score is None or pre_score < filters.pre_eval_score_min):
            logger.debug(f"❌ Project {project_id} filtered out by pre_eval_score_min")
            continue
        if filters.pre_eval_score_max is not None and (pre_score is None or pre_score > filters.pre_eval_score_max):
            logger.debug(f"❌ Project {project_id} filtered out by pre_eval_score_max")
            continue

        # LLM Score filters
        llm_score = project.get("llm_score")
        if filters.llm_score_min is not None and (llm_score is None or llm_score < filters.llm_score_min):
            logger.debug(f"❌ Project {project_id} filtered out by llm_score_min")
            continue
        if filters.llm_score_max is not None and (llm_score is None or llm_score > filters.llm_score_max):
            logger.debug(f"❌ Project {project_id} filtered out by llm_score_max")
            continue

        logger.debug(f"✅ Project {project_id} passed all filters")
        filtered_projects.append(project)

    logger.info(f"📊 Projects after filtering: {len(filtered_projects)}")

    # Sort by retrieval date (newest first), handling None values
    filtered_projects.sort(key=lambda x: x.get("retrieval_date") or "", reverse=True)

    return filtered_projects

def handle_relative_dates(filters: ProjectFilters) -> ProjectFilters:
    """Handle relative date strings"""
    today = datetime.now().date()

    # Handle date_from
    if filters.date_from:
        if filters.date_from == "today":
            filters.date_from = today.strftime('%Y-%m-%d')
        elif filters.date_from == "last_7_days":
            filters.date_from = (today - timedelta(days=7)).strftime('%Y-%m-%d')
        elif filters.date_from == "current_week":
            start_of_week = today - timedelta(days=today.weekday())
            filters.date_from = start_of_week.strftime('%Y-%m-%d')
        elif filters.date_from == "previous_week":
            end_of_last_week = today - timedelta(days=today.weekday() + 1)
            start_of_last_week = end_of_last_week - timedelta(days=6)
            filters.date_from = start_of_last_week.strftime('%Y-%m-%d')
            filters.date_to = end_of_last_week.strftime('%Y-%m-%d')
        elif filters.date_from == "current_month":
            filters.date_from = today.replace(day=1).strftime('%Y-%m-%d')
        elif filters.date_from == "previous_month":
            first_day_current_month = today.replace(day=1)
            last_day_previous_month = first_day_current_month - timedelta(days=1)
            first_day_previous_month = last_day_previous_month.replace(day=1)
            filters.date_from = first_day_previous_month.strftime('%Y-%m-%d')
            filters.date_to = last_day_previous_month.strftime('%Y-%m-%d')

    # Handle date_to
    if filters.date_to:
        if filters.date_to == "today":
            filters.date_to = today.strftime('%Y-%m-%d')

    return filters

def generate_manual_project_template(project_id: str, title: str, company: Optional[str] = None,
                                    description: Optional[str] = None, scraped_date: str = None) -> str:
    """Generate template content for manual project creation"""
    if not scraped_date:
        scraped_date = datetime.now().isoformat()

    # Clean title for filename compatibility
    clean_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')

    template = f"""---
collected_at: '{scraped_date}'
collection_channel: manual
company: {company or 'Your Company Name'}
provider_id: manual
provider_name: manual
reference_id: '{project_id}'
scraped_date: '{scraped_date}'
source_url: https://manual-entry.com/project/{project_id}
state: empty
state_history:
- note: 'Manual project created'
  state: empty
  timestamp: '{scraped_date}'
title: {title}
---

# {title}

**URL:** [Manual Entry](https://manual-entry.com/project/{project_id})
## Details
- **Start:** To be determined
- **Von:** {company or 'Your Company Name'}
- **Eingestellt:** {datetime.now().strftime('%d.%m.%Y')}
- **Ansprechpartner:** To be determined
- **Projekt-ID:** {project_id}
- **Branche:** To be determined
- **Vertragsart:** To be determined
- **Einsatzart:** To be determined
                                                % Remote

## Schlagworte
To be determined, manual, project

## Beschreibung
{description or 'Please provide a detailed project description here. Include requirements, responsibilities, skills needed, and any other relevant information.'}
"""

    return template

# API Endpoints

@app.route('/api/v1/projects', methods=['GET'])
@handle_api_errors
def get_projects():
    """Get projects with filtering and pagination"""
    try:
        # Parse query parameters
        filters_data = {
            "search": request.args.get("search"),
            "statuses": request.args.getlist("statuses"),
            "companies": request.args.getlist("companies"),
            "providers": request.args.getlist("providers"),
            "channels": request.args.getlist("channels"),
            "date_from": request.args.get("date_from"),
            "date_to": request.args.get("date_to"),
            "pre_eval_score_min": int(request.args.get("pre_eval_score_min")) if request.args.get("pre_eval_score_min") else None,
            "pre_eval_score_max": int(request.args.get("pre_eval_score_max")) if request.args.get("pre_eval_score_max") else None,
            "llm_score_min": int(request.args.get("llm_score_min")) if request.args.get("llm_score_min") else None,
            "llm_score_max": int(request.args.get("llm_score_max")) if request.args.get("llm_score_max") else None,
            "page": int(request.args.get("page", 1)),
            "page_size": int(request.args.get("page_size", 50))
        }

        filters = ProjectFilters(**filters_data)
        all_projects = get_projects_with_filters(filters)

        # Pagination
        if filters.page_size == 0:
            # Special case: page_size = 0 means "All" items
            paginated_projects = all_projects
            actual_page_size = len(all_projects)
        else:
            start_idx = (filters.page - 1) * filters.page_size
            end_idx = start_idx + filters.page_size
            paginated_projects = all_projects[start_idx:end_idx]
            actual_page_size = filters.page_size

        # Calculate pagination info
        if filters.page_size == 0:
            has_next = False
            has_prev = False
        else:
            has_next = end_idx < len(all_projects)
            has_prev = filters.page > 1

        response = ProjectListResponse(
            projects=paginated_projects,
            total=len(all_projects),
            page=filters.page,
            page_size=actual_page_size,
            has_next=has_next,
            has_prev=has_prev
        )

        return jsonify(response.model_dump())

    except Exception as e:
        logger.error(f"Error in get_projects: {e}")
        raise

@app.route('/api/v1/projects/<project_id>', methods=['GET'])
@handle_api_errors
def get_project(project_id: str):
    """Get single project details"""
    projects_dir = Path("projects")
    project_file = projects_dir / f"{project_id}.md"

    if not project_file.exists():
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Project {project_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    project_data = parse_project_file(str(project_file))
    response = ProjectResponse(**project_data)

    return jsonify(response.model_dump())

@app.route('/api/v1/projects/<project_id>', methods=['DELETE'])
@handle_api_errors
def delete_project(project_id: str):
    """Delete a project file"""
    projects_dir = Path("projects")
    project_file = projects_dir / f"{project_id}.md"

    if not project_file.exists():
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Project {project_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    try:
        project_file.unlink()
        return jsonify({"success": True, "message": f"Project {project_id} deleted successfully"}), 200
    except Exception as e:
        logger.error(f"Error deleting project file {project_id}: {e}")
        return jsonify(APIErrorResponse(
            error="InternalServerError",
            message=f"Failed to delete project file: {str(e)}",
            code=500,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 500

@app.route('/api/v1/projects/<project_id>/transition', methods=['POST'])
@handle_api_errors
def update_project_state(project_id: str):
    """Update project state"""
    data = request.get_json()
    if not data:
        raise ValidationError("No JSON data provided")
    
    logger.info(f"Transition request for {project_id}: {data}")
    update_request = ProjectStateUpdateRequest(**data)
    logger.info(f"Parsed request: force={update_request.force}, ui_context={update_request.ui_context}, from={update_request.from_state}, to={update_request.to_state}")
    
    projects_dir = Path("projects")
    project_file = projects_dir / f"{project_id}.md"
    
    if not project_file.exists():
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Project {project_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    # Update state using state manager with UI context for relaxed validation
    success = state_manager.update_state(
        str(project_file),
        update_request.to_state,
        update_request.note,
        update_request.force,
        update_request.ui_context  # Pass UI context for relaxed validation
    )

    if not success:
        return jsonify(APIErrorResponse(
            error="StateTransitionError",
            message=f"Failed to transition from {update_request.from_state} to {update_request.to_state}",
            code=400,
            details={
                "force": update_request.force,
                "ui_context": update_request.ui_context
            },
            timestamp=datetime.now().isoformat()
        ).model_dump()), 400

    # Return updated project data
    project_data = parse_project_file(str(project_file))
    response = ProjectResponse(**project_data)

    logger.info(f"✅ State transition successful: {update_request.from_state} → {update_request.to_state} (force={update_request.force}, ui_context={update_request.ui_context})")

    return jsonify({
        "success": True,
        "message": f"Project state updated to {update_request.to_state}",
        "project": response.model_dump(),
        "timestamp": datetime.now().isoformat()
    })

@app.route('/api/v1/projects/<project_id>/evaluate', methods=['POST'])
@handle_api_errors
def reevaluate_project(project_id: str):
    """Re-evaluate a project"""
    logger.info(f"🔄 Starting re-evaluation for project: {project_id}")

    # Get force parameter from request
    data = request.get_json() or {}
    force_evaluation = data.get('force', False)
    logger.debug(f"🎯 Force evaluation: {force_evaluation}")

    projects_dir = Path("projects")
    project_file = projects_dir / f"{project_id}.md"
    logger.debug(f"📁 Project file path: {project_file}")

    if not project_file.exists():
        logger.error(f"❌ Project file not found: {project_file}")
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Project {project_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    try:
        # Execute the evaluation script for this specific project
        # subprocess and sys already imported at module level

        # Build command with optional force flag
        cmd = [sys.executable, "evaluate_projects.py", str(project_file)]
        if force_evaluation:
            cmd.append("--force-evaluation")
        logger.debug(f"🚀 Executing evaluation command: {' '.join(cmd)}")

        # Run evaluate_projects.py with the specific project file
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        logger.debug(f"📋 Evaluation subprocess return code: {result.returncode}")
        logger.debug(f"📄 Evaluation subprocess stdout length: {len(result.stdout)}")
        logger.debug(f"⚠️ Evaluation subprocess stderr length: {len(result.stderr)}")

        # Log first 500 chars of output for debugging
        if result.stdout:
            logger.debug(f"📄 Evaluation subprocess stdout (first 500 chars): {result.stdout[:500]}")
        if result.stderr:
            logger.error(f"⚠️ Evaluation subprocess stderr (first 500 chars): {result.stderr[:500]}")

        if result.returncode == 0:
            logger.info(f"✅ Evaluation succeeded for project {project_id}")

            # Parse the updated project data
            project_data = parse_project_file(str(project_file))
            response = ProjectResponse(**project_data)


            return jsonify({
                "success": True,
                "message": f"Project {project_id} reevaluated successfully",
                "project": response.model_dump(),
                "output": result.stdout,
                "timestamp": datetime.now().isoformat()
            })
        else:
            logger.error(f"❌ Evaluation failed for project {project_id}")
            return jsonify(APIErrorResponse(
                error="EvaluationError",
                message=f"Evaluation failed: {result.stderr}",
                code=500,
                details={"stdout": result.stdout, "stderr": result.stderr, "return_code": result.returncode},
                timestamp=datetime.now().isoformat()
            ).model_dump()), 500

    except subprocess.TimeoutExpired:
        logger.error(f"⏰ Evaluation timed out for project {project_id}")
        return jsonify(APIErrorResponse(
            error="TimeoutError",
            message="Evaluation timed out after 5 minutes",
            code=408,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 408
    except Exception as e:
        logger.error(f"💥 Unexpected error during evaluation for project {project_id}: {e}")
        logger.error(f"🔍 Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"📋 Full traceback: {traceback.format_exc()}")
        return jsonify(APIErrorResponse(
            error="InternalServerError",
            message=f"Failed to reevaluate project: {str(e)}",
            code=500,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 500

@app.route('/api/v1/projects/<project_id>/generate', methods=['POST'])
@handle_api_errors
def generate_application(project_id: str):
    """Generate application for a specific project"""
    projects_dir = Path("projects")
    project_file = projects_dir / f"{project_id}.md"

    logger.info(f"🔧 Starting application generation for project: {project_id}")
    logger.debug(f"📁 Project file path: {project_file}")

    if not project_file.exists():
        logger.error(f"❌ Project file not found: {project_file}")
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Project {project_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    try:
        # Check if project is in a valid state for generation
        project_data = parse_project_file(str(project_file))
        valid_states = ["accepted", "rejected", "applied"]
        current_status = project_data.get("status")
        logger.debug(f"📊 Project status: {current_status}")

        if current_status not in valid_states:
            logger.warning(f"⚠️ Invalid state for generation: {current_status}")
            return jsonify(APIErrorResponse(
                error="InvalidState",
                message=f"Project {project_id} must be in 'accepted', 'rejected', or 'applied' state (current: {current_status})",
                code=400,
                timestamp=datetime.now().isoformat()
            ).model_dump()), 400

        # Execute the application generation for this specific project
        # subprocess and sys already imported at module level

        # Prepare command
        cmd = [sys.executable, "main.py", "--generate-applications", str(project_file)]
        logger.debug(f"🚀 Executing command: {' '.join(cmd)}")

        # Run main.py with generate-applications flag for this specific project
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        logger.debug(f"📋 Subprocess return code: {result.returncode}")
        logger.debug(f"📄 Subprocess stdout length: {len(result.stdout)}")
        logger.debug(f"⚠️ Subprocess stderr length: {len(result.stderr)}")

        # Log first 500 chars of output for debugging
        if result.stdout:
            logger.debug(f"📄 Subprocess stdout (first 500 chars): {result.stdout[:500]}")
        if result.stderr:
            logger.error(f"⚠️ Subprocess stderr (first 500 chars): {result.stderr[:500]}")

        if result.returncode == 0:
            logger.info(f"✅ Application generation succeeded for project {project_id}")

            # Update project state to applied (even if already applied) to track generation
            current_state = project_data.get("status")
            use_force = current_state == "applied"  # Force if already applied to allow same-state transition
            state_update_success = state_manager.update_state(
                str(project_file),
                "applied",
                f"Application generated/regenerated via API",
                force=use_force
            )

            if not state_update_success:
                logger.warning(f"Failed to update state for project {project_id} after generation")

            # Parse the updated project data
            project_data = parse_project_file(str(project_file))
            response = ProjectResponse(**project_data)


            return jsonify({
                "success": True,
                "message": f"Application generated successfully for project {project_id}",
                "project": response.model_dump(),
                "output": result.stdout,
                "timestamp": datetime.now().isoformat()
            })
        else:
            logger.error(f"❌ Application generation failed for project {project_id}")
            return jsonify(APIErrorResponse(
                error="GenerationError",
                message=f"Application generation failed: {result.stderr}",
                code=500,
                details={"stdout": result.stdout, "stderr": result.stderr, "return_code": result.returncode},
                timestamp=datetime.now().isoformat()
            ).model_dump()), 500

    except subprocess.TimeoutExpired:
        logger.error(f"⏰ Application generation timed out for project {project_id}")
        return jsonify(APIErrorResponse(
            error="TimeoutError",
            message="Application generation timed out after 5 minutes",
            code=408,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 408
    except Exception as e:
        logger.error(f"💥 Unexpected error generating application for project {project_id}: {e}")
        logger.error(f"🔍 Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"📋 Full traceback: {traceback.format_exc()}")
        return jsonify(APIErrorResponse(
            error="InternalServerError",
            message=f"Failed to generate application: {str(e)}",
            code=500,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 500

@app.route('/api/v1/projects/<project_id>/markdown', methods=['GET'])
@handle_api_errors
def get_project_markdown(project_id: str):
    """Get raw markdown content of project file"""
    projects_dir = Path("projects")
    project_file = projects_dir / f"{project_id}.md"

    if not project_file.exists():
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Project {project_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    try:
        # Read the raw markdown content
        with open(project_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # Get file metadata
        stat = project_file.stat()
        last_modified = datetime.fromtimestamp(stat.st_mtime).isoformat()

        response = MarkdownContentResponse(
            content=content,
            filename=project_file.name,
            last_modified=last_modified,
            file_size=stat.st_size
        )

        return jsonify(response.model_dump())

    except Exception as e:
        logger.error(f"Error reading markdown file {project_id}: {e}")
        return jsonify(APIErrorResponse(
            error="ReadError",
            message=f"Failed to read markdown file: {str(e)}",
            code=500,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 500

@app.route('/api/v1/projects/<project_id>/markdown', methods=['PUT'])
@handle_api_errors
def update_project_markdown(project_id: str):
    """Update markdown content of project file"""
    data = request.get_json()
    if not data:
        raise ValidationError("No JSON data provided")

    update_request = MarkdownUpdateRequest(**data)

    projects_dir = Path("projects")
    project_file = projects_dir / f"{project_id}.md"

    if not project_file.exists():
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Project {project_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    try:
        # Create backup before updating
        backup_file = project_file.with_suffix('.md.backup')
        shutil.copy2(project_file, backup_file)

        # Write the new content
        with open(project_file, 'w', encoding='utf-8') as f:
            f.write(update_request.content)

        # Get updated file metadata
        stat = project_file.stat()
        last_modified = datetime.fromtimestamp(stat.st_mtime).isoformat()

        return jsonify({
            "success": True,
            "message": f"Markdown file {project_id} updated successfully",
            "last_modified": last_modified,
            "file_size": stat.st_size,
            "timestamp": datetime.now().isoformat()
        })

    except Exception as e:
        logger.error(f"Error updating markdown file {project_id}: {e}")
        # Try to restore backup if it exists
        if backup_file.exists():
            try:
                shutil.copy2(backup_file, project_file)
                logger.info(f"Restored backup for {project_id} after failed update")
            except Exception as restore_error:
                logger.error(f"Failed to restore backup for {project_id}: {restore_error}")

        return jsonify(APIErrorResponse(
            error="WriteError",
            message=f"Failed to update markdown file: {str(e)}",
            code=500,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 500

@app.route('/api/v1/projects', methods=['POST'])
@handle_api_errors
def create_manual_project():
    """Create a new manual project with template"""
    data = request.get_json()
    if not data:
        raise ValidationError("No JSON data provided")

    create_request = CreateManualProjectRequest(**data)

    try:
        # Ensure projects directory exists
        projects_dir = Path("projects")
        projects_dir.mkdir(exist_ok=True)

        # Generate timestamp-based filename
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        project_id = f"{timestamp}_manual_project"

        # Create project file
        project_file = projects_dir / f"{project_id}.md"

        # Generate template content
        template_content = generate_manual_project_template(
            project_id=project_id,
            title=create_request.title,
            company=create_request.company,
            description=create_request.description,
            scraped_date=now.isoformat()
        )

        # Write the template to file
        with open(project_file, 'w', encoding='utf-8') as f:
            f.write(template_content)

        # Parse the created project to return its data
        project_data = parse_project_file(str(project_file))
        response = ProjectResponse(**project_data)

        logger.info(f"✅ Manual project created: {project_id}")

        return jsonify({
            "success": True,
            "message": f"Manual project '{create_request.title}' created successfully",
            "project": response.model_dump(),
            "project_id": project_id,
            "timestamp": datetime.now().isoformat()
        }), 201

    except Exception as e:
        logger.error(f"Error creating manual project: {e}")
        return jsonify(APIErrorResponse(
            error="CreationError",
            message=f"Failed to create manual project: {str(e)}",
            code=500,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 500

@app.route('/api/v1/dashboard/stats', methods=['GET'])
@handle_api_errors
def get_dashboard_stats():
    """Get dashboard statistics"""
    try:
        projects_dir = Path("projects")
        if not projects_dir.exists():
            return jsonify(DashboardStats(
                total_projects=0,
                by_status={},
                recent_activity=[],
                last_updated=datetime.now().isoformat()
            ).model_dump())

        all_projects = []
        for project_file in projects_dir.glob("*.md"):
            project_data = parse_project_file(str(project_file))
            all_projects.append(project_data)

        # Calculate statistics
        status_counts = {}
        recent_activity = []

        for project in all_projects:
            status = project.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1

            # Get recent state changes
            state_history = project.get("state_history", [])
            for change in state_history[-3:]:  # Last 3 changes per project
                recent_activity.append({
                    "project_id": project["id"],
                    "project_title": project["title"],
                    "state": change.get("state"),
                    "timestamp": change.get("timestamp"),
                    "note": change.get("note")
                })

        # Sort recent activity by timestamp
        recent_activity.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        recent_activity = recent_activity[:10]  # Top 10 recent activities

        response = DashboardStats(
            total_projects=len(all_projects),
            by_status=status_counts,
            recent_activity=recent_activity,
            last_updated=datetime.now().isoformat()
        )

        return jsonify(response.model_dump())

    except Exception as e:
        logger.error(f"Error in get_dashboard_stats: {e}")
        raise



@app.route('/api/v1/health', methods=['GET'])
def health_check():
    """Enhanced health check"""
    return jsonify({
        "status": "healthy",
        "version": "2.0.0",
        "api_version": "v1",
        "timestamp": datetime.now().isoformat(),
        "features": [
            "project_management",
            "state_transitions",
            "dashboard_analytics",
            "workflow_execution"
        ]
    })

# API Endpoints for Quick Filters

@app.route('/api/v1/quick-filters', methods=['GET'])
@handle_api_errors
def get_quick_filters():
    """Get all quick filters"""
    filters = load_quick_filters()
    return jsonify(filters.model_dump())

@app.route('/api/v1/quick-filters', methods=['POST'])
@handle_api_errors
def create_quick_filter():
    """Create a new quick filter"""
    data = request.get_json()
    if not data:
        raise ValidationError("No JSON data provided")

    create_request = QuickFilterCreateRequest(**data)

    filters = load_quick_filters()

    new_filter = QuickFilterItem(
        id=str(uuid.uuid4()),
        name=create_request.name,
        description=create_request.description,
        filters=create_request.filters,
        isDynamic=create_request.isDynamic or False,
        originalRange=create_request.originalRange,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat()
    )

    filters.filters.append(new_filter)
    save_quick_filters(filters)

    return jsonify(new_filter.model_dump()), 201

@app.route('/api/v1/quick-filters/<filter_id>', methods=['PUT'])
@handle_api_errors
def update_quick_filter(filter_id: str):
    """Update an existing quick filter"""
    data = request.get_json()
    if not data:
        raise ValidationError("No JSON data provided")

    update_request = QuickFilterUpdateRequest(**data)

    filters = load_quick_filters()

    filter_to_update = None
    for f in filters.filters:
        if f.id == filter_id:
            filter_to_update = f
            break

    if not filter_to_update:
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Quick filter with id {filter_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    if update_request.name is not None:
        filter_to_update.name = update_request.name
    if update_request.description is not None:
        filter_to_update.description = update_request.description
    if update_request.filters is not None:
        filter_to_update.filters = update_request.filters
    if update_request.isDynamic is not None:
        filter_to_update.isDynamic = update_request.isDynamic
    if update_request.originalRange is not None:
        filter_to_update.originalRange = update_request.originalRange

    filter_to_update.updated_at = datetime.now().isoformat()

    save_quick_filters(filters)

    return jsonify(filter_to_update.model_dump())

@app.route('/api/v1/quick-filters/<filter_id>', methods=['DELETE'])
@handle_api_errors
def delete_quick_filter(filter_id: str):
    """Delete a quick filter"""
    filters = load_quick_filters()

    initial_len = len(filters.filters)
    filters.filters = [f for f in filters.filters if f.id != filter_id]

    if len(filters.filters) == initial_len:
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Quick filter with id {filter_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    save_quick_filters(filters)

    return jsonify({"success": True, "message": f"Quick filter {filter_id} deleted"}), 200

# API Endpoints for Scheduling

@app.route('/api/v1/schedules', methods=['GET'])
@handle_api_errors
def get_schedules():
    """Get all schedules"""
    schedules = scheduler_manager.list_schedules()

    # Convert to response format with next run times
    response_schedules = []
    for schedule in schedules:
        # Get next run time from APScheduler
        next_run = None
        if schedule.enabled:
            job_id = f"job_{schedule.id}"
            job = scheduler_manager.scheduler.get_job(job_id)
            if job and job.next_run_time:
                next_run = job.next_run_time.isoformat()

        schedule_dict = {
            **asdict(schedule),
            'next_run': next_run
        }
        response_schedules.append(schedule_dict)

    return jsonify(response_schedules)

@app.route('/api/v1/schedules', methods=['POST'])
@handle_api_errors
def create_schedule():
    """Create a new schedule"""
    data = request.get_json()
    if not data:
        raise ValidationError("No JSON data provided")

    create_request = ScheduleCreateRequest(**data)

    schedule = scheduler_manager.create_schedule(
        name=create_request.name,
        description=create_request.description,
        workflow_type=create_request.workflow_type,
        cli_commands=create_request.cli_commands,
        cron_schedule=create_request.cron_schedule,
        timezone=create_request.timezone,
        metadata=create_request.metadata
    )

    # Get next run time
    next_run = None
    if schedule.enabled:
        job_id = f"job_{schedule.id}"
        job = scheduler_manager.scheduler.get_job(job_id)
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()

    response = ScheduleResponse(
        **asdict(schedule),
        next_run=next_run
    )

    return jsonify(response.model_dump()), 201

@app.route('/api/v1/schedules/<schedule_id>', methods=['PUT'])
@handle_api_errors
def update_schedule(schedule_id: str):
    """Update an existing schedule"""
    data = request.get_json()
    if not data:
        raise ValidationError("No JSON data provided")

    update_request = ScheduleUpdateRequest(**data)

    # Build update dict
    updates = {}
    if update_request.name is not None:
        updates['name'] = update_request.name
    if update_request.description is not None:
        updates['description'] = update_request.description
    if update_request.workflow_type is not None:
        updates['workflow_type'] = update_request.workflow_type
    if update_request.cli_commands is not None:
        updates['cli_commands'] = update_request.cli_commands
    if update_request.cron_schedule is not None:
        updates['cron_schedule'] = update_request.cron_schedule
    if update_request.timezone is not None:
        updates['timezone'] = update_request.timezone

    schedule = scheduler_manager.update_schedule(schedule_id, **updates)

    if not schedule:
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Schedule {schedule_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    # Get next run time
    next_run = None
    if schedule.enabled:
        job_id = f"job_{schedule.id}"
        job = scheduler_manager.scheduler.get_job(job_id)
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()

    response = ScheduleResponse(
        **asdict(schedule),
        next_run=next_run
    )

    return jsonify(response.model_dump())

@app.route('/api/v1/schedules/<schedule_id>', methods=['DELETE'])
@handle_api_errors
def delete_schedule(schedule_id: str):
    """Delete a schedule"""
    success = scheduler_manager.delete_schedule(schedule_id)

    if not success:
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Schedule {schedule_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    return jsonify({"success": True, "message": f"Schedule {schedule_id} deleted"}), 200

@app.route('/api/v1/schedules/<schedule_id>/toggle', methods=['POST'])
@handle_api_errors
def toggle_schedule(schedule_id: str):
    """Enable/disable a schedule"""
    schedule = scheduler_manager.toggle_schedule(schedule_id)

    if not schedule:
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Schedule {schedule_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    # Get next run time
    next_run = None
    if schedule.enabled:
        job_id = f"job_{schedule.id}"
        job = scheduler_manager.scheduler.get_job(job_id)
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()

    response = ScheduleResponse(
        **asdict(schedule),
        next_run=next_run
    )

    return jsonify(response.model_dump())

@app.route('/api/v1/schedules/<schedule_id>/run', methods=['POST'])
@handle_api_errors
def run_schedule_now(schedule_id: str):
    """Run a schedule immediately"""
    message = scheduler_manager.run_schedule_now(schedule_id)

    if message is None:
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Schedule {schedule_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    return jsonify({
        "success": True,
        "message": message,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/api/v1/schedules/<schedule_id>/runs', methods=['GET'])
@handle_api_errors
def get_schedule_runs(schedule_id: str):
    """Get execution history for a schedule"""
    schedule = scheduler_manager.get_schedule(schedule_id)

    if not schedule:
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Schedule {schedule_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    # Convert execution history to response format
    history = []
    for result in schedule.execution_history:
        history.append({
            "run_id": result.run_id,
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "status": result.status,
            "output": result.output,
            "error": result.error,
            "exit_code": result.exit_code
        })

    return jsonify(history)

@app.route('/api/v1/schedules/<schedule_id>/runs/<run_id>', methods=['GET'])
@handle_api_errors
def get_schedule_run(schedule_id: str, run_id: str):
    """Get detailed information for a specific execution run"""
    schedule = scheduler_manager.get_schedule(schedule_id)

    if not schedule:
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Schedule {schedule_id} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    # Find the specific run
    target_run = None
    for result in schedule.execution_history:
        if result.run_id == run_id:
            target_run = result
            break

    if not target_run:
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Run {run_id} not found for schedule {schedule_id}",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    # Convert to detailed response format
    run_details = {
        "run_id": target_run.run_id,
        "started_at": target_run.started_at,
        "completed_at": target_run.completed_at,
        "status": target_run.status,
        "output": target_run.output,
        "error": target_run.error,
        "exit_code": target_run.exit_code,
        "step_results": target_run.step_results if hasattr(target_run, 'step_results') else []
    }

    return jsonify(run_details)

@app.route('/api/v1/schedules/status', methods=['GET'])
@handle_api_errors
def get_scheduler_status():
    """Get overall scheduler status"""
    status = scheduler_manager.get_scheduler_status()

    # Get next job information
    next_job = None
    if status.get('jobs'):
        # Find the job with the earliest next run time
        earliest_job = min(
            (job for job in status['jobs'] if job.get('next_run_time')),
            key=lambda x: x['next_run_time'],
            default=None
        )
        if earliest_job:
            next_job = {
                'job_id': earliest_job['id'],
                'name': earliest_job['name'],
                'next_run_time': earliest_job['next_run_time']
            }

    response = SchedulerStatusResponse(
        running=status['running'],
        total_schedules=status['total_schedules'],
        enabled_schedules=status['enabled_schedules'],
        active_jobs=status['active_jobs'],
        next_job=next_job,
        timestamp=datetime.now().isoformat()
    )

    return jsonify(response.model_dump())

# New API Endpoints for Enhanced Workflow System

@app.route('/api/v1/workflows/validate', methods=['POST'])
@handle_api_errors
def validate_workflow_config():
    """Validate workflow configuration"""
    data = request.get_json()
    if not data:
        raise ValidationError("No JSON data provided")

    validation_result = scheduler_manager.validate_workflow_config(data)
    
    return jsonify({
        'valid': validation_result.valid,
        'errors': validation_result.errors,
        'warnings': validation_result.warnings,
        'success_messages': validation_result.success_messages,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/v1/workflows/examples', methods=['GET'])
@handle_api_errors
def get_workflow_examples():
    """Get workflow configuration examples"""
    examples = scheduler_manager.get_workflow_examples()
    return jsonify(examples)

@app.route('/api/v1/workflows/named', methods=['GET'])
@handle_api_errors
def get_named_workflows():
    """Get workflows configured for dashboard buttons"""
    named_workflows = scheduler_manager.get_named_workflows()
    return jsonify(named_workflows)

@app.route('/api/v1/workflows/commands/validate', methods=['POST'])
@handle_api_errors
def validate_cli_command():
    """Validate a single CLI command"""
    data = request.get_json()
    if not data or 'command' not in data:
        raise ValidationError("Command is required")

    command = data['command']
    context = data.get('context', {})
    
    validation_result = scheduler_manager.validate_cli_command(command, context)
    
    return jsonify({
        'valid': validation_result.valid,
        'errors': validation_result.errors,
        'warnings': validation_result.warnings,
        'success_messages': validation_result.success_messages,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/v1/workflows/providers/status', methods=['GET'])
@handle_api_errors
def get_provider_status():
    """Get status of all providers (enabled/disabled)"""
    try:
        import yaml
        
        # Load config
        with open('config.yaml', 'r') as f:
            config = yaml.safe_load(f)

        provider_status = {}
        providers = config.get('providers', {})
        
        for provider_id, provider_config in providers.items():
            enabled = provider_config.get('enabled', False)
            channels = list(provider_config.get('channels', {}).keys())
            
            provider_status[provider_id] = {
                'enabled': enabled,
                'channels': channels,
                'has_email': 'email' in channels,
                'has_rss': 'rss' in channels
            }

        return jsonify({
            'providers': provider_status,
            'timestamp': datetime.now().isoformat()
        })

    except Exception as e:
        logger.error(f"Error getting provider status: {e}")
        return jsonify(APIErrorResponse(
            error="InternalServerError",
            message=f"Failed to get provider status: {str(e)}",
            code=500,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 500

# Enhanced schedule creation endpoint for CLI sequences
@app.route('/api/v1/schedules/cli', methods=['POST'])
@handle_api_errors
def create_cli_schedule():
    """Create a new CLI sequence schedule"""
    data = request.get_json()
    if not data:
        raise ValidationError("No JSON data provided")

    # Validate required fields
    required_fields = ['name', 'description', 'cli_commands']
    for field in required_fields:
        if field not in data:
            return jsonify(APIErrorResponse(
                error="ValidationError",
                message=f"Missing required field: {field}",
                code=400,
                timestamp=datetime.now().isoformat()
            ).model_dump()), 400

    # Validate the workflow configuration
    validation_result = scheduler_manager.validate_workflow_config(data)
    if not validation_result.valid:
        return jsonify(APIErrorResponse(
            error="ValidationError",
            message="Workflow configuration is invalid",
            code=400,
            details={
                'errors': validation_result.errors,
                'warnings': validation_result.warnings
            },
            timestamp=datetime.now().isoformat()
        ).model_dump()), 400

    try:
        schedule = scheduler_manager.create_schedule(
            name=data['name'],
            description=data['description'],
            workflow_type='cli_sequence',
            cli_commands=data['cli_commands'],
            cron_schedule=data.get('cron_schedule', '0 9 * * 1-5'),
            timezone=data.get('timezone', 'Europe/Berlin'),
            metadata=data.get('metadata', {})
        )

        # Get next run time
        next_run = None
        if schedule.enabled:
            job_id = f"job_{schedule.id}"
            job = scheduler_manager.scheduler.get_job(job_id)
            if job and job.next_run_time:
                next_run = job.next_run_time.isoformat()

        response_dict = asdict(schedule)
        response_dict['next_run'] = next_run

        return jsonify(response_dict), 201

    except Exception as e:
        logger.error(f"Error creating CLI schedule: {e}")
        return jsonify(APIErrorResponse(
            error="CreationError",
            message=f"Failed to create CLI schedule: {str(e)}",
            code=500,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 500

# API Endpoints for Config Filters

@app.route('/api/v1/config/filters', methods=['GET'])
@handle_api_errors
def get_config_filters():
    """Get available providers and channels from configuration"""
    try:
        import yaml

        # Load config
        with open('config.yaml', 'r') as f:
            config = yaml.safe_load(f)

        providers = []
        channels = set()

        # Extract enabled providers and their channels
        if 'providers' in config:
            for provider_id, provider_config in config['providers'].items():
                if provider_config.get('enabled', False):
                    providers.append(provider_id)
                    # Extract channels for this provider
                    if 'channels' in provider_config:
                        channels.update(provider_config['channels'].keys())

        # Add manual provider and channel for manually created projects
        if "manual" not in providers:
            providers.append("manual")
        if "manual" not in channels:
            channels.add("manual")

        # Sort for consistent ordering
        providers.sort()
        channels_list = sorted(list(channels))

        return jsonify({
            "providers": providers,
            "channels": channels_list
        })

    except FileNotFoundError:
        return jsonify(APIErrorResponse(
            error="ConfigNotFound",
            message="config.yaml file not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404
    except yaml.YAMLError as e:
        return jsonify(APIErrorResponse(
            error="ConfigParseError",
            message=f"Failed to parse config.yaml: {str(e)}",
            code=500,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 500
    except Exception as e:
        logger.error(f"Error getting config filters: {e}")
        return jsonify(APIErrorResponse(
            error="InternalServerError",
            message=f"Failed to get config filters: {str(e)}",
            code=500,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 500

# API Endpoints for Log Viewer

@app.route('/api/v1/logs', methods=['GET'])
@handle_api_errors
def get_log_files():
    """List all log files in /app/logs directory"""
    logs_dir = Path("logs")

    if not logs_dir.exists():
        return jsonify(LogFilesResponse(files=[]).model_dump())

    log_files = []

    # Get all .log files
    for log_file in logs_dir.glob("*.log*"):
        if log_file.is_file():
            try:
                stat = log_file.stat()
                file_info = LogFileInfo(
                    name=log_file.name,
                    size=stat.st_size,
                    modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    is_current=log_file.name == "app.log"
                )
                log_files.append(file_info)
            except Exception as e:
                logger.warning(f"Error getting info for log file {log_file}: {e}")

    # Sort files: current log first, then by modification time (newest first)
    log_files.sort(key=lambda x: (not x.is_current, x.modified), reverse=True)

    response = LogFilesResponse(files=log_files)
    return jsonify(response.model_dump())

@app.route('/api/v1/logs/<filename>', methods=['GET'])
@handle_api_errors
def get_log_file(filename: str):
    """Serve specific log file content"""
    logs_dir = Path("logs")
    log_file = logs_dir / filename

    # Security check: ensure filename doesn't contain path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify(APIErrorResponse(
            error="InvalidFilename",
            message="Invalid filename",
            code=400,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 400

    if not log_file.exists() or not log_file.is_file():
        return jsonify(APIErrorResponse(
            error="NotFound",
            message=f"Log file {filename} not found",
            code=404,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 404

    try:
        # Serve the file as plain text
        return send_file(
            str(log_file),
            mimetype='text/plain',
            as_attachment=False,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Error serving log file {filename}: {e}")
        return jsonify(APIErrorResponse(
            error="ReadError",
            message=f"Failed to read log file: {str(e)}",
            code=500,
            timestamp=datetime.now().isoformat()
        ).model_dump()), 500

# Legacy routes for backward compatibility
@app.route('/')
def dashboard():
    """Serve the Vue3 frontend"""
    return send_from_directory('frontend/dist', 'index.html')

# Editor route removed - now handled by frontend server with query parameters

@app.route('/test')
def test_route():
    """Test route"""
    logger.info("🎯 TEST ROUTE: Test route called")
    print("🎯 TEST ROUTE: Test route called")
    return "Test route working"

@app.route('/<path:filename>')
def serve_frontend(filename):
    """Serve Vue3 frontend files"""
    logger.info(f"🎯 CATCH-ALL ROUTE: Serving frontend file: {filename}")
    print(f"🎯 CATCH-ALL ROUTE: Serving frontend file: {filename}")

    # First try to serve the requested file
    try:
        return send_from_directory('frontend/dist', filename)
    except:
        # If the file doesn't exist, serve index.html for SPA routing (unless it's an API call)
        if not filename.startswith('api/'):
            logger.info(f"🔄 Serving index.html for SPA route: {filename}")
            print(f"🔄 Serving index.html for SPA route: {filename}")
            return send_from_directory('frontend/dist', 'index.html')
        else:
            # For API calls that don't exist, return 404
            abort(404)

# Flask app lifecycle management for scheduler
def startup():
    """Initialize scheduler on startup"""
    try:
        scheduler_manager.start()
        logger.info("Scheduler started successfully")
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")

import atexit

@atexit.register
def shutdown():
    """Shutdown scheduler on app exit"""
    try:
        scheduler_manager.stop()
        logger.info("Scheduler stopped successfully")
    except Exception as e:
        logger.error(f"Error stopping scheduler: {e}")

if __name__ == '__main__':
    import os
    logger.info(f"🚀 Server starting with PID: {os.getpid()} at {datetime.now().isoformat()}")
    print("🚀 Starting Enhanced Flask Backend for Vue3 Frontend...")
    # SECURITY (Phase 0): Bind to 127.0.0.1 (loopback only) instead of 0.0.0.0.
    # debug=True is disabled to prevent the Werkzeug interactive debugger from being
    # exposed; it allows arbitrary code execution if reachable from the network.
    # TODO Phase 9: Add authentication before changing host binding.
    _host = os.environ.get('FLASK_HOST', '127.0.0.1')
    _port = int(os.environ.get('FLASK_PORT', '8002'))
    _debug = os.environ.get('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes')

    print(f"📊 API: http://{_host}:{_port}/api/v1/")
    print(f"🔧 Health: http://{_host}:{_port}/api/v1/health")
    print(f"🌐 Frontend: http://{_host}:{_port}/ (when built)")
    print(f"🔒 Binding to {_host} (loopback only by default – set FLASK_HOST=0.0.0.0 to expose)")
    print("📁 Projects Directory: projects/")
    print("🔄 State Management: Enhanced")
    print("📋 API Version: v1")
    print("⏰ Scheduler: Integrated")

    # Start scheduler
    startup()

    app.run(
        debug=_debug,
        host=_host,
        port=_port,
        threaded=True,
        use_reloader=False
    )