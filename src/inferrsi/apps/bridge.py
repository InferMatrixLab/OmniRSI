"""Map fixed Benchmark preparation and a saved attempt into a Harness spec."""

import re
from copy import deepcopy
from hashlib import sha256

from inferrsi._json_v1 import decode, encode
from inferrsi.harness.errors import PublicError

from .archive import _logical_path
from .result import validate_artifact_ref

_ATTEMPT_FIELDS = {
    "request_id", "run_id", "attempt_id", "owner", "target_request", "deadline_utc",
    "resources", "budget", "retry_of", "retry_reason",
}
_PROPOSAL_FIELDS = {"candidate", "inputs", "generation_record_ref"}
_EXECUTE_FIELDS = {"schema_version", "prepared_ref", "proposal", "attempt", "output_root"}
_INPUT_FIELDS = {
    "role", "source", "source_namespace", "source_ref", "snapshot_format", "expected_sha256",
    "materialized_path",
}
_OUTPUT_FIELDS = {"role", "path", "required", "media_type", "format_schema"}
_CANDIDATE_FIELDS = {"candidate_id", "kind", "input_role", "parent_id", "parent_digest",
                     "context_refs"}
_RECIPE_FIELDS = {
    "recipe_revision", "runtime_role", "entrypoint_kind", "entrypoint", "entrypoint_revision",
    "code_sha256", "args", "input_roles", "outputs", "required_capabilities",
}
_PREPARED_FIELDS = {
    "schema_version", "prepared_id", "task", "grader", "operation", "validation_scope",
    "protocol", "protocol_digest", "candidate_contract", "expected_candidate",
    "required_evidence", "recipe", "resources", "created_at", "display_notes",
}
_RESOURCE_FIELDS = {"role", "artifact", "required_for", "format_schema"}
_PROTOCOL_FIELDS = {
    "schema_version", "task", "grader", "operation", "validation_scope",
    "candidate_contract", "worker_recipe", "input_requirements", "claim_definitions",
    "metric_definitions", "required_evidence", "task_parameters", "baseline", "reference",
    "quality_reference", "sampling", "feedback_visibility", "budget_policy_refs",
    "resources", "display_notes",
}
_PROTOCOL_COPIES = (
    "task", "grader", "operation", "validation_scope", "candidate_contract", "required_evidence",
)
_RECORD_FIELDS = {
    "schema_version", "request_id", "run_id", "attempt_id", "request_digest", "owner",
    "target_request", "target_binding", "retry_of", "retry_reason", "generation_record_ref",
    "prepared_ref", "protocol_digest", "operation", "validation_scope", "candidate",
    "record_version", "recorded_at", "bound_inputs", "phase", "started", "side_effect_unknown",
    "process_outcome", "exit_code", "elapsed_seconds", "steps", "cancellation", "cleanup",
    "artifacts", "checks", "budget_admission", "usage", "resource_leases", "errors",
}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SEMANTIC_ID = re.compile(r"sha256:[0-9a-f]{64}")
_PLACEHOLDER = re.compile(r"\{(input|output):([^{}]+)\}")


def execution_fields(request: dict) -> dict:
    """Return the fields that an execution binder must preserve exactly."""
    if not isinstance(request, dict) or request.keys() != _EXECUTE_FIELDS:
        raise ValueError("expected AppsExecuteRequest fields")
    attempt = request["attempt"]
    proposal = request["proposal"]
    if (
        type(request["schema_version"]) is not int
        or request["schema_version"] != 1
        or not isinstance(attempt, dict)
        or attempt.keys() != _ATTEMPT_FIELDS
        or not isinstance(attempt["attempt_id"], str)
        or not attempt["attempt_id"]
        or attempt["request_id"] != attempt["attempt_id"]
        or not isinstance(proposal, dict)
        or proposal.keys() != _PROPOSAL_FIELDS
        or not isinstance(proposal["inputs"], list)
    ):
        raise ValueError("expected a pre-allocated AttemptContext")
    return {
        **attempt,
        "schema_version": 1,
        "prepared_ref": request["prepared_ref"],
        "output_root": request["output_root"],
        "candidate": proposal["candidate"],
        "generation_record_ref": proposal["generation_record_ref"],
    }


def _id(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _definition(value: object) -> bool:
    return (
        isinstance(value, dict) and value.keys() == {"name", "revision"}
        and all(_id(value[key]) for key in ("name", "revision"))
    )


def _candidate_input(candidate: dict, prepared: dict, inputs: dict) -> None:
    if not isinstance(candidate, dict) or candidate.keys() != _CANDIDATE_FIELDS:
        raise ValueError("invalid candidate fields")
    contract = prepared["candidate_contract"]
    role = candidate["input_role"]
    if (
        role not in inputs or role != contract["input_role"]
        or candidate["kind"] != contract["kind"] or not _id(candidate["candidate_id"])
        or not _definition(contract["format_schema"])
        or (candidate["parent_id"] is None) != (candidate["parent_digest"] is None)
        or not isinstance(candidate["context_refs"], list)
    ):
        raise ValueError("candidate does not match the frozen input role or format")
    if candidate["parent_id"] is not None and (
        not _id(candidate["parent_id"]) or not _digest(candidate["parent_digest"])
    ):
        raise ValueError("invalid parent identity")
    for ref in candidate["context_refs"]:
        validate_artifact_ref(ref)
        _logical_path(ref["location"])
    rule = contract["identity_rule"]
    expected = inputs[role]["expected_sha256"]
    if rule not in (
        {"name": "captured-sha256", "revision": "1"},
        {"name": "expected-sha256", "revision": "1"},
    ):
        raise PublicError(
            "UnsupportedRuleVersion", stage="binder_validation",
            message="The frozen candidate identity rule is unsupported.",
        )
    if rule["name"] == "expected-sha256" and expected is None:
        raise ValueError("candidate input is missing an expected digest")
    frozen = prepared["expected_candidate"]
    if frozen is not None and (
        not _digest(frozen["sha256"]) or expected != frozen["sha256"]
        or candidate["candidate_id"] != frozen["candidate_id"]
        or contract["format_schema"] != frozen["format_schema"]
    ):
        raise ValueError("candidate declaration conflicts with the frozen expectation")


def _input_paths(items: list) -> dict:
    inputs = {}
    for item in items:
        if not isinstance(item, dict) or item.keys() != _INPUT_FIELDS:
            raise ValueError("invalid input fields")
        role, expected = item["role"], item["expected_sha256"]
        if (
            not _id(role) or role in inputs or not _id(item["snapshot_format"])
            or not isinstance(item["source"], str) or not item["source"]
            or (expected is not None and not _digest(expected))
        ):
            raise ValueError("invalid or repeated input role or identity")
        _logical_path(item["materialized_path"])
        source_ref = item["source_ref"]
        if item["source_namespace"] == "artifact":
            validate_artifact_ref(source_ref)
            _logical_path(source_ref["location"])
            if item["source"] != source_ref["location"] or expected != source_ref["sha256"]:
                raise ValueError("input source conflicts with its fixed artifact")
        elif item["source_namespace"] not in ("controller", "target") or source_ref is not None:
            raise ValueError("invalid input source namespace")
        inputs[role] = item
    return inputs


def _add_fixed_resources(inputs: dict, resources: list) -> None:
    if not isinstance(resources, list):
        raise ValueError("invalid fixed resource list")
    seen = set()
    for resource in resources:
        if (
            not isinstance(resource, dict) or resource.keys() != _RESOURCE_FIELDS
            or not _id(resource["role"]) or resource["role"] in seen
            or not isinstance(resource["required_for"], list)
            or not resource["required_for"] or not _definition(resource["format_schema"])
            or any(use not in ("prepare", "execute", "assess") for use in resource["required_for"])
        ):
            raise ValueError("invalid or repeated fixed resource")
        seen.add(resource["role"])
        if "execute" not in resource["required_for"]:
            continue
        role, ref = resource["role"], resource["artifact"]
        validate_artifact_ref(ref)
        _logical_path(ref["location"])
        if role in inputs:
            raise ValueError("a fixed execution resource role is already bound")
        role_hash = sha256(role.encode("ascii")).hexdigest()
        inputs[role] = {
            "role": role,
            "source": ref["location"],
            "source_namespace": "artifact",
            "source_ref": ref,
            "snapshot_format": "file-v1",
            "expected_sha256": ref["sha256"],
            "materialized_path": f"inputs/{role_hash}",
        }


def _output_paths(outputs: list) -> dict:
    paths = {}
    for item in outputs:
        if (
            not isinstance(item, dict) or item.keys() != _OUTPUT_FIELDS
            or not _id(item["role"]) or item["role"] in paths
            or type(item["required"]) is not bool
            or not isinstance(item["media_type"], str) or not item["media_type"]
            or not _definition(item["format_schema"])
        ):
            raise ValueError("invalid or repeated output declaration")
        _logical_path(item["path"])
        paths[item["role"]] = item["path"]
    return paths


def _bind_args(args: list, inputs: dict, outputs: dict) -> list[str]:
    bound = []
    for arg in args:
        if not isinstance(arg, str):
            raise ValueError("recipe arguments must be strings")
        match = _PLACEHOLDER.fullmatch(arg)
        if match:
            kind, role = match.groups()
            bound.append(inputs[role] if kind == "input" else outputs[role])
        elif "{input:" in arg or "{output:" in arg:
            raise ValueError("recipe placeholders must occupy the whole argument")
        else:
            bound.append(arg)
    return bound


def _require_disjoint_paths(paths: list[str]) -> None:
    ordered = sorted(_logical_path(path) for path in paths)
    if any(second[:len(first)] == first for first, second in zip(ordered, ordered[1:])):
        raise ValueError("input and output paths must not overlap")


def _resource_projection(resource: dict) -> dict:
    if not isinstance(resource, dict) or resource.keys() != _RESOURCE_FIELDS:
        raise ValueError("invalid protocol resource")
    ref = resource["artifact"]
    validate_artifact_ref(ref)
    _logical_path(ref["location"])
    return {**resource, "artifact": {key: value for key, value in ref.items() if key != "location"}}


def verify_protocol(prepared: dict, data: bytes) -> None:
    """Check the archived protocol's identity and the preparation's frozen fields."""
    try:
        ref = prepared["protocol"]
        validate_artifact_ref(ref)
        protocol = decode(data)
        if (
            not isinstance(protocol, dict) or protocol.keys() != _PROTOCOL_FIELDS
            or type(protocol["schema_version"]) is not int or protocol["schema_version"] != 1
            or not isinstance(protocol["resources"], list)
            or not isinstance(prepared["resources"], list)
        ):
            raise ValueError("invalid ProtocolV1 envelope")
        projection = dict(protocol)
        del projection["display_notes"]
        resources = [_resource_projection(item) for item in protocol["resources"]]
        closure = list(resources)
        projection["resources"] = resources
        for key in ("baseline", "reference", "quality_reference"):
            if protocol[key] is not None:
                projection[key] = _resource_projection(protocol[key])
                closure.append(projection[key])
        digest = "sha256:" + sha256(encode(projection)).hexdigest()
        fixed_resources = [_resource_projection(item) for item in prepared["resources"]]
        expected_resources = {item["role"]: item for item in closure}
        if len(expected_resources) != len(closure):
            raise ValueError("protocol resource roles must be unique")
        if (
            ref["size_bytes"] != len(data) or ref["sha256"] != sha256(data).hexdigest()
            or prepared["protocol_digest"] != digest
            or encode(prepared["recipe"]) != encode(protocol["worker_recipe"])
            or any(encode(prepared[key]) != encode(protocol[key]) for key in _PROTOCOL_COPIES)
            or encode(fixed_resources) != encode(
                [expected_resources[role] for role in sorted(expected_resources)]
            )
        ):
            raise PublicError(
                "IdentityMismatch", stage="binder_validation",
                message="The fixed preparation disagrees with its archived protocol.",
            )
    except (KeyError, TypeError, ValueError):
        raise PublicError(
            "InvalidExecutionSpec", stage="binder_validation",
            message="The fixed preparation or protocol is malformed.",
        ) from None


def _check_prepared_ref(prepared: dict, ref: dict, *, stage: str) -> None:
    if (
        not isinstance(ref, dict) or ref.keys() != {"prepared_id", "schema_version", "artifact"}
        or not isinstance(prepared, dict) or prepared.keys() != _PREPARED_FIELDS
    ):
        raise ValueError("invalid prepared object or reference fields")
    validate_artifact_ref(ref["artifact"])
    _logical_path(ref["artifact"]["location"])
    data = encode(prepared)
    if (
        type(ref["schema_version"]) is not int or ref["schema_version"] != 1
        or type(prepared["schema_version"]) is not int or prepared["schema_version"] != 1
        or not _SEMANTIC_ID.fullmatch(ref["prepared_id"])
        or prepared["prepared_id"] != ref["prepared_id"]
        or ref["artifact"]["media_type"] != "application/json"
        or ref["artifact"]["size_bytes"] != len(data)
        or ref["artifact"]["sha256"] != sha256(data).hexdigest()
    ):
        raise PublicError(
            "IdentityMismatch", stage=stage,
            message="The preparation does not match its fixed reference.",
        )
    if (
        not _id(prepared["operation"]) or not _id(prepared["validation_scope"])
        or not _SEMANTIC_ID.fullmatch(prepared["protocol_digest"])
    ):
        raise ValueError("invalid prepared operation, scope, or protocol digest")


def _bind_spec(request: dict, prepared: dict) -> dict:
    fields = execution_fields(request)
    _check_prepared_ref(prepared, fields["prepared_ref"], stage="binder_validation")
    _logical_path(fields["output_root"])
    inputs = _input_paths(request["proposal"]["inputs"])
    _candidate_input(fields["candidate"], prepared, inputs)
    _add_fixed_resources(inputs, prepared["resources"])
    recipe = prepared["recipe"]
    if (
        not isinstance(recipe, dict) or recipe.keys() != _RECIPE_FIELDS
        or not all(_id(recipe[key]) for key in (
            "recipe_revision", "runtime_role", "entrypoint_revision",
        ))
        or recipe["entrypoint_kind"] not in ("python_module", "executable")
        or not isinstance(recipe["entrypoint"], str) or not recipe["entrypoint"]
        or not _digest(recipe["code_sha256"])
        or not isinstance(recipe["input_roles"], list)
        or not all(_id(role) for role in recipe["input_roles"])
        or len(set(recipe["input_roles"])) != len(recipe["input_roles"])
        or not isinstance(recipe["args"], list)
        or not isinstance(recipe["outputs"], list)
        or not isinstance(recipe["required_capabilities"], list)
        or not all(_id(item) for item in recipe["required_capabilities"])
    ):
        raise ValueError("invalid frozen worker recipe")
    paths = {role: item["materialized_path"] for role, item in inputs.items()}
    outputs = _output_paths(recipe["outputs"])
    _require_disjoint_paths([*paths.values(), *outputs.values()])
    worker_inputs = {role: paths[role] for role in recipe["input_roles"]}
    return {
        **fields,
        "protocol_digest": prepared["protocol_digest"],
        "operation": prepared["operation"],
        "validation_scope": prepared["validation_scope"],
        "inputs": [inputs[role] for role in sorted(inputs)],
        "required_capabilities": recipe["required_capabilities"],
        "steps": [{
            "step_id": "main",
            "runtime_role": recipe["runtime_role"],
            "entrypoint_kind": recipe["entrypoint_kind"],
            "entrypoint": recipe["entrypoint"],
            "entrypoint_revision": recipe["entrypoint_revision"],
            "expected_code_sha256": recipe["code_sha256"],
            "args": _bind_args(recipe["args"], worker_inputs, outputs),
            "cwd": ".",
            "outputs": recipe["outputs"],
            "timeout_seconds": None,
        }],
    }


def bind_execution(request: dict, prepared: dict) -> dict:
    """Bind one frozen worker at the attempt root with the original attempt deadline."""
    try:
        return deepcopy(_bind_spec(request, prepared))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PublicError(
            "InvalidExecutionSpec", stage="binder_validation",
            message="The saved request and frozen worker recipe could not be bound.",
        ) from None


def to_evidence(
    prepared: dict, prepared_ref: dict, record_ref: dict, committed_payload: dict,
) -> dict:
    """Project one committed record revision into the Benchmark evidence envelope."""
    try:
        _check_prepared_ref(prepared, prepared_ref, stage="assessment_binding")
        if (
            record_ref.keys() != {"attempt_id", "record_version", "artifact"}
            or not _id(record_ref["attempt_id"])
            or type(record_ref["record_version"]) is not int or record_ref["record_version"] < 1
            or not isinstance(committed_payload, dict)
            or committed_payload.keys() != _RECORD_FIELDS
            or type(committed_payload["schema_version"]) is not int
            or committed_payload["schema_version"] != 1
            or type(committed_payload["record_version"]) is not int
        ):
            raise ValueError("invalid fixed record shape")
        artifact = record_ref["artifact"]
        validate_artifact_ref(artifact)
        _logical_path(artifact["location"])
        if artifact["media_type"] != "application/json":
            raise ValueError("expected a JSON record")
        data = encode(committed_payload)
        if artifact["sha256"] != sha256(data).hexdigest() or artifact["size_bytes"] != len(data):
            raise PublicError(
                "ArtifactIntegrityError", stage="assessment_binding",
                message="The record bytes do not match the fixed reference.",
            )
        if committed_payload["candidate"] is None:
            raise PublicError(
                "IdentityMismatch", stage="assessment_binding",
                message="The fixed evaluation record has no candidate declaration.",
            )
        if (
            committed_payload["attempt_id"] != record_ref["attempt_id"]
            or committed_payload["request_id"] != record_ref["attempt_id"]
            or committed_payload["record_version"] != record_ref["record_version"]
            or committed_payload["prepared_ref"] != prepared_ref
            or any(committed_payload[key] != prepared[key]
                   for key in ("protocol_digest", "operation", "validation_scope"))
        ):
            raise PublicError(
                "IdentityMismatch", stage="assessment_binding",
                message="The record does not belong to this attempt and preparation.",
            )
        return deepcopy({
            "schema_version": 1,
            "record_ref": record_ref,
            "record_digest": artifact["sha256"],
            "task": prepared["task"],
            "task_source": "prepared_ref",
            "record": committed_payload,
            "artifact_checks": [],
        })
    except (AttributeError, KeyError, TypeError, ValueError):
        raise PublicError(
            "InvalidRequest", stage="assessment_binding",
            message="Expected a fixed v1 evaluation record and preparation.",
        ) from None
