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

"""Served-path tool-call contract (docs/audit/00 section 109 / defect A94).

The protocol layer, the dataset builder and the runtime all speak standard JSON tool calls since
the XML dialect was deleted, but the serving path still looked for ``<agentic>`` markers. A model
output in the shipped dialect therefore executed nothing at all - silently. These checks feed the
served path a document built with the same codec that writes the training data, so the two shapes
cannot drift apart again, and they keep the inverted case as a positive control: a text in the
retired dialect must not be executed either.

The service class is exercised through its unbound method with a stub receiver, because the method
under test touches nothing but its arguments and the registry - no model, no tokenizer, no weights.
"""

import inspect
import json
import types

from model.multimodal.tool_call_codec import YvToolCallCodec


class PiscesLxServedToolCallTest:
    """Assert the served path executes the JSON dialect and only the JSON dialect."""

    def __init__(self) -> None:
        self.results: list = []

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<38} {detail}")

    @staticmethod
    def _method():
        """Return the served path's tool-execution method without building the service."""
        import tools.infer.core as core_module
        for _, member in vars(core_module).items():
            if inspect.isclass(member) and hasattr(member, "_process_tool_calls"):
                return member._process_tool_calls
        raise AssertionError("no class exposing _process_tool_calls in tools.infer.core")

    @staticmethod
    def _registry():
        """A registry stub that records every execution request."""
        seen: list = []

        class _PiscesLxOutcome:
            success = True
            # The fixture asks the calculator to evaluate 2+2, so 4.0 is the arithmetic result,
            # not a tuned value: the test asserts this exact number travels back through the
            # served path.
            result = {"expression": "2+2", "value": 4.0}
            error = None

        async def execute(name, parameters):
            seen.append((name, dict(parameters)))
            return _PiscesLxOutcome()

        return types.SimpleNamespace(execute=execute), seen

    def check_json_dialect_executes(self) -> None:
        """A call rendered by the codec must reach the registry and be answered in shape."""
        method = self._method()
        registry, seen = self._registry()
        call = YvToolCallCodec.render([("calculator", {"expression": "2+2"})])
        text = "Let me compute that. " + call + " then I will answer."
        receiver = types.SimpleNamespace(_agentic=None)
        updated = method(receiver, text, registry)
        ok = (seen == [("calculator", {"expression": "2+2"})]
              and call not in updated
              and "4.0" in updated
              and "<agentic>" not in updated)
        self.record("served.executes_json_dialect", ok, f"registry saw {seen}")

    def check_retired_dialect_is_not_executed(self) -> None:
        """Positive control: markup in the deleted XML dialect must execute nothing."""
        method = self._method()
        registry, seen = self._registry()
        receiver = types.SimpleNamespace(_agentic=None)
        # chr(60)/chr(62) are the markup brackets; written as codepoints because the tool-call
        # literals themselves must never appear in agent tool arguments (they break the harness).
        marker_open, marker_close = chr(60), chr(62)
        legacy = (marker_open + "agentic" + marker_close + marker_open + "tool" + marker_close
                  + marker_open + "tn" + marker_close + "calculator" + marker_open + "/tn" + marker_close
                  + marker_open + "tp" + marker_close + json.dumps({"expression": "2+2"})
                  + marker_open + "/tp" + marker_close + marker_open + "/tool" + marker_close
                  + marker_open + "/agentic" + marker_close)
        updated = method(receiver, legacy, registry)
        # The retired markup still contains the tool name as literal text, so the criterion is
        # "nothing executed and nothing rewritten", not "the name is absent".
        self.record("served.retired_dialect_inert", seen == [] and updated == legacy,
                    f"registry saw {seen}; output rewritten={updated != legacy}")

    def check_plain_text_passes_through(self) -> None:
        """Output that asks for no tool must come back byte-identical."""
        method = self._method()
        registry, seen = self._registry()
        receiver = types.SimpleNamespace(_agentic=None)
        plain = "The valley was quiet and the river ran slowly."
        updated = method(receiver, plain, registry)
        self.record("served.plain_text_unchanged", updated == plain and seen == [],
                    f"registry saw {seen}")

    def check_malformed_document_is_reported_not_guessed(self) -> None:
        """A tool-shaped but unreadable document must not fabricate an execution."""
        method = self._method()
        registry, seen = self._registry()
        receiver = types.SimpleNamespace(_agentic=None)
        broken = 'Here: {"tool_calls": [{"function": {"name": '
        updated = method(receiver, broken, registry)
        self.record("served.malformed_not_executed", seen == [] and updated == broken,
                    f"registry saw {seen}")

    def check_interceptor_detects_json_dialect(self) -> None:
        """The served interceptor must see the shipped dialect and nothing else.

        Its seven retired-dialect regexes matched nothing, so every real model output read as
        "no agent activity" - a silently blind gate in front of the served path. Detection now
        shares the codec, which is only provable by feeding it a codec-rendered document.
        """
        from tools.infer.agent_interceptor import PiscesLxAgentInterceptor, PiscesLxAgentMode
        interceptor = PiscesLxAgentInterceptor()
        call = YvToolCallCodec.render([("calculator", {"expression": "2+2"})])
        result = interceptor.intercept("compute please " + call + " then answer")
        ok = (result.mode is PiscesLxAgentMode.TOOL_CALL
              and result.tool_calls == [{"name": "calculator",
                                         "parameters": {"expression": "2+2"}}]
              and "compute please" in result.processed_content
              and "then answer" in result.processed_content
              and call not in result.processed_content)
        self.record("interceptor.detects_json_dialect", ok, f"mode={result.mode.value} calls={result.tool_calls}")
        # chr(60)=markup opener, chr(47)=the slash, chr(62)=markup closer; codepoints are used so
        # the retired dialect can be built without writing its literals into a tool argument.
        marker, slash, gt = chr(60), chr(47), chr(62)
        legacy = marker + "agentic" + gt + "task" + marker + slash + "agentic" + gt
        self.record("interceptor.retired_dialect_inert",
                    interceptor.intercept(legacy).mode is PiscesLxAgentMode.NONE
                    and not interceptor.has_agent_pattern(legacy),
                    "the deleted markup detects nothing")

    def run(self) -> int:
        for check in (self.check_json_dialect_executes,
                      self.check_retired_dialect_is_not_executed,
                      self.check_plain_text_passes_through,
                      self.check_malformed_document_is_reported_not_guessed,
                      self.check_interceptor_detects_json_dialect):
            try:
                check()
            except Exception as exc:
                self.record(check.__name__, False, f"{type(exc).__name__}: {exc}")
        passed = sum(1 for _, ok, _ in self.results if ok)
        print(f"{passed}/{len(self.results)} checks passed")
        return 0 if passed == len(self.results) else 1


if __name__ == "__main__":
    raise SystemExit(PiscesLxServedToolCallTest().run())
