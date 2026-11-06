"""Command line entry point. Verb subcommands are built from the verb registry.

Exit codes: 0 the verb ran (or the policy allows it), 1 an error, 2 usage or configuration,
3 denied by policy.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from lktec_bot import __version__
from lktec_bot.adapters._http_common import DEFAULT_BASE_URL
from lktec_bot.adapters.app_token import AppTokenProvider
from lktec_bot.adapters.github_http import HttpGitHubApi
from lktec_bot.adapters.key_source import EnvKeySource
from lktec_bot.application.gate import Gate, VerbRequest, VerbResult, parse_repository
from lktec_bot.application.modules import authorize, validate_policy
from lktec_bot.application.modules.automerge.service import Automerge, Decision
from lktec_bot.application.modules.lockfiles.service import Lockfiles
from lktec_bot.application.verbs import Option, VerbSpec, handlers, load_verbs
from lktec_bot.domain.permissions import Target, Verb
from lktec_bot.domain.policy import PolicyError
from lktec_bot.ports.github_api import GitHubApi
from lktec_bot.ports.token_provider import TokenProvider
from lktec_bot.summary import mask_secret, write_outputs, write_summary
from lktec_bot.text import sanitize

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_DENIED = 3

PROVIDER_VAR = "LKTEC_BOT_TOKEN_PROVIDER"
API_URL_VAR = "LKTEC_BOT_API_URL"
CALLER_TOKEN_VAR = "LKTEC_BOT_CALLER_TOKEN"
PROVIDERS = ("app",)

# ``--target`` values of the verbs whose permission depends on what they act on.
TARGETS: Mapping[str, Target] = {"pr": Target.PULL_REQUEST, "issue": Target.ISSUE}

# No pretty exceptions: a rich traceback with locals would print the token or the key when
# something unexpected goes wrong.
app = typer.Typer(
    help="GitHub App bot core: least-privilege verbs, opt-in per repository.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    pretty_exceptions_show_locals=False,
)
policy_app = typer.Typer(help="Validate and query a .github/lktec-bot.yml policy file.")
app.add_typer(policy_app, name="policy")
automerge_app = typer.Typer(
    help="The automerge module: arm routine Dependabot pull requests and keep PRs mergeable."
)
app.add_typer(automerge_app, name="automerge")
lockfiles_app = typer.Typer(
    help="The lockfiles module: commit regenerated lock files onto a Dependabot pull request."
)
app.add_typer(lockfiles_app, name="lockfiles")


class ConfigError(ValueError):
    """The environment does not describe a usable setup."""


class UsageError(ValueError):
    """The command line does not describe a runnable request."""


def say(text: str, *, err: bool = False) -> None:
    """Print one sanitised line: no line breaks, no workflow command."""
    typer.echo(sanitize(text), err=err)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@policy_app.command("validate")
def policy_validate(
    file: Annotated[Path, typer.Argument(help="Path to the policy file.", exists=True)],
) -> None:
    """Exit 0 when the file is a valid policy, exit 1 with every reason otherwise."""
    try:
        policy = validate_policy(file.read_text(encoding="utf-8"))
    except PolicyError as exc:
        for reason in exc.reasons:
            say(f"invalid: {reason}", err=True)
        raise typer.Exit(code=EXIT_ERROR) from None
    enabled = sorted(name for name, module in policy.modules.items() if module.enabled)
    say(f"valid: version {policy.version}, enabled modules: {', '.join(enabled) or 'none'}")


@policy_app.command("check")
def policy_check(
    module: Annotated[str, typer.Option("--module", "-m", help="Module name, e.g. automerge.")],
    verb: Annotated[Verb, typer.Option("--verb", "-v", help="Verb name.")],
    file: Annotated[
        Path | None,
        typer.Option("--file", "-f", help="Policy file; omit to simulate a missing file."),
    ] = None,
) -> None:
    """Print the decision the gate would make for MODULE and VERB. Exit 0 only when allowed."""
    policy = None
    if file is not None:
        try:
            policy = validate_policy(file.read_text(encoding="utf-8"))
        except PolicyError as exc:
            say(f"denied: {file} is invalid: {'; '.join(exc.reasons)}")
            raise typer.Exit(code=EXIT_DENIED) from None
    decision = authorize(policy, module, verb)
    say(str(decision))
    raise typer.Exit(code=EXIT_OK if decision.allowed else EXIT_DENIED)


def token_provider_from_env(env: Mapping[str, str]) -> TokenProvider:
    """Pick the provider named by ``LKTEC_BOT_TOKEN_PROVIDER`` (default ``app``)."""
    name = env.get(PROVIDER_VAR, "").strip() or "app"
    if name == "app":
        return AppTokenProvider(EnvKeySource(env), base_url=api_url_from_env(env))
    raise ConfigError(
        f"{PROVIDER_VAR}={name!r} is not a known token provider (known: {', '.join(PROVIDERS)})"
    )


def api_url_from_env(env: Mapping[str, str]) -> str:
    """``LKTEC_BOT_API_URL`` for GitHub Enterprise Server, else api.github.com."""
    return env.get(API_URL_VAR, "").strip() or DEFAULT_BASE_URL


def build_gate(env: Mapping[str, str] | None = None) -> Gate:
    """Wire the production adapters. Everything comes from the environment, never from flags.

    The caller must close the gate (``contextlib.closing``) to release the provider's client.
    """
    environment = env if env is not None else os.environ
    base_url = api_url_from_env(environment)

    def api_factory(token: str) -> GitHubApi:
        return HttpGitHubApi(token, base_url=base_url)

    return Gate(
        token_provider_from_env(environment),
        api_factory,
        handlers(),
        caller_token=environment.get(CALLER_TOKEN_VAR, "").strip() or None,
        mask=mask_secret,
    )


# -- verb subcommands, built from the registry ---------------------------------------------


def _parameter(name: str, annotation: object, default: object = inspect.Parameter.empty) -> Any:
    return inspect.Parameter(
        name, inspect.Parameter.KEYWORD_ONLY, annotation=annotation, default=default
    )


def _parameters_for(option: Option) -> list[Any]:
    """The typer parameter(s) one declared option turns into."""
    if option.kind == "text":
        # A body: inline or from a file; exactly one of the two when required.
        return [
            _parameter(
                option.name,
                Annotated[str | None, typer.Option(option.flag, help=option.help)],
                None,
            ),
            _parameter(
                f"{option.name}_file",
                Annotated[
                    Path | None,
                    typer.Option(f"{option.flag}-file", help=f"{option.help} Read from a file."),
                ],
                None,
            ),
        ]
    if option.kind == "multi":
        return [
            _parameter(
                option.name,
                Annotated[list[str] | None, typer.Option(option.flag, help=option.help)],
                None,
            )
        ]
    python_type: Any
    if option.kind == "int":
        python_type = int
    elif option.kind == "choice":
        python_type = Literal[option.choices]
    else:
        python_type = str
    if option.required:
        return [
            _parameter(
                option.name, Annotated[python_type, typer.Option(option.flag, help=option.help)]
            )
        ]
    return [
        _parameter(
            option.name,
            Annotated[python_type | None, typer.Option(option.flag, help=option.help)],
            None,
        )
    ]


def collect_args(spec: VerbSpec, values: Mapping[str, object]) -> dict[str, object]:
    """Turn parsed option values into ``VerbRequest.args``; bodies are read from their file."""
    args: dict[str, object] = {}
    for option in spec.options:
        if option.kind == "text":
            inline = values.get(option.name)
            path = values.get(f"{option.name}_file")
            if inline is not None and path is not None:
                raise UsageError(f"give either {option.flag} or {option.flag}-file, not both")
            if path is not None:
                try:
                    args[option.name] = Path(str(path)).read_text(encoding="utf-8")
                except OSError as exc:
                    raise UsageError(f"{option.flag}-file: cannot read {path}: {exc}") from None
            elif inline is not None:
                args[option.name] = inline
            elif option.required:
                raise UsageError(f"{option.flag} or {option.flag}-file is required")
            continue
        value = values.get(option.name)
        if option.kind == "multi":
            if value:
                args[option.name] = list(value) if isinstance(value, list | tuple) else [value]
        elif value is not None:
            args[option.name] = value
    return args


def add_verb_command(spec: VerbSpec) -> None:
    """Register ``lktec-bot <verb> --repository owner/name --module m [options]`` for a verb."""
    verb = spec.verb

    def command(**values: object) -> None:
        try:
            owner, repo = parse_repository(str(values["repository"]))
        except ValueError as exc:
            say(f"--repository: {exc}", err=True)
            raise typer.Exit(code=EXIT_USAGE) from None
        target = TARGETS[str(values["target"])] if spec.takes_target else None
        try:
            args = collect_args(spec, values)
        except UsageError as exc:
            say(str(exc), err=True)
            raise typer.Exit(code=EXIT_USAGE) from None
        request = VerbRequest(
            owner=owner,
            repo=repo,
            module=str(values["module"]),
            verb=verb,
            target=target,
            args=args,
        )
        try:
            gate = build_gate()
        except ConfigError as exc:
            say(str(exc), err=True)
            raise typer.Exit(code=EXIT_USAGE) from None
        with closing(gate):
            result = gate.run(request)
        _report(request, result)

    parameters = [
        _parameter(
            "repository",
            Annotated[
                str, typer.Option("--repository", "-R", help="Target repository as owner/name.")
            ],
        ),
        _parameter(
            "module",
            Annotated[str, typer.Option("--module", "-m", help="Module asking for the verb.")],
        ),
    ]
    if spec.takes_target:
        parameters.append(
            _parameter(
                "target",
                Annotated[
                    Literal["pr", "issue"],
                    typer.Option("--target", help="What the verb acts on: a PR or an issue."),
                ],
            )
        )
    for option in spec.options:
        parameters.extend(_parameters_for(option))

    command.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    command.__annotations__ = {parameter.name: parameter.annotation for parameter in parameters}
    command.__name__ = verb.value.replace("-", "_")
    command.__doc__ = f"Run the {verb.value} verb through the gate."
    app.command(verb.value)(command)


def _report(request: VerbRequest, result: VerbResult) -> None:
    write_summary(result.to_markdown(request))
    write_outputs({"result": result.outcome.value, "message": result.message, "url": result.url})
    say(f"{result.outcome.value}: {result.message}")
    if result.url:
        say(f"url: {result.url}")
    for warning in result.warnings:
        say(f"warning: {warning}", err=True)
    if result.ok:
        return
    raise typer.Exit(code=EXIT_ERROR if result.outcome.value == "error" else EXIT_DENIED)


for _spec in load_verbs().values():
    add_verb_command(_spec)


# -- module commands ------------------------------------------------------------------------
# A module command decides what to do and calls verbs through the gate; it is not a verb and
# holds no token of its own.

REPOSITORY_OPTION = typer.Option("--repository", "-R", help="Target repository as owner/name.")


def _open_gate(repository: str) -> tuple[str, str, Gate]:
    try:
        owner, repo = parse_repository(repository)
    except ValueError as exc:
        say(f"--repository: {exc}", err=True)
        raise typer.Exit(code=EXIT_USAGE) from None
    try:
        return owner, repo, build_gate()
    except ConfigError as exc:
        say(str(exc), err=True)
        raise typer.Exit(code=EXIT_USAGE) from None


@automerge_app.command("arm")
def automerge_arm(
    number: Annotated[int, typer.Option("--number", help="Pull request number.", min=1)],
    repository: Annotated[str, REPOSITORY_OPTION],
) -> None:
    """Classify a Dependabot pull request; approve it and enable auto-merge when it is routine.

    Never merges and never bypasses a rule: GitHub merges once the required checks are green.
    """
    owner, repo, gate = _open_gate(repository)
    with closing(gate):
        result = Automerge(gate, owner, repo).arm(number)
    write_summary(result.to_markdown(f"{owner}/{repo}"))
    overall = {Decision.DENIED: "denied", Decision.ERROR: "error"}.get(result.decision, "ok")
    write_outputs(
        {
            "result": overall,
            "decision": result.decision.value,
            "reason": result.reason,
            "message": result.reason,
            "url": result.url,
        }
    )
    say(f"{result.decision.value}: {result.reason}")
    if result.url:
        say(f"url: {result.url}")
    for warning in result.warnings:
        say(f"warning: {warning}", err=True)
    if result.exit_code:
        raise typer.Exit(code=result.exit_code)


@automerge_app.command("sweep")
def automerge_sweep(repository: Annotated[str, REPOSITORY_OPTION]) -> None:
    """Arm routine Dependabot pull requests, update behind branches, unstick conflicting ones.

    Exit 0 with a summary table (errors on single pull requests are listed, not fatal), 3 when
    the policy denies the module, 1 when the pull requests could not be read.
    """
    owner, repo, gate = _open_gate(repository)
    with closing(gate):
        result = Automerge(gate, owner, repo).sweep()
    write_summary(result.to_markdown(f"{owner}/{repo}"))
    if result.failure is not None:
        outcome = result.failure.outcome.value
        write_outputs({"result": outcome, "message": result.failure.message})
        say(f"{outcome}: {result.failure.message}")
    else:
        message = (
            f"armed {result.armed}, re-approved {result.approved}, updated {result.updated}, "
            f"recreate requested {result.recreated}, errors {result.errors}"
        )
        write_outputs(
            {
                "result": "ok",
                "message": message,
                "armed": str(result.armed),
                "updated": str(result.updated),
                "recreated": str(result.recreated),
                "errors": str(result.errors),
            }
        )
        for row in result.rows:
            say(f"#{row.number} ({row.author}, {row.state or '-'}): {row.detail()}")
        say(f"ok: {message}")
    for warning in result.warnings:
        say(f"warning: {warning}", err=True)
    if result.exit_code:
        raise typer.Exit(code=result.exit_code)


@lockfiles_app.command("commit")
def lockfiles_commit(
    number: Annotated[int, typer.Option("--number", help="Pull request number.", min=1)],
    changed_dir: Annotated[
        Path,
        typer.Option(
            "--changed-dir",
            help="Directory with manifest.txt and files/<repo-path> from the regenerating job.",
            exists=True,
            file_okay=False,
        ),
    ],
    repository: Annotated[str, REPOSITORY_OPTION],
    head_sha: Annotated[
        str | None,
        typer.Option("--head-sha", help="The head the lock files were regenerated for."),
    ] = None,
) -> None:
    """Commit the regenerated lock files that differ from a Dependabot pull request's head.

    Only for open, non-draft pull requests by dependabot[bot]; only lock files with a known
    name. The commit is made by the commit verb and signed by GitHub. Exit 0 also when there is
    nothing to commit, 3 when the policy denies, 1 on an error (or a refused handover).
    """
    owner, repo, gate = _open_gate(repository)
    with closing(gate):
        outcome = Lockfiles(gate, owner, repo).commit(number, changed_dir, head_sha)
    write_summary(outcome.to_markdown(f"{owner}/{repo}"))
    write_outputs(
        {
            "result": outcome.result,
            "decision": outcome.decision.value,
            "reason": outcome.reason,
            "message": outcome.reason,
            "url": outcome.url,
        }
    )
    say(f"{outcome.decision.value}: {outcome.reason}")
    if outcome.url:
        say(f"url: {outcome.url}")
    for warning in outcome.warnings:
        say(f"warning: {warning}", err=True)
    if outcome.exit_code:
        raise typer.Exit(code=outcome.exit_code)


if __name__ == "__main__":  # pragma: no cover
    app()
