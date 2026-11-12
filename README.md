# Palisade

Palisade is a merchant-side trust and dispute layer for purchases that AI shopping agents make at an
online store and pay for with PayPal. The buyer signs a mandate, Palisade verifies the agent and the
merchant's policy, binds the mandate to the PayPal order, and when a dispute arrives it answers with
evidence and a narrative written by Claude.

It is an entry to the PayPal AI Hackathon 2026. Everything runs against the PayPal sandbox.

- Hosted demo: https://palisade.avisingh.dev (the store at `/shop`, the merchant console at
  `/console`, an MCP endpoint for agents at `/api/shop/mcp`)
- Stack: FastAPI, React Router, PostgreSQL, Claude (Anthropic API), PayPal REST APIs, AG Grid and
  AG Studio, Azure Container Apps behind Cloudflare

## The loop

1. An agent shops. Either the in-page agent on `/shop` (Claude, running on the server) or any
   MCP client connected to `/api/shop/mcp` searches the catalog and fills a cart.
2. The buyer signs a mandate. The agent asks for approval; the buyer opens the consent page and
   signs with a passkey, or with a labelled demo signer. The mandate is an SD-JWT in the shape of
   an AP2 Checkout Mandate: the items, the amount cap, the merchant, the expiry, the agent, and a
   hash of the merchant's policy.
3. Palisade checks the agent. Every agent request carries an RFC 9421 HTTP message signature made
   with an Ed25519 key that Palisade holds in its registry. Unsigned or replayed requests are
   refused. The merchant's policy (allowed agents, spend caps, blocked categories) is applied.
4. The mandate is bound to the PayPal order. Palisade creates the order with `custom_id` set to
   the mandate's id and SHA-256, authorizes it from the buyer's vaulted PayPal wallet, and
   captures it. Every order is tagged as placed by an agent or by a person.
5. Disputes are triaged, then answered. PayPal's dispute webhooks are verified, stored, and
   processed by durable jobs. Palisade joins the dispute to the order and the mandate. While the
   dispute is an inquiry, Claude reads the buyer's complaint and the facts record and picks one
   answer PayPal offers: fight with the record, write to the buyer, offer a partial refund, or
   refund in full. The rules are checked in code, PayPal carries the answer out, and an offer or
   a refund always waits for the merchant's click. For a fight, Claude writes a narrative from
   the facts record, Palisade builds a record PDF, and files through the Disputes API: tracking
   as `PROOF_OF_FULFILLMENT` for item-not-received, the mandate record as `OTHER` evidence once
   the claim opens for the other reasons. The merchant chooses "Review and file" or automatic.
   A pre-chargeback alert gets a timed refund.

## Try it in five minutes

No login or key is needed for the store, the consent page, or the console.

1. Open https://palisade.avisingh.dev/shop. The default buyer is the demo buyer, whose PayPal
   sandbox wallet is already linked. To pay from your own sandbox account instead, use the buyer
   menu in the header and approve the wallet link on sandbox.paypal.com once.
2. Press "Shop with the Agent" and enter the chat access code that comes with the submission.
   Ask for a shoe; the agent proposes a cart and asks for your approval.
3. The consent page opens. Press "Approve with Passkey" (your device's passkey) or "Approve with
   Demo Signer (Simulated)". The chat carries on by itself and places the order. The receipt
   links to the order in the console.
4. Open the order in the console at https://palisade.avisingh.dev/console. You see the agent, the
   signature check, the policy decision, the decoded mandate, the PayPal order, authorization,
   and capture, and the timeline.
5. File a dispute. On a demo-buyer order, press "File a Dispute (Sandbox Simulator)", type what
   the buyer says is wrong, and pick a reason: the demo buyer's own PayPal app opens the
   dispute. From your own sandbox account, open Activity on sandbox.paypal.com, pick the
   payment, and choose "Report a problem". For an item-not-received story, press "Mark as
   Shipped" on the order first so the evidence has tracking.
6. Watch the dispute page under Disputes. PayPal's webhook reaches Palisade about three and a
   half minutes after filing. Claude then picks the answer, and the page shows the complaint as
   Claude read it, the reasons, and the text the buyer would read. Try "The sole came off on my
   first run" for a full refund, "a small scuff, I would keep them" for an offer, or "I never
   ordered these" on an agent order for a fight. A message, an offer, or a refund goes to
   PayPal when you press its button.
7. For a fight, Claude's notes and the record PDF follow within fifteen seconds. In "Review and
   file" mode, press "File with PayPal"; in automatic mode nothing needs pressing. A
   not-as-described inquiry is escalated first and the evidence is filed when PayPal opens the
   claim, about three minutes later. In the measured run on 2026-11-06 the evidence was on file
   7 minutes 10 seconds after the buyer filed.
8. PayPal offers adjudication hours later. "Decide for the Seller" is a sandbox simulator that
   stands in for PayPal's reviewer, and it is labelled as such.
9. Open "PayPal Ledger" in the console. Palisade reads PayPal's own transaction records and
   compares every paid order with them: the payment's id, amount, currency, custom id, status,
   and refunds. PayPal lists a payment up to three hours after it is made, so a new order
   shows as pending first. Below the orders it lists every payment PayPal took that no paid
   order names; the six there come from a test run against another database on 2026-10-28.
10. The Analytics page shows agent versus person orders, dispute rate, win rate, and time to
   evidence, on an AG Studio dashboard. "Edit and Ask" opens Studio's chat panel: its built-in
   agents change the dashboard, and the Palisade agent explains disputes, orders, and mandates
   from the store's records. The agents take the same access code as the chat. Agents and
   Policy shows the registered agents, their public keys, the policy in force with its hash,
   and the filing mode.
11. Press "Verify the Mandate" on any agent order or its dispute. The JSON that opens is the
   public verification: the signature against the published key, the claims, the hash, the
   buyer's proof, the policy, and PayPal's own custom id on the payment, read live.

To shop through your own agent instead of the chat, connect any MCP client to the endpoint, for
example with the `claude` command-line tool:

```
claude mcp add --transport http ridgeline https://palisade.avisingh.dev/api/shop/mcp
```

The endpoint exposes the same eight tools as the chat, pays from the demo buyer's wallet, and
appears in the console as the "MCP Agent".

## What is real and what is simulated

Real, in the PayPal sandbox: orders, authorizations, captures, vaulted wallets, webhooks and their
signature verification, disputes, messages to the buyer, refund offers, accepted claims,
escalation, evidence filing, shipment trackers, refunds, the transaction ledger, and every state
change PayPal makes on its own.

Simulated, and labelled in the interface: filing a dispute as the demo buyer (a real dispute, opened
through the demo buyer's own PayPal app because only the buyer can open one), the complaint typed
in that simulator (PayPal's sandbox takes no buyer text by API, so Palisade keeps it and marks it;
a buyer on sandbox.paypal.com writes real messages), PayPal's final
decision ("Decide for the Seller", a sandbox-only adjudicate call), the USPS tracking number (the
sandbox checks no number), and the demo signer (a server-held key that stands in for a buyer's
passkey so judges need no device setup).

## PayPal APIs used

| API | What Palisade does with it | Code |
|---|---|---|
| OAuth client credentials | Tokens for the merchant's app and the demo buyer's app | `api/src/palisade_api/paypal/auth.py` |
| Orders v2 | Create with intent `AUTHORIZE` and `custom_id` = mandate id and hash; authorize from a vault id with stored-credential fields; void when a capture is refused | `orders/checkout.py` |
| Payments v2 | Capture the authorization; refund a capture (pre-chargeback alerts) | `orders/checkout.py`, `disputes/alerts.py` |
| Vault v3 | Setup token and payment token so a buyer links a wallet once | `wallets/` |
| Webhooks | Receive, verify with `verify-webhook-signature`, store, deduplicate, process by job; eleven event types handled | `webhooks/` |
| Disputes v1 | List and read disputes; in the inquiry, send a message to the buyer (multipart), make a refund offer, or accept the claim with a full refund, as Claude's triage picks; escalate an inquiry; provide evidence as multipart (JSON part plus PDF); hourly reconciliation | `disputes/` |
| Shipment Tracking | Add a tracker to the capture so not-received evidence has tracking | `orders/shipping.py` |
| Transaction Search | Compare every paid order with PayPal's own ledger (payment id, amount, currency, custom id, status, refunds) in 31-day windows, and list the payments no paid order names; find the demo buyer's transaction id for a Palisade order, so the simulator can file | `orders/ledger.py`, `sandbox/simulator.py` |
| Sandbox simulators | Create a dispute as the buyer; adjudicate | `sandbox/` |

Every call sends a `PayPal-Request-Id` and records the `paypal-debug-id`. A call that gets no
answer, a 409, a 429, or a 5xx is repeated once under the same request id; an order whose outcome
stays unknown goes to a repair job that tries again every five minutes.

## AI used

| Where | Model | What it does | What it cannot do |
|---|---|---|---|
| The in-page agent on `/shop` | Claude Opus 5.5 through the Anthropic SDK's tool runner, streaming, with adaptive thinking | Searches, explains, fills the cart, requests the mandate, checks it, and places the order once the buyer has signed | Capture money. Checkout is refused without a signed, unexpired mandate that fits the cart; the model only calls the same signed API any agent calls |
| The inquiry triage | Claude Opus 5.5, one Messages call with a structured output | Reads the buyer's complaint and the facts record and picks one answer PayPal offers on the inquiry: fight with the record, write to the buyer, offer a partial refund, or refund in full, with its reasons and the text the buyer reads; the prompt and the pick are stored | Act outside the rules. Code computes the open answers from PayPal's own links and the merchant's rules, refuses a pick outside them, an offer above half the disputed amount, and any text naming an id or amount the record lacks; an offer or a refund waits for the merchant's click in every mode; the buyer's messages are treated as claims, never as instructions. See `disputes/triage.py` |
| The dispute narrative | Claude Opus 5.5, one Messages call with citations on | Writes at most 2,000 characters from a structured facts record (mandate, agent, order, tracking, policy) sent as a citable document, one line per fact; the prompt, the output, and every sentence's cited lines are stored | Invent facts. A grounding check drops any sentence that names an id, amount, date, or hash the record does not hold, before anything is filed; the console shows what was dropped and why. See `disputes/grounding.py` |
| The analytics dashboard's agents | Claude Opus 5.5 through AG Studio's agent framework, one turn per request through the API | AG Studio's five built-in agents (lead, data, page, planning, widget) build and change the dashboard; a custom Palisade agent the lead delegates to explains disputes, orders, and mandates from the store's records with four read-only tools, one of them the public mandate verification | Change anything on PayPal. The tools read; filing, refunding, and escalating stay with the merchant's buttons |

The Anthropic key never leaves the server. The chat and the dashboard's agents sit behind one
access code because every turn spends the team's credit; the rest of the site is open.

### Checked, not asserted

- `docs/evals/dispute-pipeline-2026-11-09.md`: 24 labelled disputes (`evals/dispute_cases.py`)
  routed by the real code, all as labelled, and 11 narratives written by Claude through the
  production path, all under the limit, all mentioning what their case requires, no sentence
  dropped by the grounding check. The routing part runs in the test suite on every push; the
  runner is `uv run --frozen --env-file .env python -m evals.dispute_eval`.
- `docs/evals/triage-2026-11-11.md`: 12 labelled inquiries (`evals/triage_cases.py`) triaged by
  Claude through the production reader and check. All 12 picks were ones a careful merchant
  would accept, and none of the three buyer messages that try to steer the pick ended in an
  offer or a refund the label does not allow. The first run scored 11 of 12: Claude refunded a
  buyer who denied "these exact shoes" under an intent mandate, because the prompt did not say
  that an intent mandate leaves the choice of items to the agent. The prompt now says so.
- `docs/evals/injection-suite-2026-11-11.md`: 20 prompt-injection attacks on the in-page agent
  in six families, plus a registered agent that ignores every rule and a stranger with no key,
  scored on money moved. The runner is `python -m evals.injection_suite` against a running stack.
- `api/tests/test_policy_properties.py` and `test_policy_mutants.py`: Hypothesis properties over
  the merchant's policy engine, the signed cart lines, and the mandate token (any single-character
  change is refused or changes no claim), and eight mutants of the policy engine that the
  properties must catch.

## What makes this different

Written for a reviewer who compares entries. Each claim points at the code or the record.

- Agents are verified at the merchant, not trusted. Every agent request is signed (RFC 9421,
  Ed25519) and checked against a registry with a nonce store and a five-minute window; the
  console shows the signature result on each order. See `agents/signatures.py`.
- The mandate is a standard token, signed by the buyer's own key. An SD-JWT in the AP2 Checkout
  Mandate shape, with the buyer's passkey assertion as the key-binding proof, single use, fifteen
  minutes for a cart mandate and twenty-four hours for an intent mandate, and the merchant's
  policy hash inside so a changed policy invalidates it. See `mandates/tokens.py`.
- Anyone can verify a mandate without the console. `GET /api/mandates/{id}/verify` checks the
  issuer's signature against the key published at `/.well-known/jwks.json` (the token header
  names it by `kid`), the claims against the record, the hash the buyer signed, the buyer's own
  proof (the passkey assertion or the demo signer's signature, checked again read-only), the
  policy it was signed under, and reads every PayPal order under it live to confirm the custom id
  still names the mandate. Every order and dispute page links to it. See `mandates/verification.py`.
- The binding to PayPal is on the order itself. `custom_id` carries the mandate id and hash, so
  the dispute, the capture, and the mandate are one record that PayPal's own objects point at.
- Evidence is routed by dispute reason and stage, the way PayPal's API requires it. Tracking as
  `PROOF_OF_FULFILLMENT` in the inquiry for not-received; escalate first and file the mandate
  record as `OTHER` in the claim for the rest, because PayPal refuses `OTHER` in the inquiry. See
  `disputes/responses.py`.
- Claude decides, code bounds it, PayPal carries it out. In the inquiry, the stage PayPal built
  for settling, Claude picks the answer from the buyer's own words and the record: a mandate
  answers "I never ordered this" and says nothing about a torn seam. The open answers come from
  PayPal's links and the merchant's rules, a pick outside them is refused, the buyer's messages
  cannot steer it, and money moves only on the merchant's click. A person's order with nothing
  to file is no longer left to the merchant alone: it gets a message, an offer, or a refund.
  See `disputes/triage.py` and `docs/evals/triage-2026-11-11.md`.
- Palisade's books are checked against PayPal's. The console's ledger page reads PayPal's
  Transaction Search live and compares every paid order with PayPal's own row: the payment id,
  the amount, the custom id that names the mandate, the status, and refunds. It checks the
  other way too: a payment PayPal took that no paid order names is listed. See
  `orders/ledger.py`.
- Pre-chargeback alerts are refunded on a timer inside PayPal's twenty-hour window, cancelled if
  the alert resolves first, and only in automatic mode. See `disputes/alerts.py`.
- Two filing modes. "Review and file" (the default) prepares everything and waits for a person;
  automatic files on its own. Both are proven live on the hosted app.
- One tool set, two doors. The chat and the MCP endpoint share the same eight tools, each with a
  title and read-only or destructive hints, and both sign as registered agents, so the console
  shows an outside agent's shopping beside the chat's. See `assistant/tools.py` and `mcp/server.py`.
- Nothing is lost between webhooks. Every delivery is verified and stored before it is processed;
  jobs retry with backoff; an hourly reconciliation reads disputes that fire no webhook; a
  five-minute sweep retries jobs a stopped worker left behind; and a job worker that dies,
  as one did when its database connections timed out on 2026-11-09, starts again by itself on
  a fresh connection pool. See `jobs.py`.
- Measured, not asserted. A buyer filed a dispute on the sandbox site at 20:49:41 UTC on
  2026-11-06; Palisade had the notes ready at 20:53:20, the merchant pressed File at 20:53:40,
  and the evidence was on file at 20:56:50 (PayPal 200, debug id `f5514074d9b3b`).
- Every sentence Claude files is traceable. The narrative cites the facts record line by line,
  and the grounding check drops any sentence naming something the record lacks; the dispute page
  shows each sentence with the record lines it rests on. The committed eval found no fabricated
  value in 11 narratives. See `disputes/grounding.py` and `docs/evals/`.
- Tested against the field's two blind spots. Labelled eval results and adversarial results are
  committed under `docs/evals/`, and the policy gate carries property-based tests with a mutation
  check, so a reviewer can read what the pipeline does without running it.
- The dashboard's agents are Studio's own plus one of ours. AG Studio's built-in lead delegates
  to a custom Palisade agent with read-only tools over the store's records, all on Claude through
  the API; no model key reaches the browser. See `web/src/analytics-agent.ts`.
- Production practices throughout: typed end to end (basedpyright recommended mode, TypeScript
  strict, a generated API client), structured logs with request, order, mandate, and dispute ids,
  secrets in Azure Key Vault, rate limits at nginx, a content security policy, health and readiness
  endpoints, migrations applied at startup under a lock, 286 API tests that run inside a rolled-back
  transaction, 16 web tests.

### Where to check each claim

| Claim | Where |
|---|---|
| Agent requests are signed and verified | `api/src/palisade_api/agents/signatures.py`; `api/tests/test_agents.py` (replay and stale signatures refused) |
| The mandate is a passkey-signed SD-JWT with the policy hash inside | `api/src/palisade_api/mandates/tokens.py`, `mandates/passkeys.py`; `api/tests/test_mandates.py` |
| Checkout is refused without a fitting mandate | `api/src/palisade_api/orders/checkout.py` (`MANDATE_*` refusals); `api/tests/test_checkout.py` |
| The order carries the mandate in `custom_id` | `orders/checkout.py`; any agent order's PayPal panel in the console |
| Webhooks are verified, stored, deduplicated, and processed by jobs | `api/src/palisade_api/webhooks/`; `api/tests/test_webhooks.py`, `test_jobs.py` |
| Evidence is routed by reason and stage | `api/src/palisade_api/disputes/responses.py`; `api/tests/test_evidence.py` |
| An inquiry is triaged by Claude inside rules checked in code, and PayPal carries the pick out | `api/src/palisade_api/disputes/triage.py`; `api/tests/test_triage.py`; `evals/triage_cases.py`, `docs/evals/triage-2026-11-11.md`; in the console, all on 2026-11-11: a message on `PP-R-YGR-10199606`, an offer of 16.00 on `PP-R-XBW-10199613`, a full refund on `PP-R-YFI-10199612`, and a fight on `PP-R-KIE-10199615` whose evidence was on file 7 minutes 33 seconds after the buyer filed |
| Every paid order is compared with PayPal's ledger | `api/src/palisade_api/orders/ledger.py`; `api/tests/test_ledger.py`; the PayPal Ledger page in the console |
| The alert refund waits inside PayPal's window and cancels on resolve | `api/src/palisade_api/disputes/alerts.py`; `api/tests/test_alerts.py` |
| The chat and the MCP endpoint share one tool set | `api/src/palisade_api/assistant/tools.py`, `mcp/server.py`; `api/tests/test_mcp.py` |
| A live dispute cycle ran end to end | Dispute `PP-R-JRK-10196691` on order `RR-CVQYZP` in the hosted console: filed 2026-11-06 20:49:41 UTC, evidence on file 20:56:50 |
| The narrative cites the record and ungrounded sentences are dropped | `api/src/palisade_api/disputes/grounding.py`, `narrative.py`; `api/tests/test_grounding.py`; the "sentences kept" line on any dispute page |
| A mandate verifies with public keys and PayPal's own order | `api/src/palisade_api/mandates/verification.py`; `api/tests/test_mandates.py` (`test_a_mandate_is_verifiable_with_the_published_key_and_paypals_order`); `/.well-known/jwks.json` on the hosted app |
| The routing and the narratives are evaluated on labelled cases | `evals/dispute_cases.py`, `evals/dispute_eval.py`; `api/tests/test_dispute_cases.py`; `docs/evals/dispute-pipeline-2026-11-09.md` |
| The agent gate holds under injection and misuse | `evals/injection_attacks.py`, `evals/injection_suite.py`; `docs/evals/injection-suite-2026-11-11.md` |
| The policy engine's rules hold for every input, and a broken rule is caught | `api/tests/policy_properties.py`, `test_policy_properties.py`, `test_policy_mutants.py` |
| The dashboard's custom agent talks to Studio's built-in agents | `web/src/analytics-agent.ts`; `api/src/palisade_api/analytics/agent.py`; `api/tests/test_analytics_agent.py` |

## Architecture

- `api/` is the FastAPI service, packaged by feature: `paypal/`, `webhooks/`, `shop/`, `wallets/`,
  `orders/`, `agents/`, `policy/`, `mandates/`, `activity/`, `analytics/`, `assistant/`, `mcp/`,
  `disputes/`, `sandbox/`. Jobs run on Procrastinate in the same PostgreSQL database, inside the
  API process. Migrations are Alembic.
- `web/` is the React Router SPA with prerendering: the store with its chat panel, the consent
  page, and the merchant console (orders, disputes, agents and policy, analytics, activity).
  Tables are AG Grid Community; the analytics page is an AG Studio dashboard.
- `infra/` is Bicep for Azure Container Apps, Container Registry, Key Vault, and Log Analytics;
  a push to `main` builds both images and deploys.
- `spikes/` are the throwaway scripts that proved each PayPal behaviour in the sandbox before it
  was built.

## Run it locally

You need Git, Docker Desktop, Node 24, and `just`. Then:

```
just init
cp .env.example .env
```

Fill `.env`:

- `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID`: a sandbox REST app on a business
  account, with Vault enabled and a webhook pointing at your public URL `/api/webhooks/paypal`.
- `PAYPAL_BUYER_CLIENT_ID`, `PAYPAL_BUYER_CLIENT_SECRET`: a second sandbox REST app on the business
  account that plays the demo buyer (only a buyer's own app can open a dispute on its payments).
- `DEMO_BUYER_EMAIL`: that account's email; `PUBLIC_ORIGIN`: `http://127.0.0.1:8080` locally.
- `DATABASE_URL`: any PostgreSQL 18 database. Apply the schema once with
  `uv run --frozen --env-file .env python -m palisade_api.migrate`.
- `ANTHROPIC_API_KEY`, and `CHAT_ACCESS_KEY` (any long random string; it opens the chat).
- Four Ed25519 seeds as base64 of 32 random bytes: `SHOPPER_AGENT_KEY`, `MCP_AGENT_KEY`,
  `MANDATE_ISSUER_KEY`, `DEMO_SIGNER_KEY`.
- `AG_STUDIO_LICENSE_KEY`: an AG Studio trial key, or leave it empty and accept the watermark.

Then `docker compose up --watch` and open http://127.0.0.1:8080. The API has no host port; the
web container proxies `/api`. Checks: `uv run --frozen --env-file .env pytest`,
`uv run --frozen basedpyright`, `pnpm --dir web run typecheck lint knip test`. The three eval
runners write dated reports under `docs/evals/`: `uv run --frozen --env-file .env python -m
evals.dispute_eval` (calls Claude for 11 narratives), `python -m evals.triage_eval` (calls
Claude for 12 picks), and `python -m evals.injection_suite` (needs the running stack; places
one sandbox order as its control).

## Limits worth knowing

- Sandbox only. No code path targets a live PayPal account.
- The sandbox cannot open `UNAUTHORISED`, `INCORRECT_AMOUNT`, or `DUPLICATE_TRANSACTION` disputes
  by API, and it never produces a pre-chargeback `ALERT`; those paths are unit-tested and take the
  same routes as the reasons that are proven live.
- PayPal offers adjudication hours after evidence is filed, so a demo that ends with a decision
  uses a dispute opened earlier.
- The in-page chat and the dashboard's agents need the access code; the MCP endpoint, the store,
  and the console do not. AG Studio shows its agent panel in edit mode only, on wide screens.
- The injection suite cannot plant an instruction inside a product description or another tool
  result, because the catalog is seeded data; the system prompt's untrusted-content rule is the
  defence there, and it is not measured.
- The triage reads what the buyer wrote and cannot check it: a buyer who lies about a fault can
  be offered a refund, which is why an offer or a refund waits for the merchant. The demo
  buyer's app cannot write messages by API, so a second round needs a buyer on
  sandbox.paypal.com.
- The ledger check reads every paid order and PayPal's whole ledger since the first one on each
  visit. That suits a demo store; a busy store would store the comparison.
- A grounding check catches invented ids, amounts, dates, and hashes. It cannot catch a wrong
  claim made in plain words; the facts record and the PDF are what a reviewer should trust.

## Licence and credits

MIT, see `LICENSE`. Product photos are Unsplash-licensed shots of real shoes, credited in
`docs/product-photos.md`; the store is a demo and sells nothing.
