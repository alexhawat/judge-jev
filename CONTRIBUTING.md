# Contributing

## Who can open pull requests

This repository uses GitHub's **collaborators-only** pull request creation policy
(same as [mergeCraft](https://github.com/alexhawat/mergeCraft)): only users with
write access can open PRs against this repo. Forks are welcome for private
experimentation; outside contributors cannot open PRs here until invited as
collaborators.

## Maintainer

**@alexhawat** is the sole builder and code owner. Every path requires their
review before merge (see `.github/CODEOWNERS` and the `protect-main` ruleset).

## Fork PRs and Actions

If a fork PR is ever accepted as a collaborator workflow:

- Workflows that need secrets must gate on
  `github.event.pull_request.head.repo.full_name == github.repository`
  (same-repo only). Fork heads never receive repository secrets.
- Default workflow token permissions are **read**.
- Prefer opening PRs from a branch on this repository when you have write access.

## Development

See [README.md](README.md) and [AGENTS.md](AGENTS.md).
