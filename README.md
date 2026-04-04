# autotestgen

`autotestgen` is a closed-loop test generation harness for repository and single-target workflows.

It can discover targets, generate tests, execute them, classify failures, persist attempts in SQLite, and retry with feedback. The current codebase supports Python, JavaScript, and Go adapters, with both heuristic and model-backed generation paths.

## Status

The project is still experimental, but the repository already includes:

- language adapters for Python, JavaScript, and Go
- local target execution through the closed-loop orchestrator
- repository workflows that clone or reuse a project workspace
- benchmark runners and fixtures
- SQLite-backed run history and failure classification
- optional model-backed generation with heuristic fallback

## Requirements

- Python 3.11+
- `pytest`
- `pytest-cov`
- language toolchains for the projects you want to analyze:
  - Python for Python targets
  - Node.js/npm for JavaScript targets
  - Go for Go targets

## Installation

```bash
python -m pip install -e .
```

If you want to use model-backed generation, set `OPENAI_API_KEY` in your environment.

## CLI

The package exposes an `autotestgen` command.

### Discover Targets

```bash
autotestgen --project . --language auto discover
```

### Run a Local Target

```bash
autotestgen --project . --language python run --target sample.py::add
```

You can also target a whole module:

```bash
autotestgen --project . --language python run --target sample.py
```

### Run a Repository Workflow

```bash
autotestgen run https://github.com/example/project.git --output summary
```

For a local repository path:

```bash
autotestgen run ../some-project --max-targets 5 --repair-repo
```

### Run Benchmarks

```bash
autotestgen benchmark --spec benchmarks/default_targets.json
```

## Runtime Artifacts

Generated state and workspace outputs are written under gitignored paths such as:

- `.autotestgen/`
- `.autotestgen_repo_runs/`
- `.tmp/`

That keeps benchmark reports, SQLite state, cloned workspaces, and temporary execution artifacts out of source control.

## Project Layout

- `src/autotestgen/adapters`: language-specific discovery, generation, repair, and execution hooks
- `src/autotestgen/orchestrator`: single-target closed-loop execution
- `src/autotestgen/repo`: repository-level intake, detection, bootstrap, repair, and reporting
- `src/autotestgen/benchmark`: benchmark runner and reporting
- `src/autotestgen/storage`: SQLite persistence
- `tests`: focused unit coverage across adapters, orchestration, and repo workflows
- `benchmarks/fixtures`: sample projects used by tests and benchmark specs

For implementation details, see [ARCHITECTURE.md](./ARCHITECTURE.md).

## Running Tests

```bash
python -m pytest -q
```
