# nexus3-tool

[![PyPI version](https://img.shields.io/pypi/v/nexus3-tool)](https://pypi.org/project/nexus3-tool/)
[![Python Versions](https://img.shields.io/pypi/pyversions/nexus3-tool)](https://pypi.org/project/nexus3-tool/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Publish to PyPI](https://github.com/tkdpython/nexus3-tool/actions/workflows/publish.yml/badge.svg)](https://github.com/tkdpython/nexus3-tool/actions/workflows/publish.yml)

A command-line tool for managing [Sonatype Nexus3](https://www.sonatype.com/products/sonatype-nexus-repository) via its REST API, following a familiar docker-style command pattern.

---

## Installation

```bash
pip install nexus3-tool
```

---

## Usage

```
nexus3-tool [OPTIONS] COMMAND [ARGS]...

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.

Commands:
  login                Authenticate with a Nexus3 instance.
  list-docker-repos    List all Docker repositories.
  list-docker-images   List images and tags in a Docker repository.
  prune-docker-images  Prune old tags from a Docker image.
```

---

### login

Authenticate with your Nexus3 instance. Credentials (including SSL preference) are stored in `~/.nexus-credentials` and reused by all subsequent commands.

**Interactive (default) — prompts for username and password:**
```bash
nexus3-tool login https://nexus.example.com
```

If the server uses an internal or self-signed certificate, the tool will detect the SSL failure and prompt you to disable verification — no flags required.

**Non-interactive — for use in CI/CD pipelines:**
```bash
nexus3-tool login https://nexus.example.com --username admin --password secret

# With an internal/self-signed certificate:
nexus3-tool login https://nexus.example.com --username admin --password secret --ignore-untrusted-certs
```

---

### list-docker-repos

List all Docker-format repositories and their type (`hosted`, `proxy`, `group`).

```bash
nexus3-tool list-docker-repos
```

---

### list-docker-images

List all images and tags in a repository, with their publish date.

```bash
# List all images in a repo
nexus3-tool list-docker-images development

# Filter to a specific image (faster — server-side filtering)
nexus3-tool list-docker-images development --image-name myapp
```

---

### prune-docker-images

Remove old tags from a Docker image, keeping the most recent. The `latest` tag is always preserved and is not counted against `--keep-last`.

```bash
# Preview what would be deleted (no changes made)
nexus3-tool prune-docker-images production --image-name myapp --dry-run

# Keep the 5 most recent tags (default), prompt for confirmation
nexus3-tool prune-docker-images production --image-name myapp --keep-last 5

# Keep the 10 most recent tags, skip confirmation prompt
nexus3-tool prune-docker-images production --image-name myapp --keep-last 10 --yes
```

Tags are sorted by last-modified date. If `latest` is an alias for a versioned tag, both are annotated in the output so you can see exactly what is being kept.

> **Note:** Deleting tags removes the component from Nexus, but physical disk space is only reclaimed when a Nexus admin runs the *"Delete unused manifest and unreferenced blobs"* and *"Compact blob store"* tasks.

---

## Development

Clone the repository and install in editable mode:

```bash
git clone https://github.com/tkdpython/nexus3-tool.git
cd nexus3-tool
pip install -e .
```

You can also run any command directly without installing:

```bash
python3 -m nexus3_tool login https://nexus.example.com
python3 -m nexus3_tool list-docker-repos
python3 -m nexus3_tool list-docker-images development --image-name myapp
python3 -m nexus3_tool prune-docker-images production --image-name myapp --dry-run
```

---


## License

MIT © [tkdpython](https://github.com/tkdpython)

