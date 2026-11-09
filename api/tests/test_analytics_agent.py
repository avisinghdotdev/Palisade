"""The dashboard agents' Claude turns: behind the access code, mapped the way the Messages API takes them."""

from collections.abc import Iterator

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic import BaseModel, JsonValue

from palisade_api.analytics.agent import MODEL, PREAMBLE, AgentAnswer, StudioAgent
from palisade_api.analytics.router import studio_agent as agent_dependency
from palisade_api.assistant.access import ChatAccess, chat_access
from palisade_api.main import app

pytestmark = pytest.mark.anyio

CODE = "open-sesame"


class SystemBlock(BaseModel):
    type: str
    text: str
    cache_control: dict[str, str] | None = None


class Tool(BaseModel):
    name: str
    description: str
    input_schema: dict[str, JsonValue]


class ClaudeRequest(BaseModel):
    model: str
    system: list[SystemBlock]
    messages: list[dict[str, JsonValue]]
    tools: list[Tool] = []
    tool_choice: dict[str, str] | None = None
    output_config: dict[str, JsonValue]


def claude_answer(
    content: list[dict[str, JsonValue]], stop_reason: str = "end_turn"
) -> dict[str, object]:
    return {
        "id": "msg_turn",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 80},
    }


@pytest.fixture
def claude() -> Iterator[list[httpx2.Request]]:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json=claude_answer(
                [
                    {"type": "thinking", "thinking": "", "signature": "sig"},
                    {"type": "text", "text": "Let me look at the schema."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "view_schema",
                        "input": {"table": "disputes"},
                    },
                ],
                "tool_use",
            ),
        )

    agent = StudioAgent(
        AsyncAnthropic(
            api_key="test",
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        )
    )
    app.dependency_overrides[agent_dependency] = lambda: agent
    app.dependency_overrides[chat_access] = lambda: ChatAccess(CODE)
    yield requests
    app.dependency_overrides.clear()


def turn_body() -> dict[str, JsonValue]:
    return {
        "instructions": "You are the lead. Build what the user asks.",
        "input": [
            {"type": "user", "text": "Chart disputes by reason."},
            {
                "type": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "", "signature": "earlier"},
                    {
                        "type": "tool_use",
                        "id": "toolu_0",
                        "name": "view_report",
                        "input": {},
                    },
                ],
            },
            {
                "type": "tool_results",
                "results": [{"call_id": "toolu_0", "output": '{"pages": 1}'}],
            },
            {
                "type": "assistant_calls",
                "text": "I will check the schema.",
                "calls": [
                    {
                        "id": "toolu_x",
                        "name": "view_schema",
                        "arguments": '{"table": "orders"}',
                    }
                ],
            },
            {
                "type": "tool_results",
                "results": [{"call_id": "toolu_x", "output": '{"fields": []}'}],
            },
            {"type": "user", "text": "Go on."},
        ],
        "tools": [
            {
                "name": "view_schema",
                "description": "The tables and fields.",
                "parameters": {
                    "type": "object",
                    "properties": {"table": {"type": "string"}},
                    "required": [],
                    "additionalProperties": False,
                },
            }
        ],
        "tool_choice": "view_schema",
        "format": None,
    }


async def test_a_turn_needs_the_access_code_and_is_sent_as_claude_takes_it(
    client: httpx2.AsyncClient, claude: list[httpx2.Request]
) -> None:
    refused = await client.post("/api/analytics/agent/turn", json=turn_body())
    assert refused.status_code == 401
    assert claude == []

    answered = await client.post(
        "/api/analytics/agent/turn", json=turn_body(), headers={"X-Chat-Key": CODE}
    )

    assert answered.status_code == 200, answered.text
    turn = AgentAnswer.model_validate_json(answered.content)
    assert (turn.id, turn.stop_reason, turn.usage.input_tokens) == (
        "msg_turn",
        "tool_use",
        1200,
    )
    assert [block["type"] for block in turn.content] == ["thinking", "text", "tool_use"]
    [request] = claude
    sent = ClaudeRequest.model_validate_json(request.content)
    assert sent.model == MODEL
    [system] = sent.system
    assert system.text.startswith(PREAMBLE)
    assert system.text.endswith("You are the lead. Build what the user asks.")
    assert system.cache_control == {"type": "ephemeral"}
    roles = [message["role"] for message in sent.messages]
    assert roles == ["user", "assistant", "user", "assistant", "user", "user", "user"]
    # The replayed answer keeps its thinking block; the rebuilt one carries its words and calls.
    assert sent.messages[1]["content"] == [
        {"type": "thinking", "thinking": "", "signature": "earlier"},
        {"type": "tool_use", "id": "toolu_0", "name": "view_report", "input": {}},
    ]
    assert sent.messages[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_0", "content": '{"pages": 1}'}
    ]
    assert sent.messages[3]["content"] == [
        {"type": "text", "text": "I will check the schema."},
        {
            "type": "tool_use",
            "id": "toolu_x",
            "name": "view_schema",
            "input": {"table": "orders"},
        },
    ]
    # Opus 5.5 takes no forced tool choice; the request to call one tool becomes an instruction.
    assert sent.tool_choice == {"type": "auto"}
    assert (
        sent.messages[-1]["content"]
        == "[Call the view_schema tool now rather than answering in words.]"
    )
    [tool] = sent.tools
    assert (tool.name, tool.input_schema["properties"]) == (
        "view_schema",
        {"table": {"type": "string"}},
    )
    assert sent.output_config == {"effort": "medium"}


async def test_a_json_answer_is_asked_for_as_a_structured_output(
    client: httpx2.AsyncClient, claude: list[httpx2.Request]
) -> None:
    body = turn_body()
    body["tools"] = []
    body["tool_choice"] = "auto"
    body["format"] = {
        "name": "plan",
        "schema": {"type": "object", "properties": {"steps": {"type": "array"}}},
    }

    answered = await client.post(
        "/api/analytics/agent/turn", json=body, headers={"X-Chat-Key": CODE}
    )

    assert answered.status_code == 200, answered.text
    [request] = claude
    sent = ClaudeRequest.model_validate_json(request.content)
    assert sent.tools == []
    assert sent.tool_choice is None
    assert sent.output_config == {
        "effort": "medium",
        "format": {
            "type": "json_schema",
            "schema": {"type": "object", "properties": {"steps": {"type": "array"}}},
        },
    }


async def test_claude_not_answering_is_a_502() -> None:
    def refuse(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            529,
            json={
                "type": "error",
                "error": {"type": "overloaded_error", "message": "Overloaded"},
            },
        )

    agent = StudioAgent(
        AsyncAnthropic(
            api_key="test",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(refuse)),
        )
    )
    app.dependency_overrides[agent_dependency] = lambda: agent
    app.dependency_overrides[chat_access] = lambda: ChatAccess(CODE)
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://palisade.test"
        ) as http:
            response = await http.post(
                "/api/analytics/agent/turn",
                json=turn_body(),
                headers={"X-Chat-Key": CODE},
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 502
    assert "could not answer" in response.text
