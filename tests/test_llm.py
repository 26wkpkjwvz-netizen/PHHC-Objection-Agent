"""The Claude Code CLI backend, exercised against a fake `claude` executable."""
import json
import stat

import pytest

from app import config, llm
from app.schemas import ChunkRead, ReviewResult

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
log = {"argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd_files": sorted(os.listdir(".")),
       "has_api_key": "ANTHROPIC_API_KEY" in os.environ}
open(os.environ["FAKE_LOG"], "w").write(json.dumps(log))
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "crash":
    sys.stderr.write("boom"); sys.exit(2)
if mode == "error":
    print(json.dumps({"is_error": True, "subtype": "success", "result": "Usage limit reached"})); sys.exit(1)
if "auth" in sys.argv[1:2]:
    print(json.dumps({"loggedIn": mode != "loggedout", "authMethod": "claude.ai"})); sys.exit(0)
schema = sys.argv[sys.argv.index("--json-schema") + 1] if "--json-schema" in sys.argv else None
out = json.loads(os.environ["FAKE_OUTPUT"]) if schema else None
print(json.dumps({"is_error": False, "subtype": "success", "result": "plain answer" if not schema else json.dumps(out),
                  "structured_output": out, "total_cost_usd": 0.5,
                  "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7}}))
'''


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log.json"
    monkeypatch.setattr(config, "BACKEND", "claude_cli")
    monkeypatch.setattr(config, "CLAUDE_BIN", str(exe))
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    return lambda: json.loads(log.read_text())


def test_inline_schema_has_no_refs():
    text = json.dumps(llm.inline_schema(ReviewResult))
    assert "$ref" not in text and "$defs" not in text
    assert '"additionalProperties": false' in text


def test_read_pdf_uses_read_tool_and_subscription_login(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_OUTPUT", json.dumps({"case_category_guess": "writ", "case_type_guess": "CWP", "pages": []}))
    parsed, usage = llm.read_pdf(model="claude-haiku-4-5", system="sys", prompt="read it", pdf=b"%PDF-1.4",
                                 output=ChunkRead)
    log = fake_cli()
    argv = log["argv"]
    assert parsed.case_category_guess == "writ"
    assert usage == {"input_tokens": 15, "output_tokens": 7, "cost_usd": 0.5}
    assert log["has_api_key"] is False                       # never bills an API key
    assert "chunk.pdf" in log["cwd_files"]
    assert argv[argv.index("--tools") + 1] == "Read"
    assert argv[argv.index("--model") + 1] == "claude-haiku-4-5"
    assert "--effort" not in argv                            # Haiku takes no effort level
    assert "./chunk.pdf" in log["stdin"]


def test_reason_passes_effort_and_no_tools(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_OUTPUT", json.dumps({
        "category": "civil", "case_type": "RSA", "summary": "s", "readiness": 50, "strengths": [],
        "findings": [{"code": "86", "title": "t", "severity": "medium", "confidence": "likely",
                      "evidence": "e", "pages": "", "fix": "f", "draft_text": ""}]}))
    result, _ = llm.reason(model="claude-opus-5-5", effort="medium",
                           system_blocks=[{"type": "text", "text": "A"}, {"type": "text", "text": "B"}],
                           user_blocks=[{"type": "text", "text": "DIGEST"}], output=ReviewResult)
    argv = fake_cli()["argv"]
    assert result.findings[0].code == "86"
    assert argv[argv.index("--effort") + 1] == "medium"
    assert argv[argv.index("--tools") + 1] == ""
    assert "--bare" not in argv                              # --bare cannot use a subscription login


def test_converse_flattens_history(fake_cli):
    answer, _ = llm.converse(model="m", effort="medium", system_blocks=[{"type": "text", "text": "S"}],
                             context_blocks=[{"type": "text", "text": "CTX"}],
                             history=[{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}],
                             question="q2")
    stdin = fake_cli()["stdin"]
    assert answer == "plain answer"
    assert stdin.index("CTX") < stdin.index("OFFICE: q1") < stdin.index("YOU: a1") < stdin.index("q2")


@pytest.mark.parametrize("mode,fragment", [("crash", "boom"), ("error", "Usage limit reached")])
def test_cli_errors_are_readable(fake_cli, monkeypatch, mode, fragment):
    monkeypatch.setenv("FAKE_MODE", mode)
    monkeypatch.setenv("FAKE_OUTPUT", "{}")
    with pytest.raises(llm.LLMError, match=fragment):
        llm.reason(model="m", effort="medium", system_blocks=[], user_blocks=[], output=ReviewResult)


def test_status(fake_cli, monkeypatch):
    assert llm.status()["ok"] is True
    monkeypatch.setenv("FAKE_MODE", "loggedout")
    assert "not logged in" in llm.status()["detail"]


def test_missing_cli(monkeypatch):
    monkeypatch.setattr(config, "BACKEND", "claude_cli")
    monkeypatch.setattr(config, "CLAUDE_BIN", "definitely-not-installed-claude")
    assert llm.status()["ok"] is False
