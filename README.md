# lktec-bot

[![CI](https://github.com/lukislp/lktec-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/lukislp/lktec-bot/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/lukislp/lktec-bot/badge)](https://scorecard.dev/viewer/?uri=github.com/lukislp/lktec-bot)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue)

GitHub App bot core: a small set of least-privilege verbs (comment, approve, enable auto-merge,
update branch, later commits, releases, checks, issues and deployments) that run under a GitHub
App identity instead of a personal account. Every verb reads the target repository's
`.github/lktec-bot.yml` policy, mints a short-lived installation token narrowed to the one
permission it needs, runs, and revokes the token. A repository that has no policy file gets
nothing.

The package is generic: register your own GitHub App from the documented manifest and run the
same code. No bot name, owner, repository or environment is hard-coded.

## Why

Automation that merges, releases or deploys usually runs as a person: a fine-grained personal
access token with admin bypass, or a deploy key that pushes release commits. Those credentials
are long-lived, broader than any single job needs, and invisible in the audit log as anything
but the owner. lktec-bot replaces them with an App that

- is opt-in per repository through a committed policy file,
- holds the key in one place (the core) and hands modules only results,
- asks for exactly one permission per verb and revokes the token when the verb is done,
- never requests the `workflows` permission and has no `merge` verb (branch protection merges).

## Quick start

```sh
uv sync
uv run lktec-bot version
uv run lktec-bot policy validate .github/lktec-bot.yml
uv run lktec-bot policy check --file .github/lktec-bot.yml --module automerge --verb approve
```

A policy file that enables the `automerge` module:

```yaml
version: 1
modules:
  automerge:
    enabled: true
    exclude: []
    majors: manual
```

To run a verb against a real repository, register an App ([docs/register-your-app.md](docs/register-your-app.md)),
install it on the repository, export `LKTEC_BOT_APP_ID` and `LKTEC_BOT_PRIVATE_KEY`, then:

```sh
uv run lktec-bot ping --repository owner/name --module automerge
```

`ping` changes nothing; it proves the whole path (policy, narrowed token, revoke) and writes
the result to the job summary when run in GitHub Actions.

## Documentation

- [docs/architecture.md](docs/architecture.md): layers, the gate flow, the verb/permission
  table, how a verb is added, how a module calls a verb, why there is no merge verb and why
  `workflows` is never requested.
- [docs/register-your-app.md](docs/register-your-app.md): registering your own App, where to
  store the key, environment variables, rotation.
- [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Status: phase 1, pre-release

This is the project skeleton: policy schema, permission table, gate, token minting, HTTP
adapter, CLI and the `ping` verb, all tested. No verb that changes a repository ships yet.

Deliberately not in this repository yet:

- **Dependabot auto-merge and release workflows.** Both currently rely on the credentials this
  project replaces (a personal access token with bypass, a deploy key). They are added once
  the `approve`, `enable-automerge` and `release` verbs exist and can run under the App.
- **Releases and tags.** No version has been published; `hatch-vcs` derives the version from
  tags once they exist.

## License

Apache-2.0, see [LICENSE](LICENSE).
