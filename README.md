# toolrack

`toolrack` is the dispatcher engine behind a user-owned scripts repository.
It reads a registry of script paths, loads YAML sidecars beside those scripts,
and builds a typed Click CLI at runtime.

If you want the starter repository that uses this engine, see
[`toolrack-template`](https://github.com/anfit/toolrack-template).

## Why This Exists

AI has made it dramatically easier to write small, useful scripts. That is the
good news. The follow-up news is that once you have twenty of them, "somewhere
in a folder" stops being a system.

`toolrack` is for that moment.

The basic idea is simple:

- AI is very good at helping you produce small scripts quickly.
- A growing script library still needs structure, discoverability, and sane UX.
- AI also benefits from compartmentalization: small scripts with narrow scope
  are easier to write, review, test, and replace.
- AI work is non-deterministic by nature, so every stable, tested, deterministic
  thing you can hand off to automation is one less task balanced on your pet
  clanker's back.

This project is not trying to turn your script repo into a framework empire. It
is trying to help your collection of tiny automations stay tidy, callable, and
pleasant to use after the novelty phase.

## Showcase

The intended flow looks like this:

1. Create a scripts repository with a clear layout.
   There is a starter repo for that:
   [`toolrack-template`](https://github.com/anfit/toolrack-template).
2. Add small scripts under folders that match their area of work:
   `github/`, `jira/`, `db/`, `images/`, `deploy/`, and so on.
3. Put a sidecar next to each script so the command has typed arguments, help,
   and completion.
4. Plug `toolrack` into that repo so the whole thing becomes one coherent CLI.

The result is one command on your `PATH`, available from PowerShell, Git Bash,
or Cygwin, with shared help and completion instead of a growing folklore of
"I think that one is in a repo called maybe-tools-old?"

Example:

```text
my-tools github list-prs --state open --limit 20
my-tools jira show ISSUE-123
my-tools db query --env prod --file scripts/db/slow_report.sql
```

That is the main payoff: when you want to make a new script, it is not trapped
in "that project you did two years ago". It lives next to two similar scripts
in the folder dedicated to that area, where both humans and models can find the
pattern immediately.

Which also makes the AI prompt much nicer:

> Hey AI, make me one like this, but mauve.

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
- `core refresh-cache`: rebuild the cached command metadata next to `.toolrack`
- `core list`: show registry entries and sidecar status
- `core install-completion <shell>`: print shell completion source

## Scripts Repo Contract

A scripts repository normally contains:

- `scripts/`: runnable `.py`, `.sh`, `.bash`, or `.sql` files
- `.toolrack`: registry of enabled scripts
- `.toolrack.cache.json`: cached command metadata used for faster startup and completion
- `aliases.cfg`: optional group aliases, stored next to `.toolrack`
- `bin/`: wrappers that export the toolrack environment variables

`aliases.cfg` lets you shorten first-level or nested group paths without
renaming folders. For example:

```ini
[groups]
environments = env
refactoring = refactor
```

That turns `your-tools environments get-status` into `your-tools env get-status`.

Sidecars live next to scripts as `<script>.yml`. See
[SIDECAR_SPEC.md](/C:/projects/toolrack/SIDECAR_SPEC.md).

For a starter repository layout, use
[`toolrack-template`](https://github.com/anfit/toolrack-template).

## Development

```sh
python -m pip install -e ".[dev]"
python -m pytest
python -m pytest -m integration -s
```
