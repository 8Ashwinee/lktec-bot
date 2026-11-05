"""The gate: policy, narrowed token, verb, revoke. There is no other way to run a verb."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from lktec_bot.domain.permissions import Target, Verb, as_token_request, permissions_for
from lktec_bot.domain.policy import POLICY_PATH, Policy, PolicyError, decide, load_policy
from lktec_bot.ports.github_api import GitHubApi, GitHubApiFactory
from lktec_bot.ports.token_provider import TokenProvider

# Reading the policy file needs nothing but contents:read.
POLICY_READ_PERMISSIONS: Mapping[str, str] = {"contents": "read"}


class VerbOutcome(StrEnum):
    OK = "ok"
    DENIED = "denied"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class VerbRequest:
    """What a module asks the core to do."""

    owner: str
    repo: str
    module: str
    verb: Verb
    target: Target | None = None
    args: Mapping[str, object] = field(default_factory=dict)

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True, slots=True)
class VerbResult:
    """Every run ends in one of these; ``denied`` and ``error`` are results, not exceptions."""

    outcome: VerbOutcome
    message: str
    url: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is VerbOutcome.OK

    def to_markdown(self, request: VerbRequest) -> str:
        label = {VerbOutcome.OK: "OK", VerbOutcome.DENIED: "DENIED", VerbOutcome.ERROR: "ERROR"}
        head = f"**{label[self.outcome]}** `{request.verb.value}` on `{request.repository}`"
        line = f"{head} ({request.module}): {self.message}"
        if self.url:
            line += f"\n\n{self.url}"
        return line + "\n"


class VerbHandler(Protocol):
    """A verb: given a client bound to the narrowed token, do one thing and report."""

    def __call__(self, api: GitHubApi, request: VerbRequest) -> VerbResult: ...


class Gate:
    """Runs a verb through policy and least privilege.

    1. Load ``.github/lktec-bot.yml`` from the target repository (contents:read token, or the
       caller's token when one is passed).
    2. Decide with :func:`~lktec_bot.domain.policy.decide`; a denial is returned, not raised.
    3. Mint a token narrowed to exactly the verb's permissions, run the verb, revoke the token
       in ``finally`` - also when the verb raises.
    """

    def __init__(
        self,
        tokens: TokenProvider,
        api_factory: GitHubApiFactory,
        handlers: Mapping[Verb, VerbHandler],
        *,
        caller_token: str | None = None,
        policy_path: str = POLICY_PATH,
    ) -> None:
        self._tokens = tokens
        self._api_factory = api_factory
        self._handlers = handlers
        self._caller_token = caller_token
        self._policy_path = policy_path

    def run(self, request: VerbRequest) -> VerbResult:
        handler = self._handlers.get(request.verb)
        if handler is None:
            return VerbResult(
                VerbOutcome.ERROR, f"no handler registered for verb {request.verb.value!r}"
            )

        try:
            policy = self._load_policy(request)
        except PolicyError as exc:
            reasons = "; ".join(exc.reasons)
            return VerbResult(VerbOutcome.DENIED, f"{self._policy_path} is invalid: {reasons}")
        except Exception as exc:  # every failure must become a result, never a traceback
            return VerbResult(VerbOutcome.ERROR, f"could not read {self._policy_path}: {exc}")

        decision = decide(policy, request.module, request.verb)
        if not decision.allowed:
            return VerbResult(VerbOutcome.DENIED, decision.reason)

        try:
            permissions = as_token_request(permissions_for(request.verb, request.target))
        except ValueError as exc:
            return VerbResult(VerbOutcome.ERROR, str(exc))

        try:
            token = self._tokens.token_for(request.owner, request.repo, permissions)
        except Exception as exc:
            return VerbResult(VerbOutcome.ERROR, f"could not mint a token: {exc}")
        try:
            return handler(self._api_factory(token.value), request)
        except Exception as exc:
            return VerbResult(VerbOutcome.ERROR, f"{request.verb.value} failed: {exc}")
        finally:
            token.revoke()

    def _load_policy(self, request: VerbRequest) -> Policy | None:
        if self._caller_token is not None:
            api = self._api_factory(self._caller_token)
            return self._read_policy(api, request)
        token = self._tokens.token_for(request.owner, request.repo, POLICY_READ_PERMISSIONS)
        try:
            return self._read_policy(self._api_factory(token.value), request)
        finally:
            token.revoke()

    def _read_policy(self, api: GitHubApi, request: VerbRequest) -> Policy | None:
        text = api.get_file_content(request.owner, request.repo, self._policy_path)
        if text is None:
            return None
        return load_policy(text)
