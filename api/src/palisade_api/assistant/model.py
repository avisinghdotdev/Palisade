"""The model behind the agent: Claude through the SDK's tool runner, behind a seam tests can stand in for."""

from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Protocol, cast, final

from anthropic import AsyncAnthropic
from anthropic.lib.tools import BetaAsyncFunctionTool
from anthropic.types.beta import BetaMessageParam, BetaUsage
from pydantic import BaseModel, JsonValue, TypeAdapter

JSON = TypeAdapter(JsonValue)
MODEL = "claude-sonnet-5-5"
# Room for one reply and one tool call; a turn that needs more makes more requests.
MAX_TOKENS = 4096
# How many replies one turn may ask the model for; a runaway loop stops here.
MAX_ITERATIONS = 12

# A tool as the runner calls it: the model's arguments in, the text Claude reads out.
ToolFunction = Callable[..., Coroutine[object, object, str]]
Tool = BetaAsyncFunctionTool[ToolFunction]
OnText = Callable[[str], None]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def plus(self, usage: BetaUsage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + usage.input_tokens,
            output_tokens=self.output_tokens + usage.output_tokens,
            cache_read_tokens=self.cache_read_tokens
            + (usage.cache_read_input_tokens or 0),
            cache_write_tokens=self.cache_write_tokens
            + (usage.cache_creation_input_tokens or 0),
        )


@dataclass(frozen=True)
class Reply:
    """What a turn adds to the history: the model's messages and the tool results, in order."""

    messages: list[JsonValue]
    usage: Usage


class Model(Protocol):
    async def reply(
        self, history: Sequence[JsonValue], tools: Sequence[Tool], on_text: OnText
    ) -> Reply:
        """Answer the last message in `history`, running tools until the model stops; the words stream to `on_text`."""
        ...


@final
class ClaudeModel:
    """Claude Sonnet 5.5 through the beta tool runner, streaming, with the fixed prompt cached.

    Thinking is set to `between_tools` so the model's words keep streaming between tool calls,
    and the effort stays fixed for a conversation because changing it would miss the cache.
    """

    def __init__(self, client: AsyncAnthropic, system_prompt: str) -> None:
        self._client = client
        self._system_prompt = system_prompt

    async def reply(
        self, history: Sequence[JsonValue], tools: Sequence[Tool], on_text: OnText
    ) -> Reply:
        runner = self._client.beta.messages.tool_runner(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": self._system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages_param(history),
            tools=list(tools),
            thinking={"type": "between_tools"},
            output_config={"effort": "medium"},
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            thread=False,
            stream=True,
            max_iterations=MAX_ITERATIONS,
        )
        messages: list[JsonValue] = []
        usage = Usage()
        async for stream in runner:
            async for event in stream:
                if event.type == "text":
                    on_text(event.text)
            message = await stream.get_final_message()
            usage = usage.plus(message.usage)
            messages.append(
                JSON.validate_python(
                    {
                        "role": "assistant",
                        "content": [
                            block.model_dump(mode="json", exclude_unset=True)
                            for block in message.content
                        ],
                    }
                )
            )
            # Runs the reply's tool calls now, so their results are known here; the runner reuses them.
            results = await runner.generate_tool_call_response()
            if results is not None:
                messages.append(JSON.validate_python(results))
        return Reply(messages=messages, usage=usage)


def messages_param(history: Sequence[JsonValue]) -> list[BetaMessageParam]:
    """The stored history as the SDK types it.

    The history holds only what the API handed out and the runner built, stored as JSON, so
    the shapes are the SDK's own; validating them again would only repeat the API's work.
    """
    return cast("list[BetaMessageParam]", list(history))
