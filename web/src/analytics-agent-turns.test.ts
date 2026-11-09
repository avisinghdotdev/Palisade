import type { AgAiConversationItem } from "ag-studio";

import { describe, expect, it } from "vitest";

import { type Block, turnItems } from "./analytics-agent-turns";

const THINKING: Block = { type: "thinking", thinking: "", signature: "sig" };
const CALL: Block = {
  type: "tool_use",
  id: "toolu_1",
  name: "view_schema",
  input: { table: "disputes" },
};

const ask: AgAiConversationItem = {
  id: "u1",
  kind: "input",
  type: "message",
  role: "user",
  content: [{ type: "text", text: "Chart disputes by reason." }],
  status: "completed",
};

const conversation: AgAiConversationItem[] = [
  ask,
  {
    id: "msg_1/0",
    kind: "output",
    type: "message",
    role: "assistant",
    status: "completed",
    content: [{ type: "text", text: "Let me look.", annotations: [] }],
  },
  {
    id: "msg_1/1",
    kind: "output",
    type: "function_call",
    callId: "toolu_1",
    name: "view_schema",
    arguments: '{"table":"disputes"}',
    status: "completed",
  },
  {
    kind: "input",
    type: "function_call_output",
    callId: "toolu_1",
    output: '{"fields": []}',
    status: "completed",
  },
  {
    id: "s1",
    kind: "input",
    type: "message",
    role: "system",
    content: [{ type: "text", text: "Keep it short." }],
    status: "completed",
  },
];

describe("turnItems", () => {
  it("replays an answer the browser still holds, block for block", () => {
    const answers = new Map<string, Block[]>([
      ["msg_1", [THINKING, { type: "text", text: "Let me look." }, CALL]],
    ]);

    expect(turnItems(conversation, answers)).toEqual([
      { type: "user", text: "Chart disputes by reason." },
      {
        type: "assistant",
        content: [THINKING, { type: "text", text: "Let me look." }, CALL],
      },
      {
        type: "tool_results",
        results: [{ call_id: "toolu_1", output: '{"fields": []}' }],
      },
      { type: "user", text: "[Instruction] Keep it short." },
    ]);
  });

  it("sends an answer with two calls once, then both results, as Studio files them one by one", () => {
    const second: Block = {
      type: "tool_use",
      id: "toolu_2",
      name: "get_order",
      input: { order_number: "RR-CVQYZP" },
    };
    const interleaved: AgAiConversationItem[] = [
      ask,
      {
        id: "msg_2/0",
        kind: "output",
        type: "function_call",
        callId: "toolu_1",
        name: "view_schema",
        arguments: "{}",
        status: "completed",
      },
      {
        kind: "input",
        type: "function_call_output",
        callId: "toolu_1",
        output: "one",
        status: "completed",
      },
      {
        id: "msg_2/1",
        kind: "output",
        type: "function_call",
        callId: "toolu_2",
        name: "get_order",
        arguments: '{"order_number":"RR-CVQYZP"}',
        status: "completed",
      },
      {
        kind: "input",
        type: "function_call_output",
        callId: "toolu_2",
        output: "two",
        status: "completed",
      },
    ];
    const answers = new Map<string, Block[]>([
      ["msg_2", [THINKING, CALL, second]],
    ]);

    expect(turnItems(interleaved, answers)).toEqual([
      { type: "user", text: "Chart disputes by reason." },
      { type: "assistant", content: [THINKING, CALL, second] },
      {
        type: "tool_results",
        results: [
          { call_id: "toolu_1", output: "one" },
          { call_id: "toolu_2", output: "two" },
        ],
      },
    ]);
  });

  it("rebuilds an answer it no longer holds from the words and calls Studio kept", () => {
    expect(turnItems(conversation, new Map())[1]).toEqual({
      type: "assistant_calls",
      text: "Let me look.",
      calls: [
        {
          id: "toolu_1",
          name: "view_schema",
          arguments: '{"table":"disputes"}',
        },
      ],
    });
  });
});
