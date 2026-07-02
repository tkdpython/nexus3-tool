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
  --profile TEXT  Credential profile to use.
  --version       Show the version and exit.
  --help          Show this message and exit.

Commands:
  login                 Authenticate with a Nexus3 instance.
  list-docker-repos     List all Docker repositories.
  list-docker-images    List images and tags in a Docker repository.
  inspect-docker-image  Inspect one tag's digest and aliases.
  find-duplicate-tags   Find tags pointing at the same manifest digest.
  plan-prune            Plan a repository-wide prune without deleting.
  run-cleanup-task      Run a Nexus cleanup/compaction task.
  delete-docker-images  Delete selected Docker image tags.
  prune-docker-images   Prune old tags from a Docker image.
```

---

### login

Authenticate with your Nexus3 instance. Credentials (including SSL preference) are stored in `~/.nexus-credentials` and reused by all subsequent commands. Named profiles are stored as `~/.nexus-credentials-<profile>`.

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

# Named profiles for lab/staging/prod
nexus3-tool --profile lab login https://nexus.lab.example.com --username admin --password secret
nexus3-tool --profile lab list-docker-repos
```

CI can avoid writing a credentials file by setting `NEXUS_URL`, `NEXUS_USERNAME`, `NEXUS_PASSWORD`, and optionally `NEXUS_VERIFY_SSL=false`.

---

### list-docker-repos

List all Docker-format repositories and their type (`hosted`, `proxy`, `group`).

```bash
nexus3-tool list-docker-repos
```

---

### list-docker-images

List images and tags in a repository with their publish date. The command intentionally does **not** calculate per-image or repository usage; on large Nexus repositories those manifest/blob API scans can be very expensive. When Nexus exposes the information to your user, the command still prints available space on the backing blob store.

```bash
# List all images in a repo
nexus3-tool list-docker-images development

# Filter to a specific image (faster — server-side filtering)
nexus3-tool list-docker-images development --image-name myapp

# Match image names with shell-style wildcards (* and ?)
nexus3-tool list-docker-images development --image-name 'team-a/*'
nexus3-tool list-docker-images development --image-name 'service-?'

# Platform/CI filters
nexus3-tool list-docker-images development --image-name 'team-a/*' --older-than 30d --exclude-tags latest,main,prod
nexus3-tool list-docker-images development --sort published --reverse --limit 20
nexus3-tool list-docker-images development --json
```

> **Note:** available space is reported for the Nexus blob store that backs the repository, not for an individual Docker repository. Some Nexus users may not have permission to read blob store quota details; in that case the command reports available space as `unknown` and continues listing tags.

---

### delete-docker-images

Delete specific tags from a Docker image. `--tags` accepts a comma-separated list. Before deletion, the tool shows every selected tag, including any other tags on the same image that point at the same manifest digest as a requested tag.

```bash
# Prompt before deleting the requested tag(s) and matching aliases
nexus3-tool delete-docker-images development --image-name myapp --tags old,dev-123

# Non-interactive delete
nexus3-tool delete-docker-images development --image-name myapp --tags old,dev-123 --quiet

# Safe plan only
nexus3-tool delete-docker-images development --image-name myapp --tags old,dev-123 --dry-run

# JSON for CI/dashboards; destructive JSON mode requires --quiet
nexus3-tool delete-docker-images development --image-name myapp --tags old --dry-run --json
```

After successful deletes, the command reports whether the backing blob-store total/available space can be read by the current Nexus user. It intentionally does not calculate per-image or reclaimable usage.

> **Note:** Deleting tags removes the component from Nexus, but physical disk space is only reclaimed when a Nexus admin runs the *"Delete unused manifest and unreferenced blobs"* and *"Compact blob store"* tasks.

---

### prune-docker-images

Remove tags from a Docker image by either keeping the most recent tags or deleting tags older than a duration/date. The `latest` tag is always preserved and is not counted against `--keep-last`.

```bash
# Preview what would be deleted (no changes made)
nexus3-tool prune-docker-images production --image-name myapp --dry-run

# Keep the 5 most recent tags (default), prompt for confirmation
nexus3-tool prune-docker-images production --image-name myapp --keep-last 5

# Keep the 10 most recent tags, skip confirmation prompt
nexus3-tool prune-docker-images production --image-name myapp --keep-last 10 --yes

# Delete every tag older than 30 days, without applying a keep-last window
nexus3-tool prune-docker-images production --image-name myapp --older-than 30d --dry-run

# Protect specific tags even when they match the prune criteria
nexus3-tool prune-docker-images production --image-name myapp --older-than 1d --protect-tags latest,prod,release-2026-06 --dry-run

# Protect image references from a generic newline-delimited whitelist file
nexus3-tool prune-docker-images production --image-name team-a/api --older-than 30d --protect-images-file active-images.txt --dry-run

# Keep latest/main/prod/stable by default; add age and regex guards for CI-generated tags
nexus3-tool prune-docker-images production --image-name myapp --keep-last 10 --older-than 30d --include-regex ':[0-9a-f]{8}$' --dry-run

# Short flag equivalent
nexus3-tool prune-docker-images production --image-name myapp --keep-last 10 -y
```

Tags are sorted by last-modified date. If `--older-than` is used without `--keep-last`, all non-protected tags older than the cutoff are candidates. Durations support minutes/hours/days/weeks (`30h`, `1d`, `30d`, `2w`) or absolute dates (`YYYY-MM-DD`). If `latest` is an alias for a versioned tag, both are annotated in the output so you can see exactly what is being kept.

`--protect-images-file` accepts a portable whitelist file with one Docker image reference per line. Blank lines and `#` comments are ignored. References may be full registry paths or repository-local image paths, for example `registry.example.com/dev/team-a/api:2026.07.02`, `team-a/api:2026.07.02`, or digest-pinned references such as `team-a/api@sha256:...` when Nexus exposes matching manifest digests.

> **Note:** Deleting tags removes the component from Nexus, but physical disk space is only reclaimed when a Nexus admin runs the *"Delete unused manifest and unreferenced blobs"* and *"Compact blob store"* tasks.

---

### inspect-docker-image

Inspect one tag's manifest digest and same-manifest aliases such as `latest`, date tags, and commit-SHA tags. This command does not calculate image size/layer usage.

```bash
nexus3-tool inspect-docker-image development --image-name myapp --tag latest
nexus3-tool inspect-docker-image development --image-name myapp --tag 2026.06.30_1 --json
```

---

### find-duplicate-tags

Find tags that point to the same manifest digest. This is useful for understanding alias tags before cleanup.

```bash
nexus3-tool find-duplicate-tags development
nexus3-tool find-duplicate-tags development --image-name myapp --json
```

---

### plan-prune

Produce a repository-wide prune plan without deleting anything.

```bash
nexus3-tool plan-prune development --image-name 'team-a/*' --keep-last 10 --older-than 30d
nexus3-tool plan-prune development --image-name 'team-a/*' --older-than 30d --protect-tags latest,prod
nexus3-tool plan-prune development --json
```

---

### run-cleanup-task

Run a Nexus cleanup task and optionally wait for completion. This requires a Nexus user with task-administration permissions.

```bash
nexus3-tool run-cleanup-task
nexus3-tool run-cleanup-task --task-name "Cleanup service" --json
```

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

