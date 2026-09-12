# codex-multi-agent-config

**Make your OpenAI Plus subscription last longer: let the premium model plan, let free local models do the heavy IO.**

Every Codex turn burns your plan's quota — and most of those tokens never needed a premium model. Reading files, mapping a codebase, writing boilerplate, running tests and waiting on them: that's the bulk of a typical session, and it's exactly the kind of work a free 4B model running on your own GPU can do.

This repo ships a working setup that splits the work:

```
        you
         │
   ┌─────▼──────────────────────────────────────────┐
   │  main thread — your Plus model (Astra/gpt-5.6) │
   │  plans, decides, writes the final answer       │
   └─────┬───────────────────────────────┬──────────┘
         │ delegate_ollama_agent(task)   │ (optional) spawn_agent
         ▼                               ▼
   ┌─────────────────────┐    ┌──────────────────────────┐
   │ local Ollama worker │    │ OpenAI sub-agents        │
   │ headless codex exec │    │ (gpt-5.6-terra roles)    │
   │ on your GPU — FREE  │    │ burns quota, best quality│
   └─────────────────────┘    └──────────────────────────┘
```

- **Main thread (premium):** requirements, decisions, architecture, final synthesis. This is what your subscription is actually for.
- **IO workers (free):** file reads, codebase exploration, repetitive file writing, running CLIs/builds/tests and summarizing their output. Runs on Ollama, costs **zero** quota.

In our testing, a single delegated file-read round trip took ~40 seconds on a `qwen3.5:4b` local worker — work that would have burned tens of thousands of premium tokens if the main thread had done it inline. A read-heavy exploration session (the kind that eats a 5-hour Plus window) is exactly where this pays off.

## What's in the box

| File | Purpose |
|---|---|
| `mcp/ollama_bridge.py` | Zero-dependency MCP server that runs headless `codex exec` workers on local Ollama models |
| `AGENTS.md` | **Max-savings policy** — all IO delegated to local workers (default) |
| `AGENTS.hybrid.md` | Hybrid policy — OpenAI sub-agents when quota is healthy, local workers as automatic fallback |
| `agents/*.toml` | Codex agent roles for the hybrid mode (`explorer`/`worker`/`default` on gpt-5.6-terra) |
| `config-snippet.toml` | The `[mcp_servers.ollama_bridge]` block for your `config.toml` |
| `install.sh` | Copies everything into `~/.codex/` and registers the MCP server |

## Quick start

Prerequisites: [Ollama](https://ollama.com) installed and running, Codex (desktop app or CLI), Python 3.

```bash
git clone https://github.com/impacte-tech/codex-multi-agent-config
cd codex-multi-agent-config
./install.sh              # max-savings mode (or: ./install.sh --mode hybrid)
ollama pull qwen3.5:4b    # the default worker
```

Then **fully restart Codex** (quit the desktop app / end CLI sessions — config loads at startup).

## Using it

Just ask for IO work normally. The policy in `AGENTS.md` tells the main thread to delegate:

- *"Explore how auth is handled in this repo and report the entry points"* → read-only local worker
- *"Write the boilerplate test files for these 5 modules"* → workspace-write local worker
- *"Run the test suite and summarize what fails"* → worker that waits and reports

Steer it explicitly when you want:

- *"Delegate that to lfm2.5:8b"* — pick the worker model
- *"Run two delegations in parallel"* — independent tasks at once

Watch it work in a terminal with `ollama ps` — you'll see the worker model loaded in VRAM. That's your quota staying untouched.

## Why an MCP bridge? (the part that cost us a day)

You'd think native sub-agents could do this. They can't — and it's deliberate:

- `spawn_agent` sub-agents **always inherit the parent session's model provider**. On a Plus account that's the OpenAI backend, which rejects any non-OpenAI model slug (`The 'qwen3.5:4b' model is not supported when using Codex with a ChatGPT account`).
- Custom agent files (`~/.codex/agents/*.toml`) are applied as **bounded role overrides**: only `model`, `model_reasoning_effort`, `developer_instructions`, personality, service tier, features and skills survive. There is no `model_provider` in the whitelist (verified in Codex ≥ 0.15x source; older 0.145-era builds were more permissive).
- The model catalog has no provider field either.

So there is no supported way to point a spawned sub-agent at a local provider. The bridge sidesteps the whole question: it's an MCP tool the main thread calls, and each call spawns a **headless `codex exec`** that runs as its own main thread on the `ollama` provider — a full agent loop with shell/file tools, for free.

## Gotchas we hit (so you don't have to)

- **`codex exec` reads stdin when it's not a TTY.** Spawned from an MCP server, it inherits the MCP pipe and blocks forever waiting for EOF. The bridge passes `stdin=DEVNULL`. Symptom: worker finishes its task in seconds, then hangs until timeout with no session log.
- **Workers must run with `mcp_servers={}`** — otherwise each worker loads your MCP servers again (slow startup, a shutdown deadlock, and recursive delegation).
- **`tool_timeout_sec = 1800`** on the MCP server: Codex's default per-tool timeout will kill long delegations.
- **`wire_api = "chat"` is rejected** by recent Codex. Don't define a custom ollama provider at all — `ollama` is a reserved built-in provider id; the bridge just passes `-c model_provider=ollama`.
- **Recursion guard**: workers get an env flag so a worker can never delegate to another worker.
- **First call per model is slow** (weights into VRAM); Ollama unloads idle models after ~5 min, so expect a reload after pauses.
- **Sandbox**: workers inherit the sandbox you pass (`read-only` for reads, `workspace-write` for writes). On Linux, Codex's bubblewrap sandbox needs user namespaces — if your environment lacks them, pass `sandbox: "danger-full-access"` per call.

## Modes

**Max savings (default `AGENTS.md`)** — every mechanical task goes to a local worker. Your premium tokens are spent only on planning and the final synthesis. Best quota stretch; worker quality is local-model quality.

**Hybrid (`AGENTS.hybrid.md`)** — OpenAI sub-agents (`gpt-5.6-terra` roles) do the IO when your quota is healthy, and the policy instructs an automatic switch to local workers when delegations start failing on usage limits. Better output quality, quota still protected by the fallback.

Switching is one file: `cp AGENTS.hybrid.md ~/.codex/AGENTS.md` (or let `install.sh --mode hybrid` do it).

## Tested with

- Codex desktop app (bundled CLI 0.153.4) and Codex CLI 0.145.0
- Ollama 0.32.9 with `qwen3.5:4b` and `lfm2.5:8b` (any tool-capable Ollama model works)
- Linux; the bridge is plain Python 3 stdlib — no dependencies

## Honest limitations

- Local workers are **weaker and slower** than your Plus model. They're for mechanical IO — reads, boilerplate, test runs — not for design decisions. The policy keeps planning and final synthesis on the premium model.
- Bridge workers run as separate processes, so they don't appear as sub-agent threads in the app UI; you'll see their activity via `ollama ps` and the tool result in the chat.
- Delegations still consume your machine's GPU/RAM, not your quota.

## License

MIT — do whatever you want with it.