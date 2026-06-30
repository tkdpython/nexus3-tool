"""Nexus3 REST API client for nexus3-tool."""

from datetime import datetime
from fnmatch import fnmatchcase
from typing import Any, Dict, Iterator, List, Optional

import requests
from requests.exceptions import ConnectionError, HTTPError, SSLError, Timeout

DOCKER_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v1+json",
        "application/json",
    ]
)


class Nexus3Error(Exception):
    """Raised for all Nexus3 API errors."""

    pass


class Nexus3SSLError(Nexus3Error):
    """Raised when the server certificate cannot be verified."""

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


def _get_manifest_digest(component):
    # type: (Dict[str, Any]) -> Optional[str]
    """Return the sha256 digest of the manifest asset for a component, or None."""
    for asset in component.get("assets", []):
        path = asset.get("path", "")
        if "/manifests/" in path:
            return asset.get("checksum", {}).get("sha256")
    return None


def _get_last_modified(component):
    # type: (Dict[str, Any]) -> datetime
    """Return the most recent lastModified timestamp across a component's assets."""
    best = datetime.min
    for asset in component.get("assets", []):
        ts = _parse_date(asset.get("lastModified"))
        if ts > best:
            best = ts
    return best


def _get_component_size(component):
    # type: (Dict[str, Any]) -> int
    """Return the total file size, in bytes, across a component's assets."""
    return sum(size for _key, size in _get_asset_usage_entries(component))


def _get_asset_usage_entries(component):
    # type: (Dict[str, Any]) -> List
    """Return stable dedupe keys and sizes for a component's REST assets.

    For Docker images this is usually just the manifest asset, which is tiny.
    Prefer ``Nexus3Client.get_component_image_usage`` when a client is available;
    it downloads the manifest JSON and sums the config/layer sizes.
    """
    entries = []
    for asset in component.get("assets", []):
        try:
            size = int(asset.get("fileSize") or 0)
        except (TypeError, ValueError):
            size = 0
        checksum = asset.get("checksum") or {}
        key = (
            checksum.get("sha256")
            or checksum.get("sha1")
            or checksum.get("md5")
            or asset.get("id")
            or asset.get("downloadUrl")
            or asset.get("path")
        )
        entries.append((key, size))
    return entries


def _get_manifest_asset(component):
    # type: (Dict[str, Any]) -> Optional[Dict[str, Any]]
    """Return the Docker manifest asset for a component, if Nexus exposes one."""
    for asset in component.get("assets", []):
        path = asset.get("path", "")
        if "/manifests/" in path:
            return asset
    return None


def _get_manifest_usage_entries(manifest):
    # type: (Dict[str, Any]) -> List
    """Return dedupe keys and compressed sizes from a Docker/OCI manifest JSON."""
    entries = []
    config = manifest.get("config") or {}
    config_digest = config.get("digest")
    config_size = config.get("size")
    if config_digest and config_size is not None:
        entries.append((config_digest, int(config_size or 0)))
    for layer in manifest.get("layers") or []:
        digest = layer.get("digest")
        size = layer.get("size")
        if digest and size is not None:
            entries.append((digest, int(size or 0)))
    return entries


def _replace_manifest_reference(download_url, reference):
    # type: (str, str) -> Optional[str]
    """Return DOWNLOAD_URL with its /manifests/<ref> suffix replaced."""
    marker = "/manifests/"
    if not download_url or marker not in download_url:
        return None
    return download_url.rsplit(marker, 1)[0] + marker + reference


def _has_wildcards(value):
    # type: (Optional[str]) -> bool
    """Return True if VALUE contains shell-style wildcard characters."""
    return bool(value and ("*" in value or "?" in value))


class Nexus3Client:
    """Thin wrapper around the Nexus3 REST API."""

    def __init__(self, url, username, password, verify=True):
        # type: (str, str, str, bool) -> None
        self.base_url = url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({"Accept": "application/json"})
        self.session.verify = verify
        if not verify:
            # Suppress the InsecureRequestWarning when verification is disabled
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
            code = exc.response.status_code if exc.response is not None else "unknown"
            if code == 401:
                raise Nexus3Error("Authentication failed. Check your credentials.")
            if code == 403:
                raise Nexus3Error("Forbidden — you do not have permission to do that.")
            if code == 404:
                raise Nexus3Error("Not found: {0}".format(path))
            raise Nexus3Error("HTTP {0}: {1}".format(code, exc))
        except SSLError as exc:
            raise Nexus3SSLError("SSL certificate verification failed: {0}".format(exc))
        except ConnectionError:
            raise Nexus3Error("Cannot connect to Nexus at {0}".format(self.base_url))
        except Timeout:
            raise Nexus3Error("Connection timed out.")

    def _get_json_url(self, url, headers=None):
        # type: (str, Optional[Dict]) -> Any
        """GET an absolute URL and return its JSON body."""
        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "unknown"
            if code == 401:
                raise Nexus3Error("Authentication failed. Check your credentials.")
            if code == 403:
                raise Nexus3Error("Forbidden — you do not have permission to read the Docker manifest.")
            if code == 404:
                raise Nexus3Error("Not found: {0}".format(url))
            raise Nexus3Error("HTTP {0}: {1}".format(code, exc))
        except ValueError:
            raise Nexus3Error("Invalid JSON returned by {0}".format(url))
        except SSLError as exc:
            raise Nexus3SSLError("SSL certificate verification failed: {0}".format(exc))
        except ConnectionError:
            raise Nexus3Error("Cannot connect to Nexus at {0}".format(self.base_url))
        except Timeout:
            raise Nexus3Error("Connection timed out.")

    def _get_manifest_json(self, url):
        # type: (str) -> Dict
        return self._get_json_url(url, headers={"Accept": DOCKER_MANIFEST_ACCEPT})

    def _delete(self, path):
        # type: (str) -> None
        try:
            resp = self.session.delete(
                "{0}{1}".format(self.base_url, path),
                timeout=30,
            )
            resp.raise_for_status()
        except HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "unknown"
            if code == 401:
                raise Nexus3Error("Authentication failed.")
            if code == 403:
                raise Nexus3Error("Forbidden — you do not have permission to delete.")
            raise Nexus3Error("HTTP {0}: {1}".format(code, exc))
        except SSLError as exc:
            raise Nexus3SSLError("SSL certificate verification failed: {0}".format(exc))
        except ConnectionError:
            raise Nexus3Error("Cannot connect to Nexus at {0}".format(self.base_url))
        except Timeout:
            raise Nexus3Error("Connection timed out.")

    def _post(self, path):
        # type: (str) -> None
        try:
            resp = self.session.post(
                "{0}{1}".format(self.base_url, path),
                timeout=30,
            )
            resp.raise_for_status()
        except HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "unknown"
            if code == 401:
                raise Nexus3Error("Authentication failed.")
            if code == 403:
                raise Nexus3Error("Forbidden — you do not have permission to run that task.")
            raise Nexus3Error("HTTP {0}: {1}".format(code, exc))
        except SSLError as exc:
            raise Nexus3SSLError("SSL certificate verification failed: {0}".format(exc))
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

    def get_repository(self, repository):
        # type: (str) -> Optional[Dict]
        """Return repository metadata for REPOSITORY, or None when not found."""
        for repo in self.list_repositories():
            if repo.get("name") == repository:
                return repo
        return None

    def get_repository_blob_store_name(self, repository):
        # type: (str) -> Optional[str]
        """Return the blob store used by REPOSITORY when Nexus exposes it."""
        repo = self.get_repository(repository)
        if not repo:
            return None
        attrs = repo.get("attributes") or {}
        storage = attrs.get("storage") or {}
        return storage.get("blobStoreName")

    def list_blob_stores(self):
        # type: () -> List[Dict]
        """Return blob store quota/usage information when permitted by Nexus."""
        return self._get("/service/rest/v1/blobstores")

    def get_blob_store(self, name):
        # type: (str) -> Optional[Dict]
        """Return blob store information by name, or None when not found."""
        for store in self.list_blob_stores():
            if store.get("name") == name:
                return store
        return None

    def list_docker_repositories(self):
        # type: () -> List[Dict]
        """Return all Docker-format repositories."""
        return [r for r in self.list_repositories() if r.get("format") == "docker"]

    def get_component_image_usage(self, component):
        # type: (Dict[str, Any]) -> List
        """Return dedupe keys and compressed image sizes for a Docker component.

        Nexus REST component assets for Docker usually represent only the JSON
        manifest object, so their ``fileSize`` is often just a few KiB. The real
        compressed image size is recorded inside the Docker/OCI manifest as the
        config and layer descriptor sizes. This method downloads the manifest
        from the asset ``downloadUrl`` and returns those descriptor sizes.
        """
        manifest_asset = _get_manifest_asset(component)
        if not manifest_asset:
            return _get_asset_usage_entries(component)
        download_url = manifest_asset.get("downloadUrl")
        if not download_url:
            return _get_asset_usage_entries(component)
        try:
            manifest = self._get_manifest_json(download_url)
            entries = _get_manifest_usage_entries(manifest)

            # Multi-platform tags return an index/manifest-list. Follow each
            # child digest and add the usage from every platform manifest.
            for descriptor in manifest.get("manifests") or []:
                digest = descriptor.get("digest")
                child_url = _replace_manifest_reference(download_url, digest)
                if not child_url:
                    continue
                child_manifest = self._get_manifest_json(child_url)
                entries.extend(_get_manifest_usage_entries(child_manifest))

            if entries:
                return entries
        except Nexus3Error:
            return _get_asset_usage_entries(component)
        return _get_asset_usage_entries(component)

    def list_docker_components(self, repository):
        # type: (str) -> List[Dict]
        """Return all Docker components in REPOSITORY."""
        return list(self._iter_pages("/service/rest/v1/components", {"repository": repository}))

    def _component_to_image_row(self, comp):
        # type: (Dict[str, Any]) -> Dict[str, Any]
        """Convert a raw Nexus Docker component to a CLI image row."""
        asset_usage = _get_asset_usage_entries(comp)
        return {
            "name": comp.get("name", ""),
            "tag": comp.get("version", "?"),
            "published": _get_last_modified(comp),
            "metadata_size": sum(size for _key, size in asset_usage),
            "asset_usage": asset_usage,
        }

    def iter_docker_images(self, repository, name=None, progress_callback=None):
        # type: (str, Optional[str], Any) -> Iterator[Dict]
        """Yield Docker image rows incrementally.

        This intentionally does not download Docker manifests or blob layers.
        It only uses Nexus component metadata so listing remains fast and safe
        on large production repositories.
        """
        wildcard_name = _has_wildcards(name)
        name_pattern = name or ""
        if name and not wildcard_name:
            endpoint = "/service/rest/v1/search"
            params = {"repository": repository, "name": name, "format": "docker"}
        else:
            endpoint = "/service/rest/v1/components"
            params = {"repository": repository}

        count = 0
        seen_keys = set()
        for comp in self._iter_pages(endpoint, params):
            comp_key = comp.get("id") or (comp.get("name"), comp.get("version"))
            seen_keys.add(comp_key)
            comp_name = comp.get("name", "")
            if wildcard_name and not fnmatchcase(comp_name, name_pattern):
                continue
            if name and not wildcard_name and comp_name != name:
                continue
            count += 1
            if progress_callback:
                progress_callback(count, comp)
            yield self._component_to_image_row(comp)

        if name and not wildcard_name:
            # Nexus search indexing can lag or return a partial result just
            # after a push. Merge with /components, which is authoritative.
            for comp in self._iter_pages("/service/rest/v1/components", {"repository": repository}):
                comp_key = comp.get("id") or (comp.get("name"), comp.get("version"))
                if comp.get("name") == name and comp_key not in seen_keys:
                    seen_keys.add(comp_key)
                    count += 1
                    if progress_callback:
                        progress_callback(count, comp)
                    yield self._component_to_image_row(comp)

    def list_docker_images(self, repository, name=None):
        # type: (str, Optional[str]) -> List[Dict]
        """Return a list of components with name, tag, date and size.

        When name is provided without wildcards, uses the /search endpoint which
        supports server-side name filtering. Shell-style wildcards (* and ?) are
        matched client-side against component image names.
        """
        return list(self.iter_docker_images(repository, name=name))

    def get_image_components(self, repository, image_name):
        # type: (str, str) -> List[Dict]
        """Return all components (one per tag) for an image in a repository."""
        items = list(
            self._iter_pages(
                "/service/rest/v1/search",
                {"repository": repository, "name": image_name, "format": "docker"},
            )
        )
        # Search indexing can lag or return partial results just after push;
        # merge with /components, which is authoritative.
        seen_ids = set(comp.get("id") for comp in items)
        for comp in self._iter_pages("/service/rest/v1/components", {"repository": repository}):
            if comp.get("name") == image_name and comp.get("id") not in seen_ids:
                items.append(comp)
                seen_ids.add(comp.get("id"))
        return items

    def delete_component(self, component_id):
        # type: (str) -> None
        """Delete a single component by ID."""
        self._delete("/service/rest/v1/components/{0}".format(component_id))

    def list_tasks(self):
        # type: () -> List[Dict]
        """Return Nexus scheduled tasks when permitted."""
        data = self._get("/service/rest/v1/tasks")
        return data.get("items", []) if isinstance(data, dict) else data

    def run_task(self, task_id):
        # type: (str) -> None
        """Run a Nexus scheduled task by ID."""
        self._post("/service/rest/v1/tasks/{0}/run".format(task_id))
