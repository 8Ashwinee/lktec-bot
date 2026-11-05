from __future__ import annotations

from collections.abc import Mapping

import pytest

from lktec_bot.application import verbs
from lktec_bot.application.gate import VerbOutcome, VerbRequest, VerbResult
from lktec_bot.application.verbs import REGISTRY, handlers, load_verbs, register
from lktec_bot.application.verbs.ping import ping
from lktec_bot.domain.permissions import (
    Grant,
    Permission,
    Target,
    Verb,
    as_token_request,
    permissions_for,
)
from lktec_bot.ports.github_api import GitHubApi


def handler(api: GitHubApi, request: VerbRequest) -> VerbResult:
    return VerbResult(VerbOutcome.OK, "")


def test_load_verbs_discovers_every_module_once() -> None:
    first = load_verbs()
    second = load_verbs()

    assert first is second is REGISTRY
    assert Verb.PING in first
    assert first[Verb.PING].handler is ping


def test_only_ping_ships_in_this_release() -> None:
    # The next release that adds a verb updates this line on purpose.
    assert set(load_verbs()) == {Verb.PING}


def test_every_registered_verb_has_one_handler_whose_grants_match_the_table() -> None:
    for verb, spec in load_verbs().items():
        assert spec.verb is verb
        assert callable(spec.handler)
        if verb is Verb.COMMENT:
            assert spec.grants == {target: permissions_for(verb, target) for target in Target}
        else:
            assert spec.grants == permissions_for(verb)


def test_handlers_map_is_what_the_gate_takes() -> None:
    assert handlers() == {Verb.PING: ping}


def test_workflows_permission_appears_nowhere() -> None:
    for spec in load_verbs().values():
        grant_sets = (
            list(spec.grants.values()) if isinstance(spec.grants, Mapping) else [spec.grants]
        )
        for grant_set in grant_sets:
            assert "workflows" not in as_token_request(grant_set)
    assert "workflows" not in {permission.value for permission in Permission}


def test_register_refuses_duplicates() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register(Verb.PING, grants=permissions_for(Verb.PING))(handler)


def test_register_refuses_grants_that_differ_from_the_table() -> None:
    with pytest.raises(ValueError, match="declares grants contents:write but the table"):
        register(Verb.APPROVE, grants=frozenset({Grant(Permission.CONTENTS, "write")}))(handler)
    assert Verb.APPROVE not in REGISTRY


def test_register_checks_per_target_grants_for_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verbs, "REGISTRY", dict(REGISTRY))

    with pytest.raises(ValueError, match="issue: issues:write; pull_request: pull_requests:write"):
        register(Verb.COMMENT, grants=frozenset({Grant(Permission.ISSUES, "write")}))(handler)

    per_target = {target: permissions_for(Verb.COMMENT, target) for target in Target}
    register(Verb.COMMENT, grants=per_target)(handler)

    assert verbs.REGISTRY[Verb.COMMENT].grants == per_target
    assert Verb.COMMENT not in REGISTRY  # the real registry is untouched
