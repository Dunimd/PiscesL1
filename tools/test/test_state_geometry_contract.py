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
Geometry contract for anything that restores weights (docs/audit/00 §78, defect A84).

Restoring a checkpoint that belongs to another model is not a partial success: the
mismatched parameters stay randomly initialised and the run continues behind a green
"Resumed training from" line. The QLoRA cache path did exactly that on purpose - it
deleted every cached tensor whose shape differed and loaded the rest at DEBUG volume.

Behavioural checks run against tiny real modules; the wiring checks run against source and
carry a doctored copy of the buggy form, so a check that stopped working is caught by the
test itself rather than reported as a pass.
"""

import inspect
from typing import List, Tuple

from torch import nn
import torch

from tools.train.core import PiscesLxTrainingOperator
from tools.train.orchestrator import PiscesLxTrainOrchestrator
from tools.train.state_geometry import (
    _MAX_REPORTED_KEYS,
    PiscesLxStateGeometryError,
    PiscesLxStateGeometryGate,
)

#: Widths chosen so the two geometries share every key name and differ only in shape -
#: exactly the case a non-strict load accepts.
_NARROW = 4
_WIDE = 9

#: More samples than the gate is allowed to name, to prove the diagnosis truncates.
_MANY_LAYERS = 12


class _PiscesLxProbeNet(nn.Module):
    """Minimal module whose geometry can be varied without changing key names."""

    def __init__(self, width: int, layers: int = 1) -> None:
        super().__init__()
        self.blocks = nn.ModuleList(
            [nn.Linear(width, width, bias=False) for _ in range(layers)])


class PiscesLxStateGeometryContract:
    """Executes the restore-contract checks and reports them as pass/fail lines."""

    def __init__(self) -> None:
        self.results: List[Tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str) -> None:
        """Store one check outcome."""
        self.results.append((name, ok, detail))

    @staticmethod
    def _old_cache_pattern(model: nn.Module, state: dict) -> int:
        """Reproduce the deleted behaviour: drop mismatched keys, load the rest silently."""
        candidate = dict(state)
        model_state = model.state_dict()
        dropped = 0
        for key in list(candidate):
            if key in model_state and candidate[key].shape != model_state[key].shape:
                del candidate[key]
                dropped += 1
        model.load_state_dict(candidate, strict=False)
        return dropped

    def check_matching_geometry_accepted(self) -> None:
        """A state describing this exact geometry must pass with no exception."""
        model = _PiscesLxProbeNet(_NARROW)
        try:
            PiscesLxStateGeometryGate.require_restorable(
                model, model.state_dict(), "matching state")
            self.record("geometry::matching state accepted", True, "no refusal")
        except PiscesLxStateGeometryError as error:
            self.record("geometry::matching state accepted", False, str(error)[:80])

    def check_other_geometry_refused(self) -> None:
        """Wrong-tier weights must be refused by name, not loaded as far as they fit."""
        model = _PiscesLxProbeNet(_NARROW)
        foreign = _PiscesLxProbeNet(_WIDE).state_dict()
        try:
            PiscesLxStateGeometryGate.require_restorable(model, foreign, "checkpoint other.pt")
            self.record("geometry::foreign state refused", False, "no exception raised")
            return
        except PiscesLxStateGeometryError as error:
            text = str(error)
        named = "blocks.0.weight" in text
        shaped = f"[{_WIDE}, {_WIDE}]" in text and f"[{_NARROW}, {_NARROW}]" in text
        counted = f"{len(foreign)} of {len(foreign)} tensor(s)" in text
        self.record("geometry::foreign state refused",
                    named and shaped and counted,
                    f"names key={named} both shapes={shaped} counts={counted}")

    def check_old_pattern_was_the_bug(self) -> None:
        """Self-control: the pattern that was removed really did load silently."""
        model = _PiscesLxProbeNet(_NARROW)
        before = model.state_dict()["blocks.0.weight"].clone()
        dropped = self._old_cache_pattern(model, _PiscesLxProbeNet(_WIDE).state_dict())
        after = model.state_dict()["blocks.0.weight"]
        silent = dropped == len(model.state_dict())
        unchanged = bool(torch.equal(before, after))
        self.record("geometry::removed pattern was silent corruption",
                    silent and unchanged,
                    f"dropped={dropped} keys, weights untouched={unchanged} (no error raised)")

    def check_diagnosis_truncates(self) -> None:
        """Hundreds of mismatches must collapse to a count plus a few samples."""
        model = _PiscesLxProbeNet(_NARROW, layers=_MANY_LAYERS)
        foreign = _PiscesLxProbeNet(_WIDE, layers=_MANY_LAYERS).state_dict()
        try:
            PiscesLxStateGeometryGate.require_restorable(model, foreign, "checkpoint big.pt")
            self.record("geometry::diagnosis truncates", False, "no exception raised")
            return
        except PiscesLxStateGeometryError as error:
            text = str(error)
        hidden = _MANY_LAYERS - _MAX_REPORTED_KEYS
        self.record("geometry::diagnosis truncates",
                    f"(+{hidden} more)" in text
                    and text.count("blocks.0") <= 1
                    and text.count("vs model") == _MAX_REPORTED_KEYS,
                    f"names {_MAX_REPORTED_KEYS} of {_MANY_LAYERS} keys, suppresses {hidden}")

    def check_empty_state_refused(self) -> None:
        """An empty payload carries no weights and must not count as a restore."""
        model = _PiscesLxProbeNet(_NARROW)
        try:
            PiscesLxStateGeometryGate.require_restorable(model, {}, "empty.pt")
            self.record("geometry::empty state refused", False, "no exception raised")
        except PiscesLxStateGeometryError:
            self.record("geometry::empty state refused", True, "refused")

    @staticmethod
    def _cache_deletes_mismatched_keys(source: str) -> bool:
        """Whether the cache path still drops mismatched tensors instead of refusing."""
        return "del cached_state[k]" in source or "Cache shape mismatch" in source

    def check_cache_path_gated(self) -> None:
        """The QLoRA cache restore must refuse a foreign geometry as a whole."""
        source = inspect.getsource(PiscesLxTrainingOperator.initialize_model)
        self.record("wiring::cache refuses foreign geometry",
                    (not self._cache_deletes_mismatched_keys(source))
                    and "require_restorable" in source,
                    "per-key deletion gone, gate present")
        doctored = source + "\n        # if k in model_sd: del cached_state[k]\n"
        self.record("wiring::cache check catches the regression (self-control)",
                    self._cache_deletes_mismatched_keys(doctored),
                    "a doctored source is rejected, so the check is not vacuous")

    def check_resume_and_orchestrator_gated(self) -> None:
        """Every full-model restore site must pass the gate before it loads."""
        resume = inspect.getsource(PiscesLxTrainingOperator.load_checkpoint)
        gate_index = resume.find("require_restorable")
        strict_index = resume.find("load_state_dict(state, strict=True)")
        self.record("wiring::resume gated before strict load",
                    0 <= gate_index < strict_index,
                    f"gate@{gate_index} load@{strict_index}")

        orch = inspect.getsource(PiscesLxTrainOrchestrator)
        loads = orch.count("model.load_state_dict(sd, strict=False)")
        gates = orch.count("PiscesLxStateGeometryGate.require_restorable(model, sd,")
        self.record("wiring::every orchestrator restore is gated",
                    loads > 0 and loads == gates,
                    f"{gates} gate(s) for {loads} load(s)")

    def run(self) -> int:
        """Execute every check; non-zero exit on any failure."""
        self.check_matching_geometry_accepted()
        self.check_other_geometry_refused()
        self.check_old_pattern_was_the_bug()
        self.check_diagnosis_truncates()
        self.check_empty_state_refused()
        self.check_cache_path_gated()
        self.check_resume_and_orchestrator_gated()
        failed = [name for name, ok, _ in self.results if not ok]
        for name, ok, detail in self.results:
            print(f"[{'PASS' if ok else 'FAIL'}] {name:<48} {detail}")
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} checks passed")
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(PiscesLxStateGeometryContract().run())
