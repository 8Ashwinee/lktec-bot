# Contributing to lktec-bot

Thanks for taking the time. This repository holds the core of a GitHub App bot: the policy
schema, the gate, the token minting and the verbs. Modules and add-ons that use the verbs live
elsewhere; keep this package small and generic (nothing specific to one owner, bot name or
repository is hard-coded here, everything comes from configuration or arguments).

## How changes get in

1. Open an issue first for anything bigger than a typo, so the direction can be agreed before
   you spend time on it. A new verb starts with naming the one GitHub App permission it needs.
2. Fork the repository (or branch, if you have write access) and make your change on a branch.
3. Open a pull request against `main`.
4. `main` is protected: a PR merges only after `lint` and `test` are green, the branch is up to
   date with `main`, and a code owner has approved. Nobody pushes to `main` directly, not even
   the maintainer. Commits must be signed.

## What a pull request needs

- **Conventional Commits.** Commit and pull-request titles follow Conventional Commits
  (`feat:`, `fix:`, `docs:`, `ci:`, and so on); they drive semantic versioning once releases
  are switched on. Squash-merge keeps the PR title as the commit message.
- **Green checks.** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`
  and `uv run pytest` must pass; CI runs exactly these. Coverage of the domain and application
  layers stays high (the `fail_under` in `pyproject.toml` is the floor, not the target).
- **Architecture.** Read [`docs/architecture.md`](docs/architecture.md) first. Domain code does
  no I/O, adapters implement ports, every verb goes through the gate, and a verb never asks for
  more than the permission listed in `lktec_bot/domain/permissions.py`. The `workflows`
  permission is never requested.
- **Pin every action by SHA.** New or bumped `uses:` references carry the full commit SHA with
  the human-readable version as a trailing comment, for example
  `uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`.
- **Say what breaks.** A changed policy schema or verb signature affects every repository that
  opted in. Describe the migration in the PR body.

## Local setup

```sh
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest
```

Python 3.13 or newer and [uv](https://docs.astral.sh/uv/) are the only requirements. No GitHub
App is needed to run the test suite: adapters are tested against mocked HTTP.

## Security issues

Please do not open a public issue for a vulnerability - use the private reporting path described
in [SECURITY.md](SECURITY.md). The [Code of Conduct](CODE_OF_CONDUCT.md) applies to every
interaction in this repository.
