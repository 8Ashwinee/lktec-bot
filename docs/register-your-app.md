# Register your own GitHub App

lktec-bot is not tied to one bot identity. Anyone can register their own GitHub App, install it
on the repositories they choose, and run this package with that App's id and private key.
Nothing in the package assumes a particular App name, owner or repository.

## 1. Create the App

Open the pre-filled registration form (replace `name` and `url` with your own):

```
https://github.com/settings/apps/new?name=my-repo-bot&url=https://github.com/you/your-repo&webhook_active=false&public=false&contents=write&pull_requests=write
```

For an organisation use `https://github.com/organizations/<org>/settings/apps/new?...` with the
same query parameters.

What the parameters mean:

| Parameter              | Value   | Why                                                            |
| ---------------------- | ------- | -------------------------------------------------------------- |
| `webhook_active`       | `false` | The bot runs from workflows; it receives no events.            |
| `public`               | `false` | Only you can install it.                                       |
| `contents`             | `write` | `update-branch`, `commit`, `release` need it. Tokens are narrowed per verb; the App's maximum is not what a verb gets. |
| `pull_requests`        | `write` | `comment`, `approve`, `enable-automerge`, `update-branch`, `open-pr`. |

Add later, when the matching verbs ship and you want them: `issues: write` (`open-issue`,
`comment` on issues), `checks: write` (`check`), `deployments: write` (`deployment`).
`metadata: read` is always included. **Never grant `workflows`**: the bot never requests it and
has no use for it (see `docs/architecture.md`).

On the form, keep "Where can this GitHub App be installed?" at *Only on this account*. Submit.

## 2. Generate and store the private key

On the App's settings page, under *Private keys*, click *Generate a private key*. GitHub
downloads a `.pem` file once; it cannot be downloaded again.

- Store it in a secret store (a password manager, or the repository / organisation *Secrets*
  of GitHub Actions). Never commit it; the repository `.gitignore` ignores `*.pem`.
- Note the **App ID** shown at the top of the settings page.
- Delete the downloaded file once it is in the secret store. A key can be revoked and
  regenerated on the same page at any time.

## 3. Install the App on selected repositories

*Install App* in the left menu, pick your account, choose **Only select repositories** and
tick the repositories that will use the bot. Each of those repositories additionally needs
`.github/lktec-bot.yml` with at least one module enabled; without the file every verb is
denied, even on repositories the App is installed on.

## 4. Give the bot its identity

The core reads the App credentials from the environment:

| Variable                     | Content                                                        |
| ---------------------------- | -------------------------------------------------------------- |
| `LKTEC_BOT_APP_ID`           | The App ID (a number).                                         |
| `LKTEC_BOT_PRIVATE_KEY`      | The PEM text. Real newlines or literal `\n` both work.         |
| `LKTEC_BOT_PRIVATE_KEY_FILE` | Alternatively, a path to the `.pem` file.                      |
| `LKTEC_BOT_API_URL`          | Optional. API base URL of a GitHub Enterprise Server instance. |
| `LKTEC_BOT_TOKEN_PROVIDER`   | Optional. `app` (default); an unknown name is an error.        |
| `LKTEC_BOT_CALLER_TOKEN`     | Optional. A token used only to read the policy file.           |

In GitHub Actions put the App ID into a repository *variable* and the key into a *secret*:

```yaml
jobs:
  ping:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@<sha> # vX.Y.Z
      - uses: astral-sh/setup-uv@<sha> # vX.Y.Z
      - run: uv sync --locked
      - run: uv run lktec-bot ping --repository "${{ github.repository }}" --module ping
        env:
          LKTEC_BOT_APP_ID: ${{ vars.LKTEC_BOT_APP_ID }}
          LKTEC_BOT_PRIVATE_KEY: ${{ secrets.LKTEC_BOT_PRIVATE_KEY }}
```

The repository's `.github/lktec-bot.yml` needs `ping: {enabled: true}` under `modules` for
this. The job summary then shows `OK ping on you/your-repo (ping): pong from you/your-repo`,
or the reason it was denied (exit code 3).

## 5. What happens on every run

1. The core signs a short-lived JWT (RS256, 9 minutes, `iss` = App ID) with the private key.
2. It looks up the installation for the target repository
   (`GET /repos/{owner}/{repo}/installation`).
3. It mints an installation token for that one repository, narrowed to the verb's permission
   (`POST /app/installations/{id}/access_tokens` with `repositories` and `permissions`), and
   checks that the permissions and repositories GitHub reports for the token are no more than
   that; in Actions the token value is masked (`::add-mask::`) right away.
4. The verb runs. The token is revoked (`DELETE /installation/token`) whether or not it
   succeeded; a revoke that fails is reported as a warning, the result stands.

Installation tokens expire after one hour on their own; revoking them early is defence in
depth, not the only safeguard.

## Rotating or revoking

- **Rotate the key:** generate a new one on the App page, update the secret, then delete the
  old key on the same page. There is no downtime: both keys are valid until you delete one.
- **Pull the plug on one repository:** set `enabled: false` in its `.github/lktec-bot.yml`, or
  uninstall the App from that repository.
- **Pull the plug everywhere:** delete the private key on the App page.
