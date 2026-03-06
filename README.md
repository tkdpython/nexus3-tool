# nexus3-tool

[![PyPI version](https://badge.fury.io/py/nexus3-tool.svg)](https://badge.fury.io/py/nexus3-tool)
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

```bash
nexus3-tool login https://nexus.example.com
```

If the server uses an internal or self-signed certificate, the tool will detect the SSL failure and prompt you to disable verification — no flags required.

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

## Publishing a Release

Releases are published to PyPI automatically via GitHub Actions when a version tag is pushed:

```bash
git tag v0.3.0
git push origin v0.3.0
```

> The workflow uses [PyPI Trusted Publishers (OIDC)](https://docs.pypi.org/trusted-publishers/) — no API token secrets required.
> You must configure a Trusted Publisher on PyPI for this repository first.

### PyPI Trusted Publisher Setup

1. Log in to [pypi.org](https://pypi.org) and navigate to your project.
2. Go to **Manage → Publishing** and add a new **Trusted Publisher**:
   - **Owner:** `tkdpython`
   - **Repository:** `nexus3-tool`
   - **Workflow filename:** `publish.yml`
   - **Environment name:** `release`
3. On GitHub, create an **Environment** named `release` under **Settings → Environments**.

---

## License

MIT © [tkdpython](https://github.com/tkdpython)


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
  login              Authenticate with a Nexus3 instance.
  prune-docker-repo  Prune old tags from a Nexus3 Docker repository.
```

### Login

Authenticate with your Nexus3 instance. Credentials will be stored in `~/.nexus-credentials` for use by subsequent commands.

```bash
nexus3-tool login https://nexus.example.com
```

### Prune Docker Repository

Remove old image tags from a hosted Docker repository, keeping only the most recent.

```bash
# Keep the 5 most recent tags (default)
nexus3-tool prune-docker-repo my-app

# Keep the 10 most recent tags
nexus3-tool prune-docker-repo my-app --keep-last=10
```

---

## Planned Features

- `nexus3-tool login <url>` — Authenticate and store credentials in `~/.nexus-credentials`
- `nexus3-tool prune-docker-repo <image> --keep-last=<n>` — Prune old Docker image tags

---

## Development

Clone the repository and install in editable mode:

```bash
git clone https://github.com/tkdpython/nexus3-tool.git
cd nexus3-tool
pip install -e ".[dev]"
```

---

## Publishing a Release

Releases are published to PyPI automatically via GitHub Actions when a version tag is pushed:

```bash
git tag v0.1.0
git push origin v0.1.0
```

> The workflow uses [PyPI Trusted Publishers (OIDC)](https://docs.pypi.org/trusted-publishers/) — no API token secrets required.  
> You must configure a Trusted Publisher on PyPI for this repository first. See the [setup guide](#pypi-trusted-publisher-setup) below.

### PyPI Trusted Publisher Setup

1. Log in to [pypi.org](https://pypi.org) and navigate to your project (or create it via the first manual publish).
2. Go to **Manage → Publishing** and add a new **Trusted Publisher**:
   - **Owner:** `tkdpython`
   - **Repository:** `nexus3-tool`
   - **Workflow filename:** `publish.yml`
   - **Environment name:** `release`
3. On GitHub, create a **Environment** named `release` under **Settings → Environments** (optional but recommended for approval gates).

---

## License

MIT © [tkdpython](https://github.com/tkdpython)
