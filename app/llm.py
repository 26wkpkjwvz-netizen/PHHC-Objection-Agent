"""Model backends.

claude_cli (default): shells out to the locally installed Claude Code CLI (`claude -p`),
    which runs on whatever account that CLI is logged in with, e.g. a Claude Pro/Max
    subscription on the office Mac. No API key needed.
api: calls the Anthropic API directly with ANTHROPIC_API_KEY.
"""
import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from . import config

T = TypeVar("T", bound=BaseModel)

# Never let the CLI fall back to API-key billing: it must use its own (subscription) login.
_STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")


class LLMError(RuntimeError):
    pass


def _usage(input_tokens=0, output_tokens=0, cost_usd=0.0) -> dict:
    return {"input_tokens": input_tokens or 0, "output_tokens": output_tokens or 0,
            "cost_usd": round(cost_usd or 0.0, 4)}


def inline_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema with $defs inlined and titles dropped (portable to the CLI)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


# ------------------------------------------------------------------ Claude Code CLI backend

def claude_bin() -> str:
    path = shutil.which(config.CLAUDE_BIN) or (config.CLAUDE_BIN if Path(config.CLAUDE_BIN).exists() else None)
    if not path:
        raise LLMError(
            f"Claude Code CLI not found ('{config.CLAUDE_BIN}'). Install it and run `claude` once to "
            "log in with your Claude subscription, or set PHHC_CLAUDE_BIN to its full path.")
    return path


def _run_cli(*, model: str, effort: str | None, system: str, prompt: str, schema: dict | None,
             tools: str, workdir: str) -> dict:
    sys_file = Path(workdir) / "_system_prompt.txt"
    sys_file.write_text(system)
    cmd = [claude_bin(), "-p", "--model", model, "--output-format", "json",
           "--tools", tools, "--no-session-persistence", "--setting-sources", "",
           "--strict-mcp-config", "--system-prompt-file", str(sys_file),
           "--permission-mode", "dontAsk"]
    if tools:
        cmd += ["--allowedTools", tools]
    if effort:
        cmd += ["--effort", effort]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=workdir,
                              env=env, timeout=config.CLI_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise LLMError(f"Claude Code did not finish within {config.CLI_TIMEOUT_S}s.")
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        detail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise LLMError(f"Claude Code failed (exit {proc.returncode}): {detail or 'no output'}")
    if out.get("is_error") or out.get("subtype") != "success":
        msg = out.get("result") or out.get("subtype") or "unknown error"
        raise LLMError(f"Claude Code returned an error: {str(msg)[:800]}")
    return out


def _cli_usage(out: dict) -> dict:
    u = out.get("usage") or {}
    return _usage(u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                  + u.get("cache_creation_input_tokens", 0),
                  u.get("output_tokens", 0), out.get("total_cost_usd", 0.0))


def _cli_structured(out: dict, output: type[T]) -> T:
    data = out.get("structured_output")
    if data is None:  # older CLIs put the JSON in `result`
        try:
            data = json.loads(out.get("result") or "")
        except json.JSONDecodeError:
            raise LLMError("Claude Code returned no structured output.")
    return output.model_validate(data)


# ------------------------------------------------------------------ Anthropic API backend

_api_client = None


def _client():
    global _api_client
    if _api_client is None:
        import anthropic
        _api_client = anthropic.Anthropic(max_retries=4)
    return _api_client


def _api_call(*, model, effort, system_blocks, content, output=None, max_tokens=64000):
    kwargs = dict(model=model, max_tokens=max_tokens, system=system_blocks,
                  messages=[{"role": "user", "content": content}])
    if effort:
        kwargs.update(thinking={"type": "adaptive"}, output_config={"effort": effort})
    if output is not None:
        kwargs["output_format"] = output
    with _client().messages.stream(**kwargs) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        raise LLMError("The model declined this request.")
    if message.stop_reason == "max_tokens":
        raise LLMError("The model's answer was cut off (max_tokens).")
    u = message.usage
    usage = _usage((u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
                   + (getattr(u, "cache_creation_input_tokens", 0) or 0), u.output_tokens)
    return message, usage


# ------------------------------------------------------------------ public interface

def read_pdf(*, model: str, system: str, prompt: str, pdf: bytes, output: type[T]) -> tuple[T, dict]:
    """Have a reader model look at a PDF chunk and return structured notes."""
    if config.BACKEND == "api":
        content = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                   "data": base64.standard_b64encode(pdf).decode()}},
                   {"type": "text", "text": prompt}]
        message, usage = _api_call(model=model, effort=None, output=output, max_tokens=32000,
                                   system_blocks=[{"type": "text", "text": system,
                                                   "cache_control": {"type": "ephemeral"}}],
                                   content=content)
        if message.parsed_output is None:
            raise LLMError("Reader returned no structured output.")
        return message.parsed_output, usage

    with tempfile.TemporaryDirectory(prefix="phhc-read-") as tmp:
        (Path(tmp) / "chunk.pdf").write_bytes(pdf)
        cli_prompt = (f"{prompt}\n\nThe pages are in ./chunk.pdf. Read it with the Read tool "
                      "(at most 20 pages per call, using the pages parameter) and look at every page "
                      "before answering.")
        out = _run_cli(model=model, effort=None, system=system, prompt=cli_prompt,
                       schema=inline_schema(output), tools="Read", workdir=tmp)
        return _cli_structured(out, output), _cli_usage(out)


def reason(*, model: str, effort: str, system_blocks: list[dict], user_blocks: list[dict],
           output: type[T]) -> tuple[T, dict]:
    """Structured reasoning call (the Opus review)."""
    if config.BACKEND == "api":
        message, usage = _api_call(model=model, effort=effort, system_blocks=system_blocks,
                                   content=user_blocks, output=output)
        if message.parsed_output is None:
            raise LLMError("The reasoning model returned no structured output.")
        return message.parsed_output, usage

    with tempfile.TemporaryDirectory(prefix="phhc-reason-") as tmp:
        out = _run_cli(model=model, effort=effort, system=_join(system_blocks),
                       prompt=_join(user_blocks), schema=inline_schema(output), tools="", workdir=tmp)
        return _cli_structured(out, output), _cli_usage(out)


def converse(*, model: str, effort: str, system_blocks: list[dict], context_blocks: list[dict],
             history: list[dict], question: str) -> tuple[str, dict]:
    """Free-text follow-up answer grounded in the review context."""
    if config.BACKEND == "api":
        messages = []
        turns = history + [{"role": "user", "content": question}]
        for i, turn in enumerate(turns):
            content = (context_blocks + [{"type": "text", "text": turn["content"]}]) if i == 0 else turn["content"]
            messages.append({"role": turn["role"], "content": content})
        kwargs = dict(model=model, max_tokens=32000, system=system_blocks, messages=messages,
                      thinking={"type": "adaptive"}, output_config={"effort": effort})
        with _client().messages.stream(**kwargs) as stream:
            message = stream.get_final_message()
        if message.stop_reason == "refusal":
            return "The model declined to answer this question.", {}
        text = "".join(b.text for b in message.content if b.type == "text").strip()
        return text, _usage(message.usage.input_tokens, message.usage.output_tokens)

    transcript = "\n\n".join(f"{'OFFICE' if t['role'] == 'user' else 'YOU'}: {t['content']}" for t in history)
    prompt = _join(context_blocks)
    if transcript:
        prompt += f"\n\nCONVERSATION SO FAR\n{transcript}"
    prompt += f"\n\nOFFICE'S NEW MESSAGE\n{question}\n\nReply to the office's new message."
    with tempfile.TemporaryDirectory(prefix="phhc-chat-") as tmp:
        out = _run_cli(model=model, effort=effort, system=_join(system_blocks), prompt=prompt,
                       schema=None, tools="", workdir=tmp)
    return str(out.get("result", "")).strip(), _cli_usage(out)


def _join(blocks: list[dict]) -> str:
    return "\n\n".join(b["text"] for b in blocks)


def status() -> dict:
    """Is the configured backend ready? Shown as a banner on the dashboard."""
    if config.BACKEND == "api":
        ok = bool(os.getenv("ANTHROPIC_API_KEY"))
        return {"ok": ok, "backend": backend_label(),
                "detail": "API key found." if ok else "Set ANTHROPIC_API_KEY in .env."}
    try:
        env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
        proc = subprocess.run([claude_bin(), "auth", "status"], capture_output=True, text=True,
                              env=env, timeout=30)
        info = json.loads(proc.stdout)
    except LLMError as exc:
        return {"ok": False, "backend": backend_label(), "detail": str(exc)}
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return {"ok": False, "backend": backend_label(),
                "detail": "Could not read `claude auth status`. Open Terminal and run `claude` to log in."}
    if not info.get("loggedIn"):
        return {"ok": False, "backend": backend_label(),
                "detail": "Claude Code is not logged in. Open Terminal, run `claude` and sign in with your Claude subscription."}
    return {"ok": True, "backend": backend_label(),
            "detail": f"Claude Code is logged in ({info.get('authMethod', 'unknown method')})."}


def backend_label() -> str:
    return "Claude Code CLI (subscription login)" if config.BACKEND != "api" else "Anthropic API key"
