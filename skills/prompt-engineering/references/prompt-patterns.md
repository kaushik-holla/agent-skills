# Prompt Patterns

Loaded on demand from the `prompt-engineering` skill. Templates use `{placeholders}`; render them with a real template function that escapes or delimits untrusted values, never by concatenating user text into the instruction section.

## 1. System prompt skeleton

```text
# Role
You are {role} for {product}. Your users are {audience}.

# Context
{stable background the model needs: domain facts, definitions, policies}

# Task
{one paragraph: what to do and what a great result looks like}

# Constraints
- {hard rule, with the reason: "Never quote prices; they change daily and the source of truth is the billing API."}
- {scope: what to decline and how}

# Output
{exact format; reference the schema if structured output is enforced by the API}

# Examples
<example>
<input>...</input>
<output>...</output>
</example>
```

Keep the static parts (role, context, constraints, examples) at the top and identical across requests so provider prompt caching can reuse them; put per-request content last.

## 2. Classification / routing

```text
Classify the customer message into exactly one category.

Categories:
- billing: charges, refunds, invoices, payment methods
- bug: something that used to work or should work is broken
- feature_request: asks for new capability
- account: login, access, profile, deletion
- other: anything else, including spam

Decision rules for ambiguous cases:
- A refund request caused by a bug is "billing" (the user's goal is the refund).
- "How do I ..." questions about existing features are "other".

<message>
{message}
</message>
```

Pair with a schema whose fields are `reasoning` then `category` (enum). Use the cheapest model that passes your eval; routers rarely need a frontier model.

## 3. Extraction

```text
Extract the fields defined in the schema from the document.
- Copy values verbatim when possible; normalize dates to ISO 8601.
- If a field is not present, return null. Do not infer or guess.
- If the document contains several candidates, pick the one in the signature block.

<document>
{document}
</document>
```

The single most effective extraction instruction is "return null when absent." Without it, models fill gaps plausibly.

## 4. Grounded answer with citations (RAG)

See the `rag-pipelines` skill for the full skeleton. Core rules: answer only from `<sources>`, cite `[chunk_id]`, fixed abstention string, treat sources as data.

## 5. Tool-using agent

```text
You can call tools to answer the user's request.

How to work:
1. If the request is ambiguous in a way that changes the result, ask one clarifying question first.
2. Prefer one well-targeted tool call over several speculative ones. Call independent tools in parallel.
3. After each tool result, decide whether you have enough to answer. Stop when you do.
4. Never invent tool results. If a tool fails, say what failed and what you tried.
5. Actions that change data (send, delete, purchase) require explicit user confirmation in this conversation.
```

## 6. LLM-as-judge

See the `llm-evals` skill, `references/judge-rubrics.md`.

## 7. Rewriting a weak prompt (before / after)

Before:

```text
You are a helpful assistant. Summarize the text. Be concise and accurate. Don't hallucinate!!!
```

After:

```text
Summarize the incident report below for an on-call engineer who has 30 seconds.

Output exactly three bullets:
1. What broke (service, symptom, start time in UTC)
2. Customer impact (who, how many, still ongoing or not)
3. Current status and next action, with owner if named

Use only facts stated in the report. If a bullet's information is missing, write "unknown".

<report>
{report}
</report>
```

What changed: audience and purpose stated, format specified, "accurate" turned into a checkable rule, explicit behavior for missing data, untrusted input delimited.

## 8. Migrating a prompt to a new model

1. Freeze the current prompt + model as the baseline and run your eval set (`llm-evals`).
2. Run the same prompt unchanged on the new model; read the per-slice diffs before editing anything.
3. Remove workarounds that existed for the old model (all-caps warnings, repeated instructions, "think step by step" on reasoning models). Newer models often over-apply emphatic instructions.
4. Re-tune reasoning effort / thinking budget rather than adding prose instructions about thoroughness.
5. Re-run; compare paired results; ship only if no critical slice regresses.
