# evotest

`evotest` is an experimental closed-loop test generation harness for local targets and repository workflows.

It discovers candidate functions and modules, generates tests, executes them in isolated workspaces, measures coverage, classifies failures, stores attempts in SQLite, and retries with repair feedback. The current implementation includes Python, JavaScript, and Go adapters, with both heuristic and OpenAI-backed generation paths.

## Project Demo

Demo video: https://drive.google.com/file/d/1nRmZXt7bTT4L5_yfXwTRR3s10u6IqlYi/view?usp=sharing

## Current Scope

- language adapters for Python, JavaScript, and Go
- local target execution through the closed-loop orchestrator
- repository workflows for local paths and Git sources
- benchmark runners with shipped fixture specs
- SQLite-backed run history and failure classification
- generator modes for `auto`, `llm`, and `heuristic`

## Requirements

- Python 3.11+
- the package dependencies installed from this repository
- language toolchains for the projects you want to analyze:
  - Python for Python targets
  - Node.js for JavaScript targets
  - Go for Go targets

Notes:

- Python execution uses `pytest` and `pytest-cov`, which are installed as package dependencies.
- JavaScript execution prefers local `vitest` or `jest` binaries when present and can fall back to a minimal Node-based shim for generated tests.
- Model-backed generation needs `OPENAI_API_KEY`. In `auto` mode, the generator falls back to heuristics when no API key is configured or the provider output is unusable.

## Installation

```bash
python -m pip install -e .
```

## OpenAI Setup

`OPENAI_API_KEY` is only required for model-backed generation.

- `--generator-mode llm`: requires `OPENAI_API_KEY`
- `--generator-mode auto`: uses OpenAI when `OPENAI_API_KEY` is set, otherwise falls back to heuristics
- `--generator-mode heuristic`: never uses the API

Set the key in PowerShell:

```powershell
$env:OPENAI_API_KEY = "your-api-key"
```

Set the key in bash:

```bash
export OPENAI_API_KEY="your-api-key"
```

Example LLM-backed run:

```bash
evotest --project . --language python --generator-mode llm run --target src/samplelib/math_add.py::add
```

## CLI

The package exposes an `evotest` command.

### Discover Targets

```bash
evotest --project . --language auto discover
```

### Run a Local Target

```bash
evotest --project . --language python run --target src/samplelib/math_add.py::add
```

You can also target a whole module:

```bash
evotest --project . --language python run --target src/samplelib/math_add.py
```

### Run a Repository Workflow

For a remote repository:

```bash
evotest run https://github.com/example/project.git --output summary
```

For a local repository path:

```bash
evotest run ../some-project --max-targets 5 --repair-repo
```

Repository workflows stage work in a session directory under `.evotest_repo_runs/`. Remote repositories are cloned there. Local paths are copied there unless you request a specific git branch, commit, or ref, in which case a git clone/checkout is used.

Each repository workflow writes:

- `repo_run_report.json`
- `repo_review.md`
- `generated_tests.patch`

### Run Benchmarks

```bash
evotest benchmark --spec benchmarks/default_targets.json
```

The repository also ships language-specific benchmark specs:

- `benchmarks/default_targets.json`
- `benchmarks/javascript_targets.json`
- `benchmarks/go_targets.json`

## Runtime Artifacts

Generated state and workspace outputs are written under gitignored paths such as:

- `.evotest/state.db`
- `.evotest/generated_tests/`
- `.evotest/benchmarks/`
- `.evotest_repo_runs/`
- `.tmp/`

This keeps generated tests, SQLite state, benchmark reports, staged repository workspaces, and temporary runtime files out of source control.

## Project Layout

- `src/evotest/adapters`: language-specific discovery, generation, repair, and execution hooks
- `src/evotest/orchestrator`: single-target closed-loop execution
- `src/evotest/repo`: repository intake, detection, bootstrap, repair, and reporting
- `src/evotest/benchmark`: benchmark runner and metrics reporting
- `src/evotest/storage`: SQLite persistence
- `tests`: unit coverage across adapters, orchestration, benchmarks, and repo workflows
- `benchmarks/fixtures`: sample projects used by tests and benchmark specs

For implementation details, see [ARCHITECTURE.md](./ARCHITECTURE.md).

## Running Tests

```bash
python -m pytest -q
```
