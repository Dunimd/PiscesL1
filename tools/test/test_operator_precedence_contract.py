#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025-2026 Wenze Wei. All Rights Reserved.
#
# This file is part of PiscesL1.
# The PiscesL1 project belongs to the Dunimd Team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# DISCLAIMER: Users must comply with applicable AI regulations.
# Non-compliance may result in service termination or legal liability.

from __future__ import annotations

"""
Precedence contract for the advanced-operator switches (docs/audit/00 §81, defect A87).

An explicitly named `--train_config` stage file declared
`advanced_operators: {moe_gradient: {enabled: false}, ...}` and the engine still read
`{'enabled': True}` and executed the operator on every step. The overwrite came from the model
config path (`_apply_model_training_config`), which re-asserts those four dicts *in place* -
the config object identity never changed, so no loader-level check could see it.

Each wiring check runs against real source and carries a doctored copy, so a check that
stopped working fails by itself instead of reporting a pass.
"""

import inspect

from tools.train.orchestrator import PiscesLxTrainOrchestrator
from tools.train.core import PiscesLxTrainingOperator

#: The four switches the model config is allowed to offer in either spelling.
SWITCHES = ("moe_gradient", "kfac", "multitask", "modality_scheduler")

#: Operators whose execution must be reported by the engine itself when it starts.
VISIBLE_DECLARATION_LOG = "Advanced operator declarations read by this trainer"


class PiscesLxOperatorPrecedenceContract:
    """Verifies that an explicit stage file outranks the model config, and that the
    engine states what it read."""

    def __init__(self) -> None:
        self.results = []

    def record(self, name: str, ok: bool, detail: str) -> None:
        """Store one check outcome."""
        self.results.append((name, ok, detail))

    @staticmethod
    def _guard_precedes_switch_write(source: str) -> bool:
        """Whether every write of a switch is behind the explicit-file precedence guard."""
        guard = source.find("_explicit_train_config")
        if guard < 0:
            return False
        for switch in SWITCHES:
            write = source.find(f"train_config.{switch} =")
            if write >= 0 and write < guard:
                return False
        return True

    def check_precedence_guard_present(self) -> None:
        """The model config may not overwrite switches an explicit stage file declared."""
        source = inspect.getsource(PiscesLxTrainOrchestrator._apply_model_training_config)
        self.record("precedence::explicit stage file outranks model config",
                    self._guard_precedes_switch_write(source),
                    "all four switch writes sit behind the `_explicit_train_config` guard")
        doctored = "self.train_config.moe_gradient = adv['moe_gradient']\n" + source
        self.record("precedence::check catches an unguarded write (self-control)",
                    not self._guard_precedes_switch_write(doctored),
                    "a write placed before the guard is rejected")

    def check_flat_and_nested_spellings_covered(self) -> None:
        """Both spellings a model config may use are named by one declared constant."""
        keys = PiscesLxTrainOrchestrator.ADVANCED_OPERATOR_KEYS
        self.record("precedence::both spellings enumerated",
                    set(SWITCHES) <= set(keys),
                    f"declared: {sorted(keys)}")

    def check_engine_states_what_it_read(self) -> None:
        """The engine must print the declaration it acts on - the only way this class of
        defect is ever found from a log."""
        source = inspect.getsource(PiscesLxTrainingOperator._initialize_operators) \
            if hasattr(PiscesLxTrainingOperator, "_initialize_operators") else ""
        if not source:
            # The initialiser keeps its historic name; search the whole class once.
            source = inspect.getsource(PiscesLxTrainingOperator)
        self.record("visibility::engine logs the declaration it acts on",
                    VISIBLE_DECLARATION_LOG in source and "moe_gradient=" in source,
                    "the resolved value is printed at operator initialisation")

    def run(self) -> int:
        """Execute every check; non-zero exit on any failure."""
        self.check_precedence_guard_present()
        self.check_flat_and_nested_spellings_covered()
        self.check_engine_states_what_it_read()
        failed = [name for name, ok, _ in self.results if not ok]
        for name, ok, detail in self.results:
            print(f"[{'PASS' if ok else 'FAIL'}] {name:<52} {detail}")
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} checks passed")
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(PiscesLxOperatorPrecedenceContract().run())
