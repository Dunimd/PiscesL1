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
Agentic protocol handler: JSON tool calls carried in the ordinary text stream.

Tool calling is not a modality (docs/prompts/UPGRADE_03_ARCH.md 3.3.1, 禁令 #18). The model
writes a JSON document into the text it generates, this protocol turns it into concrete tool
calls, the executor runs them, and the artifact re-enters the model as text.

Protocol flow:
    model text:  ... {"tool_calls":[{"type":"function","function":{"name":"search",
                 "arguments":{"query":"x"}}}]} ...
        |  YvToolCallCodec.parse
        v
    process_model_output  ->  YvAgenticContext (agent groups)
        |  get_pending_tool_calls
        v
    tool executor  ->  YvAgenticResult
        |  create_result_wrapper (one flat JSON record, no tag envelope)
        v
    observation with modality="text" and metadata kind="tool_result"

The XML marker dialect (`<agentic>/<ag>/<tool>/<tn>/<tp>`) this file used to drive is removed:
no flagship model emits those tags, they had to be taught to a tokenizer to work at all, and a
compatibility layer would keep both spellings alive at once (docs/audit/00 §95).

Key components:
    - YvAgenticProtocol: protocol handler over the codec
    - YvAgenticContext: one parsed model output awaiting execution
    - YvAgenticResult: standardized execution outcome

See Also:
    - model.multimodal.tool_call_codec: the JSON dialect and its normalization rules
"""

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from .tool_call_codec import (
    YvAgenticAgentCall,
    YvToolCallBatch,
    YvToolCallCodec,
)


class YvAgenticExecutionMode(Enum):
    """How the calls inside one model output are to be run."""

    SINGLE = "single"
    SWARM = "swarm"
    SEQUENTIAL = "sequential"


@dataclass
class YvAgenticContext:
    """One parsed model output awaiting execution."""

    execution_id: str
    session_id: str
    mode: str
    start_time: float
    agent_calls: List[YvAgenticAgentCall]
    metadata: Dict[str, Any] = field(default_factory=dict)
    intermediate_results: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class YvAgenticResult:
    """Standardized outcome of one tool execution."""

    success: bool
    output: Any
    execution_time: float
    execution_mode: str
    agent_name: Optional[str] = None
    tool_name: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    intermediate_results: List[Dict[str, Any]] = field(default_factory=list)


class YvAgenticProtocol:
    """Turns model text into executable tool calls, and results back into model text."""

    def __init__(self) -> None:
        self.contexts: Dict[str, YvAgenticContext] = {}

    @staticmethod
    def _short_id() -> str:
        """Compact identifier used for sessions and executions."""
        return str(uuid.uuid4())[:8]

    @staticmethod
    def mode_for(batch: YvToolCallBatch) -> str:
        """Derive the execution mode from the parsed shape; the standard carries no marker."""
        if len(batch.calls) <= 1:
            return YvAgenticExecutionMode.SINGLE.value
        if len([group for group in batch.groups if group.agent_name]) > 1:
            return YvAgenticExecutionMode.SWARM.value
        return YvAgenticExecutionMode.SEQUENTIAL.value

    def process_model_output(
        self,
        model_output: str,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Optional[YvAgenticContext]]:
        """Parse one model output into a registered execution context.

        Returns `(model_output, context)`, where `context` is None when the text asked for no
        tool. A recognizable but malformed call document raises
        `YvToolCallProtocolError`: a call is never dropped because parsing was awkward.
        """
        batch = YvToolCallCodec.parse(model_output)
        if batch.is_empty:
            return model_output, None

        context = YvAgenticContext(
            execution_id=self._short_id(),
            session_id=session_id or self._short_id(),
            mode=self.mode_for(batch),
            start_time=time.time(),
            agent_calls=[group for group in batch.groups if group.calls],
            metadata=dict(metadata or {}),
        )
        self.contexts[context.execution_id] = context
        return model_output, context

    @staticmethod
    def get_pending_tool_calls(
        context: YvAgenticContext,
    ) -> List[Tuple[YvAgenticAgentCall, List[Any]]]:
        """Return ``(agent group, its calls)`` pairs in the order the model emitted them."""
        return [(group, list(group.calls)) for group in context.agent_calls if group.calls]

    @staticmethod
    def create_result_wrapper(result: YvAgenticResult) -> str:
        """Serialize one outcome as the standard result record."""
        return YvToolCallCodec.render_result(
            tool_name=result.tool_name or "",
            output=result.output,
            agent=result.agent_name,
            success=result.success,
            error=result.error or "",
            extra={"execution_mode": result.execution_mode,
                   "execution_time": result.execution_time},
        )

    @staticmethod
    def create_result_wrapper_with_metadata(result: YvAgenticResult) -> str:
        """The same record plus its metadata, still one flat JSON document."""
        return YvToolCallCodec.render_result(
            tool_name=result.tool_name or "",
            output=result.output,
            agent=result.agent_name,
            success=result.success,
            error=result.error or "",
            extra={"execution_mode": result.execution_mode,
                   "execution_time": result.execution_time,
                   "metadata": dict(result.metadata)},
        )

    @staticmethod
    def merge_results(results: List[YvAgenticResult]) -> YvAgenticResult:
        """Combine outcomes into one record without losing any of them."""
        if not results:
            return YvAgenticResult(
                success=False,
                output=None,
                execution_time=0.0,
                execution_mode=YvAgenticExecutionMode.SINGLE.value,
                error="No results to merge",
            )

        all_success = all(item.success for item in results)
        merged_output = {
            "results": [item.output for item in results],
            "count": len(results),
            "all_successful": all_success,
        }
        total_time = sum(item.execution_time for item in results)
        agent_names = sorted({item.agent_name for item in results if item.agent_name})
        tool_names = sorted({item.tool_name for item in results if item.tool_name})
        intermediate: List[Dict[str, Any]] = []
        for item in results:
            intermediate.extend(item.intermediate_results)

        return YvAgenticResult(
            success=all_success,
            output=merged_output,
            execution_time=total_time,
            execution_mode=YvAgenticExecutionMode.SEQUENTIAL.value,
            metadata={
                "agent_names": agent_names,
                "tool_names": tool_names,
                "intermediate_count": len(intermediate),
            },
            intermediate_results=intermediate,
        )

    @staticmethod
    def extract_tools_from_call(group: YvAgenticAgentCall) -> List[Dict[str, Any]]:
        """List one agent group's calls in the standard name/arguments shape."""
        return [{"name": call.tool_name, "arguments": dict(call.parameters)}
                for call in group.calls]

    @staticmethod
    def get_agent_names(batch: YvToolCallBatch) -> List[str]:
        """Named agents present in one parsed output, in order, without duplicates."""
        names: List[str] = []
        for group in batch.groups:
            if group.agent_name and group.agent_name not in names:
                names.append(group.agent_name)
        return names

    @staticmethod
    def validate_call(group: YvAgenticAgentCall) -> Tuple[bool, Optional[str]]:
        """Whether a group is executable: named tools are required, a named agent is not."""
        if not group.calls:
            return False, "Agent group carries no tool call"
        unnamed = [call for call in group.calls if not call.tool_name.strip()]
        if unnamed:
            return False, f"{len(unnamed)} tool call(s) have an empty name"
        return True, None

    def get_context(self, execution_id: str) -> Optional[YvAgenticContext]:
        """The context registered for one execution, if it is still open."""
        return self.contexts.get(execution_id)

    def remove_context(self, execution_id: str) -> Optional[YvAgenticContext]:
        """Close an execution so its bookkeeping does not accumulate."""
        return self.contexts.pop(execution_id, None)

    def get_statistics(self) -> Dict[str, Any]:
        """Open executions this protocol is still tracking."""
        return {
            "active_contexts": len(self.contexts),
            "context_ids": sorted(self.contexts),
        }


protocol = YvAgenticProtocol()
