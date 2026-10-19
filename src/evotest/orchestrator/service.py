from __future__ import annotations

from dataclasses import replace

from evotest.adapters import get_adapter, get_adapter_for_language, resolve_languages
from evotest.adapters.base import LanguageAdapter
from evotest.config import HarnessConfig
from evotest.feedback.representation import apply_feedback
from evotest.generator import ModelBackedTestGenerator, PromptBuilder
from evotest.models import (
    AttemptRecord,
    FailureClassification,
    FunctionTarget,
    GeneratedTestArtifact,
    ModuleTarget,
    OrchestrationResult,
    ProjectInventory,
)
from evotest.storage import SQLiteStore


class ClosedLoopOrchestrator:
    def __init__(self, config: HarnessConfig, *, adapter: LanguageAdapter | None = None) -> None:
        self.config = config.resolved()
        self.adapter = adapter
        self._adapter_cache: dict[str, LanguageAdapter] = {}
        if adapter is not None:
            self._adapter_cache[adapter.language_id] = adapter
        self.store = SQLiteStore(self.config.database_path)
        self.prompt_builder = PromptBuilder()
        self.generator = ModelBackedTestGenerator(
            config=self.config,
            adapter=adapter or get_adapter(self.config),
        )

    def initialize(self) -> None:
        self.store.initialize()

    def discover(self) -> ProjectInventory:
        self.initialize()
        inventory, _ = self._discover_inventories()
        return inventory

    def _discover_inventories(self) -> tuple[ProjectInventory, dict[str, ProjectInventory]]:
        inventories: dict[str, ProjectInventory] = {}
        for language, adapter in self._available_adapters().items():
            inventories[language] = adapter.discover(self.config.project_root)
        inventory = self._merge_inventories(inventories)
        for function in inventory.functions:
            self.store.upsert_function_target(function)
        for module in inventory.modules:
            self.store.upsert_module_target(module)
        return inventory, inventories

    def run_target(self, target_ref: str) -> OrchestrationResult:
        self.initialize()
        inventory, inventories = self._discover_inventories()
        target = self._resolve_target(target_ref, inventory)
        if target is None:
            raise ValueError(f"Target not found: {target_ref}")
        target_adapter = self._adapter_for_target(target)
        target_inventory = inventories.get(getattr(target, "language", target_adapter.language_id), inventory)
        successful_examples = self.store.load_successful_attempt_examples(
            language=target_adapter.language_id,
            adapter_id=target_adapter.adapter_id,
            exclude_target_id=target.target_id,
            limit=2,
        )
        attempts: list[AttemptRecord] = []
        previous_failures: list[str] = []
        uncovered_lines: list[int] = []
        best_coverage = 0.0
        stability_passed = False
        previous_attempt: AttemptRecord | None = None

        for attempt_number in range(1, self.config.max_iterations + 1):
            strategy = self._strategy_for_attempt(previous_attempt, uncovered_lines, attempt_number)
            context = target_adapter.build_prompt_context(
                target=target,
                inventory=target_inventory,
                previous_failures=previous_failures,
                uncovered_lines=uncovered_lines,
                strategy=strategy,
                prompt_version=self.prompt_builder.version,
                previous_attempt=previous_attempt,
                successful_examples=successful_examples,
            )
            apply_feedback(context, previous_attempt, self.config.feedback_format)
            prompt_text = self.prompt_builder.build(context)
            artifact = self._build_artifact(
                target=target,
                prompt_text=prompt_text,
                attempt_number=attempt_number,
                previous_attempt=previous_attempt,
                adapter=target_adapter,
            )
            self._persist_artifact(artifact)
            attempt = AttemptRecord(
                target_id=target.target_id,
                attempt_number=attempt_number,
                prompt_text=prompt_text,
                generated_test_path=artifact.test_file_path,
                generated_test_source=artifact.test_source,
                prompt_version=artifact.prompt_version,
                strategy=artifact.strategy,
                generation_mode=artifact.generation_mode,
                parent_attempt_number=artifact.parent_attempt_number,
                targeted_uncovered_lines=list(uncovered_lines),
                provider_name=artifact.provider_name,
                model_name=artifact.model_name,
                fallback_reason=artifact.fallback_reason,
                language=artifact.language,
                adapter_id=artifact.adapter_id,
            )
            self.store.save_attempt(attempt)

            result = target_adapter.run(
                target=target,
                test_source=artifact.test_source,
                test_file_path=artifact.test_file_path,
                attempt_number=attempt_number,
            )
            attempt.execution_result = result
            self.store.save_execution_result(result)

            coverage = result.coverage.coverage_percent if result.coverage else 0.0
            targeted_uncovered_lines = uncovered_lines
            uncovered_lines = result.coverage.missing_lines if result.coverage else []
            coverage_gain = coverage - best_coverage
            best_coverage = max(best_coverage, coverage)

            failure = target_adapter.classify_result(result)
            if failure is None and coverage < self.config.coverage_target:
                failure = FailureClassification(
                    category="test_design",
                    subtype="no_coverage_gain",
                    recoverable=True,
                    message=(
                        f"Coverage reached {coverage:.2f}%, below target "
                        f"{self.config.coverage_target:.2f}%."
                    ),
                    summary="Generated test passed but did not hit the desired target coverage.",
                )
            attempt.failure = failure
            attempt.targeted_uncovered_lines = targeted_uncovered_lines
            if failure is not None:
                self.store.save_failure(target.target_id, attempt_number, failure)
                previous_failures.append(f"{failure.subtype}: {failure.message}")

            attempts.append(attempt)
            previous_attempt = attempt

            if result.success and coverage >= self.config.coverage_target:
                stability_passed = self._rerun_for_stability(target, artifact, target_adapter)
                return OrchestrationResult(
                    target_id=target.target_id,
                    attempts=attempts,
                    final_success=stability_passed,
                    final_coverage=coverage,
                    stability_passed=stability_passed,
                    language=artifact.language,
                    adapter_id=artifact.adapter_id,
                )

            if not self._should_retry(failure, coverage_gain, attempt_number):
                break

        return OrchestrationResult(
            target_id=target.target_id,
            attempts=attempts,
            final_success=False,
            final_coverage=best_coverage,
            stability_passed=stability_passed,
            language=getattr(target, "language", target_adapter.language_id),
            adapter_id=getattr(target, "adapter_id", target_adapter.adapter_id),
        )

    def _rerun_for_stability(
        self,
        target: FunctionTarget | ModuleTarget,
        artifact: GeneratedTestArtifact,
        adapter: LanguageAdapter,
    ) -> bool:
        for rerun_index in range(self.config.stability_reruns):
            result = adapter.run(
                target=target,
                test_source=artifact.test_source,
                test_file_path=artifact.test_file_path,
                attempt_number=1000 + rerun_index,
            )
            if not result.success:
                return False
        return True

    def _build_artifact(
        self,
        *,
        target: FunctionTarget | ModuleTarget,
        prompt_text: str,
        attempt_number: int,
        previous_attempt: AttemptRecord | None,
        adapter: LanguageAdapter,
    ) -> GeneratedTestArtifact:
        if previous_attempt and previous_attempt.failure and self._should_attempt_repair(previous_attempt.failure):
            failure = previous_attempt.failure
            if self.config.feedback_format == "compact":
                failure = replace(failure, message=failure.summary[:240], summary=failure.summary[:240])
            repaired_source = adapter.repair(
                target=target,
                previous_test_source=previous_attempt.generated_test_source,
                failure=failure,
                prompt_text=prompt_text,
                attempt_number=attempt_number,
                output_dir=self.config.test_output_dir,
                parent_attempt_number=previous_attempt.attempt_number,
                llm_generator=self.generator,
            )
            if repaired_source is not None:
                return repaired_source
        generation_mode = "initial" if previous_attempt is None else "regenerate"
        return self.generator.generate(
            target=target,
            prompt_text=prompt_text,
            output_dir=self.config.test_output_dir,
            attempt_number=attempt_number,
            generation_mode=generation_mode,
            parent_attempt_number=previous_attempt.attempt_number if previous_attempt else None,
            adapter=adapter,
        )

    def _available_adapters(self) -> dict[str, LanguageAdapter]:
        if self.adapter is not None:
            return {self.adapter.language_id: self.adapter}
        adapters: dict[str, LanguageAdapter] = {}
        for language in resolve_languages(self.config):
            adapters[language] = self._adapter_cache.setdefault(language, get_adapter_for_language(self.config, language))
        return adapters

    def _adapter_for_target(self, target: FunctionTarget | ModuleTarget) -> LanguageAdapter:
        if self.adapter is not None:
            return self.adapter
        language = getattr(target, "language", self.generator.adapter.language_id)
        return self._adapter_cache.setdefault(language, get_adapter_for_language(self.config, language))

    @staticmethod
    def _merge_inventories(inventories: dict[str, ProjectInventory]) -> ProjectInventory:
        function_map: dict[str, FunctionTarget] = {}
        module_map: dict[str, ModuleTarget] = {}
        for inventory in inventories.values():
            for function in inventory.functions:
                function_map.setdefault(function.target_id, function)
            for module in inventory.modules:
                module_map.setdefault(module.target_id, module)
        functions = sorted(function_map.values(), key=lambda target: (-target.target_score, target.target_id))
        modules = sorted(module_map.values(), key=lambda target: (-target.target_score, target.target_id))
        languages = sorted(inventories)
        if len(languages) == 1:
            language = languages[0]
            adapter_id = next(iter(inventories.values())).adapter_id
        else:
            language = "mixed"
            adapter_id = "mixed"
        return ProjectInventory(functions=functions, modules=modules, language=language, adapter_id=adapter_id)

    @staticmethod
    def _find_target(target_ref: str, inventory: ProjectInventory) -> FunctionTarget | ModuleTarget | None:
        normalized = target_ref.replace("\\", "/")
        for function in inventory.functions:
            if function.target_id == normalized or f"{function.file_path}::{function.qualname}" == target_ref:
                return function
        for module in inventory.modules:
            if module.target_id == normalized or str(module.file_path) == target_ref:
                return module
        return None

    def _resolve_target(self, target_ref: str, inventory: ProjectInventory) -> FunctionTarget | ModuleTarget | None:
        if self.adapter is not None:
            return self.adapter.find_target(target_ref, inventory)
        return self._find_target(target_ref, inventory)

    @staticmethod
    def _persist_artifact(artifact: GeneratedTestArtifact) -> None:
        artifact.test_file_path.parent.mkdir(parents=True, exist_ok=True)
        artifact.test_file_path.write_text(artifact.test_source, encoding="utf-8")

    def _should_attempt_repair(self, failure: FailureClassification) -> bool:
        return failure.subtype in {
            "syntax_error",
            "indentation_error",
            "collection_error",
            "import_error",
            "missing_dependency",
            "circular_import",
            "type_error",
            "value_error",
            "attribute_error",
            "path_error",
            "bad_fixture",
            "bad_parametrization",
            "mock_misconfiguration",
            "assertion_failure",
            "equality_mismatch",
            "wrong_exception_type",
            "too_weak_oracle",
            "too_broad_assertion",
            "no_coverage_gain",
        }

    def _should_retry(
        self,
        failure: FailureClassification | None,
        coverage_gain: float,
        attempt_number: int,
    ) -> bool:
        if failure is None:
            return True
        if not failure.recoverable:
            return False
        if failure.subtype in {"permission_error", "encoding_error", "network_error", "external_service_error"}:
            return False
        if failure.subtype in {
            "assertion_failure",
            "equality_mismatch",
            "wrong_exception_type",
            "too_weak_oracle",
            "too_broad_assertion",
            "no_coverage_gain",
            "float_tolerance_failure",
        } and attempt_number > 1 and coverage_gain < 1.0:
            return False
        return True

    def _strategy_for_attempt(
        self,
        previous_attempt: AttemptRecord | None,
        uncovered_lines: list[int],
        attempt_number: int,
    ) -> str:
        if attempt_number == 1 or previous_attempt is None or previous_attempt.failure is None:
            return "coverage_expansion" if uncovered_lines else "baseline"
        subtype = previous_attempt.failure.subtype
        if subtype in {"import_error", "missing_dependency", "circular_import"}:
            return "import_recovery"
        if subtype in {
            "type_error",
            "value_error",
            "attribute_error",
            "path_error",
            "bad_fixture",
            "bad_parametrization",
            "mock_misconfiguration",
        }:
            return "argument_repair"
        if subtype in {
            "assertion_failure",
            "equality_mismatch",
            "wrong_exception_type",
            "too_weak_oracle",
            "too_broad_assertion",
            "float_tolerance_failure",
        }:
            return "assertion_repair"
        if subtype in {"no_coverage_gain", "collection_error"} or uncovered_lines:
            return "coverage_expansion"
        return "baseline"
