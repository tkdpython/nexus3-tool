"""Credential storage and retrieval for nexus3-tool."""

import json
import os
from pathlib import Path
from typing import Dict

CREDENTIALS_FILE = Path.home() / ".nexus-credentials"


def save_credentials(url, username, password, verify=True):
    # type: (str, str, str, bool) -> None
    """Save Nexus3 credentials to ~/.nexus-credentials (mode 600)."""
    creds = {
        "url": url.rstrip("/"),
        "username": username,
        "password": password,
        "verify": verify,
    }
    with open(str(CREDENTIALS_FILE), "w") as f:
        json.dump(creds, f, indent=2)
    try:
        os.chmod(str(CREDENTIALS_FILE), 0o600)
    except OSError:
        # chmod is a no-op on Windows — not a fatal error
        pass


def load_credentials():
    # type: () -> Dict[str, str]
    """Load credentials from ~/.nexus-credentials.

    Raises SystemExit if the file does not exist.
    """
    if not CREDENTIALS_FILE.exists():
        raise SystemExit("No credentials found. Run 'nexus3-tool login <url>' first.")
    with open(str(CREDENTIALS_FILE), "r") as f:
        return json.load(f)
