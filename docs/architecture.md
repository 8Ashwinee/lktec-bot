# Architecture

lktec-bot is a small hexagonal core. The domain decides, the application orchestrates, the
adapters talk to GitHub. Modules and add-ons never touch the App key or the GitHub API
directly: they call verbs, and every verb goes through one gate.

## Layers

```
src/lktec_bot/
  domain/        pure models and decisions - no I/O, no environment, no GitHub
    policy.py        schema of .github/lktec-bot.yml, Policy.allows(), Decision
    permissions.py   Verb, Permission, the verb -> permission table
  ports/         protocols the application layer talks to
    github_api.py    GitHubApi (get_file_content, create_review, enable_auto_merge, ...)
    token_provider.py TokenProvider.token_for(owner, repo, permissions) -> Token (revoke())
    key_source.py    KeySource.load() -> AppCredentials (app id + private key PEM)
  adapters/      implementations of the ports
    github_http.py   httpx REST + GraphQL client with retry on 5xx / secondary rate limit
    app_token.py     RS256 App JWT -> installation lookup -> narrowed installation token
    key_source.py    EnvKeySource (LKTEC_BOT_APP_ID / LKTEC_BOT_PRIVATE_KEY[_FILE]), FileKeySource
  application/   the gate and the verbs
    gate.py          Gate.run(VerbRequest) -> VerbResult
    verbs/           one small handler per verb, registered by Verb
  cli.py         typer entry point; one line per verb subcommand
  summary.py     markdown to $GITHUB_STEP_SUMMARY, or stdout outside Actions
```

Dependency direction is inward only: `adapters -> ports <- application -> domain`. The domain
imports nothing from the other layers; the application imports ports and domain, never an
adapter. The CLI is the only place where adapters are wired together (`cli.build_gate`).

## The gate

Every verb runs through `Gate.run`, which is the only path that holds a token:

1. **Read the policy.** `.github/lktec-bot.yml` is fetched from the *target* repository with
   a token narrowed to `contents:read` (minted and revoked just for this), or with the caller's
   token when one is passed (for example the workflow's `GITHUB_TOKEN`).
2. **Decide.** `decide(policy, module, verb)` returns a `Decision` with a human-readable
   reason. A missing file denies everything, an invalid file denies everything with the
   validation reasons, a missing or disabled module denies, otherwise the verb is allowed.
3. **Mint.** A second installation token is minted for exactly one repository
   (`repositories=[repo]`) and exactly the verb's permissions (`permissions_for(verb)`).
   Nothing else is ever requested, so a verb cannot do more than its table entry says even if
   the App installation itself has broader permissions.
4. **Run.** The handler gets a `GitHubApi` bound to that token and the request.
5. **Revoke.** The token is revoked in `finally`, also when the handler raises.

The outcome is always a `VerbResult` (`ok`, `denied` or `error`, with a message and an
optional URL). A denial is a normal result rendered into the job summary, not an exception; a
caller that wants to fail the job does so on `result.ok`.

Two tokens per run is deliberate: it keeps the verb token free of `contents:read` when the
verb does not need it, which is what makes "the minted permissions equal the verb's
permission and nothing more" a testable invariant (see `tests/test_gate.py`).

## The verb table

`lktec_bot/domain/permissions.py` maps every planned verb to its GitHub App permission, even
the ones that do not ship yet, so the permission footprint of the whole project is reviewed in
one place:

| Verb               | Permission(s)                            |
| ------------------ | ---------------------------------------- |
| `ping`             | `metadata:read`                          |
| `comment`          | `pull_requests:write` or `issues:write`, by target |
| `approve`          | `pull_requests:write`                    |
| `enable-automerge` | `pull_requests:write`                    |
| `update-branch`    | `contents:write` + `pull_requests:write` |
| `commit`           | `contents:write`                         |
| `open-pr`          | `pull_requests:write`                    |
| `release`          | `contents:write`                         |
| `check`            | `checks:write`                           |
| `open-issue`       | `issues:write`                           |
| `deployment`       | `deployments:write`                      |

Only `ping` has a handler today. It changes nothing and exists to exercise the full gate path
(policy, token, revoke) end to end, with fakes in the tests and against a real App from the
CLI.

### Why there is no `merge` verb

Merging is what branch protection is for. The bot enables auto-merge; GitHub merges once the
required checks and reviews are green. A `merge` verb would be a bypass of exactly the rules
the repository owner configured, and the App would need to be a bypass actor, which is what
this project exists to get rid of. The ruleset keeps `RepositoryRole admin` as the only bypass
actor, and only through a pull request.

### Why `workflows` is never requested

The `workflows` permission lets a token change `.github/workflows/*`. The bot is driven from
those workflows; a verb that could rewrite them could change its own gate. `Permission` has no
such member and `tests/test_permissions.py` asserts that no verb requests it. A pull request
that touches workflow files is made by a person and reviewed as such.

## Adding a verb

1. Add the permission to the table in `domain/permissions.py` (and its row above).
2. Write the handler in `application/verbs/<name>.py`:

   ```python
   @register(Verb.APPROVE)
   def approve(api: GitHubApi, request: VerbRequest) -> VerbResult:
       url = api.create_review(request.owner, request.repo, int(request.args["number"]), "APPROVE")
       return VerbResult(VerbOutcome.OK, "approved", url=url)
   ```

3. Import the module in `application/verbs/__init__.py` so the registration runs.
4. Register the CLI subcommand with one line in `cli.py`: `add_verb_command(Verb.APPROVE)`.
5. Add a gate test with the fakes in `tests/fakes.py` asserting the minted permissions equal
   `permissions_for(Verb.APPROVE)` and nothing more.

If the handler needs a `GitHubApi` method that does not exist yet, add it to the port and to
the httpx adapter together, with a respx test.

## How a module or add-on calls a verb

A module is a workflow (or a small program) that decides *when* something should happen; the
core decides *whether it may* and *with what token*. A module never sees the key:

```yaml
- run: uv run lktec-bot ping --repository "${{ github.repository }}" --module automerge
  env:
    LKTEC_BOT_APP_ID: ${{ vars.LKTEC_BOT_APP_ID }}
    LKTEC_BOT_PRIVATE_KEY: ${{ secrets.LKTEC_BOT_PRIVATE_KEY }}
```

From Python, build a `Gate` and call `run(VerbRequest(...))`; the fakes in `tests/fakes.py`
show the ports a test double has to satisfy.

The target repository opts in with `.github/lktec-bot.yml`:

```yaml
version: 1
modules:
  automerge:
    enabled: true
    exclude: ["some-package"]   # dependency names the module leaves alone
    majors: manual              # manual | auto
```

Unknown keys are errors (`extra="forbid"`), so a typo never opens a door. A module that has no
options of its own may only carry `enabled`. `lktec-bot policy validate <file>` and
`lktec-bot policy check --file <file> --module m --verb v` run the same code the gate runs.
