# Publishing to PyPI

`pipx install agent-safe-runner` resolves a package on PyPI by default. Pushing a
commit to GitHub does not create that package. The first successful PyPI upload
is required before the short installation command works.

## One-time maintainer setup

1. Sign in to the maintainer's [PyPI account](https://pypi.org/account/login/).
   Verify its email and configure PyPI's required 2FA. Never put passwords,
   recovery codes, or API tokens in the repository or chat.
2. In [Publishing](https://pypi.org/manage/account/publishing/), add a **pending
   GitHub publisher** with these exact fields:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `agent-safe-runner` |
   | GitHub repository owner | `umefor-labs` |
   | GitHub repository name | `agent-safe-runner` |
   | Workflow filename | `publish.yml` |
   | GitHub environment | `pypi` |

3. In GitHub repository Settings → Environments, configure `pypi`. Prefer a
   required reviewer and appropriate branch/tag restrictions where available.
   Grant publication only to this repository/workflow/environment combination.

Adding the publisher grants that workflow permission to publish distributions.
The maintainer should review that grant. A pending publisher does not reserve
the package name; if registration/upload is rejected, inspect the reason before
renaming anything or requesting access to someone else's package.

If the project already exists under the maintainer's account, add the same
publisher in that project's Publishing settings instead of creating a pending one.

## Publish a version

1. Keep `pyproject.toml`, `__version__`, and the changelog consistent. PyPI versions
   are immutable; do not attempt to replace an existing release's files.
2. Push reviewed code and confirm CI is green.
3. Open GitHub Actions → **Publish to PyPI** → **Run workflow** on `main`.
   Leave `publish` false for a validation-only build. Set it true only when the
   publisher configuration is ready and you intend to publish publicly.
4. Alternatively publish a GitHub release with a matching tag such as `v0.4.0`.
   The workflow rejects a release tag that disagrees with package metadata.
5. Approve any configured environment review. The workflow tests core and MCP
   on Windows/Linux, builds wheel + source distribution, validates metadata,
   checks a pipx install, and only then transfers artifacts to an isolated
   publishing job. Only that job has OIDC token permission; it does not check out
   or build source. It uses the official PyPA publishing action and no stored API token.

## Verify from the user's perspective

Check that the intended version and files appear on the
[PyPI project page](https://pypi.org/project/agent-safe-runner/), then use a fresh
environment without extra indexes or local wheel overrides:

```bash
pipx install agent-safe-runner
agent-safe --version
```

MCP users install the optional extra:

```bash
pipx install "agent-safe-runner[mcp]"
```

Do not announce that the short command works until this check succeeds. If PyPI
authentication/configuration is incomplete, keep the GitHub installation
instructions available and report publication as pending.

## Common failures

- **Invalid publisher:** check all five setup fields, especially `publish.yml`
  and environment `pypi`; GitHub login and PyPI login are separate.
- **Filename/version already exists:** inspect the existing release. Do not delete
  it to reuse a version; make a new version for changed artifacts.
- **Validation failed:** fix tests, metadata, or packaging before publishing.
- **No matching distribution:** verify publication succeeded and that the user's
  Python version is at least 3.11.

Official references: [pending publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
and [publishing with GitHub OIDC](https://docs.pypi.org/trusted-publishers/using-a-publisher/).
