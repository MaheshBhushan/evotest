# Autotestgen Architecture

This document describes the architecture that is actually implemented in this repository today.

It is a Python-first closed-loop test generation harness with these current properties:

- Python targets only
- `pytest` + `pytest-cov`
- One function or module target per run
- SQLite persistence
- Retry loop with failure classification
- Deterministic template-based test generation

It is not yet a full multi-agent system, and it is not yet using an LLM-backed generator or repair step.

## System Overview

```text
CLI
  |
  v
ClosedLoopOrchestrator
  |-- PythonProjectAnalyzer
  |-- PromptBuilder
  |-- StaticTestGenerator
  |-- PytestRunner
  |-- FailureClassifier
  `-- SQLiteStore
```

The orchestrator is the control plane. It owns the end-to-end loop for a single target:

1. Discover the codebase and persist targets.
2. Resolve the requested target.
3. Build a prompt context from source, imports, nearby symbols, existing tests, prior failures, and uncovered lines.
4. Generate a test artifact.
5. Execute the generated test with coverage enabled.
6. Classify the outcome.
7. Persist prompt, test source, execution result, and failure metadata.
8. Retry until success, plateau, or iteration limit.

## Runtime Flow

### 1. CLI entrypoint

File: [src/autotestgen/cli.py](F:/Codex-Hackathon/src/autotestgen/cli.py)

The CLI exposes two commands:

- `discover`: scan the project and list module/function targets
- `run`: execute the closed-loop harness for one target

The CLI builds a `HarnessConfig` and hands off to `ClosedLoopOrchestrator`.

### 2. Configuration

File: [src/autotestgen/config.py](F:/Codex-Hackathon/src/autotestgen/config.py)

`HarnessConfig` controls:

- `project_root`
- `database_path`
- `max_iterations`
- `max_runtime_seconds`
- `coverage_target`
- `pytest_command`
- generated test output directory
- stability rerun count

### 3. Orchestrator

File: [src/autotestgen/orchestrator/service.py](F:/Codex-Hackathon/src/autotestgen/orchestrator/service.py)

`ClosedLoopOrchestrator` is the main runtime coordinator. It wires together:

- `PythonProjectAnalyzer`
- `PromptBuilder`
- `StaticTestGenerator`
- `PytestRunner`
- `FailureClassifier`
- `SQLiteStore`

`run_target()` is the core loop. For each attempt it:

1. Builds `PromptContext`
2. Builds prompt text
3. Generates a test file
4. Validates syntax
5. Runs pytest with coverage
6. Computes coverage delta and uncovered lines
7. Classifies failure
8. Persists artifacts and metadata
9. Applies simple retry/stop rules

If a run succeeds and reaches the coverage threshold, the runner performs repeated reruns to check basic stability.

## Component Responsibilities

### Analyzer

File: [src/autotestgen/analyzer/discovery.py](F:/Codex-Hackathon/src/autotestgen/analyzer/discovery.py)

`PythonProjectAnalyzer` performs AST-based discovery.

What it does now:

- scans Python files under the project root
- skips tests, cache dirs, `.venv`, and generated output
- extracts module targets
- extracts function and method targets
- captures imports
- captures decorators
- captures function source ranges
- derives simple dependency hints from referenced names

What it does not do yet:

- target ranking
- call graph construction
- coverage-guided target selection across the repo
- type inference beyond what can be inferred from raw source

### Prompt Builder

File: [src/autotestgen/generator/engine.py](F:/Codex-Hackathon/src/autotestgen/generator/engine.py)

`PromptBuilder` assembles the generation context into a prompt-like string.

The current prompt includes:

- target id
- target kind
- imports
- source code
- nearby symbols from the same module
- related existing tests
- previous failure summaries
- uncovered lines

Important limitation:

The prompt exists and is persisted, but the current generator does not semantically use it. It is there to support the next stage of the architecture.

### Generator

File: [src/autotestgen/generator/engine.py](F:/Codex-Hackathon/src/autotestgen/generator/engine.py)

`StaticTestGenerator` is a deterministic template generator.

What it does now:

- generates pytest source for one function or module
- infers default argument values from parameter names
- handles async functions with `asyncio.run(...)`
- writes generated tests into `.autotestgen/generated_tests`
- validates generated syntax with `ast.parse`

What it does not do yet:

- call an LLM
- regenerate differently based on failure class
- perform targeted repair of a previous attempt
- synthesize mocks from dependency analysis
- use few-shot examples from stored successes

### Runner

File: [src/autotestgen/runner/pytest_runner.py](F:/Codex-Hackathon/src/autotestgen/runner/pytest_runner.py)

`PytestRunner` executes one generated test file with coverage enabled.

What it does now:

- invokes `python -m pytest`
- injects `project_root` and optional `src` into `PYTHONPATH`
- runs the generated test file only
- collects `stdout`, `stderr`, exit code, duration, and coverage JSON
- extracts target-specific coverage when available
- enforces per-attempt timeout
- reruns passing tests for simple stability checks

What it does not do yet:

- run in Docker
- copy the repo into an isolated temp workspace per attempt
- block filesystem or network side effects
- collect branch coverage or mutation metrics

### Feedback Classifier

File: [src/autotestgen/feedback/classifier.py](F:/Codex-Hackathon/src/autotestgen/feedback/classifier.py)

`FailureClassifier` converts raw pytest output into structured failure metadata.

Currently implemented categories include:

- `collection_parse.syntax_error`
- `collection_parse.indentation_error`
- `import_dependency.import_error`
- `runtime.attribute_error`
- `runtime.type_error`
- `runtime.value_error`
- `assertion_semantics.assertion_failure`
- `execution_control.timeout`
- fallback `runtime.runtime_error`
- synthetic `test_design.no_coverage_gain` when coverage is below target

This classifier is intentionally small right now. The retry policy is broader than the emitted taxonomy, so the classifier is one of the next components that needs expansion.

### Storage

File: [src/autotestgen/storage/sqlite.py](F:/Codex-Hackathon/src/autotestgen/storage/sqlite.py)

`SQLiteStore` persists run state and artifacts.

Tables:

- `targets`
- `prompts`
- `attempts`
- `results`
- `failure_classifications`

What is stored today:

- discovered target metadata
- prompt text per attempt
- generated test source and file path
- execution outcome
- coverage summary
- failure category/subtype/message

What is not stored yet:

- prompt version
- generation strategy
- repaired vs regenerated attempt mode
- parent-child attempt lineage
- successful pattern library

## Data Model

File: [src/autotestgen/models.py](F:/Codex-Hackathon/src/autotestgen/models.py)

The runtime is built around a small set of dataclasses:

- `FunctionTarget`
- `ModuleTarget`
- `ProjectInventory`
- `PromptContext`
- `GeneratedTestArtifact`
- `CoverageResult`
- `ExecutionResult`
- `FailureClassification`
- `AttemptRecord`
- `OrchestrationResult`

These models provide the contract between discovery, generation, execution, classification, and persistence.

## Current End-to-End Loop

```text
discover targets
  -> persist targets
  -> select target
  -> build prompt context
  -> generate deterministic test
  -> validate syntax
  -> run pytest with coverage
  -> classify result
  -> persist prompt/result/failure
  -> retry or stop
```

This is already a real closed loop, but it is still a scaffold rather than a full adaptive agent.

## What The Architecture Is Missing

These are the main gaps between the implemented system and the intended long-term design:

- LLM-backed generation instead of deterministic templates
- a repair stage before full regeneration
- real sandboxing or containerized execution
- richer failure taxonomy aligned with retry behavior
- target ranking and discovery heuristics
- stronger test quality signals beyond line coverage
- benchmark and prompt-version tracking as first-class runtime inputs

## Recommended Next Architecture Step

The next change should not be a multi-agent rewrite.

The next architectural upgrade should be:

1. keep the current single orchestrator
2. add a real generation backend that uses `PromptContext`
3. add a `RepairEngine` for surgical fixes
4. expand the classifier only where it changes retry behavior
5. isolate execution in a temp workspace before adding Docker

That preserves the current shape of the codebase while making the loop meaningfully adaptive.
