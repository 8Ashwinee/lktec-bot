"""Command line entry point. Verbs become subcommands with one ``add_verb_command`` line."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from lktec_bot import __version__
from lktec_bot.adapters.app_token import AppTokenProvider
from lktec_bot.adapters.github_http import HttpGitHubApi
from lktec_bot.adapters.key_source import EnvKeySource
from lktec_bot.application.gate import Gate, VerbRequest, VerbResult
from lktec_bot.application.verbs import HANDLERS
from lktec_bot.domain.permissions import Target, Verb
from lktec_bot.domain.policy import PolicyError, decide, load_policy
from lktec_bot.summary import write_summary

app = typer.Typer(
    help="GitHub App bot core: least-privilege verbs, opt-in per repository.",
    no_args_is_help=True,
)
policy_app = typer.Typer(help="Validate and query a .github/lktec-bot.yml policy file.")
app.add_typer(policy_app, name="policy")


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
        policy = load_policy(file.read_text(encoding="utf-8"))
    except PolicyError as exc:
        for reason in exc.reasons:
            typer.echo(f"invalid: {reason}", err=True)
        raise typer.Exit(code=1) from None
    enabled = sorted(name for name, module in policy.modules.items() if module.enabled)
    typer.echo(f"valid: version {policy.version}, enabled modules: {', '.join(enabled) or 'none'}")


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
            policy = load_policy(file.read_text(encoding="utf-8"))
        except PolicyError as exc:
            typer.echo(f"denied: {file} is invalid: {'; '.join(exc.reasons)}")
            raise typer.Exit(code=1) from None
    decision = decide(policy, module, verb)
    typer.echo(str(decision))
    raise typer.Exit(code=0 if decision.allowed else 1)


def build_gate(caller_token: str | None = None) -> Gate:
    """Wire the production adapters. The App key comes from the environment only."""
    tokens = AppTokenProvider(EnvKeySource())
    return Gate(tokens, HttpGitHubApi, HANDLERS, caller_token=caller_token)


def add_verb_command(verb: Verb, *, target: Target | None = None) -> None:
    """Register ``lktec-bot <verb> --repository owner/name --module m`` for a verb."""

    def command(
        repository: Annotated[
            str, typer.Option("--repository", "-R", help="Target repository as owner/name.")
        ],
        module: Annotated[str, typer.Option("--module", "-m", help="Module asking for the verb.")],
        caller_token: Annotated[
            str | None,
            typer.Option(
                "--caller-token",
                envvar="LKTEC_BOT_CALLER_TOKEN",
                help="Token used only to read the policy file (e.g. GITHUB_TOKEN).",
            ),
        ] = None,
    ) -> None:
        owner, _, repo = repository.partition("/")
        if not owner or not repo:
            typer.echo("--repository must be owner/name", err=True)
            raise typer.Exit(code=2)
        request = VerbRequest(owner=owner, repo=repo, module=module, verb=verb, target=target)
        result = build_gate(caller_token).run(request)
        _report(request, result)

    command.__name__ = verb.value.replace("-", "_")
    command.__doc__ = f"Run the {verb.value} verb through the gate."
    app.command(verb.value)(command)


def _report(request: VerbRequest, result: VerbResult) -> None:
    write_summary(result.to_markdown(request))
    typer.echo(f"{result.outcome.value}: {result.message}")
    if not result.ok:
        raise typer.Exit(code=1)


# One line per verb. Later verbs register here the same way.
add_verb_command(Verb.PING)


if __name__ == "__main__":  # pragma: no cover
    app()
