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

"""Tool-call trajectory data built from arithmetic word problems.

docs/prompts/UPGRADE_10_DELIVER.md 阶段 4 requires data covering instruction, reasoning
*and tool-calling* traces, and the framework's own tool protocol is now "JSON inside the text
stream" (docs/prompts/UPGRADE_03_ARCH.md 3.3.1). This builder therefore emits supervision in
exactly the shape the runtime parses: the assistant asks for a calculator call with
`{"tool_calls": [...]}`, the tool answer comes back as a `tool_result` JSON record, and the
final line states the result.

It reuses :class:`model.multimodal.tool_call_codec.YvToolCallCodec` as the single serializer,
so generated data cannot drift from what the model is later asked to produce - a second
hand-rolled format here would be exactly the two-dialect mistake this session removed from
the runtime.

Arithmetic is evaluated by this module's own four-operator step parser: no `eval`, no
`exec`, nothing the source text can steer into code execution.
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils.dc import PiscesLxLogger
from utils.paths import PiscesLxPaths

from model.multimodal.tool_call_codec import YvToolCallCodec

_LOG = PiscesLxLogger("PiscesLx.Tools.Data",
                     file_path=PiscesLxPaths.get_log_file("PiscesLx.Tools.Data"),
                     enable_file=True)

#: One `a op b = c` line of a worked solution.
_STEP_PATTERN = re.compile(r"(-?[\d,]+(?:\.\d+)?)\s*([*+/^.-])\s*(-?[\d,]+(?:\.\d+)?)\s*=\s*(-?[\d,]+(?:\.\d+)?)")
#: The dataset's terminal answer marker.
_FINAL_MARKER = "####"
#: Operators this builder evaluates itself; anything else is skipped rather than guessed.
_POWER_OP = "^"
_DIVISION_OPS = frozenset({"/"})
#: Tool the synthesized traces call, matching a calculator handler in the serving stack.
CALCULATOR_TOOL = "calculator"
#: Expression argument name of that tool, kept identical to the call rendered below.
EXPR_ARGUMENT = "expression"


class PiscesLxToolTraceDocument:
    """One synthesized training document plus its bookkeeping."""

    def __init__(self, question: str, reasoning: List[str], trace: List[str]) -> None:
        self.question = question
        self.reasoning = reasoning
        self.trace = trace

    @property
    def output(self) -> str:
        """The supervised continuation: reasoning, tool calls, tool answers, conclusion."""
        return "\n".join(self.reasoning + self.trace)

    def to_sample(self) -> Dict[str, Any]:
        """Schema the training reader consumes as an instruction/output pair."""
        return {"instruction": self.question, "output": self.output}


class PiscesLxToolTraceDatasetBuilder:
    """Turns arithmetic word-problem jsonl into tool-call supervision in the runtime's dialect."""

    def __init__(self, source: Path, destination: Path) -> None:
        self.source = Path(source)
        self.destination = Path(destination)

    @staticmethod
    def _number(token: str) -> Optional[float]:
        cleaned = token.replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _evaluate(left: float, operator: str, right: float) -> Optional[float]:
        """Compute one binary step; return None where the step is not safely computable."""
        if operator == "+":
            return left + right
        if operator == "-":
            return left - right
        if operator == "*":
            return left * right
        if operator in _DIVISION_OPS:
            return None if right == 0 else left / right
        if operator == _POWER_OP:
            return None
        return None

    @staticmethod
    def _format(value: float) -> str:
        rendered = f"{value:.6f}".rstrip("0").rstrip(".")
        return rendered or "0"

    @classmethod
    def _steps(cls, answer: str) -> List[Tuple[str, str]]:
        """Return ``(expression, result)`` for every arithmetic line the parser can verify."""
        steps: List[Tuple[str, str]] = []
        for raw_left, operator, raw_right, raw_result in _STEP_PATTERN.findall(answer):
            left = cls._number(raw_left)
            right = cls._number(raw_right)
            stated = cls._number(raw_result)
            if left is None or right is None or stated is None:
                continue
            computed = cls._evaluate(left, operator, right)
            if computed is None:
                continue
            # A step whose stated result disagrees with the arithmetic is left out: the
            # trace must teach a call the tool would really answer, not a plausible-looking one.
            if abs(computed - stated) > _STEP_TOLERANCE:
                continue
            expression = f"{cls._format(left)} {operator} {cls._format(right)}"
            steps.append((expression, cls._format(stated)))
        return steps

    @classmethod
    def build_document(cls, question: str, answer: str) -> Optional[PiscesLxToolTraceDocument]:
        """Synthesize one document, or None when the row carries no verifiable step."""
        body = [line.strip() for line in answer.splitlines() if line.strip()]
        final_index = next((index for index, line in enumerate(body)
                            if line.startswith(_FINAL_MARKER)), len(body))
        reasoning = [line for line in body[:final_index] if not _STEP_PATTERN.search(line)]
        steps = cls._steps(answer)
        if not steps:
            return None

        trace: List[str] = []
        for expression, result in steps:
            call = YvToolCallCodec.render([(CALCULATOR_TOOL, {EXPR_ARGUMENT: expression})])
            answer_record = YvToolCallCodec.render_result(
                CALCULATOR_TOOL, {"expression": expression, "value": float(result)})
            trace.append(f"调用工具：{call}")
            trace.append(f"工具返回：{answer_record}")
        if final_index < len(body):
            trace.append(f"结论：{body[final_index].replace(_FINAL_MARKER, '').strip()}")
        return PiscesLxToolTraceDocument(question.strip(), reasoning, trace)

    def run(self, limit: Optional[int] = None) -> Dict[str, int]:
        """Write the dataset and return build counters; the output is one jsonl file."""
        if not self.source.is_file():
            raise FileNotFoundError(f"source jsonl not found: {self.source}")
        self.destination.parent.mkdir(parents=True, exist_ok=True)

        written = 0
        skipped = 0
        with open(self.source, "r", encoding="utf-8") as reader, \
                open(self.destination, "w", encoding="utf-8", newline="\n") as writer:
            for line_number, line in enumerate(reader, start=1):
                if limit is not None and written >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                question = row.get("question") or row.get("instruction") or ""
                answer = row.get("answer") or row.get("output") or ""
                document = self.build_document(question, answer) if question and answer else None
                if document is None:
                    skipped += 1
                    continue
                writer.write(json.dumps(document.to_sample(), ensure_ascii=False) + "\n")
                written += 1
                if line_number % _PROGRESS_EVERY == 0:
                    _LOG.info(f"tool-trace build progress: read={line_number} written={written}")
        stats = {"written": written, "skipped": skipped}
        _LOG.info(f"tool-trace dataset built at {self.destination}: {stats}")
        return stats


#: Rows between progress log lines while building.
_PROGRESS_EVERY = 2000
#: Absolute tolerance between a step's stated and recomputed result.
_STEP_TOLERANCE = 1e-4


class PiscesLxToolTraceDatasetCli:
    """Argument surface for building the dataset from the command line."""

    @staticmethod
    def parse(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--source", required=True, help="input jsonl with question/answer rows")
        parser.add_argument("--destination", required=True, help="output jsonl path")
        parser.add_argument("--limit", type=int, default=None,
                            help="maximum documents to write")
        return parser.parse_args(list(argv) if argv is not None else None)

    def main(self, argv: Optional[Iterable[str]] = None) -> int:
        """Build the dataset and report the counters."""
        args = self.parse(argv)
        stats = PiscesLxToolTraceDatasetBuilder(Path(args.source), Path(args.destination)).run(args.limit)
        print(json.dumps(stats))
        return 0


if __name__ == "__main__":
    raise SystemExit(PiscesLxToolTraceDatasetCli().main())
