# openedx-search

A search library for the [Open edX](https://openedx.org) platform, supporting
[Typesense](https://typesense.org) and [Meilisearch](https://www.meilisearch.com).

**Status: experimental.** This repository is at the very start of its life and
has no functionality yet. The intended scope, and the reasons for it, are laid
out in [ADR 0001: Purpose of This Repo](docs/decisions/0001-purpose-of-this-repo.rst).

## Development

This repo uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
make requirements   # create .venv from uv.lock with all dev dependencies
make test           # run the unit tests
make quality        # run the linters
make test-all       # run everything CI runs, via tox
```

Run `make help` for the full list of targets. See
[Getting Started](docs/getting_started.rst) for how to change dependencies and
how releases work.

## Documentation

- [Registering a use case](docs/how-tos/registering-use-cases.rst)
- [Architecture decision records](docs/decisions/)

## License

The code in this repository is licensed under the AGPL-3.0 unless otherwise
noted. See [LICENSE.txt](LICENSE.txt) for details.

## Reporting Security Issues

Please do not report security issues in public. Email security@openedx.org
instead.
