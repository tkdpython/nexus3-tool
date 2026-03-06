"""
nexus3-tool CLI entry point.

Commands follow a docker-style pattern:
    nexus3-tool login <url>
    nexus3-tool list-docker-repos
    nexus3-tool list-docker-images <repo>
    nexus3-tool prune-docker-repo <image> --repo <repo> --keep-last <n>
"""

import sys

import click

from nexus3_tool import __version__
from nexus3_tool.auth import load_credentials, save_credentials
from nexus3_tool.client import Nexus3Client, Nexus3Error, Nexus3SSLError, _get_last_modified


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
def login(url):
    """Authenticate with a Nexus3 instance and store credentials.

    URL is the base URL of your Nexus3 instance, e.g. https://nexus.example.com
    """
    username = click.prompt("Username")
    password = click.prompt("Password", hide_input=True)

    click.echo("Verifying credentials...")
    verify = True
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
    help="Filter results to a specific image name (server-side, much faster for large repos).",
)
def list_docker_images(repo_name, image_name):
    """List all Docker images and tags in REPO_NAME."""
    try:
        client = _get_client()
        rows = client.list_docker_images(repo_name, name=image_name)
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

    click.echo(click.style("{:<{w}}  {}".format("IMAGE:TAG", "PUBLISHED", w=col_image), bold=True))
    click.echo("-" * (col_image + 22))
    for r in rows:
        image_tag = "{0}:{1}".format(r["name"], r["tag"])
        published = r["published"]
        if published.year == 1:
            date_str = "unknown"
        else:
            date_str = published.strftime("%Y-%m-%d %H:%M")
        click.echo("{:<{w}}  {}".format(image_tag, date_str, w=col_image))


# ---------------------------------------------------------------------------
# prune-docker-repo
# ---------------------------------------------------------------------------


@main.command("prune-docker-repo")
@click.argument("image_name")
@click.option(
    "--repo",
    required=True,
    help="Name of the Nexus3 Docker repository containing the image.",
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
def prune_docker_repo(image_name, repo, keep_last, dry_run, yes):
    """Prune old tags of IMAGE_NAME in a Docker repository.

    Tags are ordered by last-modified date; the most recent --keep-last
    tags are kept and the rest are deleted.

    Examples:

    \b
        nexus3-tool prune-docker-repo myapp --repo docker-hosted --keep-last 5
        nexus3-tool prune-docker-repo myapp --repo docker-hosted --dry-run
    """
    try:
        client = _get_client()
        components = client.get_image_components(repo, image_name)
    except (Nexus3Error, SystemExit) as exc:
        _abort(str(exc))
        return

    if not components:
        click.echo("No tags found for '{0}' in repository '{1}'.".format(image_name, repo))
        return

    # Sort newest -> oldest by last-modified asset date
    components.sort(key=_get_last_modified, reverse=True)

    to_keep = components[:keep_last]
    to_delete = components[keep_last:]

    click.echo(
        "\nImage: {repo}/{image}  ({n} tag(s) found)".format(
            repo=repo,
            image=image_name,
            n=len(components),
        )
    )

    click.echo(click.style("\nTags to keep ({0}):".format(len(to_keep)), fg="green"))
    for comp in to_keep:
        click.echo("  +  {0}:{1}".format(comp.get("name"), comp.get("version")))

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
