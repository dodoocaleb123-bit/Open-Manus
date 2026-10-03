import asyncio
import json

import pytest

from model_adapters import (
    CommandType,
    ControllerCommand,
    ControllerProtocolError,
    DeepSeekController,
    ModelResponse,
    ModelRole,
    parse_decision,
)


class FakeDecisionAdapter:
    role = ModelRole.CONTROLLER
    model = "deepseek-r1:7b"

    def __init__(self, content):
        self.content = content
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return ModelResponse(role=self.role, model=self.model, content=self.content)

    async def health_check(self):
        raise AssertionError("not used in this test")


def test_parse_delegate_command_preserves_qwen_research_role():
    decision = parse_decision(
        json.dumps(
            {
                "assistant_message": "I will research the requested websites.",
                "status": "working",
                "command": {
                    "type": "delegate_to_model",
                    "arguments": {
                        "role": "research",
                        "objective": "Compare cosmetic storefront layouts",
                        "inputs": {"max_sources": 5},
                        "expected_output": "cited research report",
                    },
                },
            }
        )
    )
    assert decision.command.type is CommandType.DELEGATE_TO_MODEL
    assert decision.command.arguments["role"] == "research"
    assert decision.command.arguments["expected_output"] == "cited research report"


def test_parse_accepts_json_markdown_fence():
    decision = parse_decision(
        '```json\n{"assistant_message":"Done","status":"completed","command":{"type":"complete_task","arguments":{}}}\n```'
    )
    assert decision.status == "completed"
    assert decision.command.type is CommandType.COMPLETE_TASK


def test_delegate_cannot_target_controller():
    with pytest.raises(ControllerProtocolError, match="cannot be controller"):
        ControllerCommand(
            CommandType.DELEGATE_TO_MODEL,
            {"role": "controller", "objective": "route", "expected_output": "plan"},
        )


def test_approval_command_requires_known_sensitive_action():
    with pytest.raises(ControllerProtocolError, match="Unsupported approval action"):
        ControllerCommand(
            CommandType.REQUEST_USER_APPROVAL,
            {"action": "send_email", "summary": "Send", "impact": "External message"},
        )


def test_github_approval_command_is_valid():
    command = ControllerCommand(
        CommandType.REQUEST_USER_APPROVAL,
        {
            "action": "push_to_github",
            "summary": "Push the generated project to the selected repository",
            "impact": "Creates a commit and changes the remote repository",
        },
    )
    assert command.to_dict()["type"] == "request_user_approval"


def test_malformed_decision_is_rejected():
    with pytest.raises(ControllerProtocolError, match="invalid JSON"):
        parse_decision("not JSON")


def test_controller_decide_requests_json_and_returns_validated_decision():
    adapter = FakeDecisionAdapter(
        json.dumps(
            {
                "assistant_message": "I am ready to build the project.",
                "status": "working",
                "command": {
                    "type": "delegate_to_model",
                    "arguments": {
                        "role": "coder",
                        "objective": "Build the requested project",
                        "expected_output": "tested project preview",
                    },
                },
            }
        )
    )
    decision = asyncio.run(DeepSeekController(adapter).decide([{"role": "user", "content": "Build it"}]))
    assert decision.command.arguments["role"] == "coder"
    assert adapter.requests[0].response_format == {"type": "json_object"}
    assert "CONTROLLER COMMAND PROTOCOL" in adapter.requests[0].messages[0]["content"]
