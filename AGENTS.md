# Global agent instructions

## Delegation policy: local Ollama workers for IO, main thread for planning

The main thread is the planner. It owns requirements, decisions, architecture,
and the final synthesis shown to the user. Everything mechanical is delegated
to free local workers so the premium model's quota is spent only where it
matters.

Delegate ALL mechanical IO work through the **ollama_bridge** MCP server.
Call its tools directly:

- `delegate_ollama_agent(task, model?, cwd?, sandbox?, timeout_sec?)` — runs a
  headless Codex agent on a local Ollama model and returns its final report.
  Always pass `cwd` (the project path) and a self-contained `task`:
  exact absolute paths, exact actions, and the expected report format.
- `list_ollama_models()` — lists usable local model ids.

Use it for:

- Reading files and exploring code (`sandbox: "read-only"`)
- Writing repetitive or boilerplate files (`sandbox: "workspace-write"`)
- Running CLI commands, builds, tests, linters; waiting on them and summarizing
  output (`sandbox: "workspace-write"`)

Model guidance (pass via `model`):

- `gemma4:e4b` — default; 8B worker with 128k context, good balance of quality and speed
- `qwen3.5:4b` — lighter/faster option for simple reads
- `lfm2.5:8b` — alternative strong worker for implementation-style tasks

Rules:

- One bounded task per call. Independent tasks: make parallel calls.
- Keep noisy intermediate output (file dumps, logs, search results) inside the
  worker; only its final report returns to the main thread.
- Do not delegate: planning, architecture decisions, user communication,
  security-sensitive approvals, or tasks requiring the strongest reasoning.
- If a delegation fails or times out, split the task smaller or raise
  `timeout_sec`.
- If the bridge is unavailable (Ollama down), do the task yourself and say so.