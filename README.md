# toolrack

`toolrack` is the dispatcher engine behind a user-owned scripts repository.
It reads a registry of script paths, loads YAML sidecars beside those scripts,
and builds a typed Click CLI at runtime.

## Deployment Model

`toolrack` is meant to be installed into a scripts repository and launched
through a repo-local wrapper in `bin/`.

The wrapper is responsible for:

- choosing the visible command name
- setting `TOOLRACK_REPO_ROOT`
- setting `TOOLRACK_SCRIPTS_ROOT`
- setting `TOOLRACK_REGISTRY_FILE` or `TOOLRACK_REGISTRY_BASENAME`
- invoking `python -m toolrack` from the repo's virtualenv

Defaults:

- CLI name: inferred from the wrapper filename, else `toolrack`
- registry basename: `.toolrack`
- repo discovery also accepts legacy `.attic` during walk-up

## Core Commands

`toolrack` provides a small management surface under `core`:

- `core register <script_path>`: add one script to the registry
- `core auto-register`: scan `scripts/` for sidecar-backed scripts
- `core unregister <script_path>`: remove one script from the registry
- `core reregister`: validate every registered script and sidecar
- `core list`: show registry entries and sidecar status
- `core install-completion <shell>`: print shell completion source

## Scripts Repo Contract

A scripts repository normally contains:

- `scripts/`: runnable `.py`, `.sh`, `.bash`, or `.sql` files
- `.toolrack`: registry of enabled scripts
- `bin/`: wrappers that export the toolrack environment variables

Sidecars live next to scripts as `<script>.yml`. See
[SIDECAR_SPEC.md](/C:/projects/toolrack/SIDECAR_SPEC.md).

For a starter repository layout, use
[toolrack-template](/C:/projects/toolrack-template/README.md).

## Development

```sh
python -m pip install -e ".[dev]"
python -m pytest
python -m pytest -m integration -s
```
