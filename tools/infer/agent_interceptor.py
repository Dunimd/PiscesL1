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
Agent Interceptor - Intercept and Route Agent-Specific Requests

This module provides functionality to detect and process agent-specific
XML patterns in user requests, routing them to appropriate handlers.

Supported XML Patterns:
    - <agentic>...</agentic>: Agent execution block
    - <ag>agent_name</ag>: Single agent invocation
    - <swarm mode="...">...</swarm>: Swarm execution
    - <orchestrate strategy="...">...</orchestrate>: Orchestration
    - <tool name="...">...</tool>: Tool invocation
    - <args>...</args>: Tool arguments

Agent Modes:
    - NONE: No agent pattern detected, standard chat
    - SINGLE: Single agent invocation
    - SWARM: Multi-agent swarm execution
    - ORCHESTRATED: Dynamic orchestration
    - TOOL_CALL: Direct tool invocation

Architecture:
    PiscesLxAgentInterceptor
    ├── Pattern Detection
    │   ├── Agentic block detection
    │   ├── Agent invocation detection
    │   ├── Swarm mode detection
    │   ├── Orchestration detection
    │   └── Tool call detection
    ├── Content Extraction
    │   ├── Agent name extraction
    │   ├── Task description extraction
    │   └── Argument parsing
    └── Result Generation
        ├── Mode determination
        ├── Request structuring
        └── Metadata collection

Usage:
    >>> interceptor = PiscesLxAgentInterceptor()
    >>> result = interceptor.intercept("请帮我分析数据 <ag>data_analyzer</ag>")
    >>> print(result.mode)  # PiscesLxAgentMode.SINGLE
    >>> print(result.agent_requests)  # [{"type": "single", "agent_name": "data_analyzer"}]
"""

import re
import json
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from utils.dc import PiscesLxLogger
from utils.paths import PiscesLxPaths
from model.multimodal.tool_call_codec import YvToolCallBatch, YvToolCallCodec, YvToolCallProtocolError


class PiscesLxAgentMode(Enum):
    """Agent execution mode enumeration."""
    NONE = "none"
    SINGLE = "single"
    SWARM = "swarm"
    ORCHESTRATED = "orchestrated"
    TOOL_CALL = "tool_call"
    HYBRID = "hybrid"


@dataclass
class PiscesLxInterceptResult:
    """
    Result of agent interception.
    
    Attributes:
        mode: Detected agent mode
        original_content: Original content before processing
        processed_content: Content after removing agent patterns
        agent_requests: List of agent request specifications
        tool_calls: List of tool call specifications
        metadata: Additional metadata about the interception
    """
    mode: PiscesLxAgentMode
    original_content: str
    processed_content: str
    agent_requests: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class PiscesLxAgentInterceptor:
    """
    Intercept and Route Agent-Specific Requests.
    
    This class detects XML-based agent patterns in user messages and
    routes them to appropriate handlers (single agent, swarm, orchestrator, tool).
    
    Features:
        - Multiple pattern detection
        - Nested pattern support
        - Argument parsing
        - Mode classification
        - Metadata extraction
    
    Pattern Examples:
        Single Agent:
            <ag>data_analyzer</ag>请分析这份数据
        
        Swarm Mode:
            <agentic>
                <swarm mode="hierarchical">分析市场数据并生成报告</swarm>
            </agentic>
        
        Orchestration:
            <agentic>
                <orchestrate strategy="dynamic">研究AI发展趋势</orchestrate>
            </agentic>
        
        Tool Call:
            <tool name="web_search">
                <args>{"query": "latest AI news"}</args>
            </tool>
    
    Example:
        >>> interceptor = PiscesLxAgentInterceptor()
        >>> result = interceptor.intercept(rendered_tool_call_document())
        >>> result.mode        # PiscesLxAgentMode.TOOL_CALL
        >>> result.tool_calls  # [{"name": ..., "parameters": {...}}]

"""
    
    def __init__(self):
        """Initialize the agent interceptor."""
        self._LOG = PiscesLxLogger("PiscesLx.Tools.Infer", file_path=PiscesLxPaths.get_log_file("PiscesLx.Tools.Infer"), enable_file=True)
        self._intercept_count = 0
        self._mode_counts: Dict[str, int] = {mode.value: 0 for mode in PiscesLxAgentMode}
    
    def intercept(self, content: str) -> PiscesLxInterceptResult:
        """Detect the tool calls a JSON-dialect document asks for.

        Detection used to run on seven ``<agentic>`` family regexes. That dialect was deleted, so
        every one of them matched nothing and this reported "no agent activity" for every real
        model output - silently blind rather than wrong. Parsing now goes through the same codec
        the protocol layer, the served execution path and the dataset builder use, so the shape
        detected here and the shape the model emits cannot drift apart (docs/audit/00 section 109).

        Args:
            content: The text to analyze.

        Returns:
            PiscesLxInterceptResult: mode NONE when nothing is requested; TOOL_CALL for unaddressed
            calls; SINGLE when one named agent is addressed; HYBRID across several agents. A
            malformed document reports NONE with the protocol error in metadata instead of a guess.
        """
        self._intercept_count += 1
        metadata: Dict[str, Any] = {"patterns_found": []}
        try:
            batch = YvToolCallCodec.parse(content)
        except YvToolCallProtocolError as error:
            self._LOG.warning(f"Interceptor: malformed tool document reported, not guessed: {error}")
            metadata["protocol_error"] = str(error)
            return PiscesLxInterceptResult(mode=PiscesLxAgentMode.NONE, original_content=content,
                                           processed_content=content, metadata=metadata)
        if batch.is_empty:
            return PiscesLxInterceptResult(mode=PiscesLxAgentMode.NONE, original_content=content,
                                           processed_content=content, metadata=metadata)

        metadata["patterns_found"].append("json_tool_calls")
        agent_requests = [{"type": "agent", "agent": group.agent_name,
                           "calls": [call.to_dict() for call in group.calls]}
                          for group in batch.groups]
        tool_calls = [{"name": call.tool_name, "parameters": call.parameters}
                      for call in batch.calls]
        named = [group.agent_name for group in batch.groups if group.named]
        if len(named) > 1:
            mode = PiscesLxAgentMode.HYBRID
        elif named:
            mode = PiscesLxAgentMode.SINGLE
        else:
            mode = PiscesLxAgentMode.TOOL_CALL
        self._update_mode_counts(mode)
        return PiscesLxInterceptResult(mode=mode, original_content=content,
                                       processed_content=self._strip_documents(content, batch),
                                       agent_requests=agent_requests, tool_calls=tool_calls,
                                       metadata=metadata)

    @staticmethod
    def _strip_documents(content: str, batch: YvToolCallBatch) -> str:
        """Remove the tool-call documents, leaving the surrounding prose untouched."""
        if not batch.consumed_spans:
            return content.strip()
        pieces: List[str] = []
        cursor = 0
        for start, end in sorted(batch.consumed_spans):
            pieces.append(content[cursor:start])
            cursor = end
        pieces.append(content[cursor:])
        return "".join(pieces).strip()

    def _update_mode_counts(self, mode: PiscesLxAgentMode):
        """Count one detection under the given mode."""
        self._mode_counts[mode.value] = self._mode_counts.get(mode.value, 0) + 1

    def has_agent_pattern(self, content: str) -> bool:
        """Whether the text contains at least one interpretable tool-call document."""
        return self.intercept(content).mode is not PiscesLxAgentMode.NONE

    def extract_agent_names(self, content: str) -> List[str]:
        """Return the agents the documents address, in emission order."""
        return [str(record.get("agent", "")) for record in self.intercept(content).agent_requests
                if record.get("agent")]

    def extract_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        """Return name/parameters records for every call the text asks for."""
        return self.intercept(content).tool_calls

    def strip_agent_patterns(self, content: str) -> str:
        """Return the text with its tool-call documents removed."""
        batch = YvToolCallCodec.parse(content)
        return self._strip_documents(content, batch)

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get interception statistics.
        
        Returns:
            Statistics dictionary
        """
        return {
            "total_intercepts": self._intercept_count,
            "mode_counts": dict(self._mode_counts),
        }
    
    def reset_statistics(self):
        """Reset interception statistics."""
        self._intercept_count = 0
        self._mode_counts = {mode.value: 0 for mode in PiscesLxAgentMode}
    
    def __repr__(self) -> str:
        return f"PiscesLxAgentInterceptor(intercepts={self._intercept_count})"
