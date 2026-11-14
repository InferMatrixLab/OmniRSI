import copy
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from inferrsi.apps import skill_evaluation as evaluation
from inferrsi.harness import skill_evaluation as harness
from inferrsi.infra.artifacts import sha256, write_json
from inferrsi.model import anthropic_agent as agent
from tests.fixtures.skill_eval_compare_support import configuration
from tools import skill_eval_compare as compare
from tools.skill_eval_pilot import SCHEMA

MODEL = "claude-opus-5-5"
SCHEMA_SMALL = {"type": "object", "properties": {"answer": {"type": "string"}},
                "required": ["answer"], "additionalProperties": False}
ANSWER = {"answer": "bounded"}
USAGE = {"input_tokens": 7, "output_tokens": 3,
         "cache_read_input_tokens": 2, "cache_creation_input_tokens": 1}


@pytest.fixture
def spec():
    return {"kind": agent.KIND, "provider": "yibuapi", "endpoint": agent.ENDPOINT,
            "api_version": "2023-06-01", "max_requests": 4, "max_output_tokens": 256,
            "max_request_bytes": 8192, "max_read_bytes": 1024, "max_tool_calls": 8}


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    write_json(root / "task.json", {"input": "bounded fixture"})
    (root / "SKILL.md").write_text("bounded skill", encoding="utf-8")
    (tmp_path / "verifier-private.txt").write_text("held-out private sentinel", encoding="utf-8")
    return root


def call(name, value, identifier="tool-one"):
    return {"type": "tool_use", "id": identifier, "name": name, "input": value}


def response(*calls, **updates):
    return {"type": "message", "role": "assistant", "model": MODEL,
            "stop_reason": "tool_use", "content": list(calls), "usage": copy.deepcopy(USAGE),
            **updates}


def read(path="task.json"):
    return response(call("read_file", {"path": path}))


def submit(value=None):
    return response(call("submit_answer", ANSWER if value is None else value))


def scripted(*responses):
    requests = []

    def send(payload, settings, timeout):
        requests.append(copy.deepcopy(payload))
        assert timeout > 0
        value = responses[len(requests) - 1]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    return send, requests


def run(workspace, spec, responses, events=None, **kwargs):
    send, requests = scripted(*responses)
    events = [] if events is None else events
    answer = agent.run_agent(workspace, SCHEMA_SMALL, "fixture prompt", MODEL, spec, 30,
                             events.append, send=send, **kwargs)
    return answer, requests, events


def save_trace(tmp_path, events):
    path = tmp_path / "trace.jsonl"
    path.write_text("".join(agent.canonical(e) + "\n" for e in events), encoding="utf-8")
    return path


def test_read_submit_round_trip_keeps_tools_and_evidence_minimal(workspace, spec, tmp_path):
    answer, requests, events = run(workspace, spec, [read(), read("SKILL.md"), submit()])
    assert answer == ANSWER and len(requests) == 3
    assert {t["name"] for t in requests[0]["tools"]} == {"read_file", "submit_answer"}
    assert "task.json" in requests[0]["messages"][0]["content"]
    assert "held-out private sentinel" not in agent.canonical(requests)
    assert requests[1]["messages"][-1]["content"][0]["type"] == "tool_result"
    assert "bounded fixture" in requests[1]["messages"][-1]["content"][0]["content"]
    assert requests[2]["messages"][-1]["content"][0]["content"] == "bounded skill"
    assert all(p["stream"] is False and p["max_tokens"] == 256 for p in requests)
    trace = agent.trace_metrics(save_trace(tmp_path, events))
    assert trace["client_kind"] == agent.KIND and trace["completed_turns"] == 1
    assert trace["usage"] == {"input_tokens": 30, "output_tokens": 9, "cached_input_tokens": 6}
    assert trace["usage_complete"] and trace["api_requests"] == 3
    assert trace["commands"][0]["sha256"] == sha256(workspace / "task.json")
    assert "fixture prompt" not in agent.canonical(events)
    assert "bounded fixture" not in agent.canonical(events)


@pytest.mark.parametrize("bad", [
    submit(), response(call("submit_answer", ANSWER),
                       call("read_file", {"path": "task.json"}, "two")),
    response(content=[{"type": "text", "text": '{"answer":"bounded"}'}], stop_reason="end_turn"),
    response(model="different-model", content=[]), response(content=[None]),
])
def test_plain_chat_premature_or_incompatible_response_is_not_success(workspace, spec, bad):
    events = []
    with pytest.raises(agent.AgentError):
        run(workspace, spec, [bad], events)
    assert events[-1]["type"] == "agent.failed"
    assert not any(e["type"] == "agent.completed" for e in events)


@pytest.mark.parametrize("bad", [
    {"answer": 1}, {"answer": True}, {"answer": "ok", "unexpected": 1}, {},
])
def test_submit_must_match_frozen_output_contract(workspace, spec, bad):
    with pytest.raises(agent.AgentError, match="invalid_or_premature_answer"):
        run(workspace, spec, [read(), submit(bad)])


@pytest.mark.parametrize("path", [
    "../verifier-private.txt", "/absolute", "C:/outside", "..\\secret", "unknown",
])
def test_unexposed_paths_never_become_tool_results(workspace, spec, path):
    with pytest.raises(agent.AgentError, match="unexposed_path"):
        run(workspace, spec, [read(path)])


def test_symlink_workspace_is_rejected_before_transport(workspace, spec):
    try:
        (workspace / "escape").symlink_to(workspace.parent / "verifier-private.txt")
    except OSError:
        pytest.skip("Host does not permit fixture symlinks")
    send, requests = scripted()
    with pytest.raises(agent.AgentError, match="workspace_symlink"):
        agent.run_agent(workspace, SCHEMA_SMALL, "fixture", MODEL, spec, 30,
                        lambda _: None, send=send)
    assert not requests


@pytest.mark.parametrize("field,value,reason,responses", [
    ("max_requests", 2, "request_limit", [read(), read()]),
    ("max_tool_calls", 1, "tool_call_limit", [read(), submit()]),
    ("max_read_bytes", 1, "tool_read_limit", [read()]),
    ("max_request_bytes", 10, "request_bytes_limit", [read()]),
    ("max_output_tokens", 1, "output_token_limit", [read()]),
])
def test_finite_limits_stop_without_retry(workspace, spec, field, value, reason, responses):
    spec[field] = value
    send, requests = scripted(*responses)
    with pytest.raises(agent.AgentError, match=reason):
        agent.run_agent(workspace, SCHEMA_SMALL, "fixture", MODEL, spec, 30,
                        lambda _: None, send=send)
    assert len(requests) <= len(responses)


def test_timeout_after_response_does_not_accept_answer_or_retry(workspace, spec, monkeypatch):
    ticks = iter([0, 0, 31])
    monkeypatch.setattr(agent, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    send, requests = scripted(read())
    with pytest.raises(agent.AgentError, match="timeout"):
        agent.run_agent(workspace, SCHEMA_SMALL, "fixture", MODEL, spec, 30,
                        lambda _: None, send=send)
    assert len(requests) == 1


@pytest.mark.parametrize("bad_usage", [
    None, {"input_tokens": 7, "output_tokens": 3}, {**USAGE, "input_tokens": True},
    {**USAGE, "output_tokens": -1}, {**USAGE, "cache_creation_input_tokens": 1.5},
])
def test_unknown_usage_stops_and_retains_incomplete_evidence(
        workspace, spec, tmp_path, bad_usage):
    events = []
    send, requests = scripted(read(), response(call("read_file", {"path": "SKILL.md"}),
                                               usage=bad_usage), submit())
    with pytest.raises(agent.AgentError, match="unknown_usage"):
        agent.run_agent(workspace, SCHEMA_SMALL, "fixture", MODEL, spec, 30,
                        events.append, send=send)
    trace = agent.trace_metrics(save_trace(tmp_path, events))
    assert len(requests) == 2 and trace["usage"] is None
    assert trace["observed_usage"]["input_tokens"] == 10
    assert not trace["usage_complete"] and trace["completed_turns"] == 0


@pytest.mark.parametrize("bad_call", [
    call("shell", {"command": "forbidden"}), call("list_files", {}),
    call("read_file", {"path": "task.json", "extra": True}),
    call("read_file", {"path": 1}), call("read_file", {"path": "task.json"}, ""),
])
def test_no_general_agent_tool_surface(workspace, spec, bad_call):
    with pytest.raises(agent.AgentError):
        run(workspace, spec, [response(bad_call)])


def test_duplicate_tool_identity_is_rejected(workspace, spec):
    with pytest.raises(agent.AgentError, match="invalid_tool_call"):
        run(workspace, spec, [response(call("read_file", {"path": "task.json"}),
                                       call("read_file", {"path": "SKILL.md"}))])


@pytest.mark.parametrize("changes", [
    {"api_key": "fake-only"}, {"endpoint": "https://example.invalid/messages"},
    {"provider": "other"}, {"max_requests": True}, {"max_requests": 1},
])
def test_unknown_credentials_settings_and_unbounded_protocol_are_rejected(spec, changes):
    spec.update(changes)
    with pytest.raises(ValueError):
        agent.validate_spec({"name": MODEL, "effort": "not_applicable"}, spec)


def test_schema_support_is_explicit_and_cannot_map_codex_effort(spec):
    agent.validate_schema(SCHEMA)
    with pytest.raises(ValueError):
        agent.validate_schema({"type": "object", "$ref": "#"})
    with pytest.raises(ValueError):
        agent.validate_spec({"name": MODEL, "effort": "xhigh"}, spec)


def test_http_transport_shape_no_redirect_and_no_auth_data_in_body(spec, monkeypatch):
    captured = []
    fake_key = "fake-provider-key-for-offline-tests"

    class Opener:
        def open(self, request, timeout):
            captured.append(request)
            assert timeout == 3
            assert request.full_url == agent.ENDPOINT and request.get_method() == "POST"
            assert request.get_header("X-api-key") == fake_key
            assert request.get_header("Anthropic-version") == "2023-06-01"
            assert request.get_header("Content-type") == "application/json"
            assert fake_key.encode() not in request.data
            assert json.loads(request.data) == payload
            return io.BytesIO(agent.canonical(read()).encode())

    def opener(*handlers):
        assert any(isinstance(h, agent.NoRedirect) for h in handlers)
        assert any(isinstance(h, agent.urllib.request.ProxyHandler) for h in handlers)
        return Opener()

    monkeypatch.setattr(agent.urllib.request, "build_opener", opener)
    payload = {"model": MODEL, "max_tokens": 256, "messages": [], "stream": False}
    assert agent.send_message(payload, spec, fake_key, 3)["type"] == "message"
    assert len(captured) == 1
    assert agent.NoRedirect().redirect_request(None, None, None, None, None, None) is None


@pytest.mark.parametrize("error", [
    HTTPError(agent.ENDPOINT, 403, "private raw body", {}, io.BytesIO(b"private raw body")),
    TimeoutError("private raw failure"),
])
def test_http_failure_is_redacted_and_sent_once(spec, monkeypatch, error):
    attempts = []

    class Opener:
        def open(self, request, timeout):
            attempts.append(request)
            raise error

    monkeypatch.setattr(agent.urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(agent.AgentError) as failure:
        agent.send_message({"model": MODEL}, spec, "fake-key-only", 1)
    assert len(attempts) == 1
    assert "private" not in str(failure.value) and "fake-key" not in str(failure.value)


@pytest.mark.parametrize("raw,reason", [
    (b'{"type":"message","type":"other"}', "invalid_response_json"),
    (b'{"value":NaN}', "invalid_response_json"),
    (b'{"value":"fake-key-only"}', "credential_echo"),
    (b"x" * (agent.MAX_RESPONSE + 1), "response_bytes_limit"),
])
def test_hostile_http_body_is_rejected_without_leakage(spec, monkeypatch, raw, reason):
    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(raw)

    monkeypatch.setattr(agent.urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(agent.AgentError, match=reason):
        agent.send_message({"model": MODEL}, spec, "fake-key-only", 1)


# Ordinary-key transport protection already existed. These fabricated keys and
# mocked HTTP bodies test encoding consistency, not an observed real-service disclosure.
FAKE_ESCAPED_KEYS = ["fake-key-offline", 'fake"key-offline',
                     r"fake\key-offline", 'fake"\\key-offline']


@pytest.mark.parametrize("key", FAKE_ESCAPED_KEYS)
@pytest.mark.parametrize("location", ["alone", "nested_value", "object_key"])
def test_real_transport_rejects_credential_substring_in_payload_before_http(
    spec, monkeypatch, key, location
):
    assert agent.execution_environment({agent.KEY_ENV: key})[agent.KEY_ENV] == key
    attempts = []

    def opener(*args):
        attempts.append(args)
        pytest.fail("credential-bearing payload must never reach HTTP")

    monkeypatch.setattr(agent.urllib.request, "build_opener", opener)
    text = key if location == "alone" else "prefix-" + key + "-suffix"
    content = {text: "unrelated"} if location == "object_key" else {"content": text}
    payload = {"model": MODEL, "messages": [content]}
    with pytest.raises(agent.AgentError) as failure:
        agent.send_message(payload, spec, key, 1, environment={})
    assert str(failure.value) == "credential_in_payload" and not attempts


@pytest.mark.parametrize("key", FAKE_ESCAPED_KEYS)
@pytest.mark.parametrize("wire_encoding", ["canonical", "unicode_escapes"])
@pytest.mark.parametrize("location", ["alone", "nested_value", "object_key"])
def test_real_transport_rejects_decoded_credential_substring_from_http(
    spec, monkeypatch, key, wire_encoding, location
):
    assert agent.execution_environment({agent.KEY_ENV: key})[agent.KEY_ENV] == key
    text = key if location == "alone" else "prefix-" + key + "-suffix"
    value = {"nested": {text: "unrelated"} if location == "object_key" else {"text": text}}
    raw = agent.canonical(value)
    if wire_encoding == "unicode_escapes":
        raw = raw.replace(agent.canonical(text),
                          '"' + "".join(f"\\u{ord(c):04x}" for c in text) + '"')
    raw = raw.encode()
    attempts = []

    class Opener:
        def open(self, request, timeout):
            attempts.append(request)
            assert request.get_header("X-api-key") == key
            assert json.loads(request.data) == {"model": MODEL}
            return io.BytesIO(raw)

    monkeypatch.setattr(agent.urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(agent.AgentError) as failure:
        agent.send_message({"model": MODEL}, spec, key, 1, environment={})
    assert str(failure.value) == "credential_echo" and len(attempts) == 1


@pytest.mark.parametrize("key", FAKE_ESCAPED_KEYS)
def test_real_transport_allowed_key_encoding_does_not_reject_clean_response(
    spec, monkeypatch, key
):
    attempts = []

    class Opener:
        def open(self, request, timeout):
            attempts.append(request)
            assert request.get_header("X-api-key") == key
            return io.BytesIO(b'{"text":"unrelated public output"}')

    monkeypatch.setattr(agent.urllib.request, "build_opener", lambda *args: Opener())
    assert agent.send_message({"model": MODEL}, spec, key, 1, environment={}) == {
        "text": "unrelated public output"}
    assert len(attempts) == 1


@pytest.mark.parametrize("mode", ["addition", "update"])
def test_opt_in_plan_freezes_identity_and_cannot_execute_or_fall_back(tmp_path, spec, mode):
    config, repo = configuration(tmp_path, mode, ids=("one",))
    data = compare.read_json(config)
    data.update(model={"name": MODEL, "effort": "not_applicable"}, client=spec)
    write_json(config, data)
    output = tmp_path / "prepared"
    digest = compare.prepare(config, repo, output)
    plan = compare.load_plan(output)
    assert plan["client"]["settings"] == spec
    assert plan["client"]["source_sha256"] == sha256(compare.ROOT / agent.SOURCE)
    assert plan["controllers"][agent.SOURCE] == sha256(compare.ROOT / agent.SOURCE)
    assert plan["client"]["runtime_state"] == "unconfigured"
    report = compare.report(output)
    assert report["client"] == plan["client"]
    assert agent.KIND in (output / "report.md").read_text(encoding="utf-8")
    assert plan["max_api_requests"] == len(plan["schedule"]) * spec["max_requests"]
    assert plan["arms"]["baseline"]["bundle"] is None if mode == "addition" else (
        plan["arms"]["baseline"]["bundle"] is not None)
    with pytest.raises(ValueError, match="exact plan"):
        compare.execute(output, "wrong", client=object())
    with pytest.raises(ValueError, match="pricing/cap"):
        compare.execute(output, digest, client=object())
    assert not list((output / "results").iterdir())
    spec["max_requests"] += 1
    data["client"] = spec
    write_json(config, data)
    changed = compare.prepare(config, repo, tmp_path / "changed")
    assert changed != digest


def test_client_command_fails_closed_before_reading_any_key_or_external_cli(spec):
    client = agent.AnthropicAgentClient(MODEL, spec, 30)
    with pytest.raises(ValueError, match="pricing/cap"):
        client.command(None, None, None, "fixture")


def test_normalized_trace_hook_uses_existing_verifier(workspace, spec, tmp_path, monkeypatch):
    _, _, events = run(workspace, spec, [read(), submit()])
    final = tmp_path / "trial" / "answer.json"

    class Client:
        timeout_seconds = 30
        trace_metrics = staticmethod(agent.trace_metrics)

        def command(self, *args):
            return ("offline-fixture",)

    def execute(command, cwd, folder, deadline):
        folder.mkdir(parents=True)
        if folder.name == "agent":
            (folder / "stdout.log").write_text(
                "".join(agent.canonical(e) + "\n" for e in events), encoding="utf-8")
            write_json(final, ANSWER)
        else:
            assert command[1] == str(tmp_path / "trusted-verifier.py")
            write_json(folder / "result.json", {"passed": True, "assertions": {"fixture": True}})
        return {"status": "completed", "returncode": 0, "elapsed_seconds": 0}

    monkeypatch.setattr(harness, "execute_command", execute)
    record = evaluation.run_trial(Client(), {"id": "one", "split": "holdout"}, "candidate", 1,
                               workspace, final.parent, tmp_path / "schema.json",
                               tmp_path / "trusted-verifier.py", "fixture", float("inf"))
    assert record["verdict"] == "PASS" and record["inputs_unchanged"]
    assert record["observations"]["client_kind"] == agent.KIND
    assert record["observations"]["api_requests"] == 2


def test_completion_without_a_tool_round_trip_is_not_verified(tmp_path):
    trace = agent.trace_metrics(save_trace(tmp_path, [{
        "type": "agent.completed", "requests": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}}]))
    assert trace["completed_turns"] == 0 and trace["usage"] is None


@pytest.mark.parametrize("bad_text", [
    {"type": "text"}, {"type": "text", "text": None}, {"type": "text", "text": []},
])
@pytest.mark.parametrize("turn", ["read", "submit"])
def test_malformed_text_block_stops_before_usage_tools_or_replay(
        workspace, spec, bad_text, turn):
    malformed = response(bad_text, call(
        "read_file" if turn == "read" else "submit_answer",
        {"path": "task.json"} if turn == "read" else ANSWER))
    responses = [malformed, submit()] if turn == "read" else [read(), malformed, submit()]
    send, requests = scripted(*responses)
    events = []
    with pytest.raises(agent.AgentError, match="incompatible_response"):
        agent.run_agent(workspace, SCHEMA_SMALL, "fixture", MODEL, spec, 30,
                        events.append, send=send)
    rejected_request = 1 if turn == "read" else 2
    assert len(requests) == rejected_request
    assert not any(e["type"] == "agent.completed" for e in events)
    assert not any(e["type"] in ("agent.response", "agent.tool")
                   and e["request"] == rejected_request for e in events)
    assert events[-1] == {"type": "agent.failed", "reason": "incompatible_response"}


def test_valid_text_blocks_can_accompany_read_and_submit(workspace, spec):
    first = response({"type": "text", "text": "Read the task."},
                     call("read_file", {"path": "task.json"}))
    last = response({"type": "text", "text": "Submit the result."},
                    call("submit_answer", ANSWER))
    answer, requests, events = run(workspace, spec, [first, last])
    assert answer == ANSWER and len(requests) == 2
    assert events[-1]["type"] == "agent.completed"
