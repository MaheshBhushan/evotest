from __future__ import annotations

from pathlib import Path

from evotest.analyzer.discovery import PythonProjectAnalyzer
from evotest.models import FunctionTarget, ModuleTarget, ProjectInventory


class PythonDiscovery:
    language_id = "python"
    adapter_id = "python"

    def discover(self, project_root: Path) -> ProjectInventory:
        inventory = PythonProjectAnalyzer(project_root).discover()
        for function in inventory.functions:
            function.language = self.language_id
            function.adapter_id = self.adapter_id
        for module in inventory.modules:
            module.language = self.language_id
            module.adapter_id = self.adapter_id
        inventory.language = self.language_id
        inventory.adapter_id = self.adapter_id
        return inventory

    def find_target(
        self,
        target_ref: str,
        inventory: ProjectInventory,
        *,
        project_root: Path,
    ) -> FunctionTarget | ModuleTarget | None:
        target = PythonProjectAnalyzer(project_root).find_target(target_ref, inventory=inventory)
        if target is not None:
            target.language = self.language_id
            target.adapter_id = self.adapter_id
        return target
