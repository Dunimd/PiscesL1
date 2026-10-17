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

"""Geometry contract for any state dict that is about to overwrite model weights.

A checkpoint whose tensors do not match the model was produced by a *different
model*, so restoring it is not a partial success: the mismatched parameters stay at
their random initialisation and training then continues on a half-fresh network while
every metric still looks healthy. This module turns that silent corruption into one
loud, attributable refusal, and is the single place that decides what "restorable"
means for the training engine (docs/audit/00 §78).
"""

from typing import Any, Dict, List, Tuple

import torch

#: How many offending keys a diagnosis is allowed to name. The rest are counted, not
#: printed: a full-tier mismatch produces hundreds of lines and buries the cause.
_MAX_REPORTED_KEYS = 8


class PiscesLxStateGeometryError(RuntimeError):
    """Raised when a state dict cannot fully describe the model it targets."""


class PiscesLxStateGeometryGate:
    """Validates a ``state_dict`` against a model before it is allowed to load.

    The gate compares shapes only. Key presence is deliberately *not* softened here:
    the caller still loads strictly, so a missing or spurious key keeps failing through
    the engine's own path. What this class adds is a diagnosis that names the geometry
    that actually changed, instead of a wall of per-tensor shape errors.
    """

    @staticmethod
    def shape_mismatches(model: torch.nn.Module,
                         state: Dict[str, Any]) -> List[Tuple[str, Tuple[int, ...], Tuple[int, ...]]]:
        """Return ``(key, checkpoint shape, model shape)`` for every tensor that differs."""
        model_state = model.state_dict()
        found: List[Tuple[str, Tuple[int, ...], Tuple[int, ...]]] = []
        for key, value in state.items():
            expected = model_state.get(key)
            if expected is None or not isinstance(value, torch.Tensor):
                continue
            if tuple(value.shape) != tuple(expected.shape):
                found.append((key, tuple(value.shape), tuple(expected.shape)))
        return found

    @classmethod
    def require_restorable(cls, model: torch.nn.Module,
                           state: Dict[str, Any], source: str) -> None:
        """Refuse a state dict that does not cover the model, naming the geometry change.

        Args:
            model: The module the state dict is about to be loaded into.
            state: The candidate ``state_dict``.
            source: Human-readable origin used in the message (file, cache, adapter...).

        Raises:
            PiscesLxStateGeometryError: If any shared tensor changes shape, or if the
                state dict carries no tensor that the model has.
        """
        mismatches = cls.shape_mismatches(model, state)
        if not mismatches and state:
            return
        if mismatches:
            sample = "; ".join(
                f"{key}: checkpoint {list(ckpt)} vs model {list(now)}"
                for key, ckpt, now in mismatches[:_MAX_REPORTED_KEYS]
            )
            more = ""
            if len(mismatches) > _MAX_REPORTED_KEYS:
                more = f" (+{len(mismatches) - _MAX_REPORTED_KEYS} more)"
            raise PiscesLxStateGeometryError(
                f"Refusing to restore {source}: it was written by a different model "
                f"geometry, so {len(mismatches)} of {len(state)} tensor(s) do not fit. "
                f"Restoring it would leave those parameters at random initialisation. "
                f"First mismatches: {sample}{more}"
            )
        raise PiscesLxStateGeometryError(
            f"Refusing to restore {source}: it carries no tensors for this model."
        )
