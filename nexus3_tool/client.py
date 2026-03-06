"""Nexus3 REST API client for nexus3-tool."""

from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

import requests
from requests.exceptions import ConnectionError, HTTPError, Timeout


class Nexus3Error(Exception):
    """Raised for all Nexus3 API errors."""

    pass


def _parse_date(date_str):
    # type: (Optional[str]) -> datetime
    """Parse a Nexus3 ISO-8601 date string.  Python 3.6+ compatible."""
    if not date_str:
        return datetime.min
    # Strip timezone suffix so strptime works on 3.6 (no %z fromisoformat)
    clean = date_str.split("+")[0].split("Z")[0].strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(clean, fmt)
        except ValueError:
            continue
    return datetime.min


def _get_last_modified(component):
    # type: (Dict[str, Any]) -> datetime
    """Return the most recent lastModified timestamp across a component's assets."""
    best = datetime.min
    for asset in component.get("assets", []):
        ts = _parse_date(asset.get("lastModified"))
        if ts > best:
            best = ts
    return best


class Nexus3Client:
    """Thin wrapper around the Nexus3 REST API."""

    def __init__(self, url, username, password):
        # type: (str, str, str) -> None
        self.base_url = url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path, params=None):
        # type: (str, Optional[Dict]) -> Any
        try:
            resp = self.session.get(
                "{0}{1}".format(self.base_url, path),
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except HTTPError as exc:
            code = exc.response.status_code
            if code == 401:
                raise Nexus3Error("Authentication failed. Check your credentials.")
            if code == 403:
                raise Nexus3Error("Forbidden — you do not have permission to do that.")
            if code == 404:
                raise Nexus3Error("Not found: {0}".format(path))
            raise Nexus3Error("HTTP {0}: {1}".format(code, exc))
        except ConnectionError:
            raise Nexus3Error("Cannot connect to Nexus at {0}".format(self.base_url))
        except Timeout:
            raise Nexus3Error("Connection timed out.")

    def _delete(self, path):
        # type: (str) -> None
        try:
            resp = self.session.delete(
                "{0}{1}".format(self.base_url, path),
                timeout=30,
            )
            resp.raise_for_status()
        except HTTPError as exc:
            code = exc.response.status_code
            if code == 401:
                raise Nexus3Error("Authentication failed.")
            if code == 403:
                raise Nexus3Error("Forbidden — you do not have permission to delete.")
            raise Nexus3Error("HTTP {0}: {1}".format(code, exc))
        except ConnectionError:
            raise Nexus3Error("Cannot connect to Nexus at {0}".format(self.base_url))
        except Timeout:
            raise Nexus3Error("Connection timed out.")

    def _iter_pages(self, path, params=None):
        # type: (str, Optional[Dict]) -> Iterator[Dict]
        """Yield every item from a paginated Nexus3 endpoint."""
        params = dict(params) if params else {}
        while True:
            data = self._get(path, params=params)
            for item in data.get("items", []):
                yield item
            token = data.get("continuationToken")
            if not token:
                break
            params["continuationToken"] = token

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_auth(self):
        # type: () -> None
        """Verify that credentials are valid by listing repositories."""
        self._get("/service/rest/v1/repositories")

    def list_repositories(self):
        # type: () -> List[Dict]
        """Return all repositories."""
        return self._get("/service/rest/v1/repositories")

    def list_docker_repositories(self):
        # type: () -> List[Dict]
        """Return all Docker-format repositories."""
        return [r for r in self.list_repositories() if r.get("format") == "docker"]

    def list_docker_images(self, repository):
        # type: (str) -> Dict[str, List[str]]
        """Return a dict of {image_name: [tag, ...]} for a Docker repository."""
        images = {}  # type: Dict[str, List[str]]
        for comp in self._iter_pages("/service/rest/v1/components", {"repository": repository}):
            name = comp.get("name", "")
            version = comp.get("version", "?")
            if name not in images:
                images[name] = []
            images[name].append(version)
        return images

    def get_image_components(self, repository, image_name):
        # type: (str, str) -> List[Dict]
        """Return all components (one per tag) for an image in a repository."""
        return list(
            self._iter_pages(
                "/service/rest/v1/search",
                {"repository": repository, "name": image_name, "format": "docker"},
            )
        )

    def delete_component(self, component_id):
        # type: (str) -> None
        """Delete a single component by ID."""
        self._delete("/service/rest/v1/components/{0}".format(component_id))
