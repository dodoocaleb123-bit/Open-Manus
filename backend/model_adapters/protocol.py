"""Structured controller protocol emitted by DeepSeek and consumed later by execution."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from .types import ModelRole


class ControllerProtocolError(ValueError):
    """Raised when DeepSeek returns an unsafe or malformed decision."""


class CommandType(StrEnum):
    DELEGATE_TO_MODEL = "delegate_to_model"
    RUN_TOOL = "run_tool"
    REQUEST_USER_INPUT = "request_user_input"
    REQUEST_USER_APPROVAL = "request_user_approval"
    PAUSE_TASK = "pause_task"
    RESUME_TASK = "resume_task"
    RETRY_TASK = "retry_task"
    CANCEL_TASK = "cancel_task"
    COMPLETE_TASK = "complete_task"


_SPECIALIST_ROLES = frozenset(
    {ModelRole.RESEARCH, ModelRole.CODER, ModelRole.VISION, ModelRole.CREATIVE}
)
_APPROVAL_ACTIONS = frozenset(
    {
        "push_to_github",
        "publish_or_deploy",
        "send_external_message",
        "delete_user_data",
        "use_or_change_sensitive_secrets",
    }
)


@dataclass(frozen=True)
class ControllerCommand:
    """One command selected by DeepSeek for the execution layer."""

    type: CommandType
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_command(self.type, self.arguments)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class ControllerDecision:
    """User-facing message plus at most one executable controller command."""

    assistant_message: str
    command: ControllerCommand | None = None
    status: str = "working"

    def __post_init__(self) -> None:
        if not self.assistant_message.strip():
            raise ControllerProtocolError("assistant_message must not be empty")
        if self.status not in {"working", "waiting_for_user", "waiting_for_approval", "completed", "paused", "cancelled", "failed"}:
            raise ControllerProtocolError(f"Unsupported decision status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "assistant_message": self.assistant_message,
            "status": self.status,
            "command": self.command.to_dict() if self.command else None,
        }


def parse_decision(content: str) -> ControllerDecision:
    """Parse one strict JSON decision, accepting an optional markdown fence."""
    raw = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ControllerProtocolError(f"DeepSeek returned invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ControllerProtocolError("DeepSeek decision must be a JSON object")
    message = payload.get("assistant_message")
    status = payload.get("status", "working")
    command_payload = payload.get("command")
    if not isinstance(message, str):
        raise ControllerProtocolError("assistant_message must be a string")
    command = None
    if command_payload is not None:
        if not isinstance(command_payload, dict):
            raise ControllerProtocolError("command must be an object or null")
        command_type = command_payload.get("type")
        arguments = command_payload.get("arguments", {})
        if not isinstance(command_type, str):
            raise ControllerProtocolError("command.type must be a string")
        if not isinstance(arguments, dict):
            raise ControllerProtocolError("command.arguments must be an object")
        try:
            command = ControllerCommand(CommandType(command_type), arguments)
        except ValueError as exc:
            raise ControllerProtocolError(str(exc)) from exc
    return ControllerDecision(assistant_message=message, status=status, command=command)


def _validate_command(command_type: CommandType, arguments: Mapping[str, Any]) -> None:
    required: dict[CommandType, tuple[str, ...]] = {
        CommandType.DELEGATE_TO_MODEL: ("role", "objective", "expected_output"),
        CommandType.RUN_TOOL: ("tool", "arguments"),
        CommandType.REQUEST_USER_INPUT: ("question",),
        CommandType.REQUEST_USER_APPROVAL: ("action", "summary", "impact"),
        CommandType.PAUSE_TASK: (),
        CommandType.RESUME_TASK: (),
        CommandType.RETRY_TASK: (),
        CommandType.CANCEL_TASK: ("reason",),
        CommandType.COMPLETE_TASK: (),
    }
    missing = [key for key in required[command_type] if key not in arguments]
    if missing:
        raise ControllerProtocolError(f"{command_type.value} is missing: {', '.join(missing)}")

    if command_type is CommandType.DELEGATE_TO_MODEL:
        try:
            role = ModelRole(str(arguments["role"]))
        except ValueError as exc:
            raise ControllerProtocolError("delegate_to_model.role must be a specialist role") from exc
        if role not in _SPECIALIST_ROLES:
            raise ControllerProtocolError("delegate_to_model.role cannot be controller")
        for key in ("objective", "expected_output"):
            if not isinstance(arguments[key], str) or not arguments[key].strip():
                raise ControllerProtocolError(f"delegate_to_model.{key} must be a non-empty string")
        inputs = arguments.get("inputs", {})
        if not isinstance(inputs, dict):
            raise ControllerProtocolError("delegate_to_model.inputs must be an object")

    elif command_type is CommandType.RUN_TOOL:
        if not isinstance(arguments["tool"], str) or not arguments["tool"].strip():
            raise ControllerProtocolError("run_tool.tool must be a non-empty string")
        if not isinstance(arguments["arguments"], dict):
            raise ControllerProtocolError("run_tool.arguments must be an object")

    elif command_type is CommandType.REQUEST_USER_INPUT:
        if not isinstance(arguments["question"], str) or not arguments["question"].strip():
            raise ControllerProtocolError("request_user_input.question must be a non-empty string")

    elif command_type is CommandType.REQUEST_USER_APPROVAL:
        action = arguments["action"]
        if action not in _APPROVAL_ACTIONS:
            raise ControllerProtocolError(f"Unsupported approval action: {action}")
        for key in ("summary", "impact"):
            if not isinstance(arguments[key], str) or not arguments[key].strip():
                raise ControllerProtocolError(f"request_user_approval.{key} must be a non-empty string")

    elif command_type is CommandType.CANCEL_TASK:
        if not isinstance(arguments["reason"], str) or not arguments["reason"].strip():
            raise ControllerProtocolError("cancel_task.reason must be a non-empty string")
