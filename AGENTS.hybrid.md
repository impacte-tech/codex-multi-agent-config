# Global agent instructions

## Delegation policy: sub-agents for IO, main thread for planning

The main thread is the planner. It owns requirements, decisions, architecture,
and the final synthesis shown to the user.

Delegate all mechanical IO work to sub-agents via `spawn_agent` (with
`fork_turns = "none"` and a fully self-contained task: exact paths, exact
actions, expected report format):

- `explorer` — reading files, exploring code, gathering evidence (read-only,
  gpt-5.6-terra)
- `worker` — writing repetitive/boilerplate files, running CLI commands,
  builds, tests, linters; waiting on them and summarizing output
  (gpt-5.6-terra)
- `default` — general bounded IO tasks (gpt-5.6-terra)

Rules:

- One bounded task per spawn. Independent tasks: spawn in parallel, then wait
  for all results before synthesizing.
- Keep noisy intermediate output (file dumps, logs, search results) inside
  sub-agents; only their summaries return to the main thread.
- Do not delegate: planning, architecture decisions, user communication,
  security-sensitive approvals, or tasks requiring the strongest reasoning.
- For heavier delegated work, request `model = "gpt-5.6-sol"` on the spawn.
- If a spawn fails or times out, split the task smaller; do not silently do
  heavy IO yourself unless delegation is unavailable.

## Fallback: local Ollama workers (ollama_bridge MCP server)

If OpenAI delegations fail due to account usage limits or quota, delegate the
same bounded IO tasks through the **ollama_bridge** MCP server instead:

- `delegate_ollama_agent(task, model?, cwd?, sandbox?, timeout_sec?)` — runs a
  headless Codex agent on a local Ollama model and returns its final report.
  Always pass `cwd` and a self-contained `task`.
- `list_ollama_models()` — lists usable local model ids.

Local workers are slower and weaker; use them only when the account path is
unavailable or the user explicitly asks for local models.