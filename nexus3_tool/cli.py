"""
nexus3-tool CLI entry point.

Commands follow a docker-style pattern:
    nexus3-tool login <url>
    nexus3-tool list-docker-repos
    nexus3-tool list-docker-images <repo>
    nexus3-tool delete-docker-images <repo> --image-name <image> --tags <tag1,tag2>
    nexus3-tool prune-docker-images <repo> --image-name <image> --keep-last <n>
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta

import click

from nexus3_tool import __version__
from nexus3_tool.auth import load_credentials, save_credentials
from nexus3_tool.client import (
    Nexus3Client,
    Nexus3Error,
    Nexus3SSLError,
    _get_last_modified,
    _get_manifest_digest,
)


def _get_profile():
    """Return the active credentials profile from Click context, if any."""
    ctx = click.get_current_context(silent=True)
    if ctx and ctx.obj:
        return ctx.obj.get("profile")
    return None


def _get_client():
    # type: () -> Nexus3Client
    """Load stored credentials and return a ready Nexus3Client."""
    creds = load_credentials(profile=_get_profile())
    verify = creds.get("verify", True)
    return Nexus3Client(creds["url"], creds["username"], creds["password"], verify=verify)


def _abort(message):
    # type: (str) -> None
    click.echo(click.style("Error: ", fg="red", bold=True) + message, err=True)
    sys.exit(1)


def _emit_json(payload):
    """Emit stable JSON for CI/dashboards."""
    click.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _parse_duration(value):
    """Parse 30d, 12h, 45m, 2w, or YYYY-MM-DD into a cutoff datetime."""
    if not value:
        return None
    text = value.strip()
    match = re.match(r"^(\d+)([mhdw])$", text, re.IGNORECASE)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        if unit == "m":
            delta = timedelta(minutes=amount)
        elif unit == "h":
            delta = timedelta(hours=amount)
        elif unit == "w":
            delta = timedelta(weeks=amount)
        else:
            delta = timedelta(days=amount)
        return datetime.utcnow() - delta
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise click.BadParameter("expected duration like 30d/12h/2w or date YYYY-MM-DD")


def _parse_csv(value):
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _compile_regex(pattern, option_name):
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise click.BadParameter("invalid {0}: {1}".format(option_name, exc))


def _filter_rows(rows, older_than=None, include_regex=None, exclude_regex=None, exclude_tags=None):
    """Apply common age/name/tag filters to image rows."""
    cutoff = _parse_duration(older_than)
    include_re = _compile_regex(include_regex, "include regex")
    exclude_re = _compile_regex(exclude_regex, "exclude regex")
    excluded_tags = set(_parse_csv(exclude_tags))
    filtered = []
    for row in rows:
        image_ref = "{0}:{1}".format(row.get("name", ""), row.get("tag", ""))
        if cutoff and row.get("published", datetime.min) >= cutoff:
            continue
        if include_re and not include_re.search(image_ref):
            continue
        if exclude_re and exclude_re.search(image_ref):
            continue
        if row.get("tag") in excluded_tags:
            continue
        filtered.append(row)
    return filtered


def _sort_rows(rows, sort_by="name", reverse=False):
    if sort_by == "published":
        key = lambda r: r.get("published", datetime.min)
    elif sort_by == "size":
        key = lambda r: r.get("size", 0)
    else:
        key = lambda r: (r.get("name", ""), r.get("tag", ""))
    rows.sort(key=key, reverse=reverse)
    return rows


def _row_payload(row):
    published = row.get("published")
    if isinstance(published, datetime):
        published = None if published.year == 1 else published.isoformat()
    return {
        "name": row.get("name"),
        "tag": row.get("tag"),
        "image": "{0}:{1}".format(row.get("name"), row.get("tag")),
        "published": published,
        "size_bytes": int(row.get("size", 0) or 0),
        "size": _format_bytes(row.get("size", 0)),
    }


def _format_bytes(num_bytes):
    # type: (int) -> str
    """Format a byte count using binary units."""
    try:
        value = float(num_bytes or 0)
    except (TypeError, ValueError):
        value = 0.0
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return "{0:.0f} {1}".format(value, unit)
            return "{0:.2f} {1}".format(value, unit)
        value /= 1024.0
    return "{0:.2f} PiB".format(value)


def _as_int(value):
    """Return VALUE as int when it looks numeric, otherwise None."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _blob_metric(blob_store, names):
    # type: (dict, list) -> object
    """Return the first numeric metric from BLOB_STORE for any key in NAMES."""
    for name in names:
        value = _as_int(blob_store.get(name))
        if value is not None:
            return value
    return None


def _get_blob_store_summary(client, repo_name):
    # type: (Nexus3Client, str) -> dict
    """Return best-effort blob store usage/available-space details for a repo."""
    summary = dict(name=None, total=None, available=None, error=None)
    try:
        blob_name = client.get_repository_blob_store_name(repo_name)
        if blob_name:
            blob_store = client.get_blob_store(blob_name)
        else:
            blob_stores = client.list_blob_stores()
            if len(blob_stores) == 1:
                blob_store = blob_stores[0]
                blob_name = blob_store.get("name")
            else:
                blob_store = None
        if blob_store:
            summary["name"] = blob_name or blob_store.get("name")
            summary["total"] = _blob_metric(blob_store, ["totalSize", "totalSizeInBytes", "blobStoreSize"])
            summary["available"] = _blob_metric(blob_store, ["availableSpace", "availableSpaceInBytes", "freeSpace"])
    except Nexus3Error as exc:
        summary["error"] = str(exc)
    return summary


def _sum_unique_asset_usage(rows):
    # type: (list) -> int
    """Return total bytes for matched rows, deduping shared blob assets."""
    return sum(size for _key, size in _unique_asset_usage_entries(rows).items())


def _unique_asset_usage_entries(rows):
    # type: (list) -> dict
    """Return deduped asset usage entries as key -> size."""
    entries = {}
    fallback_index = 0
    for row in rows:
        asset_usage = row.get("asset_usage") or []
        if not asset_usage:
            fallback_index += 1
            entries[("fallback", fallback_index)] = int(row.get("size", 0) or 0)
            continue
        for key, size in asset_usage:
            if key is None:
                fallback_index += 1
                entries[("fallback", fallback_index)] = int(size or 0)
                continue
            if key not in entries:
                entries[key] = int(size or 0)
    return entries


def _estimate_reclaimable_usage(selected_rows, remaining_rows):
    # type: (list, list) -> dict
    """Estimate selected, shared, and reclaimable bytes for a delete set.

    This is a best-effort estimate. Docker base layers and other shared layers
    are excluded from reclaimable bytes when they are still referenced by a
    remaining manifest that the current Nexus user can see/read.
    """
    selected_entries = _unique_asset_usage_entries(selected_rows)
    remaining_keys = set(_unique_asset_usage_entries(remaining_rows).keys())
    shared_keys = set(k for k in selected_entries if k in remaining_keys)
    selected_size = sum(selected_entries.values())
    shared_size = sum(selected_entries[k] for k in shared_keys)
    reclaimable_size = selected_size - shared_size
    return {
        "selected": selected_size,
        "shared": shared_size,
        "reclaimable": reclaimable_size,
        "shared_count": len(shared_keys),
        "selected_count": len(selected_entries),
    }


def _rows_for_components(client, components):
    # type: (Nexus3Client, list) -> list
    """Return size-summary rows for raw Nexus components."""
    rows = []
    for comp in components:
        asset_usage = _component_asset_usage(client, comp)
        rows.append({"size": sum(size for _key, size in asset_usage), "asset_usage": asset_usage})
    return rows


def _get_remaining_delete_context_rows(client, repo_name, to_delete, image_components):
    # type: (Nexus3Client, str, list, list) -> list
    """Return component rows that will remain after deleting TO_DELETE.

    Prefer scanning the whole Docker repository so base/shared layers referenced
    by other images are excluded from the reclaimable estimate. If that is not
    allowed by the Nexus user, fall back to the already-fetched same-image tags.
    """
    delete_ids = set(comp.get("id") for comp in to_delete)
    try:
        remaining_components = [comp for comp in client.list_docker_components(repo_name) if comp.get("id") not in delete_ids]
    except Nexus3Error:
        remaining_components = [comp for comp in image_components if comp.get("id") not in delete_ids]
    return _rows_for_components(client, remaining_components)


def _component_asset_usage(client, component):
    # type: (Nexus3Client, dict) -> list
    """Return dedupe-ready image usage entries for a Nexus component."""
    return client.get_component_image_usage(component)


def _component_size(client, component):
    # type: (Nexus3Client, dict) -> int
    """Return a Nexus component's compressed image size in bytes."""
    return sum(size for _key, size in _component_asset_usage(client, component))


def _parse_tag_list(tags):
    # type: (str) -> list
    """Parse a comma-separated tag list, preserving order and removing blanks/duplicates."""
    parsed = []
    seen = set()
    for tag in (tags or "").split(","):
        clean = tag.strip()
        if clean and clean not in seen:
            parsed.append(clean)
            seen.add(clean)
    return parsed


def _find_delete_components(components, requested_tags):
    # type: (list, list) -> list
    """Find requested tag components plus same-manifest aliases for deletion."""
    requested = set(requested_tags)
    selected = []
    selected_ids = set()
    digests = set()

    for comp in components:
        if comp.get("version") in requested:
            comp_id = comp.get("id")
            if comp_id not in selected_ids:
                selected.append(comp)
                selected_ids.add(comp_id)
            digest = _get_manifest_digest(comp)
            if digest:
                digests.add(digest)

    if digests:
        for comp in components:
            if comp.get("version") in requested:
                continue
            digest = _get_manifest_digest(comp)
            comp_id = comp.get("id")
            if digest in digests and comp_id not in selected_ids:
                selected.append(comp)
                selected_ids.add(comp_id)

    selected.sort(key=lambda comp: str(comp.get("version", "")))
    return selected


def _print_blob_store_summary(blob_summary):
    # type: (dict) -> None
    """Print a consistent blob-store remaining-space summary."""
    if blob_summary.get("name"):
        click.echo("Nexus blob store: {0}".format(blob_summary.get("name")))
    if blob_summary.get("total") is not None:
        click.echo("Blob store used: {0}".format(_format_bytes(blob_summary.get("total") or 0)))
    if blob_summary.get("available") is not None:
        click.echo("Blob store available: {0}".format(_format_bytes(blob_summary.get("available") or 0)))
    elif blob_summary.get("error"):
        click.echo("Blob store available: unknown ({0})".format(blob_summary.get("error")))
    else:
        click.echo("Blob store available: unknown (not exposed by Nexus for this repository/user).")


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version=__version__, prog_name="nexus3-tool")
@click.option(
    "--profile",
    default=None,
    envvar="NEXUS_PROFILE",
    help="Credential profile to use (default keeps ~/.nexus-credentials).",
)
@click.pass_context
def main(ctx, profile):
    """nexus3-tool — Manage Sonatype Nexus3 via its REST API."""
    ctx.ensure_object(dict)
    ctx.obj["profile"] = profile


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@main.command()
@click.argument("url")
@click.option(
    "--username",
    "-u",
    default=None,
    help="Username (prompted if not provided).",
)
@click.option(
    "--password",
    "-p",
    default=None,
    help="Password (prompted if not provided).",
)
@click.option(
    "--ignore-untrusted-certs",
    is_flag=True,
    default=False,
    help="Disable SSL certificate verification without prompting (useful in CI/CD pipelines).",
)
def login(url, username, password, ignore_untrusted_certs):
    """Authenticate with a Nexus3 instance and store credentials.

    URL is the base URL of your Nexus3 instance, e.g. https://nexus.example.com

    Interactive (default):

    \b
        nexus3-tool login https://nexus.example.com

    Non-interactive for CI/CD pipelines:

    \b
        nexus3-tool login https://nexus.example.com --username admin --password secret
        nexus3-tool login https://nexus.example.com --username admin --password secret --ignore-untrusted-certs
    """
    if username is None:
        username = click.prompt("Username")
    if password is None:
        password = click.prompt("Password", hide_input=True)

    click.echo("Verifying credentials...")
    verify = True

    if ignore_untrusted_certs:
        verify = False
        click.echo(click.style("Warning: ", fg="yellow") + "SSL verification disabled (--ignore-untrusted-certs).")
        client = Nexus3Client(url, username, password, verify=False)
        try:
            client.check_auth()
        except Nexus3Error as exc:
            _abort(str(exc))
    else:
        client = Nexus3Client(url, username, password, verify=True)
        try:
            client.check_auth()
        except Nexus3SSLError:
            click.echo(
                click.style("\nSSL Warning: ", fg="yellow", bold=True)
                + "The server certificate could not be verified.\n"
                + "  This usually means the server uses an internal or self-signed CA.\n"
                + "  Continuing without verification means connections are encrypted\n"
                + "  but the server identity will not be validated."
            )
            if not click.confirm("\nDisable SSL verification for this server?"):
                _abort("Login cancelled.")
            verify = False
            client = Nexus3Client(url, username, password, verify=False)
            try:
                client.check_auth()
            except Nexus3Error as exc:
                _abort(str(exc))
        except Nexus3Error as exc:
            _abort(str(exc))

    save_credentials(url, username, password, verify=verify, profile=_get_profile())
    if not verify:
        click.echo(click.style("Warning: ", fg="yellow") + "SSL verification disabled for this server.")
    profile_note = " ({0})".format(_get_profile()) if _get_profile() else ""
    click.echo(click.style("✓ ", fg="green") + "Logged in. Credentials saved to ~/.nexus-credentials{0}".format(profile_note))


# ---------------------------------------------------------------------------
# list-docker-repos
# ---------------------------------------------------------------------------


@main.command("list-docker-repos")
def list_docker_repos():
    """List all Docker repositories."""
    try:
        client = _get_client()
        repos = client.list_docker_repositories()
    except (Nexus3Error, SystemExit) as exc:
        _abort(str(exc))
        return  # unreachable, keeps type checkers happy

    if not repos:
        click.echo("No Docker repositories found.")
        return

    col = 30
    click.echo(click.style("{:<{col}}  {:<10}".format("NAME", "TYPE", col=col), bold=True))
    click.echo("-" * (col + 12))
    for repo in sorted(repos, key=lambda r: r.get("name", "")):
        click.echo(
            "{:<{col}}  {:<10}".format(
                repo.get("name", ""),
                repo.get("type", ""),
                col=col,
            )
        )


# ---------------------------------------------------------------------------
# list-docker-images
# ---------------------------------------------------------------------------


@main.command("list-docker-images")
@click.argument("repo_name")
@click.option(
    "--image-name",
    default=None,
    help="Filter results to an image name. Supports shell-style wildcards (* and ?).",
)
@click.option("--older-than", default=None, help="Only show tags older than a duration/date, e.g. 30d, 12h, 2w, 2026-01-31.")
@click.option("--include-regex", default=None, help="Only include IMAGE:TAG values matching this regex.")
@click.option("--exclude-regex", default=None, help="Exclude IMAGE:TAG values matching this regex.")
@click.option("--exclude-tags", default=None, help="Comma-separated tag names to exclude, e.g. latest,main,prod.")
@click.option("--sort", "sort_by", type=click.Choice(["name", "published", "size"]), default="name", show_default=True)
@click.option("--reverse", is_flag=True, help="Reverse the chosen sort order.")
@click.option("--limit", type=int, default=None, help="Maximum rows to display after filtering/sorting.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def list_docker_images(repo_name, image_name, older_than, include_regex, exclude_regex, exclude_tags, sort_by, reverse, limit, json_output):
    """List all Docker images and tags in REPO_NAME."""
    try:
        client = _get_client()
        rows = client.list_docker_images(repo_name, name=image_name)
        rows = _filter_rows(rows, older_than, include_regex, exclude_regex, exclude_tags)
        _sort_rows(rows, sort_by=sort_by, reverse=reverse)
        if limit is not None:
            rows = rows[:max(limit, 0)]
        blob_summary = _get_blob_store_summary(client, repo_name)
    except (Nexus3Error, SystemExit, click.BadParameter) as exc:
        _abort(str(exc))
        return

    total_size = _sum_unique_asset_usage(rows)
    if json_output:
        _emit_json(
            {
                "repository": repo_name,
                "image_name": image_name,
                "matched_tags": len(rows),
                "matched_image_usage_bytes": total_size,
                "matched_image_usage": _format_bytes(total_size),
                "blob_store": blob_summary,
                "images": [_row_payload(row) for row in rows],
                "note": "Image size is compressed config+layers from Docker/OCI manifests; physical reclaimable space can be lower for shared layers and requires Nexus cleanup.",
            }
        )
        return

    if not rows:
        if image_name:
            click.echo("No images matching '{0}' found in repository '{1}'.".format(image_name, repo_name))
        else:
            click.echo("No images found in repository '{0}'.".format(repo_name))
        return

    col_image = max(len("{0}:{1}".format(r["name"], r["tag"])) for r in rows)
    col_image = max(col_image, 10)  # minimum width
    col_size = max(len(_format_bytes(r.get("size", 0))) for r in rows)
    col_size = max(col_size, len("SIZE"))

    click.echo(
        click.style(
            "{:<{w}}  {:<16}  {:>{sw}}".format("IMAGE:TAG", "PUBLISHED", "SIZE", w=col_image, sw=col_size),
            bold=True,
        )
    )
    click.echo("-" * (col_image + col_size + 22))
    for r in rows:
        image_tag = "{0}:{1}".format(r["name"], r["tag"])
        published = r["published"]
        if published.year == 1:
            date_str = "unknown"
        else:
            date_str = published.strftime("%Y-%m-%d %H:%M")
        click.echo(
            "{:<{w}}  {:<16}  {:>{sw}}".format(
                image_tag,
                date_str,
                _format_bytes(r.get("size", 0)),
                w=col_image,
                sw=col_size,
            )
        )

    click.echo("\nMatched tags: {0}".format(len(rows)))
    click.echo("Matched image disk usage: {0}".format(_format_bytes(total_size)))
    click.echo("Note: image size is compressed config+layers; reclaimable disk may be lower for shared layers and requires Nexus cleanup.")

    _print_blob_store_summary(blob_summary)


# ---------------------------------------------------------------------------
# delete-docker-images
# ---------------------------------------------------------------------------


@main.command("delete-docker-images")
@click.argument("repo_name")
@click.option(
    "--image-name",
    required=True,
    help="Name of the image to delete tags from.",
)
@click.option(
    "--tags",
    required=True,
    help="Comma-separated list of tags to delete, e.g. 'old,dev-123'.",
)
@click.option("--dry-run", is_flag=True, help="Show the deletion plan without deleting anything.")
@click.option(
    "--quiet",
    is_flag=True,
    help="Delete immediately without prompting for confirmation.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def delete_docker_images(repo_name, image_name, tags, dry_run, quiet, json_output):
    """Delete selected tags of an image in REPO_NAME."""
    requested_tags = _parse_tag_list(tags)
    if not requested_tags:
        _abort("No tags supplied. Use --tags tag1,tag2")

    try:
        client = _get_client()
        components = client.get_image_components(repo_name, image_name)
    except (Nexus3Error, SystemExit) as exc:
        _abort(str(exc))
        return

    if not components:
        payload = {"repository": repo_name, "image_name": image_name, "tags": [], "deleted": [], "errors": [], "message": "No tags found."}
        if json_output:
            _emit_json(payload)
        else:
            click.echo("No tags found for '{0}' in repository '{1}'.".format(image_name, repo_name))
        return

    to_delete = _find_delete_components(components, requested_tags)
    found_tags = set(comp.get("version") for comp in components)
    missing_tags = [tag for tag in requested_tags if tag not in found_tags]

    if not to_delete:
        payload = {"repository": repo_name, "image_name": image_name, "tags": [], "missing_tags": missing_tags, "deleted": [], "errors": [], "message": "No matching tags to delete."}
        if json_output:
            _emit_json(payload)
        else:
            if missing_tags:
                click.echo(click.style("Warning: ", fg="yellow") + "tag(s) not found for {0}: {1}".format(image_name, ", ".join(missing_tags)))
            click.echo("No matching tags to delete for '{0}' in repository '{1}'.".format(image_name, repo_name))
        return

    selected_rows = _rows_for_components(client, to_delete)
    selected_row_by_id = dict((comp.get("id"), row) for comp, row in zip(to_delete, selected_rows))
    remaining_rows = _get_remaining_delete_context_rows(client, repo_name, to_delete, components)
    usage_estimate = _estimate_reclaimable_usage(selected_rows, remaining_rows)
    requested_set = set(requested_tags)
    plan_tags = [
        {
            "id": comp.get("id"),
            "name": comp.get("name"),
            "tag": comp.get("version"),
            "image": "{0}:{1}".format(comp.get("name"), comp.get("version")),
            "manifest_digest": _get_manifest_digest(comp),
            "requested": comp.get("version") in requested_set,
            "same_manifest_alias": comp.get("version") not in requested_set,
            "size_bytes": selected_row_by_id.get(comp.get("id"), {}).get("size", 0),
        }
        for comp in to_delete
    ]

    if json_output and dry_run:
        _emit_json(
            {
                "repository": repo_name,
                "image_name": image_name,
                "dry_run": True,
                "missing_tags": missing_tags,
                "selected_tags": plan_tags,
                "usage": usage_estimate,
                "note": "Estimated reclaimable space excludes blobs still referenced by visible remaining tags/images and requires Nexus cleanup/compaction.",
            }
        )
        return

    if not json_output:
        if missing_tags:
            click.echo(click.style("Warning: ", fg="yellow") + "tag(s) not found for {0}: {1}".format(image_name, ", ".join(missing_tags)))
        click.echo("\nImage: {repo}/{image}  ({n} matching tag(s) selected)".format(repo=repo_name, image=image_name, n=len(to_delete)))
        click.echo(click.style("\nTags to delete ({0}):".format(len(to_delete)), fg="red"))
        for comp in to_delete:
            version = comp.get("version", "?")
            alias_note = "" if version in requested_set else "  [same image as requested tag]"
            click.echo("  -  {0}:{1}  ({2}){3}".format(comp.get("name"), version, _format_bytes(selected_row_by_id.get(comp.get("id"), {}).get("size", 0)), alias_note))
        click.echo("\nSelected image disk usage: {0}".format(_format_bytes(usage_estimate["selected"])))
        click.echo("Shared with remaining tags/images: {0}".format(_format_bytes(usage_estimate["shared"])))
        click.echo("Estimated reclaimable after Nexus cleanup: {0}".format(_format_bytes(usage_estimate["reclaimable"])))
        click.echo("Note: reclaimable space is an estimate; Nexus frees disk after blob cleanup/compaction tasks.")

    if dry_run:
        if not json_output:
            click.echo(click.style("\n[dry-run] No changes made.", fg="yellow"))
        return

    if not quiet and not json_output:
        click.confirm("\nDelete {0} tag(s)?".format(len(to_delete)), abort=True)
    elif not quiet and json_output:
        _abort("Refusing JSON delete without --quiet or --dry-run.")

    deleted = []
    errors = []
    deleted_rows = []
    if not json_output:
        click.echo("")
    for comp in to_delete:
        tag = comp.get("version", "?")
        try:
            client.delete_component(comp["id"])
            deleted.append({"id": comp.get("id"), "image": "{0}:{1}".format(image_name, tag), "tag": tag})
            if not json_output:
                click.echo(click.style("  Deleted ", fg="red") + "{0}:{1}".format(image_name, tag))
            deleted_rows.append(selected_row_by_id.get(comp.get("id"), {"size": 0, "asset_usage": []}))
        except Nexus3Error as exc:
            errors.append({"id": comp.get("id"), "image": "{0}:{1}".format(image_name, tag), "tag": tag, "error": str(exc)})
            if not json_output:
                click.echo(click.style("  Failed to delete {0}:{1} — {2}".format(image_name, tag, exc), fg="red"))

    post_delete_estimate = _estimate_reclaimable_usage(deleted_rows, remaining_rows)
    blob_summary = _get_blob_store_summary(client, repo_name)
    if json_output:
        _emit_json(
            {
                "repository": repo_name,
                "image_name": image_name,
                "dry_run": False,
                "missing_tags": missing_tags,
                "selected_tags": plan_tags,
                "deleted": deleted,
                "errors": errors,
                "usage": usage_estimate,
                "successful_delete_reclaimable": post_delete_estimate,
                "blob_store": blob_summary,
                "note": "Estimated reclaimable space excludes blobs still referenced by visible remaining tags/images and requires Nexus cleanup/compaction.",
            }
        )
        return

    click.echo("\nDone. {ok} deleted, {err} error(s).".format(ok=len(deleted), err=len(errors)))
    click.echo("Estimated reclaimable after Nexus cleanup from successful deletes: {0}".format(_format_bytes(post_delete_estimate["reclaimable"])))
    if post_delete_estimate["shared"]:
        click.echo("Still shared with remaining tags/images: {0}".format(_format_bytes(post_delete_estimate["shared"])))
    _print_blob_store_summary(blob_summary)


# ---------------------------------------------------------------------------
# prune-docker-images
# ---------------------------------------------------------------------------


@main.command("prune-docker-images")
@click.argument("repo_name")
@click.option(
    "--image-name",
    required=True,
    help="Name of the image to prune.",
)
@click.option(
    "--keep-last",
    default=5,
    show_default=True,
    help="Number of most recent tags to keep.",
)
@click.option("--older-than", default=None, help="Only consider delete candidates older than duration/date, e.g. 30d or 2026-01-31.")
@click.option("--exclude-tags", default="latest,main,prod,stable", show_default=True, help="Comma-separated tags to always keep.")
@click.option("--include-regex", default=None, help="Only include IMAGE:TAG delete candidates matching this regex.")
@click.option("--exclude-regex", default=None, help="Exclude IMAGE:TAG delete candidates matching this regex.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview what would be deleted without making any changes.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip the confirmation prompt.",
)
def prune_docker_images(repo_name, image_name, keep_last, older_than, exclude_tags, include_regex, exclude_regex, json_output, dry_run, yes):
    """Prune old tags of an image in REPO_NAME.

    Tags are ordered by last-modified date; the most recent --keep-last
    tags are kept and the rest are deleted.

    Examples:

    \b
        nexus3-tool prune-docker-images development --image-name myapp --keep-last 5
        nexus3-tool prune-docker-images development --image-name myapp --dry-run
    """
    try:
        client = _get_client()
        components = client.get_image_components(repo_name, image_name)
    except (Nexus3Error, SystemExit) as exc:
        _abort(str(exc))
        return

    if not components:
        click.echo("No tags found for '{0}' in repository '{1}'.".format(image_name, repo_name))
        return

    # Separate the 'latest' tag — it's always kept and not counted against keep-last
    latest_comp = None
    versioned = []
    for comp in components:
        if comp.get("version") == "latest":
            latest_comp = comp
        else:
            versioned.append(comp)

    # Find which versioned tag 'latest' is an alias for (same manifest digest)
    latest_alias = None
    if latest_comp:
        latest_digest = _get_manifest_digest(latest_comp)
        if latest_digest:
            for comp in versioned:
                if _get_manifest_digest(comp) == latest_digest:
                    latest_alias = comp.get("version")
                    break

    # Sort versioned tags newest -> oldest
    versioned.sort(key=_get_last_modified, reverse=True)

    to_keep = versioned[:keep_last]
    to_delete = versioned[keep_last:]

    protected_tags = set(_parse_csv(exclude_tags))
    include_re = _compile_regex(include_regex, "include regex")
    exclude_re = _compile_regex(exclude_regex, "exclude regex")
    cutoff = _parse_duration(older_than)
    filtered_delete = []
    for comp in to_delete:
        image_ref = "{0}:{1}".format(comp.get("name", ""), comp.get("version", ""))
        if comp.get("version") in protected_tags:
            to_keep.append(comp)
            continue
        if cutoff and _get_last_modified(comp) >= cutoff:
            to_keep.append(comp)
            continue
        if include_re and not include_re.search(image_ref):
            to_keep.append(comp)
            continue
        if exclude_re and exclude_re.search(image_ref):
            to_keep.append(comp)
            continue
        filtered_delete.append(comp)
    to_delete = filtered_delete

    selected_rows = _rows_for_components(client, to_delete)
    remaining_rows = _get_remaining_delete_context_rows(client, repo_name, to_delete, components)
    usage_estimate = _estimate_reclaimable_usage(selected_rows, remaining_rows)

    if json_output and not dry_run and not yes:
        _abort("Refusing JSON prune without --yes or --dry-run.")

    if json_output:
        _emit_json(
            {
                "repository": repo_name,
                "image_name": image_name,
                "dry_run": dry_run,
                "keep_last": keep_last,
                "kept_tags": [comp.get("version") for comp in to_keep] + (["latest"] if latest_comp else []),
                "delete_tags": [comp.get("version") for comp in to_delete],
                "usage": usage_estimate,
                "note": "Plan only when dry_run=true; estimated reclaimable space excludes blobs still referenced by visible remaining tags/images and requires Nexus cleanup/compaction.",
            }
        )
        if dry_run:
            return
    total = len(components)
    latest_note = " (excludes 'latest' tag which is always kept)" if latest_comp else ""
    click.echo(
        "\nImage: {repo}/{image}  ({n} tag(s) found{note})".format(
            repo=repo_name,
            image=image_name,
            n=total,
            note=latest_note,
        )
    )

    click.echo(click.style("\nTags to keep ({0}):".format(len(to_keep) + (1 if latest_comp else 0)), fg="green"))
    if latest_comp:
        alias_note = "  [same image as {0}]".format(latest_alias) if latest_alias else ""
        click.echo("  +  {0}:latest{1}".format(image_name, alias_note))
    for comp in to_keep:
        version = comp.get("version")
        alias_note = "  [latest]".format(version) if version == latest_alias else ""
        click.echo("  +  {0}:{1}{2}".format(comp.get("name"), version, alias_note))

    if not to_delete:
        click.echo("\nNothing to delete — all tags are within the keep-last limit.")
        return

    click.echo(click.style("\nTags to delete ({0}):".format(len(to_delete)), fg="red"))
    for comp in to_delete:
        click.echo("  -  {0}:{1}".format(comp.get("name"), comp.get("version")))

    click.echo("\nEstimated selected image disk usage: {0}".format(_format_bytes(usage_estimate["selected"])))
    click.echo("Estimated shared with remaining tags/images: {0}".format(_format_bytes(usage_estimate["shared"])))
    click.echo("Estimated reclaimable after Nexus cleanup: {0}".format(_format_bytes(usage_estimate["reclaimable"])))

    if dry_run:
        click.echo(click.style("\n[dry-run] No changes made.", fg="yellow"))
        return

    if not yes:
        click.confirm(
            "\nDelete {0} tag(s)?".format(len(to_delete)),
            abort=True,
        )

    click.echo("")
    deleted = 0
    errors = 0
    for comp in to_delete:
        tag = comp.get("version", "?")
        try:
            client.delete_component(comp["id"])
            click.echo(click.style("  Deleted ", fg="red") + "{0}:{1}".format(image_name, tag))
            deleted += 1
        except Nexus3Error as exc:
            click.echo(
                click.style(
                    "  Failed to delete {0}:{1} — {2}".format(image_name, tag, exc),
                    fg="red",
                )
            )
            errors += 1

    click.echo("\nDone. {ok} deleted, {err} error(s).".format(ok=deleted, err=errors))


# ---------------------------------------------------------------------------
# repo-usage / inspect / duplicate helpers
# ---------------------------------------------------------------------------


@main.command("repo-usage")
@click.argument("repo_name")
@click.option("--image-name", default=None, help="Optional image name or wildcard filter.")
@click.option("--top", type=int, default=20, show_default=True, help="Number of images to show.")
@click.option("--sort", "sort_by", type=click.Choice(["size", "tags", "name", "oldest", "newest"]), default="size", show_default=True)
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def repo_usage(repo_name, image_name, top, sort_by, json_output):
    """Summarise image usage and tag counts for a Docker repository."""
    try:
        client = _get_client()
        rows = client.list_docker_images(repo_name, name=image_name)
        blob_summary = _get_blob_store_summary(client, repo_name)
    except (Nexus3Error, SystemExit) as exc:
        _abort(str(exc))
        return

    by_image = {}
    for row in rows:
        by_image.setdefault(row["name"], []).append(row)
    summaries = []
    for name, image_rows in by_image.items():
        dates = [r.get("published", datetime.min) for r in image_rows if r.get("published", datetime.min).year != 1]
        usage = _sum_unique_asset_usage(image_rows)
        summaries.append(
            {
                "name": name,
                "tags": len(image_rows),
                "usage_bytes": usage,
                "usage": _format_bytes(usage),
                "oldest": min(dates).isoformat() if dates else None,
                "newest": max(dates).isoformat() if dates else None,
            }
        )
    if sort_by == "tags":
        summaries.sort(key=lambda s: s["tags"], reverse=True)
    elif sort_by == "name":
        summaries.sort(key=lambda s: s["name"])
    elif sort_by == "oldest":
        summaries.sort(key=lambda s: s["oldest"] or "")
    elif sort_by == "newest":
        summaries.sort(key=lambda s: s["newest"] or "", reverse=True)
    else:
        summaries.sort(key=lambda s: s["usage_bytes"], reverse=True)
    summaries = summaries[:max(top, 0)]
    total_usage = _sum_unique_asset_usage(rows)
    if json_output:
        _emit_json({"repository": repo_name, "images": summaries, "total_usage_bytes": total_usage, "total_usage": _format_bytes(total_usage), "blob_store": blob_summary})
        return
    click.echo(click.style("{:<40}  {:>6}  {:>12}  {:<19}  {:<19}".format("IMAGE", "TAGS", "USAGE", "OLDEST", "NEWEST"), bold=True))
    click.echo("-" * 104)
    for item in summaries:
        click.echo("{:<40}  {:>6}  {:>12}  {:<19}  {:<19}".format(item["name"], item["tags"], item["usage"], item["oldest"] or "unknown", item["newest"] or "unknown"))
    click.echo("\nRepository matched usage: {0}".format(_format_bytes(total_usage)))
    click.echo("Note: usage is compressed config+layers deduped within the matched result set; reclaimable disk may be lower for shared layers.")
    _print_blob_store_summary(blob_summary)


@main.command("inspect-docker-image")
@click.argument("repo_name")
@click.option("--image-name", required=True, help="Image name to inspect.")
@click.option("--tag", required=True, help="Tag to inspect.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def inspect_docker_image(repo_name, image_name, tag, json_output):
    """Inspect one Docker image tag, aliases, digest and layer/config usage."""
    try:
        client = _get_client()
        components = client.get_image_components(repo_name, image_name)
    except (Nexus3Error, SystemExit) as exc:
        _abort(str(exc))
        return
    target = None
    for comp in components:
        if comp.get("version") == tag:
            target = comp
            break
    if not target:
        _abort("Tag not found: {0}:{1}".format(image_name, tag))
    digest = _get_manifest_digest(target)
    aliases = sorted(comp.get("version") for comp in components if comp is not target and _get_manifest_digest(comp) == digest)
    usage_entries = client.get_component_image_usage(target)
    payload = {
        "repository": repo_name,
        "image_name": image_name,
        "tag": tag,
        "manifest_digest": digest,
        "aliases": aliases,
        "layer_count": len(usage_entries),
        "size_bytes": sum(size for _key, size in usage_entries),
        "size": _format_bytes(sum(size for _key, size in usage_entries)),
        "entries": [{"digest": key, "size_bytes": size, "size": _format_bytes(size)} for key, size in usage_entries],
    }
    if json_output:
        _emit_json(payload)
        return
    click.echo("Image: {0}/{1}:{2}".format(repo_name, image_name, tag))
    click.echo("Manifest digest: {0}".format(digest or "unknown"))
    click.echo("Compressed size: {0}".format(payload["size"]))
    click.echo("Config/layer entries: {0}".format(payload["layer_count"]))
    if aliases:
        click.echo("Same-manifest aliases: {0}".format(", ".join(aliases)))


@main.command("find-duplicate-tags")
@click.argument("repo_name")
@click.option("--image-name", default=None, help="Optional image name to inspect; omit for whole repo.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def find_duplicate_tags(repo_name, image_name, json_output):
    """Find tags that point to the same Docker manifest digest."""
    try:
        client = _get_client()
        components = client.get_image_components(repo_name, image_name) if image_name else client.list_docker_components(repo_name)
    except (Nexus3Error, SystemExit) as exc:
        _abort(str(exc))
        return
    groups = {}
    for comp in components:
        digest = _get_manifest_digest(comp)
        if not digest:
            continue
        groups.setdefault((comp.get("name"), digest), []).append(comp.get("version"))
    duplicates = [
        {"image_name": name, "manifest_digest": digest, "tags": sorted(tags), "tag_count": len(tags)}
        for (name, digest), tags in groups.items()
        if len(tags) > 1
    ]
    duplicates.sort(key=lambda item: (item["image_name"], item["tags"]))
    if json_output:
        _emit_json({"repository": repo_name, "duplicates": duplicates})
        return
    if not duplicates:
        click.echo("No duplicate manifest tags found.")
        return
    for item in duplicates:
        click.echo("{0}: {1}".format(item["image_name"], ", ".join(item["tags"])))
        click.echo("  manifest: {0}".format(item["manifest_digest"]))


@main.command("plan-prune")
@click.argument("repo_name")
@click.option("--image-name", default="*", show_default=True, help="Image name or wildcard to plan.")
@click.option("--keep-last", default=5, show_default=True, help="Number of most recent non-protected tags to keep per image.")
@click.option("--exclude-tags", default="latest,main,prod,stable", show_default=True, help="Comma-separated tags to always keep.")
@click.option("--older-than", default=None, help="Only plan candidates older than duration/date.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def plan_prune(repo_name, image_name, keep_last, exclude_tags, older_than, json_output):
    """Plan a repository-wide prune without deleting anything."""
    try:
        client = _get_client()
        rows = client.list_docker_images(repo_name, name=image_name)
    except (Nexus3Error, SystemExit) as exc:
        _abort(str(exc))
        return
    by_image = {}
    for row in rows:
        by_image.setdefault(row["name"], []).append(row)
    protected = set(_parse_csv(exclude_tags))
    cutoff = _parse_duration(older_than)
    plan = []
    for name, image_rows in sorted(by_image.items()):
        candidates = [r for r in image_rows if r.get("tag") not in protected]
        candidates.sort(key=lambda r: r.get("published", datetime.min), reverse=True)
        delete_rows = candidates[keep_last:]
        if cutoff:
            delete_rows = [r for r in delete_rows if r.get("published", datetime.min) < cutoff]
        if delete_rows:
            plan.append({"image_name": name, "delete_tags": [r.get("tag") for r in delete_rows], "estimated_selected_usage_bytes": _sum_unique_asset_usage(delete_rows), "estimated_selected_usage": _format_bytes(_sum_unique_asset_usage(delete_rows))})
    if json_output:
        _emit_json({"repository": repo_name, "image_name": image_name, "plan": plan, "note": "Plan only; use prune-docker-images/delete-docker-images to execute deletes."})
        return
    if not plan:
        click.echo("No prune candidates found.")
        return
    for item in plan:
        click.echo("{0}: delete {1}".format(item["image_name"], ", ".join(item["delete_tags"])))
        click.echo("  selected image usage: {0}".format(item["estimated_selected_usage"]))


@main.command("run-cleanup-task")
@click.option("--task-name", default="Cleanup service", show_default=True, help="Nexus cleanup task name to run.")
@click.option("--wait/--no-wait", default=True, show_default=True, help="Wait for the task to return to WAITING/BROKEN.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def run_cleanup_task(task_name, wait, json_output):
    """Run a Nexus cleanup/compaction task and optionally wait for completion.

    Requires a Nexus user with task administration permissions.
    """
    try:
        client = _get_client()
        tasks = client.list_tasks()
        task = next((t for t in tasks if t.get("name") == task_name or t.get("id") == task_name), None)
        if not task:
            _abort("Cleanup task not found: {0}".format(task_name))
        task_id = task.get("id")
        client.run_task(task_id)
        states = []
        if not json_output:
            click.echo("Started cleanup task: {0}".format(task.get("name") or task_id))
        if wait:
            for _ in range(60):
                current = next((t for t in client.list_tasks() if t.get("id") == task_id), None)
                if not current:
                    break
                state = {"state": current.get("currentState"), "lastRunResult": current.get("lastRunResult")}
                states.append(state)
                if not json_output:
                    click.echo("{0} {1}".format(state["state"], state["lastRunResult"]))
                if state["state"] in ("WAITING", "BROKEN"):
                    break
                time.sleep(5)
        if json_output:
            _emit_json({"task": task, "states": states, "waited": wait})
    except (Nexus3Error, SystemExit) as exc:
        _abort(str(exc))


if __name__ == "__main__":
    main()
