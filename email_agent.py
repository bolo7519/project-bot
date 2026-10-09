#!/usr/bin/env python3
"""
Email Agent for Project Ingestion

This module provides functionality to poll an IMAP mailbox for project emails,
extract URLs, and process them into project markdown files.
"""

import imaplib
import email
import re
import logging
from typing import List, Dict, Any
from datetime import datetime
import os

import feedparser
from bs4 import BeautifulSoup
from parse_html import parse_project, to_markdown
from state_manager import ProjectStateManager
from utils.filename import create_safe_filename
from dedupe_service import DedupeService
from markdown_renderer import MarkdownRenderer
import importlib

logger = logging.getLogger(__name__)

class EmailAgent:
    """
    Email Agent for polling IMAP and processing project emails.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the Email Agent with configuration.

        Args:
            config: Configuration dictionary containing email settings
        """
        self.config = config
        self.email_config = config.get('channels', {}).get('email', {})
        self.logger = logger

    def load_adapter(self, provider_id: str, provider_config: Dict[str, Any]):
        """
        Dynamically load and instantiate the appropriate scraping adapter for a provider.

        Args:
            provider_id: Provider identifier
            provider_config: Provider-specific configuration

        Returns:
            Instantiated adapter instance

        Raises:
            ImportError: If adapter module cannot be imported
            AttributeError: If adapter class cannot be found
        """
        # Try to import provider-specific adapter
        try:
            module_name = f"scraping_adapters.{provider_id}"
            module = importlib.import_module(module_name)
            adapter_class_name = f"{provider_id.title()}Adapter"
            adapter_class = getattr(module, adapter_class_name)
            adapter = adapter_class(provider_id, provider_config)
            self.logger.debug(f"Loaded provider-specific adapter: {adapter_class_name}")
            return adapter
        except (ImportError, AttributeError) as e:
            self.logger.warning(f"Provider-specific adapter not found for {provider_id}, falling back to default", extra={
                'provider_id': provider_id,
                'error': str(e)
            })

            # Fallback to default adapter
            try:
                from scraping_adapters.default import DefaultAdapter
                adapter = DefaultAdapter(provider_id, provider_config)
                self.logger.debug("Loaded default adapter")
                return adapter
            except Exception as fallback_error:
                self.logger.error("Failed to load default adapter", extra={
                    'provider_id': provider_id,
                    'error': str(fallback_error)
                })
                raise

    def validate_config(self, provider_id: str, channel: str = 'email') -> bool:
        """
        Validate the configuration for the given provider and channel.

        Args:
            provider_id: Provider identifier
            channel: Channel to validate ('email' or 'rss')

        Returns:
            True if config is valid, False otherwise
        """
        self.logger.debug("Validating config", extra={'provider_id': provider_id, 'channel': channel})
        errors = []

        if channel == 'email':
            # Check email config
            required_email = ['server', 'username', 'password']
            for field in required_email:
                if not self.email_config.get(field):
                    errors.append(f"Missing channels.email.{field}")

            # Check provider email config
            provider_config = self.config.get('providers', {}).get(provider_id, {}).get('channels', {}).get('email', {})
            self.logger.debug("Provider email config", extra={'provider_config_keys': list(provider_config.keys())})
            if not provider_config:
                errors.append(f"No email config found for provider {provider_id}")
                for error in errors:
                    self.logger.error(f"Config validation error: {error}")
                return False

            required_provider = ['senders', 'subject_patterns', 'body_url_patterns']
            for field in required_provider:
                if not provider_config.get(field):
                    errors.append(f"Missing or empty providers.{provider_id}.channels.email.{field}")

            # Validate regex patterns
            for pattern in provider_config.get('subject_patterns', []):
                try:
                    re.compile(pattern)
                except re.error as e:
                    errors.append(f"Invalid subject pattern '{pattern}': {e}")

            for pattern in provider_config.get('body_url_patterns', []):
                try:
                    re.compile(pattern)
                except re.error as e:
                    errors.append(f"Invalid body URL pattern '{pattern}': {e}")

        elif channel == 'rss':
            # Check provider RSS config
            provider_config = self.config.get('providers', {}).get(provider_id, {}).get('channels', {}).get('rss', {})
            self.logger.debug("Provider RSS config", extra={'provider_config_keys': list(provider_config.keys())})
            if not provider_config:
                errors.append(f"No RSS config found for provider {provider_id}")
                for error in errors:
                    self.logger.error(f"Config validation error: {error}")
                return False

            required_provider = ['feed_urls']
            for field in required_provider:
                if not provider_config.get(field):
                    errors.append(f"Missing or empty providers.{provider_id}.channels.rss.{field}")

        else:
            errors.append(f"Unknown channel: {channel}")

        if errors:
            for error in errors:
                self.logger.error(f"Config validation error: {error}")
            return False

        self.logger.info("Configuration validation passed", extra={'channel': channel})
        return True

    def connect_imap(self) -> imaplib.IMAP4_SSL:
        """
        Connect to IMAP server.

        Returns:
            IMAP4_SSL connection object

        Raises:
            Exception: If connection fails
        """
        try:
            server = self.email_config.get('server')
            port = self.email_config.get('port', 993)
            username = self.email_config.get('username')
            password = os.path.expandvars(self.email_config.get('password', ''))

            if not all([server, username, password]):
                raise ValueError("IMAP server, username, and password are required")

            mail = imaplib.IMAP4_SSL(server, port)
            mail.login(username, password)
            self.logger.info("Connected to IMAP server", extra={
                'server': server,
                'username': username
            })
            return mail
        except Exception as e:
            self.logger.error("Failed to connect to IMAP", extra={
                'error': str(e),
                'server': self.email_config.get('server')
            })
            raise

    def extract_urls_from_email(self, message: email.message.EmailMessage, url_patterns: List[str], provider_config: Dict[str, Any]) -> List[str]:
        """
        Extract URLs from email message body using regex patterns.
        Handles both text/plain and text/html parts.

        Args:
            message: Email message object
            url_patterns: List of regex patterns for URL extraction

        Returns:
            List of extracted URLs
        """
        urls = []
        bodies = []

        # Get message bodies (both text and html)
        if message.is_multipart():
            for part in message.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    bodies.append(('text', body))
                elif content_type == "text/html":
                    html = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    bodies.append(('html', html))
                    # Also extract URLs from HTML links
                    soup = BeautifulSoup(html, 'html.parser')
                    for link in soup.find_all('a', href=True):
                        href = link['href']
                        if href.startswith('http'):
                            urls.append(href)
        else:
            content_type = message.get_content_type()
            body = message.get_payload(decode=True).decode('utf-8', errors='ignore')
            if content_type == "text/html":
                bodies.append(('html', body))
                # Extract from HTML
                soup = BeautifulSoup(body, 'html.parser')
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    if href.startswith('http'):
                        urls.append(href)
            else:
                bodies.append(('text', body))

        # Log body samples
        for content_type, body in bodies:
            self.logger.debug(f"Processing email {content_type} body: {body[:500]}{'...' if len(body) > 500 else ''}", extra={
                'body_length': len(body)
            })

        # Apply regex patterns to text bodies
        for content_type, body in bodies:
            if content_type == 'text':
                for pattern in url_patterns:
                    try:
                        # Use finditer to get full matches, not just groups
                        matches = [match.group(0) for match in re.finditer(pattern, body, re.IGNORECASE)]
                        urls.extend(matches)
                        self.logger.debug(f"Applied pattern to text: {pattern} -> {len(matches)} matches", extra={
                            'matches': matches[:3]  # Show first 3 matches
                        })
                    except re.error as e:
                        self.logger.warning("Invalid regex pattern", extra={
                            'pattern': pattern,
                            'error': str(e)
                        })

        # Filter URLs to only include those matching the patterns
        filtered_urls = []
        for url in urls:
            url = url.strip()
            if url:
                # Check if URL matches any of the patterns
                matches_pattern = False
                for pattern in url_patterns:
                    try:
                        if re.search(pattern, url, re.IGNORECASE):
                            matches_pattern = True
                            break
                    except re.error as e:
                        self.logger.warning("Invalid regex pattern", extra={
                            'pattern': pattern,
                            'error': str(e)
                        })
                if matches_pattern:
                    filtered_urls.append(url)

        # Apply exclude patterns to filter out unwanted URLs
        exclude_patterns = provider_config.get('url_exclude_patterns', [])
        if exclude_patterns:
            excluded_urls = []
            for url in filtered_urls:
                should_exclude = False
                for pattern in exclude_patterns:
                    try:
                        if re.search(pattern, url, re.IGNORECASE):
                            should_exclude = True
                            self.logger.debug("Excluding URL due to exclude pattern", extra={
                                'url': url,
                                'pattern': pattern
                            })
                            break
                    except re.error as e:
                        self.logger.warning("Invalid exclude regex pattern", extra={
                            'pattern': pattern,
                            'error': str(e)
                        })
                if not should_exclude:
                    excluded_urls.append(url)
            filtered_urls = excluded_urls

        # Deduplicate filtered URLs
        unique_urls = list(set(filtered_urls))
        self.logger.info("URLs extracted from email", extra={
            'total_raw_urls': len(urls),
            'total_filtered_urls': len(unique_urls),
            'urls': unique_urls[:5]  # Log first 5 for debugging
        })

        return unique_urls

    def move_to_processed(self, mail: imaplib.IMAP4_SSL, message_id: str, processed_folder: str) -> bool:
        """
        Move email to processed folder.

        Args:
            mail: IMAP connection
            message_id: Message ID to move
            processed_folder: Target folder name

        Returns:
            True if successful, False otherwise
        """
        try:

            self.logger.debug("Starting email move operation", extra={
                'message_id': message_id,
                'processed_folder': processed_folder
            })

            # First, check if the processed folder exists, and create it if it doesn't
            try:
                # Try to select the folder to check if it exists
                self.logger.debug("Checking if processed folder exists", extra={
                    'processed_folder': processed_folder
                })
                status, data = mail.select(processed_folder, readonly=True)
                if status != 'OK':
                    # Folder doesn't exist, try to create it
                    self.logger.info("Processed folder doesn't exist, attempting to create it", extra={
                        'processed_folder': processed_folder
                    })

                    # Try different folder naming conventions
                    folder_candidates = [
                        processed_folder,  # Original name
                        f'INBOX.{processed_folder}',  # INBOX prefixed
                        'Processed',  # Standard name
                        'INBOX.Processed',  # Standard INBOX prefixed
                        'Archive',  # Alternative name
                        'INBOX.Archive'  # Alternative INBOX prefixed
                    ]

                    created_folder = None
                    for candidate in folder_candidates:
                        self.logger.debug("Trying to create folder", extra={'candidate': candidate})
                        try:
                            create_status, create_data = mail.create(candidate)
                            if create_status == 'OK':
                                created_folder = candidate
                                self.logger.info("Successfully created processed folder", extra={
                                    'created_folder': created_folder
                                })
                                break
                            else:
                                self.logger.debug("Failed to create folder", extra={
                                    'candidate': candidate,
                                    'create_status': create_status,
                                    'create_data': create_data
                                })
                        except Exception as create_error:
                            self.logger.debug("Exception creating folder", extra={
                                'candidate': candidate,
                                'error': str(create_error)
                            })

                    if created_folder:
                        processed_folder = created_folder
                    else:
                        self.logger.error("Could not create any processed folder, emails will remain in inbox", extra={
                            'original_folder': processed_folder,
                            'tried_folders': folder_candidates,
                            'recommendation': 'Consider disabling move_processed for this provider or check IMAP server permissions'
                        })
                        # Continue with original folder name, let the copy operation fail gracefully
                        # The copy operation will fail, but the email processing will continue
            except Exception as folder_check_error:
                self.logger.warning("Error checking/creating processed folder", extra={
                    'processed_folder': processed_folder,
                    'error': str(folder_check_error)
                })

            # Try to copy to processed folder
            self.logger.debug("Attempting to copy email to processed folder", extra={
                'message_id': message_id,
                'processed_folder': processed_folder
            })

            # Ensure we're still in the correct source folder before copying
            try:
                current_folder_status, current_folder_data = mail.select()
                current_folder = current_folder_data[0] if current_folder_data else 'Unknown'
                self.logger.debug("Current folder before copy operation", extra={
                    'current_folder': current_folder
                })
            except Exception as folder_error:
                self.logger.warning("Could not determine current folder", extra={
                    'error': str(folder_error)
                })

            copy_status, copy_data = mail.uid('copy', message_id, processed_folder)
            if copy_status != 'OK':
                self.logger.error("Failed to copy email to processed folder", extra={
                    'message_id': message_id,
                    'processed_folder': processed_folder,
                    'copy_status': copy_status,
                    'copy_data': copy_data
                })

                # Try to understand why the copy failed
                try:
                    # Check if the folder exists by trying to select it
                    select_status, select_data = mail.select(processed_folder, readonly=True)
                    self.logger.debug("Target folder select status", extra={
                        'processed_folder': processed_folder,
                        'select_status': select_status,
                        'select_data': select_data
                    })
                except Exception as select_error:
                    self.logger.error("Could not check target folder", extra={
                        'processed_folder': processed_folder,
                        'error': str(select_error)
                    })

                # If copy fails, don't mark as deleted - leave email in inbox
                return False

            # If copy succeeded, mark as deleted in current folder
            self.logger.debug("Email copied successfully, marking as deleted", extra={
                'message_id': message_id
            })
            delete_status, delete_data = mail.uid('store', message_id, '+FLAGS', '\\Deleted')
            if delete_status != 'OK':
                self.logger.error("Failed to mark email as deleted", extra={
                    'message_id': message_id,
                    'delete_status': delete_status,
                    'delete_data': delete_data
                })
                # Email was copied but not deleted - this is a problem
                # Try to delete the copied email to avoid duplicates
                try:
                    # Switch to processed folder and delete the copied message
                    mail.select(processed_folder)
                    # Find the copied message (this is tricky, we'll just log the issue)
                    self.logger.warning("Email copied but not deleted from inbox - manual cleanup may be needed", extra={
                        'message_id': message_id,
                        'processed_folder': processed_folder
                    })
                except Exception as cleanup_error:
                    self.logger.error("Failed to cleanup after partial move", extra={
                        'message_id': message_id,
                        'error': str(cleanup_error)
                    })
                return False

            # Expunge to permanently remove from current folder
            self.logger.debug("Marking email as deleted, now expunging", extra={
                'message_id': message_id
            })
            expunge_status, expunge_data = mail.expunge()
            if expunge_status != 'OK':
                self.logger.warning("Failed to expunge deleted emails", extra={
                    'expunge_status': expunge_status,
                    'expunge_data': expunge_data
                })
                # Email was copied and marked deleted, but expunge failed
                # This means the email might still appear in the inbox until expunge is called later
                self.logger.warning("Email moved but expunge failed - email may still appear in inbox", extra={
                    'message_id': message_id
                })

            self.logger.info("Email moved to processed folder", extra={
                'message_id': message_id,
                'processed_folder': processed_folder
            })
            return True
        except Exception as e:
            self.logger.error("Failed to move email", extra={
                'message_id': message_id,
                'processed_folder': processed_folder,
                'error': str(e)
            })
            return False

    def process_email(self, mail: imaplib.IMAP4_SSL, message_id: str, provider_config: Dict[str, Any], output_dir: str) -> Dict[str, int]:
        """
        Process a single email: extract URLs, scrape projects, save to files.

        Args:
            mail: IMAP connection
            message_id: Message ID
            provider_config: Provider-specific email config
            output_dir: Directory to save project files

        Returns:
            Dict with processing results: projects_saved, urls_skipped_dedupe
        """
        projects_saved = 0
        urls_skipped_dedupe = 0

        try:
            # Ensure message_id is properly formatted for IMAP operations
            if isinstance(message_id, bytes):
                message_id_str = message_id.decode('utf-8')
            else:
                message_id_str = str(message_id)

            self.logger.debug("Processing email with message_id", extra={
                'message_id': message_id_str,
                'message_id_type': type(message_id).__name__
            })

            # Fetch the email
            status, msg_data = mail.uid('fetch', message_id, '(RFC822)')
            if status != 'OK':
                self.logger.error("Failed to fetch email", extra={
                    'message_id': message_id_str,
                    'status': status
                })
                return {
                    'projects_saved': 0,
                    'urls_skipped_dedupe': 0,
                    'urls_found': 0
                }

            email_body = msg_data[0][1]
            message = email.message_from_bytes(email_body)

            # Log email metadata
            email_date = message.get('Date', 'No Date')
            sender = message.get('From', '')
            subject = message.get('Subject', '')
            self.logger.info("Email metadata", extra={
                'message_id': message_id_str,
                'email_date': email_date,
                'sender': sender,
                'subject': subject
            })

            self.logger.debug(f"Processing email: {sender} - {subject}", extra={
                'message_id': message_id_str
            })

            senders = provider_config.get('senders', [])
            subject_patterns = provider_config.get('subject_patterns', [])

            sender_match = any(s in sender for s in senders)
            subject_match = any(re.search(pattern, subject, re.IGNORECASE) for pattern in subject_patterns)

            # Add detailed logging for debugging
            self.logger.info(f"Email matching check: sender='{sender}', subject='{subject}'", extra={
                'message_id': message_id_str,
                'sender_match': sender_match,
                'subject_match': subject_match,
                'senders_config': senders,
                'subject_patterns_config': subject_patterns
            })

            if not (sender_match and subject_match):
                self.logger.warning(f"Email filtered out: sender='{sender}' subject='{subject}' (sender_match: {sender_match}, subject_match: {subject_match})", extra={
                    'message_id': message_id_str,
                    'senders_config': senders,
                    'subject_patterns_config': subject_patterns
                })
                return {
                    'projects_saved': 0,
                    'urls_skipped_dedupe': 0,
                    'urls_found': 0
                }

            self.logger.info("Processing matching email", extra={
                'message_id': message_id_str,
                'sender': sender,
                'subject': subject
            })

            # Extract URLs
            url_patterns = provider_config.get('body_url_patterns', [])
            urls = self.extract_urls_from_email(message, url_patterns, provider_config)

            if not urls:
                self.logger.info("No URLs found in email", extra={
                    'message_id': message_id_str
                })
                return {
                    'projects_saved': 0,
                    'urls_skipped_dedupe': 0,
                    'urls_found': 0
                }

            # Limit URLs per email for testing
            max_urls_per_email = provider_config.get('max_urls_per_email', 50)  # Default to 50
            if len(urls) > max_urls_per_email:
                urls = urls[:max_urls_per_email]
                self.logger.info(f"Limited URLs per email to {max_urls_per_email}", extra={
                    'original_count': len(urls),
                    'limited_to': max_urls_per_email
                })

            # Initialize services
            dedupe_service = DedupeService(output_dir)
            adapter = self.load_adapter(provider_config['provider_id'], provider_config)
            renderer = MarkdownRenderer()

            # Process each URL
            for url in urls:
                try:
                    self.logger.debug(f"Starting to scrape project URL: {url}", extra={
                        'message_id': message_id_str
                    })

                    # Canonicalize URL for dedupe
                    canonical_url = dedupe_service.canonicalize_url(url, provider_config['provider_id'])

                    # Check if already processed
                    if dedupe_service.already_processed(provider_config['provider_id'], canonical_url):
                        urls_skipped_dedupe += 1
                        self.logger.info("URL already processed, skipping", extra={
                            'url': url,
                            'canonical_url': canonical_url,
                            'message_id': message_id_str
                        })
                        continue

                    # Parse project via adapter
                    self.logger.debug("Calling adapter.parse", extra={'url': url})
                    parse_result = adapter.parse(url)

                    # Handle new return format with optional HTML
                    if isinstance(parse_result, dict) and 'schema' in parse_result:
                        schema = parse_result['schema']
                        html_content = parse_result.get('html')
                    else:
                        # Backward compatibility for adapters that return schema directly
                        schema = parse_result
                        html_content = None

                    self.logger.debug("adapter.parse completed", extra={'url': url, 'title': schema.get('title', 'N/A')})

                    # Build provider metadata
                    provider_meta = {
                        'provider_id': provider_config['provider_id'],
                        'provider_name': adapter.get_provider_name(),
                        'collection_channel': 'email',
                        'collected_at': datetime.now().isoformat()
                    }

                    # Render markdown
                    markdown_content = renderer.render(schema, provider_meta)

                    # Create filename
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    original_title = schema.get('title', 'project')
                    filename = create_safe_filename(original_title, timestamp)

                    # Save file
                    os.makedirs(output_dir, exist_ok=True)
                    filepath = os.path.join(output_dir, filename)

                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(markdown_content)

                    # Mark as processed
                    dedupe_service.mark_processed(provider_config['provider_id'], canonical_url)

                    # Initialize state (will merge with existing frontmatter)
                    state_manager = ProjectStateManager(output_dir)
                    metadata = {
                        'scraped_date': datetime.now().isoformat(),
                        'source_url': url
                    }

                    success = state_manager.initialize_project(filepath, metadata)

                    if success:
                        projects_saved += 1
                        self.logger.info("Project saved", extra={
                            'filepath': filepath,
                            'url': url,
                            'canonical_url': canonical_url,
                            'message_id': message_id_str
                        })
                    else:
                        self.logger.warning("Failed to initialize project state", extra={
                            'filepath': filepath
                        })

                except Exception as e:
                    import traceback
                    self.logger.error("Failed to process project URL", extra={
                        'url': url,
                        'message_id': message_id_str,
                        'error': str(e),
                        'traceback': traceback.format_exc()
                    })

            # Move email to processed folder (if enabled)
            move_processed = provider_config.get('move_processed', True)
            if move_processed:
                processed_folder = provider_config.get('processed_folder', 'Processed')
                self.logger.debug("Attempting to move email to processed folder", extra={
                    'message_id': message_id_str,
                    'processed_folder': processed_folder,
                    'move_processed': move_processed
                })
                moved = self.move_to_processed(mail, message_id, processed_folder)
                self.logger.debug("Email move operation completed", extra={
                    'message_id': message_id_str,
                    'moved': moved
                })

                # If move failed, suggest disabling move_processed for this provider
                if not moved:
                    self.logger.warning("Email move failed - consider setting move_processed=false for this provider", extra={
                        'message_id': message_id_str,
                        'provider_id': provider_config.get('provider_id'),
                        'suggestion': 'Add "move_processed": false to provider config if this persists'
                    })
            else:
                self.logger.debug("Skipping email move (move_processed=false)")
                moved = False

            self.logger.info("Email processing complete", extra={
                'message_id': message_id_str,
                'projects_saved': projects_saved,
                'urls_skipped_dedupe': urls_skipped_dedupe,
                'moved_to_processed': moved if move_processed else 'disabled'
            })

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"DEBUG: Failed to process email {message_id_str}: {str(e)}")
            print(f"DEBUG: Traceback: {tb}")
            self.logger.error("Failed to process email", extra={
                'message_id': message_id_str,
                'error': str(e),
                'traceback': tb
            })

        return {
            'projects_saved': projects_saved,
            'urls_skipped_dedupe': urls_skipped_dedupe,
            'urls_found': len(urls) if 'urls' in locals() else 0
        }

    def run_once(self, provider_id: str, output_dir: str = 'projects', dry_run: bool = False) -> Dict[str, Any]:
        """
        Run one ingestion cycle for the specified provider.

        Args:
            provider_id: Provider identifier (e.g., 'freelancermap')
            output_dir: Directory to save project files
            dry_run: If True, validate config and simulate operations without side effects

        Returns:
            Summary dictionary with processing results
        """
        self.logger.info("Starting email ingestion run", extra={
            'provider_id': provider_id,
            'output_dir': output_dir,
            'dry_run': dry_run
        })

        summary = {
            'provider_id': provider_id,
            'dry_run': dry_run,
            'emails_processed': 0,
            'emails_matched': 0,
            'urls_found': 0,
            'urls_skipped_dedupe': 0,
            'projects_saved': 0,
            'errors': 0
        }

        try:
            # Validate config first
            self.logger.debug("Calling validate_config", extra={'provider_id': provider_id})
            is_valid = self.validate_config(provider_id)
            self.logger.debug("validate_config result", extra={'is_valid': is_valid})
            if not is_valid:
                self.logger.info("Configuration validation failed, aborting run", extra={'provider_id': provider_id})
                summary['errors'] += 1
                return summary

            # Get provider config
            provider_config = self.config.get('providers', {}).get(provider_id, {}).get('channels', {}).get('email', {})
            provider_config['provider_id'] = provider_id

            if dry_run:
                # Simulate operations
                self.logger.info("DRY RUN: Would connect to IMAP", extra={
                    'server': self.email_config.get('server'),
                    'source_folder': provider_config.get('source_folder', 'INBOX')
                })
                self.logger.info("DRY RUN: Would process emails", extra={
                    'estimated_emails': 'unknown (would search ALL)',
                    'filters': {
                        'senders': provider_config.get('senders', []),
                        'subject_patterns': provider_config.get('subject_patterns', []),
                        'body_url_patterns': provider_config.get('body_url_patterns', [])
                    }
                })
                self.logger.info("DRY RUN: Would save projects to", extra={
                    'output_dir': output_dir
                })
                summary['emails_processed'] = 0  # Unknown in dry run
                summary['emails_matched'] = 0
                summary['urls_found'] = 0
                summary['urls_skipped_dedupe'] = 0
                summary['projects_saved'] = 0
                self.logger.info("Email ingestion dry run complete", extra=summary)
                return summary

            # Actual run
            # Connect to IMAP
            mail = self.connect_imap()

            # Select source folder
            source_folder = provider_config.get('source_folder', 'INBOX')
            mail.select(source_folder)

            self.logger.debug("Selected source folder", extra={
                'source_folder': source_folder
            })

            # Search for emails within the configured time frame
            max_age_days = provider_config.get('max_age_days', 7)  # Default to 7 days
            import datetime
            now = datetime.datetime.now()
            since_datetime = now - datetime.timedelta(days=max_age_days)
            since_date = since_datetime.strftime('%d-%b-%Y')
            search_criteria = f'SINCE {since_date}'
            self.logger.info(f"Email search parameters: max_age_days={max_age_days}, current_datetime={now.isoformat()}, since_datetime={since_datetime.isoformat()}, since_date_formatted={since_date}, search_criteria={search_criteria}, timezone_info={str(now.tzinfo)}")
            status, messages = mail.uid('search', None, search_criteria)
            if status != 'OK':
                self.logger.error("Failed to search emails", extra={
                    'status': status
                })
                return summary

            message_ids = [mid.decode('utf-8') for mid in messages[0].split()]
            self.logger.info(f"Emails found by search: total_emails_found={len(message_ids)}, search_criteria={search_criteria}, message_ids_sample={message_ids[:5]}")

            # Limit the number of emails to process
            max_emails = provider_config.get('max_emails', 50)  # Default to 50 emails
            if len(message_ids) > max_emails:
                message_ids = message_ids[-max_emails:]  # Take the most recent ones
                self.logger.info("Limited email processing to most recent", extra={
                    'original_count': len(messages[0].split()),
                    'limited_to': max_emails
                })

            self.logger.info("Found emails in folder", extra={
                'source_folder': source_folder,
                'email_count': len(message_ids),
                'max_age_days': max_age_days
            })

            # Process each email
            for message_id in message_ids:
                summary['emails_processed'] += 1
                try:
                    result = self.process_email(mail, message_id, provider_config, output_dir)
                    projects_saved = result['projects_saved']
                    urls_skipped = result['urls_skipped_dedupe']
                    urls_found = result.get('urls_found', 0)
                    if projects_saved > 0 or urls_skipped > 0:
                        summary['emails_matched'] += 1
                    summary['projects_saved'] += projects_saved
                    summary['urls_skipped_dedupe'] += urls_skipped
                    summary['urls_found'] += urls_found
                except Exception as e:
                    summary['errors'] += 1
                    print(f"DEBUG: Error processing email {message_id}: {str(e)}")
                    import traceback
                    print(f"DEBUG: Traceback: {traceback.format_exc()}")
                    self.logger.error("Error processing email", extra={
                        'message_id': message_id,
                        'error': str(e)
                    })

            # Logout
            mail.logout()

            self.logger.info("Email ingestion run complete", extra=summary)

        except Exception as e:
            summary['errors'] += 1
            self.logger.error("Email ingestion run failed", extra={
                'error': str(e),
                'summary': summary
            })

        return summary

    def fetch_rss_feed(self, feed_url: str, limit: int = 5, max_age_days: int = 7) -> List[Dict[str, Any]]:
        """
        Fetch and parse RSS feed, filtering by age and limit.

        Args:
            feed_url: RSS feed URL to fetch
            limit: Maximum number of entries to return
            max_age_days: Only include entries from the last N days

        Returns:
            List of filtered RSS entries with metadata
        """
        try:
            self.logger.info("Fetching RSS feed", extra={
                'feed_url': feed_url,
                'limit': limit,
                'max_age_days': max_age_days
            })

            feed = feedparser.parse(feed_url)

            if feed.bozo:
                self.logger.warning("RSS feed parsing error", extra={
                    'feed_url': feed_url,
                    'error': str(feed.bozo_exception)
                })
                return []

            # Calculate cutoff date
            from datetime import timedelta
            cutoff_date = datetime.now() - timedelta(days=max_age_days)

            filtered_entries = []
            for entry in feed.entries[:limit]:
                # Parse entry date
                entry_date = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    entry_date = datetime(*entry.published_parsed[:6])
                elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                    entry_date = datetime(*entry.updated_parsed[:6])

                # Skip if too old
                if entry_date and entry_date < cutoff_date:
                    continue

                # Extract URL
                url = getattr(entry, 'link', '')
                if not url:
                    continue

                filtered_entries.append({
                    'title': getattr(entry, 'title', 'Untitled'),
                    'url': url,
                    'published_date': entry_date.isoformat() if entry_date else None,
                    'feed_url': feed_url
                })

            self.logger.info("RSS feed fetched successfully", extra={
                'feed_url': feed_url,
                'total_entries': len(feed.entries),
                'filtered_entries': len(filtered_entries)
            })

            return filtered_entries

        except Exception as e:
            self.logger.error("Failed to fetch RSS feed", extra={
                'feed_url': feed_url,
                'error': str(e)
            })
            return []

    def process_rss_entries(self, entries: List[Dict[str, Any]], provider_config: Dict[str, Any], output_dir: str) -> Dict[str, int]:
        """
        Process RSS entries: extract URLs, scrape projects, save to files.

        Args:
            entries: List of RSS entries to process
            provider_config: Provider-specific RSS config
            output_dir: Directory to save project files

        Returns:
            Dict with processing results: projects_saved, urls_skipped_dedupe
        """
        projects_saved = 0
        urls_skipped_dedupe = 0

        # Initialize services
        dedupe_service = DedupeService(output_dir)
        adapter = self.load_adapter(provider_config['provider_id'], provider_config)
        renderer = MarkdownRenderer()

        for entry in entries:
            try:
                url = entry['url']
                self.logger.debug("Processing RSS entry", extra={
                    'title': entry['title'],
                    'url': url,
                    'feed_url': entry['feed_url']
                })

                # Canonicalize URL for dedupe
                canonical_url = dedupe_service.canonicalize_url(url, provider_config['provider_id'])

                # Check if already processed
                if dedupe_service.already_processed(provider_config['provider_id'], canonical_url):
                    urls_skipped_dedupe += 1
                    self.logger.info("URL already processed, skipping", extra={
                        'url': url,
                        'canonical_url': canonical_url,
                        'title': entry['title']
                    })
                    continue

                # Parse project via adapter
                self.logger.debug("Calling adapter.parse", extra={'url': url})
                parse_result = adapter.parse(url)

                # Handle new return format with optional HTML
                if isinstance(parse_result, dict) and 'schema' in parse_result:
                    schema = parse_result['schema']
                    html_content = parse_result.get('html')
                else:
                    # Backward compatibility for adapters that return schema directly
                    schema = parse_result
                    html_content = None

                self.logger.debug("adapter.parse completed", extra={'url': url, 'title': schema.get('title', 'N/A')})

                # Build provider metadata
                provider_meta = {
                    'provider_id': provider_config['provider_id'],
                    'provider_name': adapter.get_provider_name(),
                    'collection_channel': 'rss',
                    'collected_at': datetime.now().isoformat(),
                    'feed_url': entry['feed_url'],
                    'published_date': entry['published_date']
                }

                # Render markdown
                markdown_content = renderer.render(schema, provider_meta)

                # Create filename
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                original_title = schema.get('title', 'project')
                filename = create_safe_filename(original_title, timestamp)

                # Save file
                os.makedirs(output_dir, exist_ok=True)
                filepath = os.path.join(output_dir, filename)

                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(markdown_content)

                # Mark as processed
                dedupe_service.mark_processed(provider_config['provider_id'], canonical_url)

                # Initialize state (will merge with existing frontmatter)
                state_manager = ProjectStateManager(output_dir)
                metadata = {
                    'scraped_date': datetime.now().isoformat(),
                    'source_url': url
                }

                success = state_manager.initialize_project(filepath, metadata)

                if success:
                    projects_saved += 1
                    self.logger.info("Project saved from RSS", extra={
                        'filepath': filepath,
                        'url': url,
                        'canonical_url': canonical_url,
                        'title': entry['title'],
                        'feed_url': entry['feed_url']
                    })
                else:
                    self.logger.warning("Failed to initialize project state", extra={
                        'filepath': filepath
                    })

            except Exception as e:
                import traceback
                self.logger.error("Failed to process RSS entry", extra={
                    'url': entry.get('url', 'N/A'),
                    'title': entry.get('title', 'N/A'),
                    'feed_url': entry.get('feed_url', 'N/A'),
                    'error': str(e),
                    'traceback': traceback.format_exc()
                })

        self.logger.info("RSS entries processing complete", extra={
            'entries_processed': len(entries),
            'projects_saved': projects_saved,
            'urls_skipped_dedupe': urls_skipped_dedupe
        })

        return {
            'projects_saved': projects_saved,
            'urls_skipped_dedupe': urls_skipped_dedupe
        }

    def run_rss_ingestion(self, provider_id: str, output_dir: str = 'projects', dry_run: bool = False) -> Dict[str, Any]:
        """
        Run RSS ingestion for the specified provider.

        Args:
            provider_id: Provider identifier (e.g., 'freelancermap')
            output_dir: Directory to save project files
            dry_run: If True, validate config and simulate operations without side effects

        Returns:
            Summary dictionary with processing results
        """
        self.logger.info("Starting RSS ingestion run", extra={
            'provider_id': provider_id,
            'output_dir': output_dir,
            'dry_run': dry_run
        })

        summary = {
            'provider_id': provider_id,
            'dry_run': dry_run,
            'entries_found': 0,
            'entries_processed': 0,
            'urls_skipped_dedupe': 0,
            'projects_saved': 0,
            'errors': 0
        }

        try:
            # Get provider config
            provider_config = self.config.get('providers', {}).get(provider_id, {}).get('channels', {}).get('rss', {})
            if not provider_config:
                self.logger.error("No RSS config found for provider", extra={'provider_id': provider_id})
                summary['errors'] += 1
                return summary

            provider_config['provider_id'] = provider_id

            # Get feed URLs
            feed_urls = provider_config.get('feed_urls', [])
            if not feed_urls:
                self.logger.error("No feed URLs configured for provider", extra={'provider_id': provider_id})
                summary['errors'] += 1
                return summary

            # Get limits
            limit = provider_config.get('limit', self.config.get('channels', {}).get('rss', {}).get('default_limit', 5))
            max_age_days = provider_config.get('max_age_days', self.config.get('channels', {}).get('rss', {}).get('max_age_days', 7))

            if dry_run:
                # Simulate operations
                self.logger.info("DRY RUN: Would fetch RSS feeds", extra={
                    'feed_urls': feed_urls,
                    'limit': limit,
                    'max_age_days': max_age_days
                })
                self.logger.info("DRY RUN: Would process RSS entries", extra={
                    'output_dir': output_dir
                })
                summary['entries_found'] = 0  # Unknown in dry run
                summary['entries_processed'] = 0
                summary['urls_skipped_dedupe'] = 0
                summary['projects_saved'] = 0
                self.logger.info("RSS ingestion dry run complete", extra=summary)
                return summary

            # Actual run
            all_entries = []
            for feed_url in feed_urls:
                entries = self.fetch_rss_feed(feed_url, limit, max_age_days)
                all_entries.extend(entries)

            summary['entries_found'] = len(all_entries)

            if all_entries:
                result = self.process_rss_entries(all_entries, provider_config, output_dir)
                summary['entries_processed'] = len(all_entries)
                summary['urls_skipped_dedupe'] = result['urls_skipped_dedupe']
                summary['projects_saved'] = result['projects_saved']
            else:
                self.logger.info("No RSS entries to process", extra={'provider_id': provider_id})

            self.logger.info("RSS ingestion run complete", extra=summary)

        except Exception as e:
            summary['errors'] += 1
            self.logger.error("RSS ingestion run failed", extra={
                'error': str(e),
                'summary': summary
            })

        return summary

    def get_enabled_providers(self, channel: str = 'email') -> List[str]:
        """
        Get list of enabled providers from configuration for a specific channel.

        Args:
            channel: Channel type ('email' or 'rss')

        Returns:
            List of provider IDs that are enabled for the specified channel
        """
        providers = self.config.get('providers', {})
        enabled_providers = []

        for provider_id, provider_config in providers.items():
            if provider_config.get('enabled', True):  # Default to enabled
                # Check if specified channel is configured
                if provider_config.get('channels', {}).get(channel):
                    enabled_providers.append(provider_id)

        self.logger.info(f"Found enabled providers with {channel} channels", extra={
            'enabled_providers': enabled_providers,
            'channel': channel
        })
        return enabled_providers

    def fetch_rss_feed(self, feed_url: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Fetch and parse RSS feed entries.

        Args:
            feed_url: URL of the RSS feed
            limit: Maximum number of entries to return

        Returns:
            List of feed entries with title, link, and published date
        """
        try:
            import feedparser
            self.logger.info("Fetching RSS feed", extra={'feed_url': feed_url, 'limit': limit})
            feed = feedparser.parse(feed_url)

            if feed.bozo:
                self.logger.warning("RSS feed parsing error", extra={
                    'feed_url': feed_url,
                    'error': str(feed.bozo_exception)
                })

            entries = []
            for entry in feed.entries[:limit]:
                entry_data = {
                    'title': entry.get('title', 'No Title'),
                    'link': entry.get('link', ''),
                    'published': entry.get('published_parsed'),
                    'summary': entry.get('summary', ''),
                    'id': entry.get('id', entry.get('link', ''))
                }
                entries.append(entry_data)

            self.logger.info("RSS feed fetched successfully", extra={
                'feed_url': feed_url,
                'entries_found': len(entries)
            })
            return entries

        except Exception as e:
            self.logger.error("Failed to fetch RSS feed", extra={
                'feed_url': feed_url,
                'error': str(e)
            })
            raise

    def process_rss_entries(self, entries: List[Dict[str, Any]], provider_config: Dict[str, Any], output_dir: str) -> Dict[str, int]:
        """
        Process RSS feed entries into projects.

        Args:
            entries: List of RSS feed entries
            provider_config: Provider-specific configuration
            output_dir: Directory to save project files

        Returns:
            Dict with processing results: projects_saved, urls_skipped_dedupe
        """
        projects_saved = 0
        urls_skipped_dedupe = 0

        # Initialize services
        dedupe_service = DedupeService(output_dir)
        adapter = self.load_adapter(provider_config['provider_id'], provider_config)
        renderer = MarkdownRenderer()

        for entry in entries:
            try:
                url = entry.get('link', '').strip()
                if not url:
                    continue

                self.logger.debug("Processing RSS entry", extra={
                    'title': entry.get('title'),
                    'url': url
                })

                # Canonicalize URL for dedupe
                canonical_url = dedupe_service.canonicalize_url(url, provider_config['provider_id'])

                # Check if already processed
                if dedupe_service.already_processed(provider_config['provider_id'], canonical_url):
                    urls_skipped_dedupe += 1
                    self.logger.info("URL already processed, skipping", extra={
                        'url': url,
                        'canonical_url': canonical_url
                    })
                    continue

                # Parse project via adapter
                parse_result = adapter.parse(url)

                # Handle new return format with optional HTML
                if isinstance(parse_result, dict) and 'schema' in parse_result:
                    schema = parse_result['schema']
                    html_content = parse_result.get('html')
                else:
                    schema = parse_result
                    html_content = None

                # Build provider metadata
                provider_meta = {
                    'provider_id': provider_config['provider_id'],
                    'provider_name': adapter.get_provider_name(),
                    'collection_channel': 'rss',
                    'collected_at': datetime.now().isoformat()
                }

                # Render markdown
                markdown_content = renderer.render(schema, provider_meta)

                # Create filename
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                original_title = schema.get('title', 'project')
                filename = create_safe_filename(original_title, timestamp)

                # Save file
                os.makedirs(output_dir, exist_ok=True)
                filepath = os.path.join(output_dir, filename)

                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(markdown_content)

                # Mark as processed
                dedupe_service.mark_processed(provider_config['provider_id'], canonical_url)

                # Initialize state
                state_manager = ProjectStateManager(output_dir)
                metadata = {
                    'scraped_date': datetime.now().isoformat(),
                    'source_url': url
                }

                success = state_manager.initialize_project(filepath, metadata)

                if success:
                    projects_saved += 1
                    self.logger.info("Project saved from RSS", extra={
                        'filepath': filepath,
                        'url': url,
                        'canonical_url': canonical_url,
                        'title': entry.get('title')
                    })
                else:
                    self.logger.warning("Failed to initialize project state", extra={
                        'filepath': filepath
                    })

            except Exception as e:
                import traceback
                self.logger.error("Failed to process RSS entry", extra={
                    'title': entry.get('title'),
                    'url': entry.get('link'),
                    'error': str(e),
                    'traceback': traceback.format_exc()
                })

        self.logger.info("RSS entries processing complete", extra={
            'entries_processed': len(entries),
            'projects_saved': projects_saved,
            'urls_skipped_dedupe': urls_skipped_dedupe
        })

        return {
            'projects_saved': projects_saved,
            'urls_skipped_dedupe': urls_skipped_dedupe
        }

    def run_rss_ingestion(self, provider_id: str, output_dir: str = 'projects', dry_run: bool = False) -> Dict[str, Any]:
        """
        Run RSS ingestion for the specified provider.

        Args:
            provider_id: Provider identifier (e.g., 'freelancermap')
            output_dir: Directory to save project files
            dry_run: If True, validate config and simulate operations without side effects

        Returns:
            Summary dictionary with processing results
        """
        self.logger.info("Starting RSS ingestion run", extra={
            'provider_id': provider_id,
            'output_dir': output_dir,
            'dry_run': dry_run
        })

        summary = {
            'provider_id': provider_id,
            'dry_run': dry_run,
            'entries_found': 0,
            'projects_saved': 0,
            'urls_skipped_dedupe': 0,
            'errors': 0
        }

        try:
            # Validate config for RSS channel
            if not self.validate_config(provider_id, 'rss'):
                self.logger.info("Configuration validation failed, aborting run", extra={'provider_id': provider_id})
                summary['errors'] += 1
                return summary

            # Get provider config
            provider_config = self.config.get('providers', {}).get(provider_id, {}).get('channels', {}).get('rss', {})
            if not provider_config:
                self.logger.error("No RSS config found for provider", extra={'provider_id': provider_id})
                summary['errors'] += 1
                return summary

            provider_config['provider_id'] = provider_id

            if dry_run:
                # Simulate operations
                feed_urls = provider_config.get('feed_urls', [])
                limit = provider_config.get('limit', 5)
                self.logger.info("DRY RUN: Would fetch RSS feeds", extra={
                    'feed_urls': feed_urls,
                    'limit': limit
                })
                self.logger.info("DRY RUN: Would process entries and save projects", extra={
                    'output_dir': output_dir
                })
                summary['entries_found'] = 0  # Unknown in dry run
                summary['projects_saved'] = 0
                summary['urls_skipped_dedupe'] = 0
                self.logger.info("RSS ingestion dry run complete", extra=summary)
                return summary

            # Actual run
            feed_urls = provider_config.get('feed_urls', [])
            limit = provider_config.get('limit', 5)

            all_entries = []
            for feed_url in feed_urls:
                try:
                    entries = self.fetch_rss_feed(feed_url, limit)
                    all_entries.extend(entries)
                except Exception as e:
                    self.logger.error("Failed to fetch feed", extra={
                        'feed_url': feed_url,
                        'error': str(e)
                    })
                    summary['errors'] += 1

            summary['entries_found'] = len(all_entries)

            if all_entries:
                result = self.process_rss_entries(all_entries, provider_config, output_dir)
                summary['projects_saved'] = result['projects_saved']
                summary['urls_skipped_dedupe'] = result['urls_skipped_dedupe']

            self.logger.info("RSS ingestion run complete", extra=summary)

        except Exception as e:
            summary['errors'] += 1
            self.logger.error("RSS ingestion run failed", extra={
                'error': str(e),
                'summary': summary
            })

        return summary

    def run_all_providers(self, output_dir: str = 'projects', dry_run: bool = False) -> Dict[str, Any]:
        """
        Run email ingestion for all enabled providers.

        Args:
            output_dir: Directory to save project files
            dry_run: If True, validate configs and simulate operations without side effects

        Returns:
            Aggregated summary dictionary with results from all providers
        """
        self.logger.info("Starting multi-provider email ingestion run", extra={
            'output_dir': output_dir,
            'dry_run': dry_run
        })

        # Get enabled providers
        enabled_providers = self.get_enabled_providers()
        if not enabled_providers:
            self.logger.warning("No enabled providers found with email channels")
            return {
                'providers_processed': 0,
                'total_emails_processed': 0,
                'total_emails_matched': 0,
                'total_urls_found': 0,
                'total_urls_skipped_dedupe': 0,
                'total_projects_saved': 0,
                'total_errors': 0,
                'provider_summaries': {}
            }

        # Run for each provider
        provider_summaries = {}
        total_summary = {
            'providers_processed': 0,
            'total_emails_processed': 0,
            'total_emails_matched': 0,
            'total_urls_found': 0,
            'total_urls_skipped_dedupe': 0,
            'total_projects_saved': 0,
            'total_errors': 0,
            'provider_summaries': provider_summaries
        }

        for provider_id in enabled_providers:
            try:
                self.logger.info(f"Processing provider: {provider_id}")
                summary = self.run_once(provider_id, output_dir, dry_run)
                provider_summaries[provider_id] = summary

                # Aggregate totals
                total_summary['providers_processed'] += 1
                total_summary['total_emails_processed'] += summary.get('emails_processed', 0)
                total_summary['total_emails_matched'] += summary.get('emails_matched', 0)
                total_summary['total_urls_found'] += summary.get('urls_found', 0)
                total_summary['total_urls_skipped_dedupe'] += summary.get('urls_skipped_dedupe', 0)
                total_summary['total_projects_saved'] += summary.get('projects_saved', 0)
                total_summary['total_errors'] += summary.get('errors', 0)

            except Exception as e:
                self.logger.error(f"Failed to process provider {provider_id}", extra={
                    'provider_id': provider_id,
                    'error': str(e)
                })
                provider_summaries[provider_id] = {
                    'provider_id': provider_id,
                    'dry_run': dry_run,
                    'emails_processed': 0,
                    'emails_matched': 0,
                    'urls_found': 0,
                    'urls_skipped_dedupe': 0,
                    'projects_saved': 0,
                    'errors': 1
                }
                total_summary['total_errors'] += 1

        self.logger.info("Multi-provider email ingestion run complete", extra=total_summary)
        return total_summary

    def run_all_providers_rss(self, output_dir: str = 'projects', dry_run: bool = False) -> Dict[str, Any]:
        """
        Run RSS ingestion for all enabled providers.

        Args:
            output_dir: Directory to save project files
            dry_run: If True, validate configs and simulate operations without side effects

        Returns:
            Aggregated summary dictionary with results from all providers
        """
        self.logger.info("Starting multi-provider RSS ingestion run", extra={
            'output_dir': output_dir,
            'dry_run': dry_run
        })

        # Get enabled providers for RSS
        enabled_providers = self.get_enabled_providers('rss')
        if not enabled_providers:
            self.logger.warning("No enabled providers found with RSS channels")
            return {
                'providers_processed': 0,
                'total_entries_found': 0,
                'total_entries_processed': 0,
                'total_urls_skipped_dedupe': 0,
                'total_projects_saved': 0,
                'total_errors': 0,
                'provider_summaries': {}
            }

        # Run for each provider
        provider_summaries = {}
        total_summary = {
            'providers_processed': 0,
            'total_entries_found': 0,
            'total_entries_processed': 0,
            'total_urls_skipped_dedupe': 0,
            'total_projects_saved': 0,
            'total_errors': 0,
            'provider_summaries': provider_summaries
        }

        for provider_id in enabled_providers:
            try:
                self.logger.info(f"Processing RSS provider: {provider_id}")
                summary = self.run_rss_ingestion(provider_id, output_dir, dry_run)
                provider_summaries[provider_id] = summary

                # Aggregate totals
                total_summary['providers_processed'] += 1
                total_summary['total_entries_found'] += summary.get('entries_found', 0)
                total_summary['total_entries_processed'] += summary.get('entries_processed', 0)
                total_summary['total_urls_skipped_dedupe'] += summary.get('urls_skipped_dedupe', 0)
                total_summary['total_projects_saved'] += summary.get('projects_saved', 0)
                total_summary['total_errors'] += summary.get('errors', 0)

            except Exception as e:
                self.logger.error(f"Failed to process RSS provider {provider_id}", extra={
                    'provider_id': provider_id,
                    'error': str(e)
                })
                provider_summaries[provider_id] = {
                    'provider_id': provider_id,
                    'dry_run': dry_run,
                    'entries_found': 0,
                    'entries_processed': 0,
                    'urls_skipped_dedupe': 0,
                    'projects_saved': 0,
                    'errors': 1
                }
                total_summary['total_errors'] += 1

        self.logger.info("Multi-provider RSS ingestion run complete", extra=total_summary)
        return total_summary

    def run_full_workflow(self, output_dir: str = 'projects', dry_run: bool = False) -> Dict[str, Any]:
        """
        Run the complete workflow: RSS ingestion followed by email ingestion for all enabled providers.

        Args:
            output_dir: Directory to save project files
            dry_run: If True, validate configs and simulate operations without side effects

        Returns:
            Aggregated summary dictionary with results from both RSS and email ingestion
        """
        self.logger.info("Starting full workflow run (RSS + Email)", extra={
            'output_dir': output_dir,
            'dry_run': dry_run
        })

        total_summary = {
            'workflow_type': 'full_workflow',
            'dry_run': dry_run,
            'rss_summary': {},
            'email_summary': {},
            'total_projects_saved': 0,
            'total_errors': 0,
            'completed_at': None
        }

        try:
            # Step 1: Run RSS ingestion for all enabled providers
            self.logger.info("Step 1: Running RSS ingestion for all enabled providers")
            rss_summary = self.run_all_providers_rss(output_dir, dry_run)
            total_summary['rss_summary'] = rss_summary
            total_summary['total_projects_saved'] += rss_summary.get('total_projects_saved', 0)
            total_summary['total_errors'] += rss_summary.get('total_errors', 0)

            # Step 2: Run email ingestion for all enabled providers
            self.logger.info("Step 2: Running email ingestion for all enabled providers")
            email_summary = self.run_all_providers(output_dir, dry_run)
            total_summary['email_summary'] = email_summary
            total_summary['total_projects_saved'] += email_summary.get('total_projects_saved', 0)
            total_summary['total_errors'] += email_summary.get('total_errors', 0)

            total_summary['completed_at'] = datetime.now().isoformat()
            self.logger.info("Full workflow run complete", extra=total_summary)

        except Exception as e:
            total_summary['total_errors'] += 1
            total_summary['completed_at'] = datetime.now().isoformat()
            self.logger.error("Full workflow run failed", extra={
                'error': str(e),
                'summary': total_summary
            })

        return total_summary

def run_email_ingestion(provider_ids: str, config: Dict[str, Any], output_dir: str = 'projects', dry_run: bool = False) -> Dict[str, Any]:
    """
    Convenience function to run email ingestion for one or more providers.

    Args:
        provider_ids: Provider identifier(s) - single provider, "all", or comma-separated list
        config: Full configuration dictionary
        output_dir: Output directory for project files
        dry_run: If True, validate config and simulate without side effects

    Returns:
        Summary of the ingestion run(s)
    """
    agent = EmailAgent(config)

    # Handle different provider_id formats
    if provider_ids == "all":
        return agent.run_all_providers(output_dir, dry_run)
    elif "," in provider_ids:
        # Multiple providers specified
        provider_list = [p.strip() for p in provider_ids.split(",")]
        results = []
        for provider_id in provider_list:
            result = agent.run_once(provider_id, output_dir, dry_run)
            results.append(result)
        # Aggregate results (simplified)
        return {
            'providers': provider_list,
            'results': results,
            'total_projects_saved': sum(r.get('projects_saved', 0) for r in results),
            'total_errors': sum(r.get('errors', 0) for r in results)
        }
    else:
        # Single provider
        return agent.run_once(provider_ids, output_dir, dry_run)

# Bug fix (Phase 0): run_rss_ingestion and run_full_workflow were defined twice.
# The first definition of run_rss_ingestion used a hand-rolled "all" loop that
# silently ignored max_age_days; the first run_full_workflow just delegated to
# agent.run_full_workflow() which is a stub. Both first definitions are removed;
# only the second (more complete) definition below is kept.

def run_rss_ingestion(provider_ids: str, config: Dict[str, Any], output_dir: str = 'projects', dry_run: bool = False) -> Dict[str, Any]:
    """
    Convenience function to run RSS ingestion for one or more providers.

    Args:
        provider_ids: Provider identifier(s) - single provider, "all", or comma-separated list
        config: Full configuration dictionary
        output_dir: Output directory for project files
        dry_run: If True, validate config and simulate without side effects

    Returns:
        Summary of the ingestion run(s)
    """
    agent = EmailAgent(config)

    # Handle different provider_id formats
    if provider_ids == "all":
        return agent.run_all_providers_rss(output_dir, dry_run)
    elif "," in provider_ids:
        # Multiple providers specified
        provider_list = [p.strip() for p in provider_ids.split(",")]
        results = []
        for provider_id in provider_list:
            result = agent.run_rss_ingestion(provider_id, output_dir, dry_run)
            results.append(result)
        # Aggregate results (simplified)
        return {
            'providers': provider_list,
            'results': results,
            'total_projects_saved': sum(r.get('projects_saved', 0) for r in results),
            'total_errors': sum(r.get('errors', 0) for r in results)
        }
    else:
        # Single provider
        return agent.run_rss_ingestion(provider_ids, output_dir, dry_run)

def run_full_workflow(config: Dict[str, Any], output_dir: str = 'projects', dry_run: bool = False) -> Dict[str, Any]:
    """
    Run complete workflow: RSS ingestion followed by email ingestion for all enabled providers.

    Args:
        config: Full configuration dictionary
        output_dir: Output directory for project files
        dry_run: If True, validate config and simulate without side effects

    Returns:
        Summary of the complete workflow run
    """
    agent = EmailAgent(config)

    # Run RSS ingestion for all providers
    rss_result = agent.run_all_providers_rss(output_dir, dry_run)

    # Run email ingestion for all providers
    email_result = agent.run_all_providers(output_dir, dry_run)

    # Aggregate results
    total_projects_saved = rss_result.get('total_projects_saved', 0) + email_result.get('total_projects_saved', 0)
    total_entries_found = rss_result.get('total_entries_found', 0)
    total_emails_processed = email_result.get('total_emails_processed', 0)
    total_urls_found = email_result.get('total_urls_found', 0)
    total_urls_skipped_dedupe = rss_result.get('total_urls_skipped_dedupe', 0) + email_result.get('total_urls_skipped_dedupe', 0)
    total_errors = rss_result.get('total_errors', 0) + email_result.get('total_errors', 0)

    return {
        'dry_run': dry_run,
        'total_projects_saved': total_projects_saved,
        'total_entries_found': total_entries_found,
        'total_emails_processed': total_emails_processed,
        'total_urls_found': total_urls_found,
        'total_urls_skipped_dedupe': total_urls_skipped_dedupe,
        'total_errors': total_errors,
        'rss_summary': rss_result,
        'email_summary': email_result
    }