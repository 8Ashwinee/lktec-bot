"""Domain layer: pure models and decisions. No I/O, no GitHub, no environment."""

from lktec_bot.domain.permissions import Grant, Permission, Target, Verb, permissions_for
from lktec_bot.domain.policy import (
    POLICY_PATH,
    AutomergeConfig,
    Decision,
    ModuleConfig,
    Policy,
    PolicyError,
    decide,
    load_policy,
)

__all__ = [
    "POLICY_PATH",
    "AutomergeConfig",
    "Decision",
    "Grant",
    "ModuleConfig",
    "Permission",
    "Policy",
    "PolicyError",
    "Target",
    "Verb",
    "decide",
    "load_policy",
    "permissions_for",
]
