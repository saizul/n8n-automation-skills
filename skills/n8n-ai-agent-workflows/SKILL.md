---
name: n8n-ai-agent-workflows
description: Build and review n8n AI Agent workflows - when to use an agent vs a Basic LLM Chain or plain nodes, system prompts, tool design (Call n8n Workflow Tool, HTTP Request Tool, app tools) with clear names, descriptions and input schemas, memory (Simple/Window, Postgres, Redis) and per-user session keys, structured output parsers, human approval for tools with side effects, guardrails, and cost control (model choice, max tokens, maxIterations, memory window, public chat limits). Includes an audit script that maps each agent's model, memory and tools in an exported workflow and flags missing system messages, runaway iteration limits, tools without descriptions, shared memory sessions, missing output parsers, destructive tools and unbounded token use. Use when the user builds a chatbot, AI agent, RAG or LLM automation in n8n, asks why the agent picks the wrong tool, loops, forgets context, mixes up users, or costs too much.
license: MIT
compatibility: Python 3.8+ standard library only; reads exported workflow JSON. Node parameter names follow the n8n LangChain nodes (@n8n/n8n-nodes-langchain).
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# n8n AI agent workflows

An agent is a loop: the model reads the system prompt, memory and tool descriptions,
picks a tool, reads the result, repeats until it answers or hits maxIterations. Most
problems come from vague tools, shared memory and missing limits.

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- Building a support bot, internal assistant, email/ticket triage or RAG flow in n8n
- "The agent calls the wrong tool / no tool / loops until max iterations"
- "It forgets the conversation" or "users see each other's history"
- LLM bills are higher than expected, or a public chat bot is exposed
- Getting reliable JSON out of the model for downstream nodes

## When not to use

- Plain workflow structure, retries, webhooks: `n8n-workflow-design`, `n8n-error-handling`,
  `n8n-webhook-security`
- Choosing between model providers on price alone: check the providers' current pricing pages

## Workflow

1. **Decide if it needs an agent** (decision list in
   [references/agent-patterns.md](references/agent-patterns.md#agent-or-not)):
   fixed steps -> normal nodes; one model call (classify, extract, summarise) -> Basic LLM
   Chain or the provider node with a Structured Output Parser; open-ended requests that
   need to choose among tools -> AI Agent.
2. **Audit an existing workflow** (read-only):
   ```bash
   python3 scripts/agent_audit.py wf.json
   python3 scripts/agent_audit.py workflows/ --json --fail-on high
   python3 scripts/agent_audit.py wf.json --max-iterations 8 --max-window 10
   ```
   The header line per agent shows model, memory, tools and maxIterations.
3. **Write the system message** with the template in
   [references/agent-patterns.md](references/agent-patterns.md#system-message): role, scope,
   which tool for which question, what to do when a tool fails, refusal rules, output format.
4. **Design the tools.** One job per tool, `verb_noun` names, a description that says when
   to use it, the input fields with an example and what it returns. Prefer "Call n8n
   Workflow Tool" sub-workflows that validate input and return small JSON. Tools that
   delete, pay, refund, email customers or change records get a human approval step (a
   "send and wait for approval" node in the tool sub-workflow, or the tool only creates a
   draft). See [references/agent-patterns.md](references/agent-patterns.md#tools).
5. **Set memory per conversation.** Session key from the chat/session id
   (`={{ $json.sessionId }}`), never a fixed string. Window 5-10 turns; Postgres or Redis
   memory for production (survives restarts, works with multiple workers).
6. **Bound the cost** with [references/cost-control.md](references/cost-control.md):
   small model first, max output tokens, maxIterations 5-10, short tool outputs, memory
   window, per-session message limits on public chats, caching of repeated lookups.
7. **Test with a fixed eval set**: 10-20 real questions including edge cases (off-topic,
   prompt injection in user input, tool failure, ambiguous request). Record which tool was
   called (enable "Return Intermediate Steps" while testing) and the answer; re-run after
   every prompt or tool change.
8. **Re-run the audit** and report the changes and remaining risks.

## Output

```
Agent "AI Agent" (Support chatbot): model gpt-4o, memory Window(100), tools: HTTP Request, Delete customer
HIGH  HTTP Request tool has no description -> rename get_customer, describe input {email}
HIGH  (if fixed session key) all users share memory -> sessionKey ={{ $json.sessionId }}
MED   default system message -> drafted below
MED   maxIterations 50 -> 8
MED   Delete customer: destructive -> approval step in the tool sub-workflow
MED   public chat without auth -> Basic Auth + 20 messages/session limit
LOW   no max tokens, temperature 1.4, memory 100 turns -> 800 tokens, 0.2, 8 turns
Estimated effect: ~60-80% fewer tokens per conversation (shorter history + no loops)
```
