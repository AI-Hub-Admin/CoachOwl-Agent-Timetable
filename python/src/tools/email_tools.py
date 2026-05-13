import traceback
from pathlib import Path
import urllib
import re

from ..constants import FILES_WORKING_DIR  ## Path Variable
from ..utils import read_files

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None

def get_email_config():
    """
    Get email configuration from environment variables.

    Provider selection:
        - EMAIL_PROVIDER: "gmail" (default) or "126"

    Credentials (preferred, provider-specific):
        - Gmail: SENDER_EMAIL_GMAIL, SENDER_PASSWORD_GMAIL, (optional) SENDER_NAME_GMAIL
        - 126:   SENDER_EMAIL_126,   SENDER_PASSWORD_126,   (optional) SENDER_NAME_126

    Credentials (fallback, provider-agnostic):
        - SENDER_EMAIL, SENDER_PASSWORD, (optional) SENDER_NAME
    """
    provider = (os.getenv("EMAIL_PROVIDER", "gmail") or "gmail").strip().lower()
    if provider in {"gmail.com", "googlemail.com"}:
        provider = "gmail"
    provider_config = EMAIL_PROVIDER_CONFIGS.get(provider)
    if not provider_config:
        raise ValueError(
            f"Unsupported EMAIL_PROVIDER={provider!r}. Supported: {', '.join(sorted(EMAIL_PROVIDER_CONFIGS.keys()))}"
        )

    provider_env_suffix = provider.upper()
    sender_email = os.getenv(f"SENDER_EMAIL_{provider_env_suffix}", "") or os.getenv("SENDER_EMAIL", "")
    sender_password = os.getenv(f"SENDER_PASSWORD_{provider_env_suffix}", "") or os.getenv("SENDER_PASSWORD", "")
    sender_name = os.getenv(f"SENDER_NAME_{provider_env_suffix}", "") or os.getenv("SENDER_NAME", "")

    if sender_password == "" or sender_email == "":
        raise ValueError(
            "Missing email credentials. Please set either provider-specific env vars "
            f"(SENDER_EMAIL_{provider_env_suffix}, SENDER_PASSWORD_{provider_env_suffix}) "
            "or generic ones (SENDER_EMAIL, SENDER_PASSWORD)."
        )

    return {
        "provider": provider,
        "smtp_server": provider_config["smtp_server"],
        "smtp_port": provider_config["smtp_port"],
        "use_tls": provider_config.get("use_tls", True),
        "sender_email": sender_email,
        "sender_password": sender_password,
        "sender_name": sender_name,
    }

# Email provider configuration mapping
EMAIL_PROVIDER_CONFIGS = {
    "gmail": {
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "use_tls": True
    },
    "qq": {
        "smtp_server": "smtp.qq.com",
        "smtp_port": 587,
        "use_tls": True
    },
    "163": {
        "smtp_server": "smtp.163.com",
        "smtp_port": 25,
        "use_tls": False
    },
    "126": {
        "smtp_server": "smtp.126.com",
        "smtp_port": 25,
        "use_tls": False
    }
}

import smtplib
import logging
import os
from typing import List, Dict, Any, Optional, Callable
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.base import MIMEBase
from email.header import Header
from email.utils import formataddr
from email import encoders
import mimetypes


# Files working directory - auto-resolve relative paths
def get_files_working_dir():
    """
    Get the file working directory with an auto-discovery strategy:
    1) Use `files-wd` relative to the current working directory
    2) Walk up from this script directory to find a directory containing `files-wd` (up to 10 levels)
    3) Fallback to `files-wd` under the current working directory

    This allows the code to work across different machines/directories without manual configuration.
    """
    # Prefer a working dir relative to the current working directory
    # cwd_files_wd = Path.cwd() / "assets" / "files-wd"
    cwd_files_wd = FILES_WORKING_DIR
    if cwd_files_wd.exists():
        return cwd_files_wd.resolve()

    # Walk up from this script to find a directory containing `files-wd`
    script_dir = Path(__file__).parent
    current = script_dir
    max_levels = 10  # Walk up at most 10 levels
    for _ in range(max_levels):
        files_wd = current / "files-wd"
        if files_wd.exists():
            return files_wd.resolve()
        parent = current.parent
        if parent == current:  # Reached filesystem root
            break
        current = parent

    # Fallback: use `files-wd` under current working directory
    return Path.cwd() / "files-wd"


# FILES_WORKING_DIR = get_files_working_dir()
logging.info(f"INFO: Files working directory: {FILES_WORKING_DIR}")

def is_html_content(body: str) -> bool:
    """
    Simple HTML detection using html tag matching.

    Returns:
        True if body contains HTML tags
    """

    if not body:
        return False

    # simple html tag detection
    html_pattern = re.compile(
        r"<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>.*?</\1>|<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*/?>",
        re.IGNORECASE | re.DOTALL
    )

    return bool(html_pattern.search(body))

def util_send_email_with_attachment(
        recipient_email: str,
        subject: str,
        body: str,
        attachment_paths: List[str],
        email_config: Optional[Dict[str, Any]] = None,
        resolve_file_path_func: Optional[Callable[[str], Any]] = None
) -> Dict[str, Any]:
    """
    Send an email with attachments via SMTP (supports Gmail/126 via EMAIL_PROVIDER_CONFIGS).

    Args:
        recipient_email: Recipient email address.
        subject: Email subject.
        body: Email body (plain text or HTML).
        attachment_paths: List of attachment paths.
        email_config: Email configuration. If None, read from environment variables (see get_email_config()).
        resolve_file_path_func: Optional path resolver for attachments.

    Returns:
        Dict: Send result.
    """
    try:
        # Load credentials/config either from env (default) or from an explicit config dict
        if email_config is None:
            email_config = get_email_config()

        sender_email = (email_config or {}).get("sender_email", "")
        sender_password = (email_config or {}).get("sender_password", "")
        sender_name = (email_config or {}).get("sender_name", "")

        mail_host = (email_config or {}).get("smtp_server", "")
        mail_port = int((email_config or {}).get("smtp_port", 0) or 0)
        use_tls = bool((email_config or {}).get("use_tls", True))

        if not sender_email or not sender_password or not mail_host or not mail_port:
            raise ValueError("Invalid email configuration. Please check environment variables or email_config.")

        # Create message
        message = MIMEMultipart('alternative')
        message.set_charset('utf-8')

        # Set sender headers
        sender_name_encoded = Header(sender_name, 'utf-8').encode()
        message['From'] = formataddr((sender_name_encoded, sender_email))
        message['To'] = recipient_email
        message['Subject'] = Header(subject, 'utf-8')

        # Add body
        if is_html_content(body):
            text_part = MIMEText(body, 'html', 'utf-8')
        else:
            text_part = MIMEText(body, 'plain', 'utf-8')
        # text_part = MIMEText(body, 'plain', 'utf-8')
        message.attach(text_part)

        # Add attachments
        valid_attachments = 0
        for file_path in attachment_paths:
            try:
                # Resolve path if a resolver is provided
                if resolve_file_path_func:
                    actual_file_path = resolve_file_path_func(file_path)
                else:
                    actual_file_path = file_path

                # Accept either a Path-like object or a string
                if hasattr(actual_file_path, 'exists'):
                    file_exists = actual_file_path.exists()
                    file_path_str = str(actual_file_path)
                else:
                    file_exists = os.path.exists(str(actual_file_path))
                    file_path_str = str(actual_file_path)

                if not file_exists:
                    logging.warning(f"Attachment file not found: {file_path_str}")
                    continue

                # Read file
                with open(file_path_str, 'rb') as f:
                    file_data = f.read()
                    file_name = os.path.basename(file_path_str)

                # Determine MIME type
                file_extension = os.path.splitext(file_name)[1].lower()
                mime_type_map = {
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.png': 'image/png',
                    '.gif': 'image/gif',
                    '.bmp': 'image/bmp',
                    '.tiff': 'image/tiff',
                    '.pdf': 'application/pdf',
                    '.doc': 'application/msword',
                    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    '.xls': 'application/vnd.ms-excel',
                    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    '.ppt': 'application/vnd.ms-powerpoint',
                    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                    '.txt': 'text/plain',
                    '.zip': 'application/zip',
                    '.rar': 'application/x-rar-compressed'
                }

                ctype = mime_type_map.get(file_extension)
                if ctype is None:
                    ctype, encoding = mimetypes.guess_type(file_path_str)
                    if ctype is None or encoding is not None:
                        ctype = 'application/octet-stream'

                maintype, subtype = ctype.split('/', 1)

                # Pick the appropriate MIME class
                if maintype == 'image':
                    if subtype == 'jpeg':
                        attach_part = MIMEImage(file_data, 'jpeg')
                    else:
                        attach_part = MIMEImage(file_data, subtype)
                elif maintype == 'application':
                    attach_part = MIMEApplication(file_data, subtype)
                else:
                    attach_part = MIMEBase(maintype, subtype)
                    attach_part.set_payload(file_data)
                    encoders.encode_base64(attach_part)

                # Attachment headers
                attach_part['Content-Disposition'] = f'attachment; filename="{file_name}"'
                attach_part.add_header('Content-Type', ctype)
                message.attach(attach_part)
                valid_attachments += 1
                logging.info(f"Added attachment: {file_name} (MIME: {ctype})")

            except Exception as e:
                logging.warning(f"Failed to add attachment {file_path}: {e}")
                continue

        # Send email
        logging.info(f"Connecting to SMTP server: {mail_host}:{mail_port}")
        try:
            smtp = smtplib.SMTP(mail_host, mail_port, timeout=30)
            if use_tls:
                smtp.starttls()  # Enable TLS
                logging.info(f"TLS started, logging in as {sender_email}...")
            else:
                logging.info(f"Logging in as {sender_email} (no TLS)...")
            smtp.login(sender_email, sender_password)
            logging.info(f"Logged in successfully, sending email...")
            smtp.sendmail(sender_email, recipient_email, message.as_string())
            smtp.quit()
            logging.info(f"Email sent successfully to {recipient_email}")

            return {
                "success": True,
                "message": f"Email sent successfully to {recipient_email}",
                "attachments_count": valid_attachments
            }
        except smtplib.SMTPAuthenticationError as e:
            logging.error(f"SMTP authentication failed: {str(e)}")
            return {
                "success": False,
                "message": f"Email authentication failed: {str(e)}",
                "attachments_count": 0
            }
        except smtplib.SMTPConnectError as e:
            logging.error(f"SMTP connection failed: {str(e)}")
            return {
                "success": False,
                "message": f"Email server connection failed: {str(e)}",
                "attachments_count": 0
            }
        except Exception as e:
            logging.error(f"SMTP send error: {str(e)}")
            return {
                "success": False,
                "message": f"Email send error: {str(e)}",
                "attachments_count": 0
            }

    except Exception as e:
        logging.error(f"Failed to send email: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Email send failed: {str(e)}",
            "attachments_count": 0
        }


def resolve_file_path(file_path: str) -> Path:
    """
    Resolve file paths. Supports the `/files-wd/` virtual prefix and relative paths.

    Args:
        file_path: Can be one of:
            - `/files-wd/...` virtual path
            - absolute path
            - relative path

    Returns:
        Path: Resolved path
    """
    print (f"DEBUG: Input resolve_file_path input file_path {file_path}")
    # Virtual `/files-wd/` path
    if file_path.startswith('/files-wd/'):
        # Strip `/files-wd/`
        relative_path = file_path[10:]  # strip '/files-wd/'

        # Remove `download/` segment if present
        if relative_path.startswith('download/'):
            relative_path = relative_path[9:]  # strip 'download/'

        # URL-decode each path segment
        path_parts = relative_path.split('/')
        decoded_parts = []
        for part in path_parts:
            if part:
                try:
                    decoded_part = urllib.parse.unquote(part)
                    decoded_parts.append(decoded_part)
                except:
                    decoded_parts.append(part)

        # Build path relative to FILES_WORKING_DIR
        resolved_path = FILES_WORKING_DIR / '/'.join(decoded_parts)
        logging.info(f"Resolved /files-wd/ path: {file_path} -> {resolved_path}")
        return resolved_path

    # Absolute path
    if os.path.isabs(file_path):
        return Path(file_path)

    # Relative to current working directory
    return Path.cwd() / file_path


def resolve_file_wd_path(file_path: str) -> Path:
    """
    Resolve a path relative to the file workspace.

    Base: `assets/files-wd/user_id/uuid/xxxx.html`

    Args:
        file_path: Can be one of:
            - `/files-wd/...` virtual path
            - absolute path
            - relative path

    Returns:
        Path: Resolved path
    """
    print (f"DEBUG: Input resolve_file_path input file_path {file_path}")
    # Virtual `/files-wd/` path
    if file_path.startswith('/files-wd/'):
        # Strip `/files-wd/`
        relative_path = file_path[10:]  # strip '/files-wd/'

        # Remove `download/` segment if present
        if relative_path.startswith('download/'):
            relative_path = relative_path[9:]  # strip 'download/'

        # URL-decode each path segment
        path_parts = relative_path.split('/')
        decoded_parts = []
        for part in path_parts:
            if part:
                try:
                    decoded_part = urllib.parse.unquote(part)
                    decoded_parts.append(decoded_part)
                except:
                    decoded_parts.append(part)

        # Build path relative to FILES_WORKING_DIR
        resolved_path = FILES_WORKING_DIR / '/'.join(decoded_parts)
        logging.info(f"Resolved /files-wd/ path: {file_path} -> {resolved_path}")
        return resolved_path

    # Absolute path
    if os.path.isabs(file_path):
        return Path(file_path)

    # Relative to the user files directory: /assets/files-wd
    return FILES_WORKING_DIR / file_path

def send_email_with_attachments(
        recipient_email: str,
        subject: str,
        body: str,
        attachment_paths: List[str]
) -> Dict[str, Any]:
    """
    Send an email with attachments.

    Default provider is Gmail. Override via:
        - EMAIL_PROVIDER="gmail" (default) or "126"

    Gmail credentials (recommended):
        - SENDER_EMAIL_GMAIL, SENDER_PASSWORD_GMAIL, (optional) SENDER_NAME_GMAIL

    126 credentials:
        - SENDER_EMAIL_126, SENDER_PASSWORD_126, (optional) SENDER_NAME_126

    Args:
        recipient_email: Recipient email address
        subject: Email subject
        body: Email body (plain text or HTML)
        attachment_paths: List of attachment paths

    Returns:
        Dict: Send result
    """
    try:
        # Credentials are loaded from environment variables
        result = util_send_email_with_attachment(
            recipient_email=recipient_email,
            subject=subject,
            body=body,
            attachment_paths=attachment_paths,
            email_config=None,
            resolve_file_path_func=resolve_file_wd_path  # pass in a path resolver
        )

        return result

    except Exception as e:
        logging.error(f"Failed to send email: {str(e)}")
        logging.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Email send failed: {str(e)}",
            "attachments_count": 0
        }

def main():
    """
        run: python -m python.src.tools.email_tools

        WARNING:root:Attachment file not found: /Users/rockingdingo/Desktop/workspace/github/aiagenta2z/agent_auto/coachowl/user_4f3c/0de42f4c-e973-4fd3-aff3-5a81c3c9bda6/research_report.html
        DEBUG: send_email_with_attachments result {'success': True, 'message': 'Email sent successfully to ...', 'attachments_count': 0}

        Main function
    """
    recipient_email = os.getenv("RECIPIENT_EMAIL", "")
    if not recipient_email:
        raise ValueError("Please set RECIPIENT_EMAIL to run this demo.")

    subject = os.getenv("EMAIL_SUBJECT", "Demo Email With Attachments")

    # Demo report path (relative to the files working directory)
    body_html_path = "user_4f3c/0de42f4c-e973-4fd3-aff3-5a81c3c9bda6/research_report.html"
    resolve_file_path_body = resolve_file_wd_path(body_html_path)
    resolve_file_path_body_str = str(resolve_file_path_body.resolve())
    print (f"DEBUG: resolve_file_path_body {resolve_file_path_body_str}")
    content_html = read_files(resolve_file_path_body_str)
    body = content_html
    print (f"DEBUG: Reading From Path {resolve_file_path_body.resolve()} and content Length {len(content_html)}")
    # Paths relative to the `files-wd` working directory
    attachment_paths = ["user_4f3c/0de42f4c-e973-4fd3-aff3-5a81c3c9bda6/research_report.html"]
    # attachment_paths = ["./user_4f3c/0de42f4c-e973-4fd3-aff3-5a81c3c9bda6/research_report.html"]
    result = send_email_with_attachments(recipient_email, subject, body, attachment_paths)
    print(f"DEBUG: send_email_with_attachments result {result}")

if __name__ == '__main__':
    main()
