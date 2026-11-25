# Cost control for n8n AI workflows

Tokens per request roughly = system message + tool descriptions + memory window + user
message + every tool result, **times the number of agent iterations**, plus output tokens.

## Levers, biggest first

| Lever | Typical effect | How in n8n |
| --- | --- | --- |
| Do not use an agent when a chain will do | 1 call instead of 2-6 | Basic LLM Chain + Structured Output Parser |
| Smaller model for routine requests | Often 5-20x cheaper per token | Pick a "mini/haiku/flash" class model; route hard cases to a bigger model |
| Limit iterations | Caps runaway loops | AI Agent > Options > Max Iterations 5-10 |
| Trim tool output | Tool results are resent every iteration | Set node in the tool sub-workflow returning only needed fields |
| Shorter memory window | Linear in turns | Window 5-10; summarise older turns if needed |
| Max output tokens | Caps long answers | Chat model > Options > Maximum Number of Tokens |
| Fewer, better tools | Tool descriptions are in every call | Merge overlapping tools; split domains into sub-agents |
| Cache repeated answers | Avoids calls entirely | Look up a cache table (question hash -> answer, TTL) before the agent |
| Low temperature | Fewer retries from malformed tool calls | 0-0.3 for tool use |

## Public chat bots

- Require authentication on the Chat Trigger, or embed it behind your own logged-in app.
- Per-session limit: count messages per `sessionId` (Postgres/Redis) at the start of the
  workflow; after N messages answer with a fixed text, without calling the model.
- Per-IP limits at the reverse proxy (see `n8n-webhook-security`).
- Cap input length (e.g. 2,000 characters) before the agent.

## Measure

- Most chat model nodes return token usage in their output or in the execution data; log
  `workflow, sessionId, model, input_tokens, output_tokens` to a table per execution.
- A daily Schedule workflow sums tokens per workflow and alerts above a budget.
- Set spending limits and alerts in the provider dashboard as the last line of defence.

## Quick estimate

```
calls_per_request  = iterations (agent) or 1 (chain)
tokens_per_call    = system + tools + memory_turns * avg_turn + input + avg_tool_result
cost_per_request   = calls_per_request * tokens_per_call * input_price
                   + output_tokens * output_price
```

Use the provider's current price list; prices change often, so do not hard-code them in
workflows or docs.
