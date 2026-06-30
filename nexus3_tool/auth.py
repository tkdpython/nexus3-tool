"""Credential storage and retrieval for nexus3-tool."""

import json
import os
from pathlib import Path
from typing import Dict

CREDENTIALS_FILE = Path.home() / ".nexus-credentials"


def _credentials_file(profile=None):
    # type: (str) -> Path
    """Return the credentials file path for PROFILE.

    The default profile keeps backwards compatibility with the historical
    ~/.nexus-credentials path. Named profiles are stored as sibling files so
    users can switch between lab/staging/prod without overwriting credentials.
    """
    if not profile or profile == "default":
        return CREDENTIALS_FILE
    safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in profile)
    return Path.home() / (".nexus-credentials-{0}".format(safe))


def save_credentials(url, username, password, verify=True, profile=None):
    # type: (str, str, str, bool, str) -> None
    """Save Nexus3 credentials to a profile credentials file (mode 600)."""
    creds = {
        "url": url.rstrip("/"),
        "username": username,
        "password": password,
        "verify": verify,
    }
    credentials_file = _credentials_file(profile)
    with open(str(credentials_file), "w") as f:
        json.dump(creds, f, indent=2)
    try:
        os.chmod(str(credentials_file), 0o600)
    except OSError:
        # chmod is a no-op on Windows — not a fatal error
        pass


def load_credentials(profile=None):
    # type: (str) -> Dict[str, str]
    """Load credentials from environment or a profile credentials file.

    Environment variables are preferred so CI can run without writing secrets:
    NEXUS_URL, NEXUS_USERNAME, NEXUS_PASSWORD and optional NEXUS_VERIFY_SSL.

    Raises SystemExit if the file does not exist.
    """
    env_url = os.environ.get("NEXUS_URL")
    env_username = os.environ.get("NEXUS_USERNAME")
    env_password = os.environ.get("NEXUS_PASSWORD")
    if env_url and env_username and env_password:
        verify_raw = os.environ.get("NEXUS_VERIFY_SSL", "true").strip().lower()
        verify = verify_raw not in ("0", "false", "no", "off")
        return {"url": env_url.rstrip("/"), "username": env_username, "password": env_password, "verify": verify}

    credentials_file = _credentials_file(profile)
    if not credentials_file.exists():
        profile_note = " for profile '{0}'".format(profile) if profile else ""
        raise SystemExit("No credentials found{0}. Run 'nexus3-tool login <url>' first.".format(profile_note))
    with open(str(credentials_file), "r") as f:
        return json.load(f)
