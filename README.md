# lktec-bot

GitHub App bot core: a small set of least-privilege verbs (comment, approve, enable auto-merge, update branch, later commits, releases and deployments) that run under a GitHub App identity instead of a personal account. Every verb reads the target repository's `.github/lktec-bot.yml` policy, mints a short-lived installation token narrowed to the one permission it needs, and is opt-in per repository. Anyone can register their own GitHub App from the documented manifest and run the same code.
