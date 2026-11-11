# Case Study: Omnichannel Customer Operations Automation

**An e-commerce support back office as chained n8n workflows: it screens fraud, understands what each customer wants, fixes addresses, judges damage claims from photos, and sends only the expensive decisions to a human.**

![n8n](https://img.shields.io/badge/n8n-6%20workflows-EA4B71?style=flat-square) ![Vision](https://img.shields.io/badge/LLM-vision%20inspection-blue?style=flat-square) ![Supabase](https://img.shields.io/badge/ledger-Supabase-3ECF8E?style=flat-square) ![HITL](https://img.shields.io/badge/human--in--the--loop-Slack-green?style=flat-square) ![Status](https://img.shields.io/badge/status-portfolio%20build-lightgrey?style=flat-square)

| | |
|---|---|
| **Type** | Portfolio build (reference implementation for a fictional home-goods store, not a client engagement) |
| **My role** | Sole designer and builder: routing design, prompt and schema design, data model, escalation policy |
| **Scale** | 6 workflows, 66 nodes, 5 Supabase tables, intake plus 3 specialist flows plus reconciliation |
| **Core vs extension** | The core flow is workflows 01-03 (intake and routing, address resolver, damage engine). 04 (refund escalation) and 05 (reconciliation) extend it. |
| **Stack** | n8n, OpenRouter (text and vision models), Supabase, Airtable, Slack API, OpenStreetMap Nominatim |
| **Status** | Runs on sample data, not real customers. Most of my own audit findings are fixed and re-tested live (see [Testing](#testing-and-verification)); what is still open is listed at the bottom. |

> **If you only read one section:** [Why this is interesting](#why-this-is-interesting) and [What I would fix next](#what-i-would-fix-next).

---

## The problem

Most support tickets in a small online store are routine and repetitive: "where is my order", "I typo'd my apartment number", "my item arrived broken". Each one costs an agent several minutes of lookups across the order system, the customer record and the carrier, even though the right answer is usually obvious. At the same time, a few tickets are dangerous: fraudulent accounts, large refunds, angry high-value customers. Those need judgement and accountability.

The goal is not to remove humans. It is to make sure **people only spend time on the tickets where their judgement is worth the cost**, and to make that boundary explicit.

## Outcome

- Every inquiry is authenticated, screened against a fraud list, matched to the customer and order, classified by intent and sentiment, and logged as a ticket, before a person sees it.
- **Address changes** on unfulfilled orders are applied automatically; shipped orders are flagged for a carrier intercept instead.
- **Damage claims** are inspected by a vision model. Clear, low-value cases get an automatic replacement order; everything else is escalated with the evidence attached.
- **Refunds over a threshold** go to a Slack approval card with options, and nothing is paid out until a person clicks.
- Every action is written to a **ticket ledger**, then reconciled into an operations view.

## Why this is interesting

1. **The auto-approve rule is three explicit conditions, not a vibe.** Replace automatically only if damage is detected *and* confidence is at least 60 *and* order value is at most $100. See [the fast path](#the-fast-path-is-a-policy-not-a-prompt).
2. **Fraud is checked before the system spends anything.** A blacklisted customer is dropped with a quiet `200` before any lookup or LLM call.
3. **One front door, specialist back rooms.** Intent classification routes to separate workflows, so each can be tested and changed in isolation.
4. **A ledger records every decision**, which is what makes an automated system auditable to a finance or support lead.

---

## Architecture

```mermaid
flowchart TD
  A[POST /support-intake] --> B{Secret header valid?}
  B -- no --> X[401]
  B -- yes --> N[Normalize payload: channel, email, order number, message, attachments]
  N --> C{Email on fraud blacklist?}
  C -- yes --> D[Drop quietly, 200 OK]
  C -- no --> P[Look up customer + order]
  P --> L[LLM: intent, sentiment, urgency<br/>typed output]
  L --> T[(Create ticket in ledger)]
  T --> ACK[200 acknowledged]
  ACK --> R{Route by intent}
  R -- address update --> W2
  R -- damaged item --> W3
  R -- refund --> W4
  R -- order status --> OS{Order found?}
  OS -- yes --> SR[Status reply from order record, auto_resolved]
  OS -- no --> HO
  R -- anything else --> HO[Human handoff, escalated_to_human]
  subgraph W2[02 Address resolver]
    G[Geocode proposed address] --> GM{Geocode matched?}
    GM -- no --> AU[Order unchanged, ask customer, awaiting_customer]
    GM -- yes --> F{Order unfulfilled?}
    F -- yes --> U[Update order address, ticket auto_resolved]
    F -- no --> I[Flag carrier intercept, escalated_to_human]
  end
  subgraph W3[03 Vision damage engine]
    HP{Photo supplied?} -- no --> ES
    HP -- yes --> DL[Download photo as binary] --> V[Vision model inspects photo]
    V --> E{Damage AND confidence >= 60<br/>AND order <= $100}
    E -- yes --> RO[Create replacement order -R1<br/>record claim, auto_resolved]
    E -- no --> ES[escalated_to_human]
  end
  subgraph W4[04 Refund escalation]
    H[Evaluate threshold and options] --> SL[Slack approval card] --> LG[(Ledger)]
  end
  U & RO --> W5[05 Reconciliation + CSAT follow-up]
  Z[Any failure] -.-> DLQ[Error workflow: DLQ + alert]
```

### Stage by stage

| # | Workflow | What it does | Why it is built this way |
|---|---|---|---|
| 01 | **Intake and LTV matcher** | Validates a secret header, normalises the payload (channel, email, order number found in the message if not supplied, attachment flag), checks the fraud blacklist, loads customer and order, classifies intent and sentiment, writes a ticket (the number comes from a database sequence), acknowledges, routes. Order-status questions get a reply built from the order record; everything with no automated handler is handed to a person. | Cheap, deterministic checks run first; the LLM runs only for legitimate traffic. |
| 02 | **Address resolver** | Builds the proposed address from the customer's correction and geocodes it through Nominatim (city, state and ZIP only, not the street). A failed or empty match leaves the order unchanged and asks the customer to confirm; otherwise it branches on fulfilment status. | Changing an address on a shipped order is not an edit, it is a carrier problem, so the workflow treats the two cases differently. |
| 03 | **Vision damage engine** | Escalates straight away if there is no photo; otherwise downloads the photo, sends it to a pinned vision model as image data, and gets typed findings against a severity rubric. A three-part rule decides replace or escalate. | Only unambiguous, cheap cases are automated; ambiguity is a human's job. |
| 04 | **High-value escalation** | Computes the reason for escalation and the options (including store credit at 120% of order value) and posts a Slack approval card. | Money decisions above a threshold need a named human. |
| 05 | **Reconciliation** | Records resolution metrics (measured time from ticket creation to resolution) and a CSAT follow-up into an operations hub. | Automation needs a feedback loop or nobody knows whether it is working. |
| - | **Error handler** | Logs failures to a dead-letter table and alerts DevOps. | Unattended systems must say when they break. |

---

## Deep dives

### The intake classifier

Intent is a closed set so it can be routed on:

```jsonc
{
  "intent":    "ADDRESS_UPDATE | DAMAGED_ITEM | ORDER_STATUS | REFUND_REQUEST | GENERAL_INQUIRY",
  "sentiment": "satisfied | neutral | frustrated | hostile",
  "urgency":   "low | medium | high | critical",
  "extracted_address_correction": "string or null",
  "reasoning": "short justification"
}
```

The prompt supplies the order and customer record alongside the message, so "where is my package" is judged against the real order state. The classifier also extracts the corrected address (for example "Apt 4B") so the next workflow does not need a second model call.

### The fast path is a policy, not a prompt

The vision model returns `damage_detected`, `damage_severity` (`minor`, `moderate`, `catastrophic`, `no_damage`), `confidence_score` (0-100), an item description and its reasoning. Then an IF node, not the model, decides:

```
damage_detected == true
AND confidence_score >= 60
AND order_value <= 100
```

If all three hold, the workflow creates a replacement order (the original order number with an `-R1` suffix), records the claim, marks the ticket `auto_resolved` and tells the customer. Otherwise the ticket becomes `escalated_to_human` and the customer is told a person is reviewing it. The model supplies evidence; policy stays in logic I can read, tune and defend.

### Address resolution that knows what it cannot undo

The same customer request ("change my apartment number") means two different things depending on one fact. If the order is still `unfulfilled`, the shipping address is updated in the order table and the ticket is auto-resolved. If it has shipped, the workflow records a carrier intercept request, marks the ticket for a human and sends a different message. Treating them as one case would silently promise something the warehouse can no longer deliver.

### Why the ledger matters

Every workflow writes its outcome back to `support_tickets` with a status (`auto_resolved`, `escalated_to_human`), the action executed and a resolution time. That makes the system reviewable after the fact: how many tickets did automation close, which ones went to humans and why. Without a ledger, automation is a black box and nobody will trust it.

---

## Decision log

### Decision 1: fraud check before enrichment and before the LLM
- Blacklisted senders are dropped with a quiet `200`, so an attacker learns nothing and the system spends nothing. Cost: a lookup on every request.

### Decision 2: route on a closed intent set
- Closed enums mean a Switch can route deterministically. Cost: anything outside the five intents needs a branch (see gaps).

### Decision 3: policy in logic, evidence from the model
- Applied to the fast path and to the refund threshold. The model never holds the authority to spend money.

### Decision 4: separate workflows per specialist
- Independent testing and change. Cost: Execute Workflow links to maintain (placeholders in this export).

### Decision 5: acknowledge after the ticket exists
- The caller gets a `200` only once the ticket is recorded, so "accepted" means "will not be lost".

---

## Failure modes I designed for

| What goes wrong | What happens |
|---|---|
| Bad or missing secret | `401`, nothing else runs |
| Blacklisted customer | Dropped quietly, no lookups, no LLM cost |
| Order already shipped | Address change becomes a carrier-intercept request for a human |
| Address cannot be geocoded | Order left unchanged, ticket marked `awaiting_customer`, customer asked to confirm |
| Damage claim with no photo | Escalated for human review, no model call |
| Order-status request for an unknown order, or any other intent | Handed to a person, ticket `escalated_to_human` |
| Damage claim is ambiguous or high value | Escalated with the model's reasoning, never auto-approved |
| Any refund request | Slack approval card stating the reason (over $100, or a frustrated or hostile customer); no automatic payout |
| Any workflow throws | Error workflow logs it and alerts DevOps |

## Data model

| Table | Purpose |
|---|---|
| `support_customers` | Customer profile: email, name, lifetime value tier, spend, order count |
| `support_orders` | Order number, status (`unfulfilled`, shipped), shipping address, carrier, tracking, total |
| `support_tickets` | The ledger: ticket number, intent, sentiment, status, action executed, resolution time |
| `support_damage_claims` | Claim record: ticket, order, severity, confidence, reasoning |
| `support_fraud_blacklist` | `email_or_phone` entries to drop on sight |

Airtable holds the operations hub and the dead-letter table.

## Testing and verification

I ran the vision step by hand against real photos using the same prompt, rubric and model credential as the workflow. This is a small manual check, **not an accuracy measurement**; there is no routing or time-saved figure for this build yet.

| Image | How it was sent | Result |
|---|---|---|
| Unrelated photo (blister packs) for a "cracked diffuser" claim | URL, twice | Correctly `no_damage`, 95-98 confidence, accurate description of what was in the photo |
| Chipped teapot lid | URL | Provider error (HTTP 422), the node failed |
| Chipped teapot lid | URL, retry | Answered that the image was "completely blank and white" with 98 confidence |
| Hairline-cracked mug | URL, twice | Called it intact at 98 confidence, once describing a wooden table that is not in the photo |
| Chipped teapot lid | Base64 data URL, free router | `damage_detected`, `moderate`, 95 |
| Hairline-cracked mug | Base64 data URL, free router | `damage_detected`, `moderate`, 95, correctly describing the crack through the logo |
| Both photos | Base64 data URL, a pinned free vision model (Nemotron VL) | Both `moderate`, 95-98 |

What this shows:
- The workflow does pass the image to the model, and the model reads it when it receives it.
- When the provider has to fetch a remote URL itself, results were unreliable: an error, a "blank image", and a missed crack. When the same photos were sent inline as base64, every answer was correct.
- A confident wrong answer in the safe direction (`no_damage`) only escalates to a human under the fast-path rule, which is why that rule requires damage to be detected. A wrong answer in the other direction is the case worth a larger test.
- One of the free models I tried was rate-limited, which is the practical limit of free tiers.

### Live runs through the intake webhook (after the fixes)

These went through the real webhook on my n8n instance against seeded orders, and I read the ticket rows afterwards.

| Case | Result |
|---|---|
| Order-status question for an unfulfilled order | Ticket `auto_resolved`, `order_status_reply_prepared`, 0 s |
| Address correction ("Apt 4B") on an unfulfilled order | Geocoder matched, order updated, ticket `auto_resolved`, 1 s |
| Damage claim with no photo | `escalated_for_human_review`, reason "no photo supplied" |
| General question (shipping and returns policy) | `human_handoff`, ticket `escalated_to_human` |
| Damage claim with a photo of blister packs | Photo downloaded as a 79 KB JPEG and sent as binary to the pinned model; it answered `no_damage` at 98 confidence and described the photo accurately; the claim was escalated, as the rule requires |

Two things this found that I would otherwise have missed: the first pinned vision model had been retired by the provider (a 404 on every claim), and the node's default output limit was larger than my account balance allowed (a 402). I capped output at 800 tokens, which is plenty for the typed result.

Not run live: the geocode-failure path, the shipped-order intercept, the refund-to-Slack path with the router, and a genuine damage photo going down the fast path. The photo-handling change was checked on one photo, so it is not an accuracy measurement.

Other checks:
- [`samples/support-intake.json`](samples/support-intake.json) is a ready payload.
- Cases worth running: a damage claim under $100 (should replace), the same claim over $100 (should escalate), an address change on an unfulfilled and on a shipped order, a blacklisted email, a wrong secret.
- Pending: a labelled set of about 30 photos and messages to report real accuracy.

## What I fixed, and what is still open

I audited my own build and wrote down eight findings. This is where each one stands.

| # | Finding | Status |
|---|---|---|
| 1 | Send the photo as image data, and pin a vision model | **Fixed.** An HTTP Request node downloads the photo, the model receives binary image data, and the model is pinned (`google/gemini-2.5-flash` via OpenRouter, 800-token output cap). Checked on one photo. |
| 2 | `ORDER_STATUS` and `GENERAL_INQUIRY` had no branch | **Fixed.** Status gets a reply from the order record; unknown orders and everything else go to a person. |
| 3 | Geocoder result did not gate the update | **Fixed, with a limit.** A failed or empty match blocks the update. The lookup uses city, state and ZIP, so it catches bad localities but does not validate the street. |
| 4 | Reconciliation metrics partly assumed | **Partly fixed.** Resolution time is now measured from ticket creation, and the 12-second fallback is gone. The 12-hour human baseline and the per-intent dollar savings are still assumptions, so any "time saved" or "cost saved" figure is an estimate. |
| 5 | Refund approval depends on the shared router | **Documented, not tested.** A note on the workflow explains the dependency; I have not run a refund through to a Slack button click. |
| 6 | Secrets in nodes | **Partly fixed.** The Slack token now comes from an n8n credential. The intake shared secret is still a literal in an IF node (stripped from this export); it should become a Header Auth credential on the webhook. |
| 7 | Ticket numbers were random | **Fixed.** A database sequence fills them (`sql/ticket-number-sequence.sql`). |
| 8 | Demo defaults in code | **Fixed.** The default photo, placeholder customer name and fixed product text are gone. |

Still open:
- **A dead photo URL fails the run.** If the download fails, the workflow errors into the dead-letter queue instead of escalating the claim.
- **Notices are formatted, not sent.** Customer messages are built and logged by Set nodes; there is no email or chat send step in this build.
- **No accuracy measurement.** A labelled set of about 30 photos and messages is still needed before any accuracy claim.
- **The fast-path replacement branch** has not been run live with a genuine damage photo since the changes.

## Configuration and swap-ins

Everything below is a setting or a node swap, not a rewrite. The defaults are what I used because they are free or cheapest, not because the design needs them.

| What | Default here | Change it to |
|---|---|---|
| **Intent LLM** | OpenRouter free router | Any chat model. |
| **Vision model** | Pinned `google/gemini-2.5-flash` via OpenRouter, 800-token output cap | Any vision-capable model (GPT-4o class, Claude, Gemini, a local LLaVA-style model). Pin one specific model for consistent results, and re-check it is still served: my first pin was retired by the provider. |
| **Fast-path rule** | Damage detected, confidence at least 60, order value at most $100 | Any thresholds, or add conditions such as customer tier or number of prior claims. One IF node. |
| **Refund escalation** | Above $100, or a frustrated or hostile customer | Any threshold. Store credit is offered at 120% of order value; change the multiplier. |
| **Fraud list** | Supabase table | Any lookup, or a fraud service API. |
| **Geocoder** | OpenStreetMap Nominatim | Google, Mapbox, Smarty, Loqate. |
| **Database** | Supabase | Postgres, Airtable, MySQL. |
| **Approvals** | Slack | Discord, Teams, email. |
| **Store, products and notice text** | Fictional home-goods brand | Your own brand, policies and wording. |
| **Intents** | Five | Add intents and routes (for example subscription changes). |

## What this demonstrates

- **Policy-as-logic around an LLM**: explicit thresholds govern anything that moves money.
- **Multi-modal AI** used as an evidence source, with a typed schema, confidence scoring and a hands-on test of where it fails.
- **Defensive intake design**: authentication, fraud screening, early acknowledgement.
- **Modular orchestration** across a relational ledger, geocoding, chat approvals and an operations hub.
- **Auditability**: a ticket ledger recording what the system did and why.
- **Self-audit**: a written defect list with fixes.

## Repository

| File | Purpose |
|---|---|
| [`workflows/01-omnichannel-intake-ltv-matcher.json`](workflows/01-omnichannel-intake-ltv-matcher.json) | Intake, auth, fraud check, enrichment, classification, routing |
| [`workflows/02-logistics-address-resolver.json`](workflows/02-logistics-address-resolver.json) | Address validation, update or carrier intercept |
| [`workflows/03-vision-damage-warranty-engine.json`](workflows/03-vision-damage-warranty-engine.json) | Vision inspection, fast-path replacement or escalation |
| [`workflows/04-financial-escalation-slack-hitl.json`](workflows/04-financial-escalation-slack-hitl.json) | Refund threshold, options, Slack approval card |
| [`workflows/05-ops-reconciliation-csat.json`](workflows/05-ops-reconciliation-csat.json) | Resolution metrics and CSAT follow-up |
| [`workflows/06-error-handler-dlq.json`](workflows/06-error-handler-dlq.json) | Global error handler |
| [`samples/support-intake.json`](samples/support-intake.json) | Sample request payload |
| [`sql/ticket-number-sequence.sql`](sql/ticket-number-sequence.sql) | Sequence and default for ticket numbers |

### Run it yourself
1. Import the six workflows into n8n. All are exported **inactive**.
2. Replace every placeholder (`REPLACE_ME`, `YOUR_*`, `WORKFLOW_ID_*`), re-select credentials, and re-link the Execute Workflow nodes and the Error workflow setting to your imported copies.
3. Create the five Supabase tables, then run [`sql/ticket-number-sequence.sql`](sql/ticket-number-sequence.sql) for sequential ticket numbers. Create the Airtable operations hub, and a Header Auth credential for the Slack bot token (`Authorization: Bearer ...`). Import the interactions router from the incident repo for Slack buttons.
4. Edit the sample so `attachments` holds a public image URL (the placeholder in it does not resolve), then send it:

   ```bash
   curl -X POST https://YOUR-N8N-HOST/webhook/support-intake \
     -H "X-Lumina-Secret-Key: YOUR_SHARED_SECRET" \
     -H "Content-Type: application/json" \
     -d @samples/support-intake.json
   ```

Brand names, people and addresses are fictional demo data. MIT licensed.

*Built by Laurence. Part of a set of four automation case studies: [lead gen and outreach](https://github.com/lomonol/n8n-lead-gen-outreach-engine), [incident platform](https://github.com/lomonol/n8n-ai-incident-escalation-platform), [lead waterfall](https://github.com/lomonol/n8n-lead-waterfall-engine).*
