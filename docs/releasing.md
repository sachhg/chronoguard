# Releasing

## Cutting a version

1. Bump `__version__` in `src/chronoguard/_version.py`. That's the single
   source; `pyproject.toml` and the CLI both read from it.
2. Make sure CI is green on `main`.
3. Tag and push:

   ```bash
   git tag -a v0.2.0 -m "v0.2.0"
   git push origin v0.2.0
   ```

4. Create the GitHub release:

   ```bash
   gh release create v0.2.0 --generate-notes
   ```

## Publishing to PyPI

**Read this before the first publish.** A version number on PyPI is burned the
moment you upload it: you cannot re-upload `0.1.0` even after deleting it. The
project name is claimed permanently too. `chronoguard` was described as a
placeholder name when this project started, so decide whether you actually want
it before claiming it.

Publishing uses **trusted publishing**, so no API token is stored in the repo or
on your machine. PyPI verifies the workflow's OIDC identity instead.

### One-time setup

1. Go to <https://pypi.org/manage/account/publishing/> and add a pending
   publisher:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `chronoguard` |
   | Owner | `sachhg` |
   | Repository name | `chronoguard` |
   | Workflow name | `publish.yml` |
   | Environment | leave blank |

2. Do the same at <https://test.pypi.org/manage/account/publishing/> for the
   dry run.

### Publishing

The workflow is manual only. It never fires off a tag push, because an
accidental publish cannot be taken back.

```bash
gh workflow run publish.yml -f repository=testpypi   # dry run first
gh workflow run publish.yml -f repository=pypi       # the real thing
```

TestPyPI first, always. Then check the dry run installs cleanly:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ chronoguard
chronoguard --version
```

The workflow runs the offline suite, builds, runs `twine check`, and refuses to
publish if a `vX.Y.Z` tag disagrees with the packaged version. A tag that
disagrees would ship the wrong code under the right name, which is worse than
failing the build.

## Publishing by hand

If you would rather not use the workflow:

```bash
python -m build
python -m twine check dist/*
python -m twine upload --repository testpypi dist/*
python -m twine upload dist/*
```

That needs an API token from <https://pypi.org/manage/account/token/> in
`~/.pypirc` or `TWINE_PASSWORD`. Trusted publishing avoids having a long-lived
token lying around, which is why the workflow exists.

## What ships

The wheel carries the package plus four packaged data files:
`data/probe_cases.json`, `data/model_cutoffs.json`,
`fixtures/data/web_corpus.json` and `fixtures/data/doc_store.json`. CI asserts
all four are present, since a packaging change that drops them breaks the
library silently rather than loudly.

The sdist additionally carries the tests, `docs/` and `examples/`.
