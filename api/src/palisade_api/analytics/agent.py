"""The console's dashboard agents on Claude: one turn in, one answer out, as AG Studio asks.

AG Studio runs its agent loop in the browser and hands each turn to an adapter: the
conversation so far, the instructions, and the tools the agent may call. The browser sends
that here, the API makes one Claude call, and the browser runs the tools Claude asked for
against the dashboard. The Anthropic key never leaves the server, and the route sits behind
the chat access code because every turn spends the team's credit.
"""

from typing import Annotated, Literal, cast, final

import structlog
from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic
from anthropic.types import (
    ContentBlockParam,
    MessageParam,
    TextBlockParam,
    ToolChoiceParam,
    ToolParam,
    ToolUseBlockParam,
)
from anthropic.types.output_config_param import OutputConfigParam
from anthropic.types.tool_param import InputSchema
from pydantic import BaseModel, Field, JsonValue, TypeAdapter

MODEL = "claude-opus-5-5"
ARGUMENTS = TypeAdapter(dict[str, JsonValue])
# Room for an answer and the tool calls that build a widget; a long turn makes more requests.
MAX_TOKENS = 8000
PREAMBLE = """You work inside the merchant console of Palisade, on its analytics dashboard, which is built with AG Studio. The dashboard holds Ridgeline Running's paid orders and PayPal disputes. Ridgeline Running is an online shoe store where AI shopping agents buy under mandates the buyer signs; Palisade verifies each agent, binds the mandate to the PayPal payment, and answers disputes with evidence. Every number you state comes from a tool result or the dashboard's data, never from memory. You read and arrange; you never file evidence, refund, or change a dispute, and you say so when asked to."""

logger = structlog.stdlib.get_logger(__name__)


class ToolSchema(BaseModel):
    name: str
    description: str
    parameters: dict[str, JsonValue]


class UserTurn(BaseModel):
    type: Literal["user"]
    text: str


class AssistantTurn(BaseModel):
    """Claude's own answer from an earlier turn, replayed block for block, thinking included."""

    type: Literal["assistant"]
    content: list[dict[str, JsonValue]]


class ToolCall(BaseModel):
    id: str
    name: str
    # The call's arguments as the JSON text Studio keeps.
    arguments: str


class AssistantCalls(BaseModel):
    """An earlier answer the browser no longer holds whole: its words and the tools it called."""

    type: Literal["assistant_calls"]
    text: str
    calls: list[ToolCall]


class ToolResult(BaseModel):
    call_id: str
    output: str


class ToolResults(BaseModel):
    type: Literal["tool_results"]
    results: list[ToolResult]


TurnItem = Annotated[
    UserTurn | AssistantTurn | AssistantCalls | ToolResults, Field(discriminator="type")
]


class JsonFormat(BaseModel):
    name: str
    json_schema: dict[str, JsonValue] = Field(alias="schema")


class AgentTurn(BaseModel):
    """One turn as Studio's adapter hands it over."""

    instructions: str = ""
    input: list[TurnItem]
    tools: list[ToolSchema] = []
    # "auto", "none", "required", or the name of the one tool to call.
    tool_choice: str = "auto"
    format: JsonFormat | None = None


class AgentUsage(BaseModel):
    input_tokens: int
    output_tokens: int


class AgentAnswer(BaseModel):
    """Claude's answer: its content blocks as sent, for the browser to show and to replay."""

    id: str
    model: str
    stop_reason: str | None
    content: list[dict[str, JsonValue]]
    usage: AgentUsage


class AgentUnavailable(Exception):
    def __init__(self, why: str) -> None:
        super().__init__(f"the dashboard agent could not answer: {why}")


def messages(items: list[TurnItem]) -> list[MessageParam]:
    """The conversation as the Messages API takes it; same-role neighbours merge on its side."""
    result: list[MessageParam] = []
    for item in items:
        if isinstance(item, UserTurn):
            result.append({"role": "user", "content": item.text})
        elif isinstance(item, AssistantTurn):
            # The blocks are Claude's own, stored as JSON by the browser; the API typed them.
            result.append(
                {
                    "role": "assistant",
                    "content": cast("list[ContentBlockParam]", item.content),
                }
            )
        elif isinstance(item, AssistantCalls):
            blocks: list[ContentBlockParam] = []
            if item.text.strip():
                text: TextBlockParam = {"type": "text", "text": item.text}
                blocks.append(text)
            for call in item.calls:
                arguments = (
                    ARGUMENTS.validate_json(call.arguments)
                    if call.arguments.strip()
                    else {}
                )
                use: ToolUseBlockParam = {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    # The SDK types a tool's input as a dict of anything.
                    "input": cast("dict[str, object]", arguments),
                }
                blocks.append(use)
            result.append({"role": "assistant", "content": blocks})
        else:
            result.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_result.call_id,
                            "content": tool_result.output,
                        }
                        for tool_result in item.results
                    ],
                }
            )
    return result


def tool_param(schema: ToolSchema) -> ToolParam:
    return {
        "name": schema.name,
        "description": schema.description,
        # Studio's JSON Schema for the parameters is already an object schema.
        "input_schema": cast("InputSchema", schema.parameters),
    }


@final
class StudioAgent:
    def __init__(self, client: AsyncAnthropic) -> None:
        self._client = client

    async def turn(self, request: AgentTurn) -> AgentAnswer:
        """One Claude call for the turn.

        Opus 5.5 takes no forced tool choice, so "required" or a tool's name becomes an
        instruction at the end of the conversation.

        Raises:
            AgentUnavailable: Claude refused the request or did not answer.
        """
        history = messages(request.input)
        tool_choice: ToolChoiceParam = {"type": "auto"}
        if request.tool_choice == "none":
            tool_choice = {"type": "none"}
        elif request.tool_choice != "auto":
            wanted = (
                "a tool"
                if request.tool_choice == "required"
                else f"the {request.tool_choice} tool"
            )
            history.append(
                {
                    "role": "user",
                    "content": f"[Call {wanted} now rather than answering in words.]",
                }
            )
        output_config: OutputConfigParam = (
            {"effort": "medium"}
            if request.format is None
            else {
                "effort": "medium",
                "format": {
                    "type": "json_schema",
                    # The SDK types a JSON Schema as a dict of anything.
                    "schema": cast("dict[str, object]", request.format.json_schema),
                },
            }
        )
        system = f"{PREAMBLE}\n\n{request.instructions}".strip()
        try:
            if request.tools:
                message = await self._client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=[
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=history,
                    tools=[tool_param(schema) for schema in request.tools],
                    tool_choice=tool_choice,
                    output_config=output_config,
                )
            else:
                message = await self._client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=[
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=history,
                    output_config=output_config,
                )
        except (APIStatusError, APIConnectionError) as error:
            raise AgentUnavailable(str(error)) from error
        logger.info(
            "studio_agent_turn",
            stop_reason=message.stop_reason,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            tools=len(request.tools),
        )
        return AgentAnswer(
            id=message.id,
            model=message.model,
            stop_reason=message.stop_reason,
            content=[
                block.model_dump(mode="json", exclude_unset=True)
                for block in message.content
            ],
            usage=AgentUsage(
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
            ),
        )
