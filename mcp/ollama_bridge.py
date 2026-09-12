#!/usr/bin/env python3
"""ollama_bridge: MCP stdio server that delegates IO tasks to local Ollama
models via headless `codex exec` runs.

Why: Codex >= 0.15x restricts spawned sub-agents to the parent session's
model provider, so a ChatGPT-authenticated main thread (e.g. gpt-6-astra)
cannot spawn sub-agents on the local `ollama` provider. This bridge gives
the main thread a tool that runs a full agent loop on a local Ollama model
and returns the result.

Zero dependencies: implements the MCP stdio protocol (newline-delimited
JSON-RPC 2.0) directly. Logs go to stderr; stdout is reserved for protocol.
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request

SERVER_NAME = "ollama-bridge"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"
OLLAMA_API = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_TIMEOUT = 900
CODEX_BIN = os.environ.get("OLLAMA_BRIDGE_CODEX", "codex")

TOOLS = [
    {
        "name": "delegate_ollama_agent",
        "description": (
            "Delegate a bounded IO task to a headless Codex agent running on a "
            "local Ollama model (reads files, writes repetitive files, runs CLI "
            "commands/tools, waits on builds/tests and reports). The worker is a "
            "full agent with shell/file tools, so give it a self-contained task: "
            "exact paths, what to do, and what to report. Returns the agent's "
            "final report. Use for mechanical IO work; keep planning on the main "
            "thread. Independent tasks can be delegated in parallel calls."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Fully self-contained task description: absolute paths, exact actions, expected report format.",
                },
                "model": {
                    "type": "string",
                    "description": f"Ollama model id (default: {DEFAULT_MODEL}).",
                    "default": DEFAULT_MODEL,
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory for the agent. Pass the repo/project path.",
                },
                "sandbox": {
                    "type": "string",
                    "enum": ["read-only", "workspace-write", "danger-full-access"],
                    "description": "Sandbox mode (default: workspace-write). Use read-only for pure inspection.",
                    "default": "workspace-write",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": f"Max runtime in seconds (default: {DEFAULT_TIMEOUT}).",
                    "default": DEFAULT_TIMEOUT,
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "list_ollama_models",
        "description": "List locally available Ollama model ids usable with delegate_ollama_agent.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def log(msg):
    print(f"[ollama_bridge] {msg}", file=sys.stderr, flush=True)


def list_ollama_models():
    try:
        with urllib.request.urlopen(f"{OLLAMA_API}/api/tags", timeout=5) as r:
            data = json.load(r)
        return sorted(m["name"] for m in data.get("models", []))
    except Exception as exc:
        return f"error contacting Ollama at {OLLAMA_API}: {exc}"


def run_agent(args):
    task = (args.get("task") or "").strip()
    if not task:
        return "error: 'task' is required", True

    # Recursion guard: workers must not delegate further.
    if os.environ.get("OLLAMA_BRIDGE_WORKER") == "1":
        return (
            "error: nested delegation is disabled. This is already an Ollama "
            "worker; do the IO work directly with your own tools.",
            True,
        )

    model = args.get("model") or DEFAULT_MODEL
    cwd = args.get("cwd") or os.getcwd()
    sandbox = args.get("sandbox") or "workspace-write"
    timeout_sec = int(args.get("timeout_sec") or DEFAULT_TIMEOUT)

    if not os.path.isdir(cwd):
        return f"error: cwd does not exist: {cwd}", True

    # Some models (e.g. gemma4) habitually pass `justification` on exec calls;
    # Codex rejects that unless paired with sandbox_permissions, and escalation
    # is unavailable in headless runs (approval policy is Never). Tell workers
    # to issue plain exec calls instead.
    worker_prompt = (
        task + "\n\nExecution notes: you run headless with full access and no "
        "approval prompts. Issue plain exec_command calls — do NOT pass "
        "`justification` or `sandbox_permissions` arguments; escalation is "
        "unavailable and never needed."
    )

    available = list_ollama_models()
    if isinstance(available, str):
        return f"error: {available}", True
    if model not in available:
        return (
            "error: model '{}' not available in Ollama. Available models:\n{}".format(
                model, "\n".join(available)
            ),
            True,
        )

    with tempfile.TemporaryDirectory(prefix="ollama-bridge-") as tmpdir:
        last_message_path = os.path.join(tmpdir, "last-message.txt")
        cmd = [
            CODEX_BIN,
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            sandbox,
            "--output-last-message",
            last_message_path,
            # Workers do IO with built-in tools only: no MCP servers (they are
            # slow to start, can cause a shutdown deadlock, and would allow
            # recursive delegation), no sub-agents.
            "-c",
            "mcp_servers={}",
            "-c",
            "agents.enabled=false",
            "-c",
            "model_provider=ollama",
            "-m",
            model,
            worker_prompt,
        ]
        log(
            f"dispatching model={model} cwd={cwd} sandbox={sandbox} timeout={timeout_sec}s"
        )
        worker_env = dict(os.environ)
        worker_env["OLLAMA_BRIDGE_WORKER"] = "1"
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env=worker_env,
                # codex exec reads stdin when it is not a TTY; without this it
                # would block forever on the MCP pipe this server is reading.
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return (
                f"error: agent timed out after {timeout_sec}s. Task may be too large; "
                "split it into smaller delegations or raise timeout_sec.",
                True,
            )
        except FileNotFoundError:
            return f"error: codex binary not found at '{CODEX_BIN}'", True

        final = ""
        try:
            with open(last_message_path, encoding="utf-8") as f:
                final = f.read().strip()
        except OSError:
            pass

        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-2000:].strip()
            msg = f"agent exited with code {proc.returncode}"
            if final:
                msg += f"\n\nLast message:\n{final}"
            if tail:
                msg += f"\n\nstderr/stdout tail:\n{tail}"
            return msg, True

        if not final:
            final = (proc.stdout or "")[-4000:].strip() or "(no output)"
        return final, False


def handle_call(name, args):
    if name == "delegate_ollama_agent":
        return run_agent(args or {})
    if name == "list_ollama_models":
        result = list_ollama_models()
        if isinstance(result, str):
            return result, True
        return "\n".join(result), False
    return f"error: unknown tool '{name}'", True


def main():
    log("starting (ollama at {}), default model {}".format(OLLAMA_API, DEFAULT_MODEL))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            log(f"bad JSON received: {exc}")
            continue

        method = msg.get("method")
        msg_id = msg.get("id")
        is_notification = msg_id is None

        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
            reply = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        elif method == "notifications/initialized":
            continue
        elif method == "ping":
            reply = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        elif method == "tools/list":
            reply = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            params = msg.get("params") or {}
            text, is_error = handle_call(params.get("name"), params.get("arguments"))
            reply = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": is_error,
                },
            }
        elif method is None:
            continue
        else:
            if is_notification:
                continue
            reply = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }

        if not is_notification:
            print(json.dumps(reply), flush=True)


SERVER_VERSION = "1.0.0"

if __name__ == "__main__":
    main()
