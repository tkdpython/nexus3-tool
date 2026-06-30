"""
nexus3-tool CLI entry point.

Commands follow a docker-style pattern:
    nexus3-tool login <url>
    nexus3-tool list-docker-repos
    nexus3-tool list-docker-images <repo>
    nexus3-tool delete-docker-images <repo> --image-name <image> --tags <tag1,tag2>
    nexus3-tool prune-docker-images <repo> --image-name <image> --keep-last <n>
"""

import sys

import click

from nexus3_tool import __version__
from nexus3_tool.auth import load_credentials, save_credentials
from nexus3_tool.client import (
    Nexus3Client,
    Nexus3Error,
    Nexus3SSLError,
    _get_asset_usage_entries,
    _get_last_modified,
    _get_manifest_digest,
)


def _get_client():
    # type: () -> Nexus3Client
    """Load stored credentials and return a ready Nexus3Client."""
    creds = load_credentials()
    verify = creds.get("verify", True)
    return Nexus3Client(creds["url"], creds["username"], creds["password"], verify=verify)


def _abort(message):
    # type: (str) -> None
    click.echo(click.style("Error: ", fg="red", bold=True) + message, err=True)
    sys.exit(1)


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
    total = 0
    seen = set()
    fallback = 0
    for row in rows:
        asset_usage = row.get("asset_usage") or []
        if not asset_usage:
            fallback += int(row.get("size", 0) or 0)
            continue
        for key, size in asset_usage:
            if key is None:
                fallback += int(size or 0)
                continue
            if key in seen:
                continue
            seen.add(key)
            total += int(size or 0)
    return total + fallback


def _component_asset_usage(component):
    # type: (dict) -> list
    """Return dedupe-ready asset usage entries for a Nexus component."""
    return _get_asset_usage_entries(component)


def _component_size(component):
    # type: (dict) -> int
    """Return a Nexus component's total asset size in bytes."""
    return sum(size for _key, size in _component_asset_usage(component))


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
def main():
    """nexus3-tool — Manage Sonatype Nexus3 via its REST API."""
    pass


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

    save_credentials(url, username, password, verify=verify)
    if not verify:
        click.echo(click.style("Warning: ", fg="yellow") + "SSL verification disabled for this server.")
    click.echo(click.style("✓ ", fg="green") + "Logged in. Credentials saved to ~/.nexus-credentials")


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
def list_docker_images(repo_name, image_name):
    """List all Docker images and tags in REPO_NAME."""
    try:
        client = _get_client()
        rows = client.list_docker_images(repo_name, name=image_name)
        blob_summary = _get_blob_store_summary(client, repo_name)
    except (Nexus3Error, SystemExit) as exc:
        _abort(str(exc))
        return

    if not rows:
        if image_name:
            click.echo("No images matching '{0}' found in repository '{1}'.".format(image_name, repo_name))
        else:
            click.echo("No images found in repository '{0}'.".format(repo_name))
        return

    # Sort by image name then tag
    rows.sort(key=lambda r: (r["name"], r["tag"]))

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

    total_size = _sum_unique_asset_usage(rows)
    click.echo("\nMatched tags: {0}".format(len(rows)))
    click.echo("Matched image disk usage: {0}".format(_format_bytes(total_size)))

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
@click.option(
    "--quiet",
    is_flag=True,
    help="Delete immediately without prompting for confirmation.",
)
def delete_docker_images(repo_name, image_name, tags, quiet):
    """Delete selected tags of an image in REPO_NAME.

    Tags listed with --tags are deleted. If another tag of the same image points
    at the same manifest digest, it is also selected so the confirmation prompt
    shows all matching aliases before deletion.
    """
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
        click.echo("No tags found for '{0}' in repository '{1}'.".format(image_name, repo_name))
        return

    to_delete = _find_delete_components(components, requested_tags)
    found_tags = set(comp.get("version") for comp in components)
    missing_tags = [tag for tag in requested_tags if tag not in found_tags]

    if missing_tags:
        click.echo(
            click.style("Warning: ", fg="yellow")
            + "tag(s) not found for {0}: {1}".format(image_name, ", ".join(missing_tags))
        )

    if not to_delete:
        click.echo("No matching tags to delete for '{0}' in repository '{1}'.".format(image_name, repo_name))
        return

    click.echo(
        "\nImage: {repo}/{image}  ({n} matching tag(s) selected)".format(
            repo=repo_name,
            image=image_name,
            n=len(to_delete),
        )
    )
    click.echo(click.style("\nTags to delete ({0}):".format(len(to_delete)), fg="red"))
    requested_set = set(requested_tags)
    for comp in to_delete:
        version = comp.get("version", "?")
        alias_note = "" if version in requested_set else "  [same image as requested tag]"
        click.echo(
            "  -  {0}:{1}  ({2}){3}".format(
                comp.get("name"),
                version,
                _format_bytes(_component_size(comp)),
                alias_note,
            )
        )

    selected_rows = [
        {
            "size": _component_size(comp),
            "asset_usage": _component_asset_usage(comp),
        }
        for comp in to_delete
    ]
    selected_size = _sum_unique_asset_usage(selected_rows)
    click.echo("\nSelected image disk usage: {0}".format(_format_bytes(selected_size)))

    if not quiet:
        click.confirm(
            "\nDelete {0} tag(s)?".format(len(to_delete)),
            abort=True,
        )

    click.echo("")
    deleted = 0
    errors = 0
    deleted_rows = []
    for comp in to_delete:
        tag = comp.get("version", "?")
        try:
            client.delete_component(comp["id"])
            click.echo(click.style("  Deleted ", fg="red") + "{0}:{1}".format(image_name, tag))
            deleted += 1
            deleted_rows.append(
                {
                    "size": _component_size(comp),
                    "asset_usage": _component_asset_usage(comp),
                }
            )
        except Nexus3Error as exc:
            click.echo(
                click.style(
                    "  Failed to delete {0}:{1} — {2}".format(image_name, tag, exc),
                    fg="red",
                )
            )
            errors += 1

    freed_size = _sum_unique_asset_usage(deleted_rows)
    click.echo("\nDone. {ok} deleted, {err} error(s).".format(ok=deleted, err=errors))
    click.echo("Space freed by successful deletes: {0}".format(_format_bytes(freed_size)))
    _print_blob_store_summary(_get_blob_store_summary(client, repo_name))


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
def prune_docker_images(repo_name, image_name, keep_last, dry_run, yes):
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


if __name__ == "__main__":
    main()
