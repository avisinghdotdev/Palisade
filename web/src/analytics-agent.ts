import {
  type AgAiEvent,
  type AgAiHarnessSetup,
  type AgAiOutputItem,
  type AgAiTool,
  type AgAiToolContext,
  type AgLlmAdapter,
  type AgLlmRequest,
  type AgLlmResponse,
  type AgStudioApi,
  createAiHarness,
  directLlmRunner,
} from "ag-studio";

import type { AgentAnswer, AgentTurn } from "@/api/client";

import {
  type Block,
  isText,
  isToolUse,
  turnItems,
} from "@/analytics-agent-turns";
import {
  analyticsAgentTurn,
  dispute,
  disputeResponse,
  disputes,
  order,
  verifyMandate,
} from "@/api/client";
import { client } from "@/api/client/client.gen";
import { errorDetail, storedChatKey } from "@/shopper";

// The turn endpoint declares the chat access code as its security scheme; the client reads
// the stored code for it, as it does for the shop's chat.
client.setConfig({ auth: storedChatKey });

const DISPUTE_ID = /^PP-[A-Z0-9-]{1,40}$/;
const ORDER_NUMBER = /^RR-[A-Z0-9]{6}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const NOTES_SHOWN = 600;

// Claude's answers by id, so the next turn replays each one block for block, thinking
// included, and the cached prefix holds. Lost on reload; the turn is then rebuilt from what
// Studio kept.
const answers = new Map<string, Block[]>();

function toolChoice(choice: AgLlmRequest["toolChoice"]): string {
  if (choice === undefined) return "auto";
  return typeof choice === "string" ? choice : choice.name;
}

function describe(error: unknown): string {
  return error instanceof Error && error.message !== ""
    ? error.message
    : errorDetail(error);
}

function said(id: string, text: string): AgAiEvent[] {
  return [
    { type: "TEXT_MESSAGE_START", messageId: id, role: "assistant" },
    { type: "TEXT_MESSAGE_CONTENT", messageId: id, delta: text },
    { type: "TEXT_MESSAGE_END", messageId: id },
  ];
}

/** Claude's blocks as the events Studio streams: its words, then each tool call. */
function events(answer: AgentAnswer): AgAiEvent[] {
  const result: AgAiEvent[] = [];
  answer.content.forEach((block, index) => {
    const id = `${answer.id}/${String(index)}`;
    if (isText(block)) {
      result.push(...said(id, block.text));
    } else if (isToolUse(block)) {
      result.push(
        {
          type: "TOOL_CALL_START",
          toolCallId: block.id,
          toolCallName: block.name,
        },
        {
          type: "TOOL_CALL_ARGS",
          toolCallId: block.id,
          delta: JSON.stringify(block.input ?? {}),
        },
        { type: "TOOL_CALL_END", toolCallId: block.id },
      );
    }
  });
  if (answer.stop_reason === "refusal") {
    result.push(
      ...said(`${answer.id}/refusal`, "The agent declined to answer that."),
    );
  }
  return result;
}

function toResponse(answer: AgentAnswer): AgLlmResponse {
  const output: AgAiOutputItem[] = [];
  answer.content.forEach((block, index) => {
    const id = `${answer.id}/${String(index)}`;
    if (isText(block)) {
      output.push({
        id,
        kind: "output",
        type: "message",
        role: "assistant",
        status: "completed",
        content: [{ type: "text", text: block.text, annotations: [] }],
      });
    } else if (isToolUse(block)) {
      output.push({
        id,
        kind: "output",
        type: "function_call",
        callId: block.id,
        name: block.name,
        arguments: JSON.stringify(block.input ?? {}),
        status: "completed",
      });
    }
  });
  const truncated = answer.stop_reason === "max_tokens";
  return {
    id: answer.id,
    createdAt: Date.now(),
    model: answer.model,
    output,
    status: truncated ? "incomplete" : "completed",
    ...(truncated
      ? { incompleteDetails: { reason: "max_output_tokens" as const } }
      : {}),
    usage: {
      inputTokens: answer.usage.input_tokens,
      outputTokens: answer.usage.output_tokens,
    },
  };
}

/** Studio's provider boundary: every agent's turn goes to the API, which holds the Claude key. */
const adapter: AgLlmAdapter = {
  executeTurn(request, options) {
    const body: AgentTurn = {
      instructions: request.instructions ?? "",
      input: turnItems(request.input, answers),
      tools: (request.tools ?? []).map(({ name, description, parameters }) => ({
        name,
        description,
        parameters,
      })),
      tool_choice: toolChoice(request.toolChoice),
      format:
        request.responseFormat.type === "json"
          ? {
              name: request.responseFormat.name,
              schema: request.responseFormat.schema,
            }
          : null,
    };
    const answer = analyticsAgentTurn({
      body,
      signal: options?.signal,
      throwOnError: true,
    }).then(({ data }) => {
      answers.set(data.id, data.content);
      return data;
    });
    return {
      stream: {
        async *[Symbol.asyncIterator]() {
          try {
            yield* events(await answer);
          } catch (error) {
            yield {
              type: "RUN_ERROR",
              message: describe(error),
            } satisfies AgAiEvent;
          }
        },
      },
      complete: answer.then(toResponse, (error: unknown) => ({
        id: "failed",
        createdAt: Date.now(),
        output: [],
        status: "failed" as const,
        error: { code: "palisade_agent", message: describe(error) },
      })),
    };
  },
};

type Run = (
  args: Record<string, unknown>,
  signal: AbortSignal,
) => Promise<unknown>;

/** A read-only tool over Palisade's own API; the result goes to Claude as JSON text. */
function tool(
  name: string,
  description: string,
  properties: Record<string, { type: "string"; description: string }>,
  run: Run,
): AgAiTool {
  return {
    name,
    description,
    kind: "client",
    schema: () => ({
      name,
      description,
      parameters: {
        type: "object",
        properties,
        required: Object.keys(properties),
        additionalProperties: false,
      },
    }),
    async execute({ args }, ctx: AgAiToolContext) {
      try {
        return ctx.success(JSON.stringify(await run(args, ctx.signal)));
      } catch (error) {
        return ctx.error(describe(error));
      }
    },
  };
}

function argument(
  args: Record<string, unknown>,
  name: string,
  shape: RegExp,
): string {
  const value = args[name];
  if (typeof value !== "string" || !shape.test(value)) {
    throw new Error(`${name} is missing or not in the expected form`);
  }
  return value;
}

function palisadeTools(): AgAiTool[] {
  return [
    tool(
      "list_disputes",
      "Every PayPal dispute on the store's orders, newest first: id, order, who placed the order (agent or person), reason, stage, status, outcome, amount, and the seller's response deadline.",
      {},
      async (_args, signal) => {
        const { data } = await disputes({ signal, throwOnError: true });
        return data;
      },
    ),
    tool(
      "get_dispute",
      "One dispute in full, with Palisade's response to it: what the console can do now and why, and every answer Palisade prepared (type, status, the notes Claude wrote, how many sentences the grounding check kept and dropped, when it was filed).",
      {
        dispute_id: {
          type: "string",
          description: "The dispute id, such as PP-R-JRK-10196691.",
        },
      },
      async (args, signal) => {
        const path = { dispute_id: argument(args, "dispute_id", DISPUTE_ID) };
        const [detail, response] = await Promise.all([
          dispute({ path, signal, throwOnError: true }),
          disputeResponse({ path, signal, throwOnError: true }),
        ]);
        return {
          dispute: detail.data,
          palisade: {
            filing_mode: response.data.filing_mode,
            action: response.data.action,
            why: response.data.why,
            evidence: response.data.evidence.map((row) => ({
              evidence_type: row.evidence_type,
              status: row.status,
              stage: row.stage,
              prepared_at: row.prepared_at,
              filed_at: row.filed_at,
              notes: row.notes.slice(0, NOTES_SHOWN),
              sentences_kept:
                row.grounding?.filter((statement) => statement.kept).length ??
                null,
              sentences_dropped:
                row.grounding?.filter((statement) => !statement.kept).length ??
                null,
            })),
          },
        };
      },
    ),
    tool(
      "get_order",
      "One order: who placed it (an agent under a mandate, or a person), the agent and the policy hash, the decoded mandate, the PayPal ids and the custom id, the shipment, and every call to PayPal.",
      {
        order_number: {
          type: "string",
          description: "The order number, such as RR-CVQYZP.",
        },
      },
      async (args, signal) => {
        const { data } = await order({
          path: { number: argument(args, "order_number", ORDER_NUMBER) },
          signal,
          throwOnError: true,
        });
        return data;
      },
    ),
    tool(
      "verify_mandate",
      "Palisade's public verification of a mandate: the issuer's signature on the token, the claims against the record, the hash the buyer signed, the buyer's own proof, the policy it was signed under, and whether every PayPal order under it still carries the mandate as its custom id.",
      {
        mandate_id: { type: "string", description: "The mandate id, a UUID." },
      },
      async (args, signal) => {
        const { data } = await verifyMandate({
          path: { mandate_id: argument(args, "mandate_id", UUID) },
          signal,
          throwOnError: true,
        });
        return data;
      },
    ),
  ];
}

const PALISADE_INSTRUCTIONS = [
  "You are Palisade, the agent that knows the store's disputes and orders. Use the read-only tools yourself; never ask the user to run one.",
  "When the user names a dispute id, call get_dispute with it first. For an order number, call get_order. For a mandate id, or to check that an order's mandate is genuine, call verify_mandate; get_order gives you the mandate id. To say what needs a response, call list_disputes and look for status WAITING_FOR_SELLER_RESPONSE, then get_dispute on each to report what Palisade prepared.",
  "Answer in a few plain sentences: the facts, then what Palisade did or will do, then what the merchant should do, if anything. Quote ids exactly. Put every id and code in backticks, such as `PP-R-JRK-10196691` and `MERCHANDISE_OR_SERVICE_NOT_RECEIVED`, because the chat renders markdown and would swallow the underscores; say what a code means in plain words as well.",
  "You only read. You cannot file evidence, refund, escalate, or change a dispute; when asked to, say so and point to the dispute's page in the console, where the merchant presses the button.",
].join("\n");

const STARTERS = [
  {
    label: "What needs a response?",
    prompt:
      "Which disputes are waiting for the seller, and what has Palisade prepared for each? Ask the Palisade agent and summarise.",
  },
  {
    label: "Explain the newest dispute",
    prompt:
      "Find the newest dispute on this dashboard and have the Palisade agent explain it: the reason, how the order was placed, and what Palisade filed or prepared.",
  },
  {
    label: "Verify a mandate",
    prompt:
      "Take the newest order a shopping agent placed on this dashboard and have the Palisade agent verify its mandate. Report each check.",
  },
  {
    label: "Chart disputes by reason",
    prompt:
      "Add a column chart to this page showing disputes by reason, split by whether an agent or a person placed the order.",
  },
];

/**
 * The dashboard's agents: AG Studio's five on Claude through the API, and Palisade, which
 * the lead hands disputes, orders, and mandates to.
 */
export const analyticsAi: AgAiHarnessSetup = ({ api }) =>
  // In the browser Studio passes its full API; the setup type names only the core one.
  createAiHarness(api as AgStudioApi, ({ builtIn, tools }) => {
    const palisade = directLlmRunner({
      id: "palisade",
      name: "Palisade",
      description:
        "Explains the store's disputes, orders, and mandates from Palisade's records: what needs a response, what evidence Palisade prepared, whether a mandate verifies. Give it a dispute id, an order number, or a mandate id.",
      adapter,
      maxTurns: 8,
      instructions: () => PALISADE_INSTRUCTIONS,
      tools: palisadeTools,
    });
    const lead = directLlmRunner({
      ...builtIn.lead,
      adapter,
      instructions: (ctx, params) =>
        `${builtIn.lead.instructions?.(ctx, params) ?? ""}\n\nWhen the user asks about a dispute, an order, a mandate, or what needs a response, delegate to the palisade agent with the ids it needs and report what it says as given. It only reads; it never files or refunds.`,
      tools: (ctx, params) => [
        ...(builtIn.lead.tools?.(ctx, params) ?? []).filter(
          (studioTool) => studioTool.name !== "delegate_to",
        ),
        tools.delegateTo(["data", "page", "planning", "widget", "palisade"]),
      ],
    });
    const others = (["data", "page", "planning", "widget"] as const).map((id) =>
      directLlmRunner({ ...builtIn[id], adapter }),
    );
    return {
      agents: [lead, ...others, palisade],
      primary: "lead",
      promptStarters: STARTERS,
    };
  });
