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

"""Model Context Protocol integration helpers for Yv multimodal agents.

This module provides comprehensive MCP (Model Context Protocol) integration
components for the Yv model, including tool registries, protocol adapters,
and reasoning hooks for agent coordination.

Module Components:
    1. YvMCPToolRegistry:
       - Tool registration and execution management
       - Native/external execution statistics
       - Dual registration with unified executor
       - Multipath reasoning integration

    2. YvCoreMCPProtocol:
       - Protocol adapter for MCP message handling
       - Message construction and routing
       - Capability negotiation

Key Features:
    - MCP (Model Context Protocol) contract compliance
    - Dual registration with YvMCPUnifiedToolExecutor
    - Native and external tool execution tracking
    - Multipath reasoning engine integration
    - Backward compatibility with utils.mcp implementations
    - Tree search reasoning support

Performance Characteristics:
    - Tool registration: O(1) dictionary insertion
    - Execution routing: O(log N) with reasoning engine
    - Message handling: O(M) where M = message size

Usage Example:
    >>> from model.multimodal.mcp import YvMCPToolRegistry
    >>> 
    >>> # Initialize registry
    >>> registry = YvMCPToolRegistry(
    ...     agentic_id="agent-123",
    ...     message_handler=handle_message
    ... )
    >>> 
    >>> # Register tool
    >>> registry.register_tool("search", search_handler, metadata)
    >>> 
    >>> # Execute tool
    >>> result = await registry.execute_tool("search", query="hello")

Note:
    Extends YvMCPToolRegistry with Yv-specific features.
    Integrates with YvMultiPathReasoningEngine for intelligent routing.
"""

import uuid
import asyncio
import time
from datetime import datetime
from typing import Dict, Any, Callable, List, Optional, Union
from .mcp_protocol import (
    YvMCPMessageType, YvMCPMessage, YvAgenticAction, YvAgenticObservation,
    YvCoreMCPToolRegistry, YvMCPUnifiedToolExecutor, YvMCPService,
    YvMCPToolMetadata, YvMCPToolType, YvMCPProtocol,
    YvMCPExecutionResult, YvMCPExecutionManager,
)
from ..reasoning import YvMultiPathReasoningEngine
from utils.dc import PiscesLxLogger
from utils.paths import PiscesLxPaths

_LOG = PiscesLxLogger("PiscesLx.Multimodal.MCP",
                      file_path=PiscesLxPaths.get_log_file("PiscesLx.Multimodal.MCP"),
                      enable_file=True)

#: Tags the unified executor records for the two registration paths. `YvMCPToolMetadata`
#: has no `priority` field, so the classification that was previously smuggled in as
#: `priority=10/5` is carried as a tag instead.
NATIVE_TOOL_TAG = "native"
EXTERNAL_TOOL_TAG = "external"

class YvMCPToolRegistry:
    """Registry bridging Yv tools with the shared Pisces MCP ecosystem.
    
    A comprehensive tool registry that extends YvMCPToolRegistry with Yv-specific
    features including native/external execution tracking, dual registration with the
    unified executor, and optional integration with the multipath reasoning engine.
    
    Architecture:
        1. Tool Registration:
           - Dual registration with unified executor and core registry
           - Native handler storage for local execution
           - Metadata tracking with timestamps
        
        2. Execution Management:
           - Native execution via stored handlers
           - External execution via unified executor
           - Execution statistics tracking
        
        3. Reasoning Integration:
           - Optional YvMultiPathReasoningEngine integration
           - Intelligent execution mode recommendation
           - Tree search reasoning support
    
    Key Features:
        - MCP (Model Context Protocol) contract compliance
        - Dual registration with YvMCPUnifiedToolExecutor
        - Native and external tool execution tracking
        - Multipath reasoning engine integration
        - Backward compatibility with utils.mcp implementations
    
    Attributes:
        agentic_id (str): Identifier for the owning agent instance.
        message_handler (Callable): Callback used to dispatch inbound MCP messages.
        tools (Dict[str, Dict[str, Any]]): Metadata for registered tools.
        capabilities (Dict[str, Dict[str, Any]]): Registered capability descriptors.
        reasoning_engine (Optional[YvMultiPathReasoningEngine]): Optional reasoning backend.
        execution_stats (Dict[str, Union[int, float]]): Local execution counters.
        _native_tools (Dict[str, Callable]): Mapping of native tool names to handlers.
        unified_executor (YvMCPUnifiedToolExecutor): Shared executor for dual registration.
        core_registry (YvMCPToolRegistry): Underlying registry from model.multiplexed tool-calling layer.
    
    Example:
        >>> registry = YvMCPToolRegistry(
        ...     agentic_id="agent-123",
        ...     message_handler=handle_message
        ... )
        >>> await registry.register_tool("search", "Search tool", {}, search_handler)
    
    Note:
        Extends YvMCPToolRegistry with Yv-specific features.
        Execution stats include native_executions, external_calls, total_executions.
    """

    def __init__(self, agentic_id: str, message_handler: Callable, reasoning_engine: Optional[YvMultiPathReasoningEngine] = None):
        """Initialize the registry and attach optional reasoning capabilities.
        
        Args:
            agentic_id (str): Identifier for the owning agent.
            message_handler (Callable): Coroutine invoked for inbound MCP messages.
            reasoning_engine (Optional[YvMultiPathReasoningEngine]): Optional reasoning
                helper used to recommend execution modes based on context.
        
        Note:
            Initializes unified_executor via YvMCPService.executor().
            Creates core_registry as YvMCPToolRegistry instance.
        """
        self.agentic_id = agentic_id
        self.message_handler = message_handler
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.capabilities: Dict[str, Dict[str, Any]] = {}
        self.reasoning_engine = reasoning_engine
        self.execution_stats = {
            "native_executions": 0,
            "external_calls": 0,
            "total_executions": 0,
            "average_execution_time": 0.0
        }
        self._native_tools: Dict[str, Callable] = {}
        
        # Initialize unified tool executor for better integration
        self.unified_executor = YvMCPService.executor()
        
        # One metadata store, shared with the executor: the executor resolves tool names from
        # its own registry, so a second `YvCoreMCPToolRegistry()` here was state nothing read
        # and it could diverge from what actually executes (docs/audit/00 §94).
        self.core_registry = self.unified_executor.registry
    
    async def register_tool(self, name: str, description: str, parameters: Dict[str, Any], native_handler: Optional[Callable] = None):
        """Register one tool in the metadata store the unified executor resolves names from.
        
        Args:
            name (str): Tool identifier used for execution.
            description (str): Human-readable description of the tool's purpose.
            parameters (Dict[str, Any]): Parameter schema for the tool,
                typically following JSON Schema format.
            native_handler (Optional[Callable]): Optional coroutine invoked for
                native execution. If None, tool is registered as external.
        
        Note:
            A tool is registered **once**. `YvMCPToolMetadata` has no `priority` field, so the
            native/external distinction is carried by `tool_type` and a tag; the earlier
            `function=`/`priority=` keywords and the calls to nonexistent `register_tool(...)`
            methods made *every* registration raise, so no tool ever reached the executor
            (docs/audit/00 §94).
        """
        self.tools[name] = {
            "description": description,
            "parameters": parameters,
            "timestamp": datetime.now().isoformat(),
            "has_native_handler": native_handler is not None,
            "execution_mode": "native" if native_handler else "external"
        }

        if native_handler:
            self._native_tools[name] = native_handler

        tool_metadata = YvMCPToolMetadata(
            name=name,
            description=description,
            tool_type=YvMCPToolType.NATIVE if native_handler else YvMCPToolType.EXTERNAL,
            parameters=parameters,
            handler=native_handler,
            tags=[NATIVE_TOOL_TAG if native_handler else EXTERNAL_TOOL_TAG],
        )
        self.core_registry.register(tool_metadata)
        _LOG.info(f"[YvMCPToolRegistry] Registered {tool_metadata.tool_type.value} tool: {name}")
    
    async def handle_tool_call(self, tool_name: str, **kwargs) -> Any:
        """Execute a tool invocation using the core registry executor.

        Args:
            tool_name (str): Name of the tool to invoke.
            **kwargs: Arguments forwarded to the tool handler.

        Returns:
            Dict[str, Any]: `{"success", "output", "error", "duration_s", "tool"}` — a plain
                record so the artifact can be fed back to the model as text.

        Note:
            This leg used to call `core_registry.execute_tool(...)`, a method the registry does
            not have: every tool invocation raised AttributeError even after registration was
            fixed. Name resolution and execution belong to the unified executor, which is what
            reads the registry this class now writes into (docs/audit/00 §94). `execute` is
            synchronous and returns a result record, so the record is converted here rather than
            awaited.
        """
        outcome = self.unified_executor.execute(tool_name, kwargs)
        output = outcome.output
        if asyncio.iscoroutine(output):
            # The executor resolves and calls handlers synchronously, so a coroutine handler
            # came back as an un-awaited coroutine object: `success` said True while the real
            # result was discarded, and the artifact handed back to the model would have been
            # a repr like `<coroutine object ...>`. Awaited here, in this async caller.
            output = await output
        return {
            "tool": outcome.tool_name,
            "success": outcome.success,
            "output": output,
            "error": outcome.error,
            "duration_s": outcome.duration_s,
        }
    
    def get_execution_stats(self) -> Dict[str, Any]:
        """Return execution statistics from the objects that actually record them.

        `YvCoreMCPToolRegistry` has no `get_stats`, so this call raised for every caller;
        outcomes live in the executor's manager and tool listings in its registry.
        """
        stats = dict(self.unified_executor.stats())
        stats["tracked_tools"] = len(self.tools)
        stats["native_handlers"] = len(self._native_tools)
        return stats
    
    def reset_execution_stats(self):
        """Reset locally tracked execution counters and print a status message."""
        self.execution_stats = {
            "native_executions": 0,
            "external_calls": 0,
            "total_executions": 0,
            "average_execution_time": 0.0
        }
        print("[YvMCPToolRegistry] Execution statistics reset")
    
    async def register_native_tool(self, name: str, description: str, 
                                   parameters: Dict[str, Any], handler: Callable):
        """Convenience wrapper for registering a native tool.

        Args:
            name (str): Tool identifier.
            description (str): Short description of the tool.
            parameters (Dict[str, Any]): Tool parameter schema.
            handler (Callable): Native handler invoked for execution.
        """
        await self.register_tool(name, description, parameters, native_handler=handler)

class YvCoreMCPProtocol:
    """Protocol adapter enabling dual-track MCP execution for Yv agents.

    The adapter wraps :class:`YvMCPProtocol` to add metadata
    for dual-track routing and to integrate with the Yv tool registry when
    available.
    """

    def __init__(self, agent_id: str, tool_registry: Optional[YvMCPToolRegistry] = None):
        """Initialize the protocol adapter and link the optional tool registry.

        Args:
            agent_id (str): Identifier of the agent using the protocol.
            tool_registry (Optional[YvMCPToolRegistry]): Registry providing
                tool metadata and reasoning hooks.
        """
        # YvMCPProtocol is a stateless factory of well-formed messages, so the adapter
        # holds the factory itself rather than an instance of it.
        self.message_factory = YvMCPProtocol
        self.agent_id = agent_id
        self.tool_registry = tool_registry
        self.execution_modes = {
            "native": "Direct native execution for zero-latency performance",
            "external": "External MCP server execution for extended capabilities",
            "auto": "Intelligent routing based on tool availability and performance"
        }
    
    @staticmethod
    def create_message(
        message_type: YvMCPMessageType,
        agent_id: str,
        payload: Dict[str, Any],
        correlation_id: str = ""
    ) -> YvMCPMessage:
        """Create an MCP message via the base protocol helper.

        Args:
            message_type (YvMCPMessageType): Message type identifier.
            agent_id (str): Sender agent identifier.
            payload (Dict[str, Any]): Message payload.
            correlation_id (str): Optional correlation identifier.

        Returns:
            YvMCPMessage: Constructed message instance.
        """
        # Delegate to base protocol via fully qualified name
        return YvMCPProtocol.create_message(message_type, agent_id, payload, correlation_id)

    async def create_tool_call_message(self, tool_name: str, arguments: Dict[str, Any], 
                                       receiver_id: str, execution_mode: str = "auto") -> YvMCPMessage:
        """Construct a tool call message, optionally leveraging dual-track routing.

        Args:
            tool_name (str): Tool identifier.
            arguments (Dict[str, Any]): Tool invocation arguments.
            receiver_id (str): Message recipient identifier.
            execution_mode (str): Preferred execution mode (``"native"``, ``"external"``, ``"auto"``).

        Returns:
            YvMCPMessage: Tool call message populated with routing metadata.
        """
        # Evaluate routing strategy when automatic mode is requested.
        if execution_mode == "auto" and self.tool_registry:
            execution_mode = await self._determine_execution_mode(tool_name, arguments)
        
        # Build the envelope through the message factory.
        message = self.message_factory.create_message(
            message_type=YvMCPMessageType.TOOL_CALL,
            sender_id=self.agent_id,
            payload={
                "tool_name": tool_name,
                "arguments": arguments,
                "execution_mode": execution_mode,
            },
        )
        
        # Add receiver-specific information
        message.payload["receiver_id"] = receiver_id
        message.payload["dual_track_enabled"] = True
        
        return message
    
    async def _determine_execution_mode(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Determine execution mode using tool metadata and optional reasoning."""
        if not self.tool_registry:
            return "external"

        # Check whether the tool has a registered native handler.
        tool_info = self.tool_registry.tools.get(tool_name, {})
        if tool_info.get("has_native_handler", False):
            return "native"
        
        # Leverage the reasoning engine to select an execution mode when available.
        if self.tool_registry.reasoning_engine:
            reasoning_result = await self.tool_registry.reasoning_engine.analyze_execution_mode(
                tool_name, arguments, self.tool_registry.tools
            )
            return reasoning_result.get("recommended_mode", "external")
        
        return "external"
    
    async def execute_tool_with_fallback(self, tool_name: str, arguments: Dict[str, Any], 
                                       receiver_id: str) -> Dict[str, Any]:
        """Execute a tool, falling back to external routing when native execution fails."""
        if self.tool_registry and tool_name in self.tool_registry._native_tools:
            result = await self.tool_registry.handle_tool_call(tool_name, **arguments)
            return {
                "success": True,
                "result": result,
                "execution_mode": "native",
                "execution_time": 0.0
            }

        message = await self.create_tool_call_message(tool_name, arguments, receiver_id, "external")
        return {
            "success": True,
            "message": message,
            "execution_mode": "external",
            "execution_time": None
        }
