# Script Sidecar YAML Specification

Each runnable script may have an adjacent sidecar named `<script>.yml`. The
sidecar describes the CLI surface for that script: help text, options,
positionals, choices, and environment-variable documentation.

## File Naming

| Script | Sidecar |
|---|---|
| `scripts/reports/generate.py` | `scripts/reports/generate.yml` |
| `scripts/files/archive.sh` | `scripts/files/archive.yml` |
| `scripts/db/maintenance.sql` | `scripts/db/maintenance.yml` |

## Supported Script Types

Interpreter selection is inferred from the file extension:

| Extension | Invocation |
|---|---|
| `.py` | `python <script> [args...]` |
| `.sh` | `bash <script> [args...]` |
| `.bash` | `bash <script> [args...]` |
| `.sql` | `psql -f <script>` |

`.sql` scripts must not declare CLI arguments. They are configured entirely by
environment variables.

## Top-Level Fields

```yaml
description: Short help text shown in command listings and --help output.

args:
  - name: target
    required: true

env:
  - name: API_TOKEN
    required: true

epilog: |
  Extra examples or notes shown at the bottom of --help.
```

Fields:

- `description`: optional but strongly recommended
- `args`: optional list of argument descriptors
- `env`: optional list of environment variable descriptors
- `epilog`: optional extra help text

## `args` Entries

Each `args` item describes one CLI argument or option.

```yaml
args:
  - name: input
    positional: true
    required: true
    type: path
    help: File to read.

  - name: format
    choices: [text, json]
    default: text
    help: Output format.

  - name: verbose
    flag: true
    help: Show extra logging.
```

Supported keys:

- `name`: internal argument name; required
- `option`: explicit option flag such as `--filter-id`
- `positional`: if `true`, creates a positional argument
- `required`: whether the value must be supplied
- `default`: default shown in help and used by Click for omitted options
- `type`: `string`, `int`, `float`, `bool`, or `path`
- `flag`: boolean switch with no value
- `multiple`: repeatable option such as `--tag a --tag b`
- `variadic`: positional argument that consumes all remaining values
- `choices`: enumerated allowed values
- `help`: help text for this argument

## `env` Entries

`env` is informational. `toolrack` shows these variables in `--help`, but does
not export or validate them for the script.

```yaml
env:
  - name: API_TOKEN
    required: true
    help: Token used for authentication.

  - name: API_BASE_URL
    default: https://example.internal
    help: Override the service endpoint.
```

Supported keys:

- `name`: environment variable name; required
- `help`: help text shown in the command epilog
- `required`: whether the script expects the variable to exist
- `default`: documented default used by the script

## Generic Examples

Named options:

```yaml
description: Generate a report for one project.
args:
  - name: project
    required: true
    help: Project identifier.
  - name: output
    type: path
    default: report.txt
    help: Output file path.
```

Positional plus optional flag:

```yaml
description: Archive one directory into a timestamped file.
args:
  - name: source_dir
    positional: true
    required: true
    type: path
    help: Directory to archive.
  - name: compress
    flag: true
    help: Compress the archive after creation.
```

Variadic positional:

```yaml
description: Print metadata for one or more files.
args:
  - name: files
    positional: true
    variadic: true
    required: true
    type: path
    help: One or more files to inspect.
```

SQL script:

```yaml
description: Run a maintenance query against the current database.
env:
  - name: PGHOST
    help: PostgreSQL host.
  - name: PGDATABASE
    help: Database name.
  - name: PGUSER
    help: Database user.
  - name: PGPASSWORD
    help: Database password.
```

## Validation Rules

The CLI rejects sidecars that violate these rules:

1. `.sql` scripts must not declare `args`.
2. `name` values must be unique within one sidecar.
3. `variadic: true` requires `positional: true`.
4. A variadic argument must be the last entry in `args`.
5. An argument cannot be both `multiple: true` and `positional: true`.
6. `flag: true` must not declare a non-boolean type.

## Runtime Behavior

`toolrack` uses the sidecar to build Click commands dynamically:

- descriptions and per-argument help appear in `--help`
- `choices` become validated option values
- `type` maps to Click argument types
- `env` entries appear in an "Environment variables" help section

The script itself is still executed as an external process. `toolrack` does not
import user scripts.
