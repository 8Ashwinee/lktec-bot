# Composite actions

One action per verb plus a shared `setup`. Every verb action runs the verb through the gate:
it reads the target repository's `.github/lktec-bot.yml` from the default branch, mints a
token narrowed to the verb's permission, runs, revokes the token and writes the result to the
job summary. The App key never leaves the step's environment and is never on a command line.

Pin the actions by commit SHA like every other action (`uses: lukislp/lktec-bot/actions/<name>@<sha> # vX.Y.Z`).
Inside this repository they are used as `./actions/<name>` after a checkout (see
`.github/workflows/e2e.yml`).

## Common inputs and outputs

| Input            | Required | Default                    | Meaning                                                      |
| ---------------- | -------- | -------------------------- | ------------------------------------------------------------ |
| `app-id`         | yes      |                            | The App id (`vars.LKTEC_BOT_APP_ID` or a secret).            |
| `private-key`    | yes      |                            | The App's PEM key (`secrets.LKTEC_BOT_PRIVATE_KEY`).         |
| `module`         | no       | `automerge` (`ping` for ping) | Module asking for the verb; must be enabled in the policy. |
| `repository`     | no       | `${{ github.repository }}` | Target repository as `owner/name`.                           |
| `api-url`        | no       | empty                      | API base URL of a GitHub Enterprise Server.                  |
| `fail-on-denied` | no       | `false`                    | Fail the step on a policy denial instead of reporting it.    |

| Output    | Meaning                                                                 |
| --------- | ----------------------------------------------------------------------- |
| `result`  | `ok`, `denied` or `error`.                                              |
| `message` | One line saying what happened.                                          |
| `url`     | The comment, review or pull request URL when there is one, else empty.  |

A **denied** decision is a result, not a failure: the step succeeds with `result=denied` and
the reason in `message`, so a workflow can branch on it. Set `fail-on-denied: true` to fail
instead. An **error** (token, API, refused draft) always fails the step with `result=error`.
Exit codes of the underlying CLI: 0 ran, 1 error, 2 usage, 3 denied.

## `setup`

Installs the package from the action's own checkout with `pip install --no-deps`, every
dependency and the build backend pinned by hash in `requirements.txt` (exported from `uv.lock`
with `uv export --frozen --no-dev --group build --no-emit-project --hashes`; CI checks that
the file matches the lock). Needs network access to `pypi.org` and `files.pythonhosted.org`
only. Input `python-version` (default `3.13`).

```yaml
- uses: lukislp/lktec-bot/actions/setup@<sha> # vX.Y.Z
```

## `ping`

Proves the whole path without changing anything. Module default `ping`.

```yaml
- id: ping
  uses: lukislp/lktec-bot/actions/ping@<sha> # vX.Y.Z
  with:
    app-id: ${{ vars.LKTEC_BOT_APP_ID }}
    private-key: ${{ secrets.LKTEC_BOT_PRIVATE_KEY }}
- run: test "${{ steps.ping.outputs.result }}" = ok
```

## `comment`

Inputs: `number` (required), `target` (`pr`, default, or `issue`), `body` or `body-file`,
`marker`. With a marker the body gets an invisible `<!-- lktec-bot:<marker> -->` tag and later
runs update the bot's own comment that carries it instead of adding another; a comment by
anyone else is never edited. Permission: `pull_requests:write` for `pr`, `issues:write` for
`issue`.

```yaml
- id: comment
  uses: lukislp/lktec-bot/actions/comment@<sha> # vX.Y.Z
  with:
    app-id: ${{ vars.LKTEC_BOT_APP_ID }}
    private-key: ${{ secrets.LKTEC_BOT_PRIVATE_KEY }}
    number: ${{ github.event.pull_request.number }}
    body-file: report.md
    marker: report
- run: echo "${{ steps.comment.outputs.url }}"
```

## `approve`

Inputs: `number` (required), optional `body` or `body-file`. Submits an `APPROVE` review;
refuses drafts and closed pull requests (`result=error`); does nothing when the bot already
approved the current head. Permission: `pull_requests:write`.

```yaml
- uses: lukislp/lktec-bot/actions/approve@<sha> # vX.Y.Z
  with:
    app-id: ${{ vars.LKTEC_BOT_APP_ID }}
    private-key: ${{ secrets.LKTEC_BOT_PRIVATE_KEY }}
    number: ${{ github.event.pull_request.number }}
```

## `enable-automerge`

Inputs: `number` (required), `method` (`squash`, default, `merge` or `rebase`). Already
enabled with the same method is `ok`; with another method it is an error. The repository must
allow auto-merge (*Settings > General > Allow auto-merge*) and the pull request must not be
mergeable yet (otherwise GitHub answers "clean status" and the verb reports an error).
Permission: `contents:write` plus `pull_requests:write` (GitHub refuses the mutation with
`pull_requests:write` alone; verified live).

```yaml
- uses: lukislp/lktec-bot/actions/enable-automerge@<sha> # vX.Y.Z
  with:
    app-id: ${{ vars.LKTEC_BOT_APP_ID }}
    private-key: ${{ secrets.LKTEC_BOT_PRIVATE_KEY }}
    number: ${{ github.event.pull_request.number }}
    method: squash
```

## `update-branch`

Inputs: `number` (required). Asks GitHub to merge the base branch into the pull request
branch; `message` says `accepted` or `already up to date`. Permission: `contents:write` plus
`pull_requests:write`.

```yaml
- uses: lukislp/lktec-bot/actions/update-branch@<sha> # vX.Y.Z
  with:
    app-id: ${{ vars.LKTEC_BOT_APP_ID }}
    private-key: ${{ secrets.LKTEC_BOT_PRIVATE_KEY }}
    number: ${{ github.event.pull_request.number }}
```

## Forks

A `pull_request` run from a fork has no secrets, so the verb actions cannot mint a token
there. Skip the job when `github.event.pull_request.head.repo.fork` is true, as the e2e
workflow does.
