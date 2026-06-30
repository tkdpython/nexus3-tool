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
  list-docker-images    List images and tags in a Docker repository, including size usage.
  repo-usage            Summarise top images by usage/tag count.
  inspect-docker-image  Inspect one tag's digest, aliases and layer usage.
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

List images and tags in a repository, with their publish date and compressed Docker image size. The command downloads each tag's Docker/OCI manifest and sums its config and layer descriptor sizes; this avoids reporting only the tiny Nexus manifest asset size. It also prints a summary of the total disk space used by all matched tags, deduplicating shared layer blobs where matching digests are present, and when Nexus exposes the information to your user, the available space on the backing blob store.

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
nexus3-tool list-docker-images development --sort size --reverse --limit 20
nexus3-tool list-docker-images development --json
```

> **Note:** available space is reported for the Nexus blob store that backs the repository, not for an individual Docker repository. Some Nexus users may not have permission to read blob store quota details; in that case the command still shows matched image usage and reports available space as `unknown`.

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

After successful deletes, the command reports the selected image size and a best-effort estimate of reclaimable space after Nexus cleanup. The reclaimable estimate excludes blobs that are still referenced by remaining tags/images visible to the current Nexus user, so shared `FROM` base layers are not counted when they are still in use elsewhere.

> **Note:** The reclaimable number is still an estimate. Nexus only physically frees disk after an administrator runs the *"Delete unused manifest and unreferenced blobs"* and *"Compact blob store"* tasks, and the estimate can only account for manifests the current user can read.

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

# Keep latest/main/prod/stable by default; add age and regex guards for CI-generated tags
nexus3-tool prune-docker-images production --image-name myapp --keep-last 10 --older-than 30d --include-regex ':[0-9a-f]{8}$' --dry-run

# Short flag equivalent
nexus3-tool prune-docker-images production --image-name myapp --keep-last 10 -y
```

Tags are sorted by last-modified date. If `latest` is an alias for a versioned tag, both are annotated in the output so you can see exactly what is being kept.

> **Note:** Deleting tags removes the component from Nexus, but physical disk space is only reclaimed when a Nexus admin runs the *"Delete unused manifest and unreferenced blobs"* and *"Compact blob store"* tasks.

---

### repo-usage

Show the biggest images, noisy tag families, and repo-level usage summary.

```bash
nexus3-tool repo-usage development --top 20
nexus3-tool repo-usage development --sort tags
nexus3-tool repo-usage development --image-name 'team-a/*' --json
```

---

### inspect-docker-image

Inspect one tag's manifest digest, compressed config/layer size, and same-manifest aliases such as `latest`, date tags, and commit-SHA tags.

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

