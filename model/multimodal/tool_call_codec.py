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

"""Tool-call protocol: structured JSON inside the ordinary text stream.

Tool calling is not a modality. The model writes JSON into the text it generates, an
external tool parses and executes it, and the artifact comes back as text again
(docs/prompts/UPGRADE_03_ARCH.md 3.3.1). This module is the single codec for that JSON,
aligned with the shapes the flagship models actually emit:

1. OpenAI / DeepSeek / Qwen style
   ``{"tool_calls": [{"id": "0", "type": "function",
   "function": {"name": "search", "arguments": {"query": "x"}}}]}``
   (`arguments` may also be a JSON *string*, which is what these APIs serialize.)
2. Anthropic style
   ``{"type": "tool_use", "name": "search", "input": {"query": "x"}}``
   (or a list of such content blocks.)
3. Bare call
   ``{"name": "search", "arguments": {"query": "x"}}``

An optional ``"agent"`` key attributes a call to a named sub-agent; multi-agent output is
simply several calls, so no group tag exists.

Deliberate choices:

* **No XML tags.** The previous protocol used ``<agentic>/<ag>/<tool>/<tn>/<tp>`` markers,
  which no flagship standard emits and which had to be taught to a tokenizer to work at all.
  `model/multimodal/xml_utils.py` was deleted rather than kept as a compatibility layer
  (docs/audit/00 §95).
* **Never drop a call silently.** If the text carries a recognizable tool-call shape that
  is malformed, this codec raises :class:`YvToolCallProtocolError`. Unrelated JSON in prose
  is ignored, because a model discussing ``{"a": 1}`` is not asking for a tool.
* JSON is decoded with a bounded scan: only top-level balanced objects/arrays are candidates.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: Keys that make a JSON document recognizable as a tool-call request.
TOOL_CALLS_KEY = "tool_calls"
FUNCTION_KEY = "function"
ARGS_KEY = "arguments"
NAME_KEY = "name"
INPUT_KEY = "input"
TYPE_KEY = "type"
TOOL_USE_TYPE = "tool_use"
AGENT_KEY = "agent"
ID_KEY = "id"

#: Fenced code block opener, ```` ```json ```` or plain ```` ``` ````.
_CODE_FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)

#: The nesting depth at which a scan gives up, guarding against pathological input.
_MAX_JSON_DEPTH = 64


class YvToolCallProtocolError(ValueError):
    """A recognizable tool-call document is malformed; the call must not be dropped."""


@dataclass
class YvAgenticToolCall:
    """One requested tool invocation with its resolved arguments."""

    tool_name: str
    parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Standard-shaped record for logging and re-serialization."""
        return {TYPE_KEY: "function", FUNCTION_KEY: {NAME_KEY: self.tool_name,
                                                     ARGS_KEY: self.parameters}}


@dataclass
class YvAgenticAgentCall:
    """The agent a call was attributed to, if the output named one."""

    agent_name: str = ""
    calls: List[YvAgenticToolCall] = field(default_factory=list)

    @property
    def named(self) -> bool:
        """Whether the model actually addressed a sub-agent."""
        return bool(self.agent_name)


@dataclass
class YvToolCallBatch:
    """Everything the codec found in one model output, in emission order."""

    groups: List[YvAgenticAgentCall] = field(default_factory=list)
    raw_text: str = ""
    consumed_spans: List[Tuple[int, int]] = field(default_factory=list)

    @property
    def calls(self) -> List[YvAgenticToolCall]:
        """Flat list of all calls across agents."""
        return [call for group in self.groups for call in group.calls]

    @property
    def is_empty(self) -> bool:
        """Whether the output asked for no tool at all."""
        return not self.calls


class YvToolCallCodec:
    """Parses and renders the JSON tool-call documents the model emits in text."""

    @classmethod
    def parse(cls, text: str) -> YvToolCallBatch:
        """Extract every tool call from generated text.

        Args:
            text: Raw decoded model output.

        Returns:
            YvToolCallBatch: Groups in the order the calls appeared; empty when the text
            contains no tool-call document.

        Raises:
            YvToolCallProtocolError: A document carrying a tool-call key is present but
                cannot be interpreted (unknown shape, missing name, bad arguments).
        """
        batch = YvToolCallBatch(raw_text=text)
        for candidate, start, end in cls._iter_candidates(text):
            parsed = cls._loads(candidate)
            if parsed is None:
                continue
            if not cls._looks_like_tool_document(parsed):
                continue
            groups = cls._normalize(parsed, candidate)
            for group in groups:
                cls._merge_group(batch, group)
            batch.consumed_spans.append((start, end))
        return batch

    @classmethod
    def render(cls, calls: List[Tuple[str, Dict[str, Any]]],
               agent: Optional[str] = None, indent: int = 2) -> str:
        """Serialize tool calls into the standard document form.

        Used by the data pipeline to write tool-call traces and by tests to build inputs;
        the emitted shape is the OpenAI/DeepSeek/Qwen one because that is the widest standard.
        """
        entries: List[Dict[str, Any]] = []
        for index, (name, arguments) in enumerate(calls):
            entry: Dict[str, Any] = {
                ID_KEY: str(index),
                TYPE_KEY: "function",
                FUNCTION_KEY: {NAME_KEY: name, ARGS_KEY: dict(arguments)},
            }
            if agent:
                entry[AGENT_KEY] = agent
            entries.append(entry)
        return json.dumps({TOOL_CALLS_KEY: entries}, indent=indent, ensure_ascii=False)

    @classmethod
    def render_result(cls, tool_name: str, output: Any, agent: Optional[str] = None,
                      success: bool = True, error: str = "",
                      extra: Optional[Dict[str, Any]] = None) -> str:
        """Serialize a tool artifact the way it must re-enter the text stream.

        The single serializer for results: callers that need success/error/metadata attach
        them here instead of each inventing an envelope, which is how the old protocol ended
        up with an XML wrapper around a JSON payload.
        """
        record: Dict[str, Any] = {TYPE_KEY: "tool_result", NAME_KEY: tool_name,
                                  "success": success, "output": output,
                                  "error": error}
        if agent:
            record[AGENT_KEY] = agent
        if extra:
            record.update(extra)
        return json.dumps(record, ensure_ascii=False, default=str)

    @staticmethod
    def _loads(candidate: str) -> Optional[Any]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    @classmethod
    def _iter_candidates(cls, text: str):
        """Yield ``(snippet, start, end)`` for fenced JSON and balanced top-level documents."""
        for match in _CODE_FENCE.finditer(text):
            yield match.group(1).strip(), match.start(), match.end()
        depth = 0
        start = -1
        in_string = False
        escape = False
        for index, char in enumerate(text):
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                if depth:
                    in_string = True
                continue
            if char in "{[":
                if depth == 0:
                    start = index
                depth += 1
                if depth > _MAX_JSON_DEPTH:
                    raise YvToolCallProtocolError(
                        f"tool-call JSON nested deeper than {_MAX_JSON_DEPTH} levels at "
                        f"offset {start}")
            elif char in "}]":
                if depth:
                    depth -= 1
                    if depth == 0 and start >= 0:
                        yield text[start:index + 1], start, index + 1
                        start = -1

    @staticmethod
    def _looks_like_tool_document(payload: Any) -> bool:
        if isinstance(payload, dict):
            if TOOL_CALLS_KEY in payload or NAME_KEY in payload or TYPE_KEY in payload:
                return True
        if isinstance(payload, list):
            return any(isinstance(item, dict) and
                       (NAME_KEY in item or TYPE_KEY in item) for item in payload)
        return False

    @classmethod
    def _normalize(cls, payload: Any, candidate: str) -> List[YvAgenticAgentCall]:
        """Map every accepted industry shape onto agent groups of concrete calls."""
        if isinstance(payload, dict) and TOOL_CALLS_KEY in payload:
            entries = payload[TOOL_CALLS_KEY]
            if not isinstance(entries, list):
                raise YvToolCallProtocolError(
                    f"`{TOOL_CALLS_KEY}` must be a list, got "
                    f"{type(entries).__name__}: {candidate[:120]}")
            return [cls._agent_group(entry, payload.get(AGENT_KEY)) for entry in entries]
        if isinstance(payload, list):
            return [cls._agent_group(item) for item in payload]
        if isinstance(payload, dict):
            return [cls._agent_group(payload)]
        raise YvToolCallProtocolError(
            f"recognized a tool-call document but cannot interpret it: {candidate[:120]}")

    @classmethod
    def _agent_group(cls, entry: Any, inherited_agent: Any = None) -> YvAgenticAgentCall:
        if not isinstance(entry, dict):
            raise YvToolCallProtocolError(
                f"tool-call entry must be an object, got {type(entry).__name__}")
        agent_name = entry.get(AGENT_KEY) or inherited_agent or ""
        call = cls._call_from_entry(entry)
        return YvAgenticAgentCall(agent_name=str(agent_name), calls=[call])

    @classmethod
    def _call_from_entry(cls, entry: Dict[str, Any]) -> YvAgenticToolCall:
        kind = entry.get(TYPE_KEY)
        if kind == TOOL_USE_TYPE:
            # Anthropic-style content block: name at top level, arguments under `input`.
            name = entry.get(NAME_KEY)
            arguments = entry.get(INPUT_KEY, {})
        elif FUNCTION_KEY in entry and isinstance(entry[FUNCTION_KEY], dict):
            function = entry[FUNCTION_KEY]
            name = function.get(NAME_KEY)
            arguments = function.get(ARGS_KEY, {})
        elif NAME_KEY in entry:
            name = entry[NAME_KEY]
            arguments = entry.get(ARGS_KEY, entry.get(INPUT_KEY, {}))
        else:
            raise YvToolCallProtocolError(
                f"tool-call object has no recognizable name/arguments fields: "
                f"{json.dumps(entry, ensure_ascii=False, default=str)[:160]}")

        if not isinstance(name, str) or not name.strip():
            raise YvToolCallProtocolError(
                f"tool call has a missing or non-string name: {str(name)[:80]}")
        return YvAgenticToolCall(tool_name=name.strip(),
                                 parameters=cls._arguments(arguments, name))

    @staticmethod
    def _arguments(arguments: Any, tool_name: str) -> Dict[str, Any]:
        if isinstance(arguments, str):
            if not arguments.strip():
                return {}
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as error:
                raise YvToolCallProtocolError(
                    f"arguments for tool '{tool_name}' are not valid JSON: {error}") from error
        if arguments is None:
            return {}
        if not isinstance(arguments, dict):
            raise YvToolCallProtocolError(
                f"arguments for tool '{tool_name}' must be an object, got "
                f"{type(arguments).__name__}")
        return dict(arguments)

    @staticmethod
    def _merge_group(batch: YvToolCallBatch, group: YvAgenticAgentCall) -> None:
        """Keep calls of the same agent together, preserving first-seen order."""
        for existing in batch.groups:
            if existing.agent_name == group.agent_name:
                existing.calls.extend(group.calls)
                return
        batch.groups.append(group)
