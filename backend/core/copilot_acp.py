"""GitHub Copilot CLI — Agent Client Protocol (ACP) transport.

Talks to `copilot --acp` over stdio using newline-delimited JSON-RPC 2.0.
This bypasses the Windows argv limit that cripples the classic `copilot -p`
transport (see call_cli_provider in llm_providers.py).

Design decisions for the initial cut:

* One `ACPClient` instance per process (module-level singleton). The subprocess
  is spawned lazily on the first call and reused for the lifetime of the
  backend. If it dies we transparently respawn on the next call.

* One ACP *session* per turn — create → prompt → drop. Synapse already builds
  `full_prompt` with the entire conversation history baked in, so relying on
  Copilot's server-side session memory would double-count context. Fresh
  session per turn keeps semantics identical to the classic transport.

* Copilot's native tools are disabled (`--available-tools=""`, `--allow-all`,
  `--no-ask-user`) so it can only produce text. Synapse's XML `<tool_call>`
  scaffolding inside `full_prompt` continues to work unchanged, and there are
  no permission prompts to handle.

* All streamed text chunks (`agent_message_chunk`) are collected into a single
  string and returned when the `session/prompt` request resolves. The engine
  is not streaming-aware yet, so we surface the same blob-response contract
  as `call_cli_provider`.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from typing import Any


# Protocol/framing constants
_ACP_PROTOCOL_VERSION = 1
_LAUNCH_TIMEOUT_SEC = 20.0        # how long to wait for `initialize` to answer
_PROMPT_TIMEOUT_SEC = 300.0       # how long to wait for a single turn


class ACPError(RuntimeError):
    """Raised for any ACP-level failure (framing, RPC error, timeout)."""


class ACPClient:
    """Persistent JSON-RPC client for a single `copilot --acp` subprocess."""

    # module-level singleton
    _instance: "ACPClient | None" = None
    _instance_lock = asyncio.Lock()

    @classmethod
    async def get(cls) -> "ACPClient":
        async with cls._instance_lock:
            if cls._instance is None or not cls._instance.is_alive():
                cls._instance = ACPClient()
                await cls._instance._start()
        return cls._instance

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._client_request_handlers: dict[str, Any] = {}
        self._next_id: int = 1
        # One in-flight `session/prompt` at a time — Copilot is single-threaded
        # per session, and we create a fresh session per turn anyway.
        self._prompt_lock = asyncio.Lock()
        self._current_prompt_buf: list[str] = []
        self._current_prompt_session_id: str | None = None
        # Cached from initialize response (informational only)
        self.agent_info: dict = {}
        self.agent_capabilities: dict = {}

    # ── lifecycle ───────────────────────────────────────────────────────────

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def _start(self) -> None:
        """Spawn `copilot --acp`, run initialize, cache capabilities."""
        # Locate the copilot binary; on Windows it's a .cmd shim.
        executable = shutil.which("copilot")
        if not executable:
            raise ACPError("`copilot` binary not found in PATH.")

        # `--acp` puts Copilot in JSON-RPC server mode.
        # `--allow-all --no-ask-user --available-tools=""` disables Copilot's
        # native tools so it only produces text; Synapse handles tools via
        # the XML scaffolding already baked into `full_prompt`.
        cmd_args = [
            "--acp",
            "--allow-all",
            "--no-ask-user",
            "--available-tools=",
        ]

        if sys.platform == "win32" and executable.lower().endswith((".bat", ".cmd")):
            spawn_argv = ["cmd.exe", "/c", executable, *cmd_args]
        else:
            spawn_argv = [executable, *cmd_args]

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *spawn_argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise ACPError(f"Failed to spawn copilot ACP: {e}") from e

        assert self._proc.stdin and self._proc.stdout and self._proc.stderr

        self._reader_task = asyncio.create_task(
            self._read_loop(), name="acp-reader"
        )
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(), name="acp-stderr"
        )

        # Handshake
        init_result = await asyncio.wait_for(
            self._request(
                "initialize",
                {
                    "protocolVersion": _ACP_PROTOCOL_VERSION,
                    "clientCapabilities": {
                        # We don't expose FS ops from the client side; Copilot's
                        # native tools are disabled anyway, so no callbacks.
                        "fs": {"readTextFile": False, "writeTextFile": False},
                    },
                },
            ),
            timeout=_LAUNCH_TIMEOUT_SEC,
        )
        self.agent_info = init_result.get("agentInfo", {})
        self.agent_capabilities = init_result.get("agentCapabilities", {})
        print(
            f"[acp] connected to {self.agent_info.get('name', '?')} "
            f"v{self.agent_info.get('version', '?')} "
            f"(protocol {init_result.get('protocolVersion', '?')})",
            flush=True,
        )

    async def close(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), timeout=3.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                self._proc.kill()
            except Exception:
                pass
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
        self._proc = None
        # Fail any still-pending requests
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ACPError("ACP subprocess closed"))
        self._pending.clear()

    # ── framing / io ────────────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        buf = b""
        try:
            while True:
                chunk = await self._proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.rstrip(b"\r")
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except Exception as e:
                        print(f"[acp] BAD FRAME: {e} :: {line[:200]!r}", flush=True)
                        continue
                    self._dispatch(msg)
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"[acp] reader crash: {e}", flush=True)

    async def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        try:
            while True:
                chunk = await self._proc.stderr.read(4096)
                if not chunk:
                    return
                text = chunk.decode("utf-8", errors="replace").rstrip()
                if text:
                    print(f"[acp:stderr] {text}", flush=True)
        except asyncio.CancelledError:
            return

    def _dispatch(self, msg: dict) -> None:
        # JSON-RPC 2.0: responses have `id` + (`result` or `error`);
        # notifications have `method` + no `id`;
        # server-initiated requests have `method` + `id`.
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(
                        ACPError(f"RPC error: {json.dumps(msg['error'])}")
                    )
                else:
                    fut.set_result(msg.get("result", {}))
            return

        method = msg.get("method")
        if method == "session/update":
            self._handle_session_update(msg.get("params", {}))
            return

        if "id" in msg and method:
            # Server-side request (e.g. request_permission, fs read/write).
            # We disabled the tools/fs that would trigger these; if one arrives
            # anyway, respond with a generic "not supported" error so Copilot
            # can move on.
            asyncio.create_task(self._reject_server_request(msg))
            return

        # Untracked notifications — log for visibility during development.
        if method:
            print(f"[acp] notify {method}: {json.dumps(msg.get('params', {}))[:200]}", flush=True)

    async def _reject_server_request(self, msg: dict) -> None:
        assert self._proc and self._proc.stdin
        response = {
            "jsonrpc": "2.0",
            "id": msg["id"],
            "error": {
                "code": -32601,
                "message": f"Client does not implement {msg.get('method')}",
            },
        }
        payload = (json.dumps(response) + "\n").encode("utf-8")
        try:
            self._proc.stdin.write(payload)
            await self._proc.stdin.drain()
        except Exception:
            pass

    def _handle_session_update(self, params: dict) -> None:
        # Only care about updates for the current in-flight prompt
        sid = params.get("sessionId")
        if sid != self._current_prompt_session_id:
            return
        update = params.get("update", {})
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content", {})
            if content.get("type") == "text":
                self._current_prompt_buf.append(content.get("text", ""))
        # agent_thought_chunk / tool_call / plan / etc. are ignored for now.

    async def _send(self, obj: dict) -> None:
        assert self._proc and self._proc.stdin
        payload = (json.dumps(obj) + "\n").encode("utf-8")
        self._proc.stdin.write(payload)
        await self._proc.stdin.drain()

    async def _request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request, return its result."""
        if not self.is_alive():
            raise ACPError("ACP subprocess is not running")
        rid = self._next_id
        self._next_id += 1
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        )
        return await fut

    # ── high-level API ──────────────────────────────────────────────────────

    async def prompt(self, text: str, cwd: str | None = None) -> tuple[str, str]:
        """Send a single-turn prompt, return (assistant_text, session_id).

        Creates a fresh ACP session each call so history state stays in
        Synapse's `full_prompt` — the single source of truth.
        """
        cwd = cwd or os.getcwd()
        async with self._prompt_lock:
            # 1. new session
            new_res = await self._request(
                "session/new",
                {"cwd": cwd, "mcpServers": []},
            )
            session_id = new_res.get("sessionId")
            if not session_id:
                raise ACPError(f"session/new returned no sessionId: {new_res}")

            # 2. prompt
            self._current_prompt_session_id = session_id
            self._current_prompt_buf = []
            try:
                prompt_res = await asyncio.wait_for(
                    self._request(
                        "session/prompt",
                        {
                            "sessionId": session_id,
                            "prompt": [{"type": "text", "text": text}],
                        },
                    ),
                    timeout=_PROMPT_TIMEOUT_SEC,
                )
            finally:
                collected = "".join(self._current_prompt_buf)
                self._current_prompt_buf = []
                self._current_prompt_session_id = None

            stop = prompt_res.get("stopReason")
            if stop and stop not in ("end_turn", "max_tokens"):
                # Refusal / cancelled / max_turn_requests → surface as error
                # only if no text at all was produced; otherwise return partial.
                if not collected.strip():
                    raise ACPError(f"prompt stopped with reason={stop}")

            return collected, session_id


# ─── convenience wrapper for llm_providers.py ──────────────────────────────

async def call_copilot_acp(
    full_prompt: str,
    cwd: str | None = None,
) -> tuple[str, int, int, str | None]:
    """Match the return signature of call_cli_provider.

    Returns (text, input_tokens_est, output_tokens_est, session_id).
    Token counts are estimated from character length since ACP doesn't
    surface per-request usage.
    """
    from core import usage_tracker  # local import to avoid cycles

    client = await ACPClient.get()
    text, session_id = await client.prompt(full_prompt, cwd=cwd)
    in_tok = usage_tracker.estimate_tokens_from_text(full_prompt)
    out_tok = usage_tracker.estimate_tokens_from_text(text)
    return text, in_tok, out_tok, session_id
