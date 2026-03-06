# nexus3-tool

[![PyPI version](https://badge.fury.io/py/nexus3-tool.svg)](https://badge.fury.io/py/nexus3-tool)
[![Python Versions](https://img.shields.io/pypi/pyversions/nexus3-tool)](https://pypi.org/project/nexus3-tool/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Publish to PyPI](https://github.com/tkdpython/nexus3-tool/actions/workflows/publish.yml/badge.svg)](https://github.com/tkdpython/nexus3-tool/actions/workflows/publish.yml)

A command-line tool for managing [Sonatype Nexus3](https://www.sonatype.com/products/sonatype-nexus-repository) via its REST API, following a familiar docker-style command pattern.

> ⚠️ **This package is in early development (v0.1.0).** Commands are scaffolded but not yet fully implemented. Watch this space!

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
