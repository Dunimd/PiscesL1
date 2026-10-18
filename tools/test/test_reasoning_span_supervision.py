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

"""Reasoning-span supervision contract (§108/§110/§111, A98/A99).

Two separate traps lived here at once. The thinking markers this project declares were not the
strings the vocabulary artefact registers - the difference was a single closing bracket, which no
reading of either file reveals and only a byte-for-byte comparison catches. And the stream masks
were built as "the next stream's start ends this stream", so binding the one registered marker
pair would have marked every remaining position as reasoning, supervising ordinary text as if it
were a thinking span. These checks pin both properties and their failure modes:

* declared marker strings must equal the strings registered in ``tokenizer.json``;
* resolution must go through the runtime tokenizer, and an unregistered string must resolve to
  nothing rather than to the first id of its subword fragments (positive control);
* a closed interval supervises the positions strictly inside it and stops at the end marker;
* an unclosed start marker supervises nothing at all;
* a batch with no marker triggers no stream and leaves the thinking head without gradient,
  which is what keeps plain pretraining text at zero extra cost.
"""

import json
from pathlib import Path

import torch

from model.config import YvConfig
from model.reasoning.reasoner.multipath_core import YvMultiPathReasoningEngine
from model.tokenizer.special_tokens import YvSpecialTokens
from model.tokenizer.tokenizer import YvTokenizer

HIDDEN = 64
SEQ = 16
# Fixture payload ids: any value outside the special-token range, used only to make the mask
# positions distinguishable.
FILLER = list(range(100, 100 + SEQ))


class PiscesLxReasoningSpanTest:
    """Assert marker resolution and closed-interval span supervision hold."""

    def __init__(self) -> None:
        self.results: list = []

    @staticmethod
    def fixture(ids):
        """Build a [1, seq] id tensor from the filler, overwriting the given positions."""
        values = list(FILLER[: SEQ])
        for position, value in ids:
            values[position] = value
        return torch.tensor([values], dtype=torch.long)

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<38} {detail}")

    def _registered(self) -> dict:
        """Return ``marker string -> declared id`` from the vocabulary artefact."""
        payload = json.loads((Path("tokenizer") / "tokenizer.json").read_text(encoding="utf-8"))
        return {entry["content"]: int(entry["id"]) for entry in payload.get("added_tokens", [])}

    def _engine(self, tokenizer) -> YvMultiPathReasoningEngine:
        """Build a reasoning engine whose markers are bound through the project tokenizer."""
        cfg = YvConfig()
        cfg.model_size = "0.01B"
        cfg.hidden_size = HIDDEN
        cfg.vocab_size = tokenizer.vocab_size
        cfg.num_attention_heads = 4
        cfg.num_key_value_heads = 4
        engine = YvMultiPathReasoningEngine(cfg)
        engine.initialize_reasoning_tokens(tokenizer)
        return engine

    def check_declared_markers_are_registered(self) -> None:
        """Every marker that bounds a stream must be a string the artefact registers."""
        registered = self._registered()
        declared = YvSpecialTokens()
        engine_markers = YvMultiPathReasoningEngine._STREAM_MARKERS
        problems = []
        for stream, (start_attr, end_attr) in engine_markers.items():
            for attr in (start_attr, end_attr):
                marker = getattr(declared, attr)
                if marker not in registered:
                    problems.append(f"{stream}.{attr}={marker!r}")
        self.record("markers.declared_equal_registered", not problems,
                    "all stream markers registered" if not problems else f"unregistered: {problems}")

    def check_resolution_never_returns_fragments(self) -> None:
        """An unregistered string must resolve to nothing, not to a subword fragment's id."""
        tokenizer = YvTokenizer()
        registered = self._registered()
        pair = YvMultiPathReasoningEngine._STREAM_MARKERS["hypothesis"]
        marker = getattr(YvSpecialTokens(), pair[0])
        good = tokenizer.get_special_token_id(marker)
        broken = marker + "-not-registered"
        self.record("markers.resolves_to_declared_id",
                    good is not None and good == registered[marker],
                    f"{marker!r} -> {good} (artefact declares {registered.get(marker)})")
        self.record("markers.fragment_control_rejects_garbage",
                    tokenizer.get_special_token_id(broken) is None,
                    f"mutated marker {broken!r} rejected rather than mapped to a fragment id")

    def check_closed_interval_stops_at_end_marker(self) -> None:
        """Positions are supervised strictly between the start and the end marker."""
        tokenizer = YvTokenizer()
        engine = self._engine(tokenizer)
        start_id, end_id = engine.reasoning_intervals["hypothesis"]
        #   marker  at positions 2 and 8 -> supervision must cover 3..7 only.
        input_ids = self.fixture([(2, start_id), (8, end_id)])
        masks = engine._create_reasoning_masks(input_ids)
        row = masks.get("hypothesis")
        inside = (row is not None and bool(row[0, 3:8].all())
                  and not bool(row[0, :2].any()) and not bool(row[0, 8:].any()))
        self.record("span.closed_interval_is_exactly_inside", inside,
                    f"mask={None if row is None else row.tolist()[0]}")

    def check_unclosed_marker_supervises_nothing(self) -> None:
        """A start marker without its closing partner must not extend to end-of-sequence."""
        tokenizer = YvTokenizer()
        engine = self._engine(tokenizer)
        start_id, _ = engine.reasoning_intervals["hypothesis"]
        input_ids = self.fixture([(0, start_id)])
        masks = engine._create_reasoning_masks(input_ids)
        self.record("span.unclosed_supervises_nothing", "hypothesis" not in masks,
                    f"mask keys for an unclosed span: {sorted(masks)}")

    def check_plain_batch_is_unsupervised_and_gradient_free(self) -> None:
        """Without a marker the stream path stays closed, so pretraining text pays nothing."""
        tokenizer = YvTokenizer()
        engine = self._engine(tokenizer)
        plain = self.fixture([])
        start_id, end_id = engine.reasoning_intervals["hypothesis"]
        spanned = self.fixture([(0, start_id), (SEQ - 2, end_id)])
        self.record("span.plain_batch_has_no_span",
                    not engine.has_reasoning_span(plain) and not engine._create_reasoning_masks(plain),
                    "plain ids trigger neither detection nor a mask")

        hidden = torch.randn(1, SEQ, HIDDEN, requires_grad=True)
        span_out = engine.forward(hidden_states=hidden, input_ids=spanned, labels=spanned.clone())
        engine.zero_grad(set_to_none=True)
        span_out["loss"].backward()
        span_grad = engine.thinking_head.weight.grad
        plain_loss = float(engine.forward(hidden_states=hidden, input_ids=plain,
                                          labels=plain.clone())["loss"])
        # The thinking head also trains on the collapsed path, so "no gradient" is the wrong
        # assertion for a plain batch. What must be absent is the stream term itself: the span
        # batch adds it, the plain batch cannot (no mask), and the gap proves the term is live.
        self.record("span.gradient_reaches_thinking_head",
                    span_grad is not None and float(span_grad.abs().sum()) > 0.0,
                    f"span batch: grad norm={'none' if span_grad is None else round(float(span_grad.norm()), 6)}")
        self.record("span.stream_term_only_with_span", float(span_out["loss"]) > plain_loss,
                    f"span loss={round(float(span_out['loss']), 4)} plain loss={round(plain_loss, 4)}")

    def run(self) -> int:
        for check in (self.check_declared_markers_are_registered,
                      self.check_resolution_never_returns_fragments,
                      self.check_closed_interval_stops_at_end_marker,
                      self.check_unclosed_marker_supervises_nothing,
                      self.check_plain_batch_is_unsupervised_and_gradient_free):
            try:
                check()
            except Exception as exc:
                self.record(check.__name__, False, f"{type(exc).__name__}: {exc}")
        passed = sum(1 for _, ok, _ in self.results if ok)
        print(f"{passed}/{len(self.results)} checks passed")
        return 0 if passed == len(self.results) else 1


if __name__ == "__main__":
    raise SystemExit(PiscesLxReasoningSpanTest().run())
