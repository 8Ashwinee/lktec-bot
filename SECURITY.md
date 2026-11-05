# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's private vulnerability reporting:
https://github.com/lukislp/lktec-bot/security/advisories/new

Do not open a public issue for security problems. You will get an acknowledgement within a few
days and a fix or mitigation plan before any public disclosure.

## Scope

This package holds a GitHub App's private key in memory, mints installation tokens and acts on
repositories with them. Report anything that could let a verb obtain a permission it does not
need, act on a repository that has not opted in through `.github/lktec-bot.yml`, keep a token
alive longer than the verb, or leak the private key or a token through logs, summaries or
error messages.

The policy file lives in the target repository and is read on every run, so a pull request
that changes it is a change to what the bot may do. Review such changes like code.

## Supported versions

Only the latest tagged release receives security fixes; consumers are moved by Dependabot.
