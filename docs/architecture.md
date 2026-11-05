# Architecture

lktec-bot is a small hexagonal core. The domain decides, the application orchestrates, the
adapters talk to GitHub. Modules and add-ons never touch the App key or the GitHub API
directly: they call verbs, and every verb goes through one gate.

## Layers

```
src/lktec_bot/
  domain/        pure models and decisions - no I/O, no environment, no GitHub
    policy.py        shape of .github/lktec-bot.yml (version, modules, enabled), Decision
    permissions.py   Verb, Permission, the canonical verb -> permission table
  ports/         protocols the application layer talks to
    github_api.py    GitHubApi (get_file_content, create_review, enable_auto_merge, ..., close)
    token_provider.py TokenProvider.token_for(owner, repo, permissions) -> Token, TokenError
    key_source.py    KeySource.load() -> AppCredentials (app id + private key PEM)
  adapters/      implementations of the ports
    _http_common.py  base URL, API version, User-Agent, header builder shared by both clients
    github_http.py   httpx REST + GraphQL client with the retry policy described below
    app_token.py     RS256 App JWT -> installation lookup -> narrowed installation token
    key_source.py    EnvKeySource (LKTEC_BOT_APP_ID / LKTEC_BOT_PRIVATE_KEY[_FILE]), FileKeySource
  application/   the gate, the module registry and the verbs
    gate.py          Gate.run(VerbRequest) -> VerbResult; owner/repo validation
    modules.py       module registry: name -> allowed verbs + options model; authorize()
    verbs/           one file per verb: @register(Verb.X, grants=..., options=...) on the handler
      _common.py     --number parsing and the draft/closed refusal shared by the PR verbs
  cli.py         typer entry point; verb subcommands are built from the verb registry
  summary.py     markdown to $GITHUB_STEP_SUMMARY, result/message/url to $GITHUB_OUTPUT,
                 ::add-mask:: for minted tokens
  text.py        sanitising and markdown escaping for everything that is printed
actions/         composite actions (setup + one per verb), see actions/README.md
tools/           repository checks run by CI (strict YAML, action pinning)
```

Dependency direction is inward only: `adapters -> ports <- application -> domain`. The domain
imports nothing from the other layers; the application imports ports and domain, never an
adapter. The CLI is the only place where adapters are wired together (`cli.build_gate`).

## The gate

Every verb runs through `Gate.run`, which is the only path that holds a token. The decision
order is fixed and every step fails closed:

1. **Handler.** The verb must have a registered handler, else `error`.
2. **Module known.** The module must exist in the module registry (`application/modules.py`),
   else `denied` - before anything is minted or read.
3. **Read the policy.** `.github/lktec-bot.yml` is fetched from the *default branch* of the
   target repository (never from a pull request head, so a PR cannot bring its own
   permissions) with a token narrowed to `contents:read`, or with the caller's token when
   `LKTEC_BOT_CALLER_TOKEN` is set (for example the workflow's `GITHUB_TOKEN`). The file is
   validated against the registry: an unknown module name, or an option its module does not
   take, makes the whole file invalid and denies everything.
4. **Decide.** `authorize(policy, module, verb)` returns a `Decision` with a readable reason:
   missing file, invalid file, module not declared, module not enabled, verb not in the
   module's verb set - each one `denied`.
5. **Mint.** A second installation token is minted for exactly one repository
   (`repositories=[repo]`) and exactly the verb's permissions (`permissions_for(verb)`).
6. **Check the token.** What GitHub reports for the token must be a subset of what was
   requested plus `metadata:read`, and its repository list must be exactly the target. A
   token that is broader, or whose scope the provider cannot report, is revoked and the run
   is an `error`. The same check runs on the policy-read token.
7. **Run.** The handler gets a `GitHubApi` bound to that token and the request with `actor`
   filled in (the login the token acts as, `<app-slug>[bot]`, taken from the installation
   lookup); the client is closed afterwards. A verb that looks for its own earlier work
   (`comment` with a marker, `approve`'s idempotency check) matches on that login and fails
   closed when the provider could not report it.
8. **Revoke.** Every token is revoked whether or not its user succeeded. A failed revoke is
   retried once and then reported as a warning (`token not revoked ...`) on the result; it
   never replaces the result.

The outcome is always a `VerbResult` (`ok`, `denied` or `error`, with a message, an optional
URL and warnings). A denial is a normal result rendered into the job summary, not an
exception.

Errors are told apart: a `TokenError` from the provider becomes `could not mint ...` with the
provider's message, anything else a provider raises is reported as an unexpected error with
its type, and nothing the gate catches ever turns into a traceback.

When `GITHUB_ACTIONS` is set, `::add-mask::<token>` is printed for every minted token right
after minting (`summary.mask_secret`, wired into the gate by `cli.build_gate`), so the runner
redacts the value from every later log line. Every message the CLI prints or writes to the
summary goes through `text.sanitize`: line breaks collapse to spaces and `::` becomes `: :`,
so no message can form a workflow command; `to_markdown` additionally escapes markdown.

Two tokens per run is deliberate: it keeps the verb token free of `contents:read` when the
verb does not need it, which is what makes "the minted permissions equal the verb's
permission and nothing more" a testable invariant (see `tests/test_gate.py`).

### Exit codes

| Code | Meaning                                                      |
| ---- | ------------------------------------------------------------ |
| 0    | the verb ran (`policy check`: the policy allows it)           |
| 1    | error (token, API, invalid file for `policy validate`)         |
| 2    | usage or configuration (bad `--repository`, unknown provider) |
| 3    | denied by policy or by the module registry                    |

A workflow that wants to treat a denial as a soft failure can test for `3`. The composite
actions do exactly that: a denial is `result=denied` on a successful step unless
`fail-on-denied: true`.

### Outputs

Every verb command prints `<outcome>: <message>` (and `url: <url>` when there is one), writes
the markdown line to `$GITHUB_STEP_SUMMARY`, and appends `result`, `message` and `url` to
`$GITHUB_OUTPUT` when that variable is set, each collapsed to one line. The composite actions
expose those three as their outputs.

### Environment

| Variable                   | Meaning                                                              |
| -------------------------- | -------------------------------------------------------------------- |
| `LKTEC_BOT_APP_ID`, `LKTEC_BOT_PRIVATE_KEY[_FILE]` | the App identity (see `docs/register-your-app.md`) |
| `LKTEC_BOT_TOKEN_PROVIDER` | which provider mints tokens; `app` (default) is the only one today. An unknown name is a clear error, exit 2. |
| `LKTEC_BOT_API_URL`        | API base URL for GitHub Enterprise Server, default `https://api.github.com` |
| `LKTEC_BOT_CALLER_TOKEN`   | optional token used only to read the policy file                     |

There is no command-line flag for any token: flags end up in process listings and shell
history, environment variables of a workflow step do not.

## The module registry

A module is the caller of a verb (`--module automerge`). The policy file in the target
repository enables a module; the registry bounds what an enabled module may do:

| Module      | Verbs                                              | Options                                      |
| ----------- | -------------------------------------------------- | -------------------------------------------- |
| `automerge` | `approve`, `enable-automerge`, `update-branch`, `comment` | `exclude: [str]`, `majors: manual \| auto` |
| `ping`      | `ping`                                             | none                                         |

An enabled module cannot run a verb outside its set, no matter what the policy file says.
Options are validated with a pydantic model that forbids unknown keys; a module without an
options model accepts no options at all. The domain only knows the shape of the file
(`version`, `modules`, `enabled` as a strict boolean); which modules exist and what they take
is application knowledge, so the domain stays free of module names.

Adding a module is one `register_module(name, verbs, OptionsModel)` call in
`application/modules.py`.

## The verb table

`lktec_bot/domain/permissions.py` is the canonical list: it maps every planned verb to its
GitHub App permission, even the ones that do not ship yet, so the permission footprint of the
whole project is reviewed in one place:

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

The phase-1 verbs ship: `ping`, `comment`, `approve`, `enable-automerge` and `update-branch`.
`tests/test_verbs.py` pins the set of shipped verbs; adding one updates that line on purpose.

| Verb               | Options                                   | Behaviour                                                                 |
| ------------------ | ----------------------------------------- | ------------------------------------------------------------------------- |
| `ping`             | none                                      | changes nothing; exercises policy, token, scope check and revoke          |
| `comment`          | `--target pr\|issue --number N --body\|--body-file [--marker ID]` | posts a comment; with a marker, appends `<!-- lktec-bot:ID -->` and on later runs updates the bot's own comment carrying it (matched on the actor login, never on the marker alone); unchanged body is a no-op |
| `approve`          | `--number N [--body\|--body-file]`        | `APPROVE` review; refuses drafts and closed PRs (`error`); no second review for a head the bot already approved |
| `enable-automerge` | `--number N --method squash\|merge\|rebase` | GraphQL `enablePullRequestAutoMerge`; refuses drafts and closed PRs; already enabled with the same method is `ok`, with another method an `error` |
| `update-branch`    | `--number N`                              | `PUT .../update-branch` with the current head sha; 202 is `ok`, GitHub's 422 "no new commits" is `ok` with a message, anything else an `error` |

Every result carries the relevant URL (comment, review or pull request). Nothing a verb
prints or stores contains a token.

Verb handlers declare their grants next to the code (`@register(Verb.X, grants=...)`), and
`register` refuses a handler whose grants differ from the table. The table is the source of
truth; the declaration on the handler is the reviewable copy. Both have to change for a verb's
footprint to change.

`enable-automerge` is a GraphQL mutation (`enablePullRequestAutoMerge`). The adapter resolves
the pull request's node id from `owner/repo/number` first; the port never exposes node ids.
GitHub documents the mutation under the pull-request permission, but it may additionally need
`contents:write` on some repositories. This must be verified against a real repository when
the verb ships; if so, the table row and the registration change together.

### Why there is no `merge` verb

Merging is what branch protection is for. The bot enables auto-merge; GitHub merges once the
required checks and reviews are green. A `merge` verb would be a bypass of exactly the rules
the repository owner configured, and the App would need to be a bypass actor, which is what
this project exists to get rid of. The ruleset keeps `RepositoryRole admin` as the only bypass
actor, and only through a pull request.

### Why `workflows` is never requested

The `workflows` permission lets a token change `.github/workflows/*`. The bot is driven from
those workflows; a verb that could rewrite them could change its own gate. `Permission` has no
such member, and `tests/test_permissions.py` and `tests/test_verbs.py` assert that no verb
and no registration requests it. A pull request that touches workflow files is made by a
person and reviewed as such.

## Adding a verb

1. Make sure the verb and its permission are in the table in `domain/permissions.py` (and in
   the row above); every planned verb already is.
2. Write the handler in `application/verbs/<name>.py`, one file, nothing else to register:

   ```python
   @register(
       Verb.APPROVE,
       grants=frozenset({Grant(Permission.PULL_REQUESTS, "write")}),
       options=(Option("number", "Pull request number.", kind="int", required=True),),
   )
   def approve(api: GitHubApi, request: VerbRequest) -> VerbResult:
       url = api.create_review(request.owner, request.repo, number_of(request), "APPROVE")
       return VerbResult(VerbOutcome.OK, "approved", url=url)
   ```

   `load_verbs()` discovers the file with `pkgutil`; the CLI builds the subcommand from the
   registry: `--repository` and `--module` always, `--target pr|issue` when `grants` is a
   mapping from `Target` to grant set (`comment`), and one flag per declared `Option`. An
   option is declarative (`kind` is `int`, `str`, `choice` with `choices`, or `text`, which
   becomes `--name` plus `--name-file`); parsed values arrive in `VerbRequest.args` under the
   option's name. The registry knows nothing about typer.
3. Add the verb to the verb set of every module that may call it (`application/modules.py`).
4. Add a gate test with the fakes in `tests/fakes.py` asserting the minted permissions equal
   `permissions_for(Verb.APPROVE)` and nothing more, and update the shipped-verbs line in
   `tests/test_verbs.py`.

If the handler needs a `GitHubApi` method that does not exist yet, add it to the port and to
the httpx adapter together, with a respx test.

## The HTTP adapter's retry policy

- 5xx: retried for `GET`, `HEAD`, `PUT` and `DELETE` only. `POST` and `PATCH` are not: the
  first attempt may have succeeded before the answer was lost, and a second comment or review
  is not something a retry may create.
- 403/429 secondary rate limit (`Retry-After`, exhausted `X-RateLimit-Remaining`, or the
  words in the body): retried for every method, because GitHub rejected the request before
  acting on it.
- Transport errors (connection dropped, timeout): wrapped into `GitHubHttpError`; retried once
  for idempotent methods.
- A `Retry-After` above the back-off limit (30 s by default) fails the call instead of being
  capped: the job would otherwise wait for a limit it cannot see.
- GraphQL answers that are not objects, carry `errors`, have no `data`, or hold a null node
  on the path the adapter needs all become `GitHubHttpError` with a message naming the node.

Both clients are closed after use (`contextlib.closing` in the gate for the API client, in the
CLI for the gate, which closes the token provider).

## How a module or add-on calls a verb

A module is a workflow (or a small program) that decides *when* something should happen; the
core decides *whether it may* and *with what token*. A module never sees the key:

```yaml
- run: uv run lktec-bot ping --repository "${{ github.repository }}" --module ping
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
  ping:
    enabled: true               # only needed to run the end-to-end check
```

Unknown top-level keys, unknown module names and unknown module options are errors, so a typo
never opens a door. `lktec-bot policy validate <file>` and
`lktec-bot policy check --file <file> --module m --verb v` run the same code the gate runs.
