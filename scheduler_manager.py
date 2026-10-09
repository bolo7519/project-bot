#!/usr/bin/env python3
"""
Scheduler Manager for Bewerbungs-Bot
APScheduler integration for automated workflow execution
"""

import os
import json
import uuid
import subprocess
import logging
import sys
import shlex
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from threading import Lock

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from pytz import timezone

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
SCHEDULES_FILE = Path("data/schedules.json")
DEFAULT_TIMEZONE = timezone('Europe/Berlin')
JOB_TIMEOUT_SECONDS = 1800  # 30 minutes timeout

@dataclass
class CommandStep:
    """Individual command step in a CLI sequence"""
    command: str
    name: str
    description: Optional[str] = None
    timeout: int = 300
    continue_on_error: bool = False
    retry_count: int = 0
    environment: Optional[Dict[str, str]] = None

@dataclass
class ExecutionResult:
    """Result of a scheduled job execution"""
    run_id: str
    started_at: str
    completed_at: Optional[str] = None
    status: str = "running"  # running, success, failed, timeout
    output: Optional[str] = None
    error: Optional[str] = None
    exit_code: Optional[int] = None
    step_results: Optional[List[Dict[str, Any]]] = None

    def __post_init__(self):
        if self.step_results is None:
            self.step_results = []

@dataclass
class ValidationResult:
    """Result of workflow validation"""
    valid: bool = True
    errors: List[str] = None
    warnings: List[str] = None
    success_messages: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
        if self.success_messages is None:
            self.success_messages = []

    def add_error(self, message: str):
        self.errors.append(message)
        self.valid = False

    def add_warning(self, message: str):
        self.warnings.append(message)

    def add_success(self, message: str):
        self.success_messages.append(message)

@dataclass
class Schedule:
    """Schedule configuration"""
    id: str
    name: str
    description: str
    enabled: bool
    workflow_type: str  # cli_sequence
    cli_commands: List[CommandStep] = None  # CLI command sequence
    cron_schedule: str = "0 9 * * 1-5"
    timezone: str = "Europe/Berlin"
    created_at: str = ""
    updated_at: str = ""
    last_run: Optional[str] = None
    last_status: Optional[str] = None
    execution_history: List[ExecutionResult] = None
    validation_status: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.execution_history is None:
            self.execution_history = []
        if self.cli_commands is None:
            self.cli_commands = []
        if self.validation_status is None:
            self.validation_status = {
                'last_validated': None,
                'is_valid': True,
                'errors': [],
                'warnings': []
            }
        if self.metadata is None:
            self.metadata = {}
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = datetime.now().isoformat()

class SchedulerManager:
    """Manages scheduled jobs using APScheduler"""

    _instance = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return

        self._initialized = True
        self.schedules: Dict[str, Schedule] = {}
        self.scheduler = None
        self._setup_scheduler()
        self._load_schedules()

    def _setup_scheduler(self):
        """Initialize APScheduler with proper configuration"""
        jobstores = {
            'default': MemoryJobStore()
        }
        executors = {
            'default': {'type': 'threadpool', 'max_workers': 2}
        }
        job_defaults = {
            'coalesce': True,
            'max_instances': 1,
            'misfire_grace_time': 30
        }

        self.scheduler = BackgroundScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone=DEFAULT_TIMEZONE
        )

        logger.info("Scheduler initialized")

    def start(self):
        """Start the scheduler"""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler started")

    def stop(self):
        """Stop the scheduler"""
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=True)
            logger.info("Scheduler stopped")

    def _load_schedules(self):
        """Load schedules from JSON file"""
        try:
            if SCHEDULES_FILE.exists():
                with open(SCHEDULES_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                for schedule_data in data.get('schedules', []):
                    try:
                        # Convert execution_history back to ExecutionResult objects
                        history = []
                        for hist_data in schedule_data.get('execution_history', []):
                            history.append(ExecutionResult(**hist_data))

                        schedule_data['execution_history'] = history
                        
                        # Convert cli_commands back to CommandStep objects
                        cli_commands = []
                        for cmd_data in schedule_data.get('cli_commands', []):
                            cli_commands.append(CommandStep(**cmd_data))
                        schedule_data['cli_commands'] = cli_commands

                        schedule = Schedule(**schedule_data)
                        self.schedules[schedule.id] = schedule

                        # Add to scheduler if enabled
                        if schedule.enabled:
                            self._add_schedule_to_scheduler(schedule)

                    except Exception as e:
                        logger.error(f"Error loading schedule {schedule_data.get('id', 'unknown')}: {e}")

                logger.info(f"Loaded {len(self.schedules)} schedules")

        except Exception as e:
            logger.error(f"Error loading schedules file: {e}")
            # Create empty schedules file if it doesn't exist or is corrupted
            self._save_schedules()

    def _save_schedules(self):
        """Save schedules to JSON file"""
        try:
            SCHEDULES_FILE.parent.mkdir(exist_ok=True)

            # Convert schedules to dict format
            schedules_data = []
            for schedule in self.schedules.values():
                schedule_dict = asdict(schedule)
                # Convert ExecutionResult objects to dicts
                schedule_dict['execution_history'] = [
                    asdict(result) for result in schedule.execution_history
                ]
                # Convert CommandStep objects to dicts
                schedule_dict['cli_commands'] = [
                    asdict(cmd) for cmd in schedule.cli_commands
                ]
                schedules_data.append(schedule_dict)

            data = {
                'schedules': schedules_data,
                'last_updated': datetime.now().isoformat()
            }

            with open(SCHEDULES_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.debug(f"Saved {len(schedules_data)} schedules")

        except Exception as e:
            logger.error(f"Error saving schedules: {e}")

    def create_schedule(self, name: str, description: str, workflow_type: str,
                        cli_commands: List[Dict[str, Any]] = None,
                        cron_schedule: str = "0 9 * * 1-5", timezone: str = "Europe/Berlin",
                        metadata: Dict[str, Any] = None) -> Schedule:
        """Create a new schedule"""
        schedule_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        # Convert cli_commands dicts to CommandStep objects
        command_steps = []
        if cli_commands:
            for cmd_data in cli_commands:
                command_steps.append(CommandStep(**cmd_data))

        schedule = Schedule(
            id=schedule_id,
            name=name,
            description=description,
            enabled=True,
            workflow_type=workflow_type,
            cli_commands=command_steps,
            cron_schedule=cron_schedule,
            timezone=timezone,
            metadata=metadata or {},
            created_at=now,
            updated_at=now
        )

        self.schedules[schedule_id] = schedule
        self._add_schedule_to_scheduler(schedule)
        self._save_schedules()

        logger.info(f"Created schedule: {name} ({schedule_id})")
        return schedule

    def update_schedule(self, schedule_id: str, **updates) -> Optional[Schedule]:
        """Update an existing schedule"""
        if schedule_id not in self.schedules:
            return None

        schedule = self.schedules[schedule_id]

        # Handle CLI commands update
        if 'cli_commands' in updates:
            cli_commands = updates['cli_commands']
            command_steps = []
            for cmd_data in cli_commands:
                if isinstance(cmd_data, dict):
                    command_steps.append(CommandStep(**cmd_data))
                elif isinstance(cmd_data, CommandStep):
                    command_steps.append(cmd_data)
            updates['cli_commands'] = command_steps

        # Update fields
        for key, value in updates.items():
            if hasattr(schedule, key):
                setattr(schedule, key, value)

        schedule.updated_at = datetime.now().isoformat()

        # Remove from scheduler and re-add if enabled
        self._remove_schedule_from_scheduler(schedule_id)
        if schedule.enabled:
            self._add_schedule_to_scheduler(schedule)

        self._save_schedules()
        logger.info(f"Updated schedule: {schedule.name} ({schedule_id})")
        return schedule

    def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a schedule"""
        if schedule_id not in self.schedules:
            return False

        schedule = self.schedules[schedule_id]

        # Remove from scheduler
        self._remove_schedule_from_scheduler(schedule_id)

        # Remove from storage
        del self.schedules[schedule_id]
        self._save_schedules()

        logger.info(f"Deleted schedule: {schedule.name} ({schedule_id})")
        return True

    def toggle_schedule(self, schedule_id: str) -> Optional[Schedule]:
        """Enable/disable a schedule"""
        if schedule_id not in self.schedules:
            return None

        schedule = self.schedules[schedule_id]
        schedule.enabled = not schedule.enabled
        schedule.updated_at = datetime.now().isoformat()

        if schedule.enabled:
            self._add_schedule_to_scheduler(schedule)
        else:
            self._remove_schedule_from_scheduler(schedule_id)

        self._save_schedules()
        logger.info(f"Toggled schedule: {schedule.name} ({schedule_id}) -> {'enabled' if schedule.enabled else 'disabled'}")
        return schedule

    def run_schedule_now(self, schedule_id: str) -> Optional[str]:
        """Run a schedule immediately"""
        if schedule_id not in self.schedules:
            return None

        schedule = self.schedules[schedule_id]

        # Execute the workflow directly in a separate thread to avoid blocking
        import threading
        thread = threading.Thread(target=self._execute_workflow, args=(schedule_id,))
        thread.daemon = True
        thread.start()

        logger.info(f"Started immediate execution for schedule: {schedule.name}")
        return f"Started execution for {schedule.name}"

    def get_schedule(self, schedule_id: str) -> Optional[Schedule]:
        """Get a specific schedule"""
        return self.schedules.get(schedule_id)

    def list_schedules(self) -> List[Schedule]:
        """Get all schedules"""
        return list(self.schedules.values())

    def get_scheduler_status(self) -> Dict[str, Any]:
        """Get scheduler status information"""
        jobs = []
        if self.scheduler:
            for job in self.scheduler.get_jobs():
                jobs.append({
                    'id': job.id,
                    'name': job.name,
                    'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
                    'trigger': str(job.trigger)
                })

        return {
            'running': self.scheduler.running if self.scheduler else False,
            'total_schedules': len(self.schedules),
            'enabled_schedules': len([s for s in self.schedules.values() if s.enabled]),
            'active_jobs': len(jobs),
            'jobs': jobs
        }

    def _add_schedule_to_scheduler(self, schedule: Schedule):
        """Add a schedule to the APScheduler"""
        try:
            job_id = f"job_{schedule.id}"

            # Remove existing job if it exists
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            # Create trigger
            # Strip stray surrounding quotes that may have been written to
            # schedules.json (e.g. "\"0 9 * * 1-5\"" → "0 9 * * 1-5")
            cron_expr = schedule.cron_schedule.strip().strip('"').strip("'")
            trigger = CronTrigger.from_crontab(
                cron_expr,
                timezone=timezone(schedule.timezone)
            )

            # Add job
            self.scheduler.add_job(
                func=self._execute_workflow,
                trigger=trigger,
                id=job_id,
                name=schedule.name,
                args=[schedule.id],
                max_instances=1
            )

            logger.debug(f"Added job to scheduler: {schedule.name} ({job_id})")

        except Exception as e:
            logger.error(f"Error adding schedule to scheduler: {schedule.name} - {e}")

    def _remove_schedule_from_scheduler(self, schedule_id: str):
        """Remove a schedule from the APScheduler"""
        try:
            job_id = f"job_{schedule_id}"
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                logger.debug(f"Removed job from scheduler: {job_id}")
        except Exception as e:
            logger.error(f"Error removing schedule from scheduler: {schedule_id} - {e}")

    def validate_cli_command(self, command: str, context: Dict[str, Any] = None) -> ValidationResult:
        """Validate a CLI command for security and correctness"""
        result = ValidationResult()
        context = context or {}

        try:
            # Parse command safely
            cmd_parts = shlex.split(command)
        except ValueError as e:
            result.add_error(f"Invalid shell syntax: {e}")
            return result

        if len(cmd_parts) < 2:
            result.add_error("Command must include script name")
            return result

        # Security: ensure command starts with python
        if cmd_parts[0] not in ['python', 'python3', sys.executable]:
            result.add_error("Commands must start with 'python' or 'python3'")
            return result

        # Validate script exists
        script_name = cmd_parts[1]
        script_path = Path(script_name)
        
        # Security: prevent path traversal
        try:
            script_path.resolve().relative_to(Path.cwd().resolve())
        except ValueError:
            result.add_error(f"Script path outside working directory: {script_name}")
            return result

        if not script_path.exists():
            result.add_error(f"Script not found: {script_name}")
            return result

        # Validate providers if specified
        if '--provider' in cmd_parts:
            provider_idx = cmd_parts.index('--provider') + 1
            if provider_idx < len(cmd_parts):
                provider = cmd_parts[provider_idx]
                if provider != 'all' and not self._is_provider_available(provider):
                    result.add_error(f"Provider '{provider}' is disabled or misconfigured")

        # Validate script-specific parameters
        self._validate_script_parameters(script_name, cmd_parts, result)

        if result.valid:
            result.add_success(f"Command validation passed for {script_name}")

        return result

    def validate_workflow_config(self, workflow_config: Dict[str, Any]) -> ValidationResult:
        """Validate complete workflow configuration"""
        result = ValidationResult()

        # Validate basic structure
        required_fields = ['name', 'workflow_type']
        for field in required_fields:
            if field not in workflow_config:
                result.add_error(f"Missing required field: {field}")

        workflow_type = workflow_config.get('workflow_type')

        # Validate workflow type - only cli_sequence is supported
        if workflow_type != 'cli_sequence':
            result.add_error("Invalid workflow_type. Only 'cli_sequence' is supported")

        # Validate CLI commands
        cli_commands = workflow_config.get('cli_commands', [])
        if not cli_commands:
            result.add_error("CLI sequence workflow must have at least one command")
        else:
            for i, cmd_config in enumerate(cli_commands):
                self._validate_command_step(cmd_config, i, result)

        # Validate cron schedule
        cron_schedule = workflow_config.get('cron_schedule')
        if cron_schedule:
            self._validate_cron_schedule(cron_schedule, result)

        # Validate timezone
        timezone_str = workflow_config.get('timezone', 'Europe/Berlin')
        try:
            from pytz import timezone
            timezone(timezone_str)
            result.add_success(f"Timezone '{timezone_str}' is valid")
        except Exception:
            result.add_error(f"Invalid timezone: {timezone_str}")

        return result

    def _validate_command_step(self, cmd_config: Dict[str, Any], index: int, result: ValidationResult):
        """Validate individual command step"""
        step_prefix = f"Command {index + 1}"

        # Check required fields
        if 'command' not in cmd_config:
            result.add_error(f"{step_prefix}: Missing 'command' field")
            return

        if 'name' not in cmd_config:
            result.add_warning(f"{step_prefix}: Missing 'name' field")

        # Validate command syntax and security
        command = cmd_config['command']
        cmd_result = self.validate_cli_command(command)
        
        for error in cmd_result.errors:
            result.add_error(f"{step_prefix}: {error}")
        for warning in cmd_result.warnings:
            result.add_warning(f"{step_prefix}: {warning}")

        # Validate timeout
        timeout = cmd_config.get('timeout', 300)
        if not isinstance(timeout, int) or timeout <= 0 or timeout > 3600:
            result.add_warning(f"{step_prefix}: Invalid timeout {timeout}, should be 1-3600 seconds")

    def _validate_cron_schedule(self, cron_expr: str, result: ValidationResult):
        """Validate cron schedule syntax"""
        try:
            from apscheduler.triggers.cron import CronTrigger
            # Strip stray surrounding quotes (same normalisation as _add_schedule_to_scheduler)
            normalized = cron_expr.strip().strip('"').strip("'")
            CronTrigger.from_crontab(normalized)
            result.add_success("Cron schedule syntax is valid")
        except Exception as e:
            result.add_error(f"Invalid cron schedule '{cron_expr}': {e}")

    def _is_provider_available(self, provider: str) -> bool:
        """Check if provider is enabled and properly configured"""
        try:
            from application_generator import load_application_config
            config = load_application_config("config.yaml")
            
            providers = config.get("providers", {})
            if provider not in providers:
                return False
                
            provider_config = providers[provider]
            if not provider_config.get("enabled", False):
                return False
                
            # Check if provider has at least one configured channel
            channels = provider_config.get("channels", {})
            return len(channels) > 0
            
        except Exception as e:
            logger.error(f"Error checking provider availability: {e}")
            return False

    def _validate_script_parameters(self, script_name: str, cmd_parts: List[str], result: ValidationResult):
        """Validate script-specific parameters"""
        # Get available scripts and their supported parameters
        supported_scripts = {
            'main.py': {
                'flags': ['--email-ingest', '--rss-ingest', '--full-workflow', '--generate-applications', 
                         '--no-applications', '--dry-run', '--no-purge'],
                'params': ['--provider', '--output-dir', '--application-threshold', '--cv-file', '--config']
            },
            'evaluate_projects.py': {
                'flags': ['--pre-eval-only', '--force-evaluation'],
                'params': ['--config', '--cv', '--project-file']
            },
            'file_purger.py': {
                'flags': ['--dry-run', '--force'],
                'params': ['--config', '--categories']
            }
        }

        if script_name not in supported_scripts:
            result.add_warning(f"Unknown script '{script_name}' - parameter validation skipped")
            return

        script_config = supported_scripts[script_name]
        
        # Check for unsupported parameters
        i = 2  # Skip 'python' and script name
        while i < len(cmd_parts):
            arg = cmd_parts[i]
            if arg.startswith('--'):
                if arg not in script_config['flags'] and arg not in script_config['params']:
                    result.add_warning(f"Unknown parameter '{arg}' for script '{script_name}'")
                
                # Skip value for parameter flags
                if arg in script_config['params'] and i + 1 < len(cmd_parts):
                    i += 1  # Skip the parameter value
            i += 1

    def get_named_workflows(self) -> List[Dict[str, Any]]:
        """Get workflows configured for dashboard buttons"""
        named_workflows = []
        
        for schedule in self.schedules.values():
            if (schedule.enabled and 
                schedule.metadata.get('dashboard_button', False)):
                
                named_workflows.append({
                    'id': schedule.id,
                    'name': schedule.name,
                    'description': schedule.description,
                    'icon': schedule.metadata.get('icon', '🔧'),
                    'priority': schedule.metadata.get('priority', 'normal'),
                    'category': schedule.metadata.get('category', 'general'),
                    'step_count': len(schedule.cli_commands) if schedule.workflow_type == 'cli_sequence' else 1,
                    'last_run': schedule.last_run,
                    'last_status': schedule.last_status
                })
        
        # Sort by priority (high -> normal -> low) then by name
        priority_order = {'high': 0, 'normal': 1, 'low': 2}
        named_workflows.sort(key=lambda w: (
            priority_order.get(w['priority'], 1),
            w['name'].lower()
        ))
        
        return named_workflows

    def get_workflow_examples(self) -> Dict[str, Any]:
        """Get workflow configuration examples"""
        return {
            'basic_examples': {
                'email_ingestion': {
                    'name': 'Email Ingestion',
                    'description': 'Fetch new projects from email notifications',
                    'workflow_type': 'cli_sequence',
                    'cli_commands': [
                        {
                            'command': 'python main.py --email-ingest --provider freelancermap',
                            'name': 'Email Ingestion',
                            'description': 'Process emails from freelancermap',
                            'timeout': 600
                        }
                    ],
                    'metadata': {
                        'dashboard_button': True,
                        'icon': '📧',
                        'priority': 'high'
                    },
                    'cron_schedule': '0 9-17 * * 1-5'
                },
                'rss_ingestion': {
                    'name': 'RSS Ingestion',
                    'description': 'Fetch new projects from RSS feeds',
                    'workflow_type': 'cli_sequence',
                    'cli_commands': [
                        {
                            'command': 'python main.py --rss-ingest --provider freelancermap',
                            'name': 'RSS Ingestion',
                            'description': 'Process RSS feeds from freelancermap',
                            'timeout': 300
                        }
                    ],
                    'metadata': {
                        'dashboard_button': True,
                        'icon': '📰',
                        'priority': 'high'
                    },
                    'cron_schedule': '0 9,12,15,18 * * 1-5'
                },
                'evaluation_only': {
                    'name': 'Project Evaluation',
                    'description': 'Evaluate scraped projects without ingestion',
                    'workflow_type': 'cli_sequence',
                    'cli_commands': [
                        {
                            'command': 'python evaluate_projects.py',
                            'name': 'Full Evaluation',
                            'description': 'Run complete project evaluation',
                            'timeout': 1200
                        }
                    ],
                    'metadata': {
                        'dashboard_button': True,
                        'icon': '📊',
                        'priority': 'normal'
                    },
                    'cron_schedule': '0 10 * * 1-5'
                }
            },
            'advanced_examples': {
                'full_pipeline': {
                    'name': 'Complete Processing Pipeline',
                    'description': 'Full workflow with multi-provider ingestion and evaluation',
                    'workflow_type': 'cli_sequence',
                    'cli_commands': [
                        {
                            'command': 'python main.py --email-ingest --provider all',
                            'name': 'Multi-Provider Email',
                            'description': 'Ingest from all email providers',
                            'timeout': 600,
                            'continue_on_error': True
                        },
                        {
                            'command': 'python main.py --rss-ingest --provider all',
                            'name': 'Multi-Provider RSS',
                            'description': 'Ingest from all RSS providers',
                            'timeout': 300,
                            'continue_on_error': True
                        },
                        {
                            'command': 'python evaluate_projects.py',
                            'name': 'Project Evaluation',
                            'description': 'Evaluate all scraped projects',
                            'timeout': 1200
                        },
                        {
                            'command': 'python main.py --generate-applications --threshold 85',
                            'name': 'Generate Applications',
                            'description': 'Generate applications for accepted projects',
                            'timeout': 900,
                            'continue_on_error': True
                        },
                        {
                            'command': 'python file_purger.py',
                            'name': 'Cleanup',
                            'description': 'Remove old rejected projects',
                            'timeout': 180,
                            'continue_on_error': True
                        }
                    ],
                    'metadata': {
                        'dashboard_button': True,
                        'icon': '🚀',
                        'priority': 'high',
                        'category': 'complete'
                    },
                    'cron_schedule': '0 8-18 * * 1-5'
                }
            },
            'validation_examples': {
                'provider_commands': [
                    'python main.py --email-ingest --provider freelancermap',
                    'python main.py --rss-ingest --provider solcom',
                    'python main.py --full-workflow --provider all'
                ],
                'evaluation_commands': [
                    'python evaluate_projects.py',
                    'python evaluate_projects.py --pre-eval-only',
                    'python evaluate_projects.py --force-evaluation'
                ],
                'application_commands': [
                    'python main.py --generate-applications',
                    'python main.py --generate-applications --threshold 90',
                    'python main.py --generate-applications --all-accepted'
                ]
            }
        }

    def _execute_workflow(self, schedule_id: str):
        """Execute a workflow job"""
        if schedule_id not in self.schedules:
            logger.error(f"Schedule not found: {schedule_id}")
            return

        schedule = self.schedules[schedule_id]

        # Only CLI sequences are supported
        if schedule.workflow_type == 'cli_sequence':
            self._execute_cli_sequence(schedule_id)
        else:
            logger.error(f"Unsupported workflow type: {schedule.workflow_type}")

    def _execute_cli_sequence(self, schedule_id: str):
        """Execute CLI command sequence"""
        if schedule_id not in self.schedules:
            logger.error(f"Schedule not found: {schedule_id}")
            return

        schedule = self.schedules[schedule_id]
        run_id = str(uuid.uuid4())
        started_at = datetime.now().isoformat()

        # Create execution result
        result = ExecutionResult(
            run_id=run_id,
            started_at=started_at,
            status="running"
        )

        # Add to execution history
        schedule.execution_history.insert(0, result)
        schedule.last_run = started_at
        schedule.last_status = "running"
        
        # Keep only last 10 executions
        if len(schedule.execution_history) > 10:
            schedule.execution_history = schedule.execution_history[:10]
        
        self._save_schedules()

        logger.info(f"Starting CLI sequence execution: {schedule.name} (run: {run_id})")

        try:
            # Validate workflow before execution
            workflow_config = {
                'name': schedule.name,
                'workflow_type': schedule.workflow_type,
                'cli_commands': [asdict(cmd) for cmd in schedule.cli_commands],
                'cron_schedule': schedule.cron_schedule,
                'timezone': schedule.timezone
            }
            
            validation = self.validate_workflow_config(workflow_config)
            if not validation.valid:
                result.status = "failed"
                result.completed_at = datetime.now().isoformat()
                result.error = f"Validation failed: {'; '.join(validation.errors)}"
                schedule.last_status = "failed"
                self._save_schedules()
                return

            # Execute each command step
            for step_index, cmd_step in enumerate(schedule.cli_commands):
                step_result = self._execute_command_step(cmd_step, step_index, result, schedule.name)
                result.step_results.append(step_result)
                
                # Check if we should continue on error
                if not step_result['success'] and not cmd_step.continue_on_error:
                    result.status = "failed"
                    result.completed_at = datetime.now().isoformat()
                    result.error = f"Step {step_index + 1} failed: {step_result['error']}"
                    schedule.last_status = "failed"
                    self._save_schedules()
                    return

            # All steps completed successfully
            result.status = "success"
            result.completed_at = datetime.now().isoformat()
            schedule.last_status = "success"
            logger.info(f"CLI sequence completed successfully: {schedule.name}")

        except Exception as e:
            result.status = "failed"
            result.completed_at = datetime.now().isoformat()
            result.error = str(e)
            schedule.last_status = "failed"
            logger.error(f"CLI sequence execution error: {schedule.name} - {e}")

        # Save updated schedule
        schedule.updated_at = datetime.now().isoformat()
        self._save_schedules()

    def _execute_command_step(self, cmd_step: CommandStep, step_index: int, execution_result: ExecutionResult, schedule_name: str) -> Dict[str, Any]:
        """Execute a single command step"""
        step_result = {
            'step_index': step_index,
            'name': cmd_step.name,
            'command': cmd_step.command,
            'started_at': datetime.now().isoformat(),
            'success': False,
            'output': '',
            'error': '',
            'exit_code': None
        }

        logger.info(f"Executing step {step_index + 1}: {cmd_step.name}")
        logger.debug(f"Command: {cmd_step.command}")

        try:
            # Parse command
            cmd_parts = shlex.split(cmd_step.command)

            # Replace bare 'python' / 'python3' with the interpreter that is
            # running the server — ensures the correct venv is used even when
            # launchd's PATH does not include the venv bin directory.
            if cmd_parts and cmd_parts[0] in ('python', 'python3'):
                cmd_parts[0] = sys.executable

            working_dir = os.path.dirname(os.path.abspath(__file__))

            # Prepare environment
            env = os.environ.copy()
            if cmd_step.environment:
                env.update(cmd_step.environment)

            # Execute command
            process = subprocess.Popen(
                cmd_parts,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=working_dir,
                env=env
            )

            try:
                stdout, stderr = process.communicate(timeout=cmd_step.timeout)
                
                step_result.update({
                    'completed_at': datetime.now().isoformat(),
                    'exit_code': process.returncode,
                    'output': stdout,
                    'error': stderr,
                    'success': process.returncode == 0
                })

                if process.returncode == 0:
                    logger.info(f"Step {step_index + 1} completed successfully")
                    if stdout.strip():
                        logger.debug(f"Step output: {stdout.strip()}")
                else:
                    logger.error(f"Step {step_index + 1} failed with exit code: {process.returncode}")
                    if stderr.strip():
                        logger.error(f"Step error: {stderr.strip()}")

            except subprocess.TimeoutExpired:
                process.kill()
                step_result.update({
                    'completed_at': datetime.now().isoformat(),
                    'error': f"Command timed out after {cmd_step.timeout} seconds",
                    'success': False
                })
                logger.error(f"Step {step_index + 1} timed out after {cmd_step.timeout} seconds")

        except Exception as e:
            step_result.update({
                'completed_at': datetime.now().isoformat(),
                'error': str(e),
                'success': False
            })
            logger.error(f"Step {step_index + 1} execution error: {e}")

        return step_result



# Global instance
scheduler_manager = SchedulerManager()

def get_scheduler_manager() -> SchedulerManager:
    """Get the global scheduler manager instance"""
    return scheduler_manager