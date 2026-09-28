"""redact() and its wiring into the real stack modules, with a fake provider
SDK that raises an exception containing a fake key. No network."""

import json
import sys
import types

from stacks.trace_schema import Trace, redact

FAKE_ANT = "sk-ant-api03-FAKEFAKEFAKEFAKE1234567890"
FAKE_OAI = "sk-proj-FAKEFAKEFAKEFAKEFAKEFAKE123456"
FAKE_BEARER = "Bearer abcdef0123456789.ghijkl-MNOP"


def test_redact_patterns():
    for raw in (FAKE_ANT, FAKE_OAI, FAKE_BEARER):
        out = redact(f"boom {raw} end")
        assert raw not in out and "[REDACTED]" in out
    assert redact("headers x-api-key: sk-ant-api03-FAKEFAKEFAKE99 ok") == "headers [REDACTED] ok"
    assert redact("sent x-api-key=abc123 done") == "sent [REDACTED] done"
    assert redact("plain error 500") == "plain error 500"
    assert redact(None) is None and redact("") == ""


def test_short_sk_strings_are_left_alone():
    assert redact("sk-short") == "sk-short"


def test_trace_to_dict_redacts_error_backstop():
    tr = Trace(run_id="r", stack="mock", task="lookup", prompt_id="p", bank_id=None,
               condition="full", rep=1, error=f"AuthError: bad key {FAKE_ANT}")
    line = json.dumps(tr.to_dict())
    assert FAKE_ANT not in line and "[REDACTED]" in line


def _fake_sdk(monkeypatch, name, cls_name, attr_chain):
    class Boom(Exception):
        pass

    def raiser(*a, **k):
        raise Boom(f"401 invalid x-api-key {FAKE_ANT} / {FAKE_BEARER} / {FAKE_OAI}")

    inner = types.SimpleNamespace(**{attr_chain[-1]: raiser})
    for part in reversed(attr_chain[:-1]):
        inner = types.SimpleNamespace(**{part: inner})
    mod = types.ModuleType(name)
    setattr(mod, cls_name, lambda **kw: inner)
    monkeypatch.setitem(sys.modules, name, mod)


def _kw():
    return dict(run_id="r", task="lookup", prompt_id="p", bank_id=None, condition="full", rep=1)


def _assert_clean(tr):
    assert tr.error and tr.error.startswith("Boom:")
    for secret in (FAKE_ANT, FAKE_OAI, "abcdef0123456789"):
        assert secret not in tr.error
    assert "[REDACTED]" in tr.error
    dumped = json.dumps(tr.to_dict())
    for secret in (FAKE_ANT, FAKE_OAI, "abcdef0123456789"):
        assert secret not in dumped


def test_anthropic_stack_redacts_raised_exception(monkeypatch):
    _fake_sdk(monkeypatch, "anthropic", "Anthropic", ["messages", "create"])
    from stacks import anthropic_ws
    _assert_clean(anthropic_ws.probe("hi", **_kw()))


def test_openai_stack_redacts_raised_exception(monkeypatch):
    _fake_sdk(monkeypatch, "openai", "OpenAI", ["responses", "create"])
    from stacks import openai_agents
    _assert_clean(openai_agents.probe("hi", **_kw()))
