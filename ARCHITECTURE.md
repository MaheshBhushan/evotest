# Autotestgen Architecture

This document describes the architecture that is implemented in this repository today.

`autotestgen` is a closed-loop test generation harness with three main execution modes:

- single-target discovery and execution
- repository workflows against a local path or Git repo
- benchmark runs over a JSON spec

The current codebase supports Python, JavaScript, and Go through a shared adapter interface. Test generation can use an OpenAI-backed provider or fall back to language-specific heuristic generation.

## System Overview

```text
CLI
  |-- discover ------------------------------> ClosedLoopOrchestrator
  |                                             |-- adapter discovery
  |                                             `-- SQLiteStore
  |
  |-- run --target --------------------------> ClosedLoopOrchestrator
  |                                             |-- PromptBuilder
  |                                             |-- ModelBackedTestGenerator
  |                                             |-- language adapter
  |                                             `-- SQLiteStore
  |
  |-- run <repo/path/url> -------------------> RepositoryWorkflowRunner
  |                                             |-- RepositoryIntake
  |                                             |-- ProjectDetector
  |                                             |-- EnvironmentBootstrapper
  |                                             |-- ClosedLoopOrchestrator
  |                                             `-- RepoRepairEngine
  |
  `-- benchmark -----------------------------> BenchmarkRunner
                                                `-- ClosedLoopOrchestrator per target
```

The architecture is not multi-agent. It is a single-process orchestration model with adapter-based language specialization and optional model-backed generation.

## Core Runtime Model

The runtime revolves around a few shared contracts in [src/autotestgen/models.py](F:/Codex-Hackathon/src/autotestgen/models.py):

- `FunctionTarget` and `ModuleTarget`: discovered testable units
- `ProjectInventory`: language-scoped or merged target inventory
- `PromptContext`: normalized generation context
- `GeneratedTestArtifact`: generated test file metadata and source
- `ExecutionResult`: runner output plus coverage
- `FailureClassification`: normalized failure taxonomy
- `AttemptRecord`: one persisted generation/execution attempt
- `OrchestrationResult`: final outcome for one target

These models let the orchestrator stay generic while each adapter owns language-specific behavior.

## Configuration

Configuration lives in [src/autotestgen/config.py](F:/Codex-Hackathon/src/autotestgen/config.py).

`HarnessConfig` controls:

- project root and SQLite database path
- artifacts root and generated test output directory
- selected language or `auto`
- retry, timeout, coverage, and stability thresholds
- generator mode: `auto`, `llm`, or `heuristic`
- OpenAI model settings and API key
- Python executable and pytest command for execution/bootstrap

Artifact roots default under `.autotestgen/`, while repository workflows default under `.autotestgen_repo_runs/`.

## Adapter Layer

The adapter contract is defined in [src/autotestgen/adapters/base.py](F:/Codex-Hackathon/src/autotestgen/adapters/base.py).

Each language adapter is responsible for:

- discovery
- target resolution
- prompt-context construction
- heuristic generation
- model-output validation
- test repair
- execution
- failure classification

Current adapters:

- Python: [src/autotestgen/adapters/python/adapter.py](F:/Codex-Hackathon/src/autotestgen/adapters/python/adapter.py)
- JavaScript: [src/autotestgen/adapters/javascript/adapter.py](F:/Codex-Hackathon/src/autotestgen/adapters/javascript/adapter.py)
- Go: [src/autotestgen/adapters/go/adapter.py](F:/Codex-Hackathon/src/autotestgen/adapters/go/adapter.py)

Language detection and adapter selection live in [src/autotestgen/adapters/registry.py](F:/Codex-Hackathon/src/autotestgen/adapters/registry.py). In `auto` mode the system can merge inventories from multiple languages into one mixed inventory.

## Closed-Loop Orchestrator

The single-target control plane is [src/autotestgen/orchestrator/service.py](F:/Codex-Hackathon/src/autotestgen/orchestrator/service.py).

`ClosedLoopOrchestrator` owns the loop for one selected target:

1. initialize SQLite storage
2. discover targets through the available adapter set
3. persist discovered targets
4. resolve the requested target
5. load a small set of successful prior examples for the same language/adapter
6. build a `PromptContext` using the target adapter
7. render prompt text with `PromptBuilder`
8. generate or repair a test artifact
9. persist the prompt and artifact metadata
10. execute the generated test through the target adapter
11. classify the execution result
12. persist execution and failure data
13. update uncovered lines and retry strategy
14. retry until success, hard-stop, or iteration limit

If a run succeeds and reaches the coverage threshold, the orchestrator reruns the generated test a few times to perform a basic stability check.

### Retry and Strategy Selection

The orchestrator does not retry blindly. It derives a strategy from the previous failure and uncovered lines, for example:

- `baseline`
- `import_recovery`
- `argument_repair`
- `assertion_repair`
- `coverage_expansion`

It also distinguishes between:

- initial generation
- full regeneration
- targeted repair of the previous generated test

That metadata is stored on each `AttemptRecord`.

## Prompt and Generation Pipeline

Prompt construction and heuristic generation live in [src/autotestgen/generator/engine.py](F:/Codex-Hackathon/src/autotestgen/generator/engine.py).

### PromptBuilder

`PromptBuilder` converts `PromptContext` into a structured prompt with embedded metadata tags. The prompt carries:

- prompt version
- strategy
- language and framework
- target contract
- execution constraints
- source under test
- nearby context
- prior failures
- coverage goals
- reference examples
- output contract

The metadata block is machine-readable and is reused later by the generator.

### ModelBackedTestGenerator

The top-level generator is [src/autotestgen/generator/model_backed.py](F:/Codex-Hackathon/src/autotestgen/generator/model_backed.py).

Its flow is:

1. decide whether LLM use is allowed by `generator_mode`
2. create an OpenAI-backed provider if an API key is available
3. request test code from the provider
4. normalize model output
5. ask the active adapter to validate the generated test
6. if provider use fails or validation fails, fall back to heuristic generation

This means the real architecture today is not "template only". It is model-backed when configured, with deterministic fallback when model use is unavailable or invalid.

### Heuristic Generation

Heuristic generation is still important:

- it guarantees a local fallback path
- it preserves retry metadata such as prompt version and strategy
- it generates language-specific test files when no provider is available

## Execution and Classification

Execution is adapter-owned, not orchestrator-owned.

That distinction matters:

- Python uses pytest-based execution and coverage capture
- JavaScript and Go run through their own adapter-specific runners
- failure classification is also language-specific, though it maps into a shared `FailureClassification` shape

The orchestrator only relies on `ExecutionResult` and `FailureClassification`.

For Python, the main runtime runner is [src/autotestgen/runner/pytest_runner.py](F:/Codex-Hackathon/src/autotestgen/runner/pytest_runner.py). It writes generated tests to the configured artifact root, executes them with coverage, captures stdout/stderr, and extracts missing lines for retry guidance.

## Persistence

Persistence lives in [src/autotestgen/storage/sqlite.py](F:/Codex-Hackathon/src/autotestgen/storage/sqlite.py).

The SQLite schema currently includes:

- `targets`
- `prompts`
- `attempts`
- `results`
- `failure_classifications`

Stored fields include:

- discovered target metadata and serialized payloads
- prompt text, prompt version, and strategy
- generated test path and source
- generation mode and parent attempt number
- targeted uncovered lines
- provider name, model name, and fallback reason
- execution result, duration, and coverage payload
- failure category, subtype, recoverability, message, and summary

The store also exposes a small retrieval API for successful historical examples, which are fed back into prompt construction.

## Repository Workflow

Repository-level automation lives in [src/autotestgen/repo/service.py](F:/Codex-Hackathon/src/autotestgen/repo/service.py).

`RepositoryWorkflowRunner` wraps the single-target orchestrator with repo intake and reporting:

```text
source repo/path/url
  -> RepositoryIntake prepares workspace
  -> ProjectDetector infers language/framework/package manager
  -> EnvironmentBootstrapper installs dependencies when needed
  -> ClosedLoopOrchestrator discovers targets
  -> selected targets run through the normal closed loop
  -> optional RepoRepairEngine edits source files and revalidates
  -> report JSON + markdown review + unified diff patch are written
```

### Intake

[src/autotestgen/repo/intake.py](F:/Codex-Hackathon/src/autotestgen/repo/intake.py) handles:

- local path copy into a fresh workspace
- remote `git clone`
- optional branch/ref/commit checkout
- timestamped session directory naming

### Detection

[src/autotestgen/repo/detect.py](F:/Codex-Hackathon/src/autotestgen/repo/detect.py) infers:

- language
- test framework
- package manager
- bootstrap method

It also supports mixed-language repositories.

### Bootstrap

[src/autotestgen/repo/bootstrap.py](F:/Codex-Hackathon/src/autotestgen/repo/bootstrap.py) installs repo dependencies based on the detected project shape:

- Python: creates `.autotestgen/venv`, installs `pytest`, `pytest-cov`, then repo dependencies
- JavaScript: runs the appropriate package-manager install command
- Go: runs `go mod download`

### Source Repair

[src/autotestgen/repo/repair.py](F:/Codex-Hackathon/src/autotestgen/repo/repair.py) is separate from generated-test repair.

It attempts to repair the repository source file itself by:

- reading the current source
- building a repair prompt from the failing generated test and execution logs
- requesting a full rewritten source file from the provider
- writing the new source
- rerunning the previous generated test to verify the repair
- reverting the source if validation still fails

This is only available when model-backed repair is configured.

## Benchmark Architecture

Benchmark execution lives in [src/autotestgen/benchmark/runner.py](F:/Codex-Hackathon/src/autotestgen/benchmark/runner.py).

`BenchmarkRunner`:

- loads a JSON spec of benchmark targets
- creates an isolated artifact root per benchmark target
- instantiates a fresh `ClosedLoopOrchestrator` per spec entry
- runs each target independently
- aggregates pass rate, coverage gain, fallback rate, and repair success metrics
- optionally writes a JSON report to disk

This keeps benchmark execution close to the real runtime path instead of introducing a second benchmarking-only execution stack.

## Current Artifact Layout

The runtime writes generated state under gitignored directories:

- `.autotestgen/state.db`
- `.autotestgen/generated_tests/`
- `.autotestgen/benchmarks/`
- `.autotestgen_repo_runs/<session>/`

Repository workflow outputs typically include:

- `repo_run_report.json`
- `repo_review.md`
- `generated_tests.patch`

## Current Limitations

These limitations are real in the current implementation:

- execution is not sandboxed or containerized
- repository bootstrap can modify the workspace clone by installing dependencies
- stability checking is basic rerun-based validation, not full flake detection
- target ranking is still lightweight and mostly adapter-driven
- failure taxonomies are normalized but not deeply specialized across all languages
- repository source repair depends on provider access and rewrites whole files rather than patching AST nodes
- benchmark runs are sequential, not parallelized

## Architectural Summary

The implemented architecture is a layered pipeline:

- adapters specialize language behavior
- the closed-loop orchestrator owns retry logic and persistence
- prompt construction and generation are shared across languages
- repository workflows and benchmarks reuse the same orchestrator instead of bypassing it

That is the right shape for the current project. The system is already beyond a Python-only scaffold, but it is still intentionally pragmatic: one orchestrator, one persistence layer, adapter-owned execution, and optional model-backed generation with deterministic fallback.
