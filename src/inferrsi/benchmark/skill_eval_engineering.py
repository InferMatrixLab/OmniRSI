"""One public, reconstructed serving-parser engineering task.

Task-owned definition, public vectors and pure judgment. Nothing here starts a
process or reads a file: the controller replays the retained artifact inside its
own sandbox and supplies the responses, never agent-reported checks.
"""
import re

ADAPTER = "engineering-artifact-v1"
GENERATION_MODE = "provider-generation-only-v1"
MAX_SOURCE_BYTES = 65536
STARTER = (
    'def evaluate(status, frames, requested_tokens):\n'
    '    return {"success": status == 200, "reason": "length" if status == 200 else "http_error"}\n'
)
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["artifact", "summary"],
    "properties": {
        "artifact": {"type": "string", "enum": ["parser.py"]},
        "summary": {"type": "string"},
    },
}
PROMPT = (
    "Read task.json and the assigned knowledge. Repair only parser.py, implementing "
    "evaluate(status, frames, requested_tokens) under the public task contract. "
    "Do not change frozen inputs or create other files. No network, installs, other agents, "
    "commits or publication. Return artifact='parser.py' and a concise summary, not a verdict. "
    "The controller retains the actual file and independently checks its behavior."
)
GENERATION_SCHEMA = {
    **SCHEMA,
    "required": ["artifact", "summary", "artifact_source"],
    "properties": {**SCHEMA["properties"], "artifact_source": {"type": "string"}},
}
GENERATION_PROMPT = (
    "Read the supplied task.json, parser.py starter and assigned knowledge. Generate the "
    "complete repaired parser.py source as artifact_source in submit_answer. "
    "Implement evaluate(status, frames, requested_tokens) under the public task contract. "
    "You have no shell, execution, file-writing, network or test tool. Do not claim to "
    "have run tests. Return artifact='parser.py', a concise summary and artifact_source; "
    "do not return a verdict. A trusted controller writes only parser.py from the source "
    "string and an independent isolated verifier checks its behavior later."
)
REQUEST = (
    "Repair parser.py: evaluate(status, frames, requested_tokens) returns exactly "
    "{success: bool, reason: str}. Inputs are an integer HTTP status, decoded frames, "
    "and a positive integer requested count. Content frames have exactly {content: str}; "
    "a terminal has exactly {finish_reason: 'length'|'stop', completion_tokens: "
    "nonnegative int}. Boolean counts are invalid. At most one optional final '[DONE]' "
    "is allowed; DONE alone is not a terminal. First reject HTTP non-200 as http_error. "
    "Then inspect frames in order: a dict containing error gives stream_error; malformed "
    "fields, duplicate terminals, post-terminal content, misplaced or duplicate DONE "
    "give malformed_frame. Require a terminal after scanning, otherwise missing_terminal "
    "(also when the final frame is DONE). For length require count == requested; for stop "
    "require count <= requested, otherwise token_mismatch. Only valid length or stop "
    "returns success=true with that reason; every failure returns success=false. "
    "Change only parser.py; all other files are frozen. No imports/dependencies, network "
    "or hardware are required. Tests are public, not holdout. This reconstructed CPU "
    "fixture derives from the HTTP-200 error-stream observation in "
    "https://github.com/InferMatrixLab/InferRSI/pull/90; it does not reproduce the original "
    "server, capacity behavior or GPU performance."
)
REASONS = ("length", "stop", "http_error", "stream_error", "malformed_frame",
           "missing_terminal", "token_mismatch")
# Public behavior probes, not a hidden benchmark or proof of generalization.
# Expected outputs remain in the trusted parent, never in the artifact subprocess.
VECTORS = (
    ("length", 200, [{"content": "text"}, {"finish_reason": "length", "completion_tokens": 3},
                     "[DONE]"], 3, True, "length"),
    ("early_stop", 200, [{"finish_reason": "stop", "completion_tokens": 1}], 3, True, "stop"),
    ("empty_stop", 200, [{"finish_reason": "stop", "completion_tokens": 0}, "[DONE]"],
     3, True, "stop"),
    ("http_failure", 400, [], 3, False, "http_error"),
    ("stream_error", 200, [{"content": "partial"}, {"error": "capacity"}],
     3, False, "stream_error"),
    ("missing_terminal", 200, [{"content": "partial"}, "[DONE]"],
     3, False, "missing_terminal"),
    ("length_mismatch", 200, [{"finish_reason": "length", "completion_tokens": 2}],
     3, False, "token_mismatch"),
    ("stop_overflow", 200, [{"finish_reason": "stop", "completion_tokens": 4}],
     3, False, "token_mismatch"),
    ("unknown_frame", 200, [{"unexpected": True}], 3, False, "malformed_frame"),
    ("boolean_count", 200, [{"finish_reason": "length", "completion_tokens": True}],
     1, False, "malformed_frame"),
    ("duplicate_terminal", 200, [{"finish_reason": "stop", "completion_tokens": 1},
                                 {"finish_reason": "stop", "completion_tokens": 1}],
     3, False, "malformed_frame"),
    ("misplaced_done", 200, ["[DONE]", {"content": "late"}],
     3, False, "malformed_frame"),
    ("negative_count", 200, [{"finish_reason": "stop", "completion_tokens": -1}],
     3, False, "malformed_frame"),
    ("post_terminal_content", 200, [{"finish_reason": "stop", "completion_tokens": 1},
                                   {"content": "late"}], 3, False, "malformed_frame"),
    ("extra_content_field", 200, [{"content": "text", "extra": True}],
     3, False, "malformed_frame"),
    ("extra_terminal_field", 200, [{"finish_reason": "stop", "completion_tokens": 1,
                                   "extra": True}], 3, False, "malformed_frame"),
    ("unknown_finish", 200, [{"finish_reason": "unknown", "completion_tokens": 1}],
     3, False, "malformed_frame"),
    ("duplicate_done", 200, [{"finish_reason": "stop", "completion_tokens": 1},
                            "[DONE]", "[DONE]"], 3, False, "malformed_frame"),
    ("non_object_frame", 200, [42], 3, False, "malformed_frame"),
)


def validate_task(task):
    if not isinstance(task, dict) or set(task) != {"id", "split", "request", "provenance"}:
        raise ValueError("Engineering task requires id/split/request/provenance only")
    if any(type(value) is not str or not value.strip() for value in task.values()):
        raise ValueError("Engineering task metadata must be nonempty strings")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", task["id"]):
        raise ValueError("Engineering task ID must be a safe path component")
    if task["request"] != REQUEST:
        raise ValueError("Engineering adapter supports only its frozen serving-parser request")
    if task["split"] != "development":
        raise ValueError("Engineering probes are public development cases, not holdout/discovery")


def submission(task, output):
    """Return (source bytes, None) for a replayable answer, else (None, failed checks)."""
    validate_task(task)
    valid = (isinstance(output, dict)
             and set(output) == {"artifact", "summary", "artifact_source"}
             and output["artifact"] == "parser.py"
             and type(output["summary"]) is str
             and type(output["artifact_source"]) is str)
    if not valid:
        return None, {"passed": False, "assertions": {"artifact_schema": False}}
    try:
        source = output["artifact_source"].encode("utf-8")
    except UnicodeEncodeError:
        source = b""
    if not source or len(source) > MAX_SOURCE_BYTES:
        return None, {"passed": False, "assertions": {"artifact_size": False}}
    return source, None


def vector_inputs() -> list:
    """Arguments of every public vector, without its name or expected result."""
    return [[status, frames, count] for _, status, frames, count, _, _ in VECTORS]


def judge(responses: list) -> dict:
    """Compare one retained sandbox response per vector with its expected result."""
    checks = {}
    for (name, _, _, _, success, reason), actual in zip(VECTORS, responses, strict=True):
        checks[name] = (isinstance(actual, dict) and set(actual) == {"success", "reason"}
                        and type(actual["success"]) is bool
                        and actual == {"success": success, "reason": reason})
    return {"passed": all(checks.values()), "assertions": checks}
