"""
nexus3-tool CLI entry point.

Commands follow a docker-style pattern:
    nexus3-tool login <url>
    nexus3-tool prune-docker-repo <image> --keep-last=<n>
"""

import click

from nexus3_tool import __version__


@click.group()
@click.version_option(version=__version__, prog_name="nexus3-tool")
def main() -> None:
    """nexus3-tool — Manage Sonatype Nexus3 via its REST API."""
    pass


@main.command()
@click.argument("url")
def login(url: str) -> None:
    """Authenticate with a Nexus3 instance and store credentials.

    URL is the base URL of your Nexus3 instance, e.g. https://nexus.example.com
    """
    click.echo(
        click.style("nexus3-tool", fg="cyan", bold=True)
        + f" v{__version__} — coming in a future release! 🚀"
    )
    click.echo(f"  Would store credentials for: {url}")


@main.command("prune-docker-repo")
@click.argument("image_name")
@click.option(
    "--keep-last",
    default=5,
    show_default=True,
    help="Number of most recent image tags to keep.",
)
def prune_docker_repo(image_name: str, keep_last: int) -> None:
    """Prune old tags from a Nexus3 Docker repository.

    IMAGE_NAME is the name of the hosted Docker repository / image to prune.
    """
    click.echo(
        click.style("nexus3-tool", fg="cyan", bold=True)
        + f" v{__version__} — coming in a future release! 🚀"
    )
    click.echo(
        f"  Would prune '{image_name}', keeping the {keep_last} most recent tags."
    )


if __name__ == "__main__":
    main()
