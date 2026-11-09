import type { AgAiConversationItem } from "ag-studio";

import type { AgentAnswer, AgentTurn } from "@/api/client";

/**
 * How AG Studio's conversation becomes the API's turn. Studio keeps the user's words, the
 * agent's words and tool calls, and the tool results; the API wants Claude's own answers back
 * block for block when the browser still holds them, so the thinking blocks and the cached
 * prefix survive, and a rebuilt turn from Studio's items otherwise.
 */
export type TurnItem = AgentTurn["input"][number];
export type Block = AgentAnswer["content"][number];

export interface TextBlock {
  type: "text";
  text: string;
}

export interface ToolUseBlock {
  type: "tool_use";
  id: string;
  name: string;
  input: unknown;
}

export function isText(block: Block): block is Block & TextBlock {
  return block["type"] === "text" && typeof block["text"] === "string";
}

export function isToolUse(block: Block): block is Block & ToolUseBlock {
  return (
    block["type"] === "tool_use" &&
    typeof block["id"] === "string" &&
    typeof block["name"] === "string"
  );
}

/** The id of the answer an output item came from: items are named `<answer id>/<index>`. */
function answerId(itemId: string): string {
  return itemId.split("/")[0] ?? "";
}

function words(
  item: Extract<AgAiConversationItem, { type: "message" }>,
): string {
  return item.content
    .flatMap((part) => (part.type === "text" ? [part.text] : []))
    .join("\n");
}

type Output = Extract<AgAiConversationItem, { kind: "output" }>;
type Result = Extract<AgAiConversationItem, { type: "function_call_output" }>;

function isOutput(item: AgAiConversationItem): item is Output {
  return item.kind === "output";
}

function isResult(item: AgAiConversationItem): item is Result {
  return item.type === "function_call_output";
}

/**
 * Studio's conversation as the API takes it. `answers` holds Claude's answers by id, as the
 * adapter received them; an answer still there is replayed whole, one that is gone (a reload)
 * is rebuilt from the words and calls Studio kept.
 *
 * Studio files each tool call next to its own result (call, result, call, result), while the
 * Messages API wants one answer followed by one message holding every result it asked for.
 * So each answer is emitted once, where its first item sits, and all of its results follow it.
 */
export function turnItems(
  input: AgAiConversationItem[],
  answers: ReadonlyMap<string, Block[]>,
): TurnItem[] {
  const outputs = input.filter(isOutput);
  const results = input.filter(isResult);
  const answered = new Set<string>();
  const used = new Set<string>();
  const items: TurnItem[] = [];
  for (const item of input) {
    if (item.type === "message" && item.kind === "input") {
      const text = words(item);
      items.push({
        type: "user",
        text: item.role === "system" ? `[Instruction] ${text}` : text,
      });
    } else if (isResult(item)) {
      // A result whose call no answer holds; the ones that belong to an answer went with it.
      if (!used.has(item.callId)) {
        used.add(item.callId);
        items.push({
          type: "tool_results",
          results: [{ call_id: item.callId, output: item.output }],
        });
      }
    } else if (isOutput(item) && !answered.has(answerId(item.id))) {
      const id = answerId(item.id);
      answered.add(id);
      const own = outputs.filter((output) => answerId(output.id) === id);
      const calls = own.flatMap((output) =>
        output.type === "function_call"
          ? [
              {
                id: output.callId,
                name: output.name,
                arguments: output.arguments,
              },
            ]
          : [],
      );
      const answer = answers.get(id);
      items.push(
        answer === undefined
          ? {
              type: "assistant_calls",
              text: own
                .map((output) =>
                  output.type === "message" ? words(output) : "",
                )
                .join(""),
              calls,
            }
          : { type: "assistant", content: answer },
      );
      const callIds = new Set(calls.map((call) => call.id));
      const ownResults = results.filter((result) => callIds.has(result.callId));
      for (const result of ownResults) used.add(result.callId);
      if (ownResults.length > 0) {
        items.push({
          type: "tool_results",
          results: ownResults.map((result) => ({
            call_id: result.callId,
            output: result.output,
          })),
        });
      }
    }
  }
  return items;
}
