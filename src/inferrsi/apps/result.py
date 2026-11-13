"""Assemble and check the AppsResultV1 output envelope."""

import re
from dataclasses import fields
from hashlib import sha256

from inferrsi._json_v1 import encode
from inferrsi.harness.errors import PUBLIC_ERROR_CODES, PartialExecutionFacts

_EXIT_CODES = {
    "success": 0,
    "error": 2,
    "not_started": 3,
    "in_progress": 4,
    "recovery_required": 5,
    "persistence_error": 6,
    "execution_failed": 7,
    "fail": 8,
    "inconclusive": 9,
}
_ERROR_OUTCOMES = {
    "AttemptInProgress": "in_progress",
    "RecoveryRequired": "recovery_required",
    "ExecutionPersistenceError": "persistence_error",
    "ArchivePersistenceError": "persistence_error",
}
_RESULT_FIELDS = {
    "schema_version", "operation", "outcome", "reason_code", "exit_code",
    "primary_scope", "primary_claim_id", "attempt_id", "admission", "execution",
    "prepared_ref", "record_ref", "assessment_ref", "assessment", "error",
}
_ERROR_FIELDS = {
    "code", "stage", "attempt_id", "started", "latest_record_ref",
    "partial_record", "retained_refs", "field_errors", "message",
}
_PARTIAL_FIELDS = {field.name for field in fields(PartialExecutionFacts)}
_REF_FIELDS = {
    "prepared_ref": ("prepared_id", "schema_version"),
    "record_ref": ("attempt_id", "record_version"),
    "assessment_ref": ("assessment_id", "schema_version"),
}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_RECORD_OBSERVATIONS = {"record_ref", "observed_at", "controller_observation"}
_VERDICT_OUTCOMES = {"PASS": "success", "FAIL": "fail", "INCONCLUSIVE": "inconclusive"}


def is_public_error(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.keys() == _ERROR_FIELDS
        and isinstance(value["code"], str)
        and value["code"] in PUBLIC_ERROR_CODES
    )


def _execute_outcome(record: dict) -> str:
    if record.get("phase") == "recovery_required":
        return "recovery_required"
    if record.get("phase") != "finished":
        raise ValueError("expected a terminal Harness record")
    process = record.get("process_outcome")
    if process == "not_started":
        return "not_started"
    if process in ("timed_out", "cancelled", "execution_error"):
        return "execution_failed"
    if process == "exited" and type(record.get("exit_code")) is int:
        return "success" if record["exit_code"] == 0 else "execution_failed"
    raise ValueError("unsupported Harness process outcome")


def execution_result(*, record: dict | None = None, error: dict | None = None) -> dict:
    """Project a committed Harness record or a PublicError into AppsResultV1."""
    if (record is None) == (error is None):
        raise ValueError("expected one Harness record or PublicError")
    facts = record if record is not None else {}
    return {
        **_operation_result(
            "execute", error, _execute_outcome(record) if record is not None else "error"
        ),
        "attempt_id": facts.get("attempt_id"),
        "admission": facts.get("budget_admission"),
        "execution": record,
        "prepared_ref": facts.get("prepared_ref"),
        "record_ref": facts.get("record_ref"),
    }


def preparation_result(*, prepared_ref: dict | None = None, error: dict | None = None) -> dict:
    """Project a committed PreparedRef or a PublicError into AppsResultV1."""
    if (prepared_ref is None) == (error is None):
        raise ValueError("expected one PreparedRef or PublicError")
    return {**_operation_result("prepare", error, "success"), "prepared_ref": prepared_ref}


def assessment_result(
    *,
    primary: tuple[str, str] | None = None,
    assessment: dict | None = None,
    assessment_ref: dict | None = None,
    error: dict | None = None,
) -> dict:
    """Project the selected claim and fixed assessment into AppsResultV1."""
    verdict = _selected_verdict(assessment, primary) if assessment is not None else None
    result = _operation_result("assess", error, _VERDICT_OUTCOMES.get(verdict, "error"))
    if assessment is not None:
        result.update({
            field: assessment[field] for field in ("attempt_id", "prepared_ref", "record_ref")
        })
    if primary is not None:
        result["primary_claim_id"], result["primary_scope"] = primary
    return {**result, "assessment": assessment, "assessment_ref": assessment_ref}


def _operation_result(operation: str, error: dict | None, outcome: str) -> dict:
    reason = error["code"] if error is not None else None
    if reason is not None:
        outcome = _ERROR_OUTCOMES.get(reason, "error")
    return {
        "schema_version": 1,
        "operation": operation,
        "outcome": outcome,
        "reason_code": reason,
        "exit_code": _EXIT_CODES[outcome],
        "primary_scope": None,
        "primary_claim_id": None,
        "attempt_id": None,
        "admission": None,
        "execution": None,
        "prepared_ref": None,
        "record_ref": None,
        "assessment_ref": None,
        "assessment": None,
        "error": error,
    }


def publication_error(result: dict) -> dict:
    """Report an output failure while keeping every confirmed underlying fact."""
    prior = result["error"] or {}
    record = result["execution"] or {}
    code = "ArchivePersistenceError"
    return {
        **result,
        "outcome": "persistence_error",
        "reason_code": code,
        "exit_code": 6,
        "error": {
            "code": code,
            "stage": "output_publication",
            "attempt_id": result["attempt_id"] or prior.get("attempt_id"),
            "started": record.get("started", prior.get("started")),
            "latest_record_ref": result["record_ref"] or prior.get("latest_record_ref"),
            "partial_record": prior.get("partial_record"),
            "retained_refs": prior.get("retained_refs", []),
            "field_errors": prior.get("field_errors", []),
            "message": "Could not publish the Apps output files.",
        },
    }


def validate_artifact_ref(artifact: object) -> None:
    if (
        not isinstance(artifact, dict)
        or artifact.keys() != {"location", "sha256", "size_bytes", "media_type"}
        or not isinstance(artifact["location"], str) or not artifact["location"]
        or not isinstance(artifact["sha256"], str) or not _SHA256.fullmatch(artifact["sha256"])
        or type(artifact["size_bytes"]) is not int or artifact["size_bytes"] < 0
        or not isinstance(artifact["media_type"], str) or not artifact["media_type"]
    ):
        raise ValueError("invalid Apps reference metadata")


def _check_ref(ref: object, id_field: str, version_field: str) -> None:
    if not isinstance(ref, dict) or ref.keys() != {id_field, version_field, "artifact"}:
        raise ValueError("invalid Apps reference fields")
    identity, version = ref[id_field], ref[version_field]
    if (
        not isinstance(identity, str) or not identity
        or type(version) is not int or version < 1
        or (version_field == "schema_version" and version != 1)
    ):
        raise ValueError("invalid Apps reference metadata")
    validate_artifact_ref(ref["artifact"])


def _check_partial(partial: dict) -> None:
    if not isinstance(partial, dict) or partial.keys() != _PARTIAL_FIELDS:
        raise ValueError("invalid partial execution fields")
    if any(not isinstance(partial[key], str) or not partial[key]
           for key in ("request_id", "run_id", "attempt_id")):
        raise ValueError("invalid partial execution identity")
    digest, version = partial["request_digest"], partial["proposed_record_version"]
    if (digest is not None and (not isinstance(digest, str)
                               or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest))) or (
        version is not None and (type(version) is not int or version < 0)
    ):
        raise ValueError("invalid partial execution revision")
    if partial["phase"] not in (
        None, "registered", "preparing", "running", "stopping", "finalizing",
        "finished", "recovery_required",
    ) or (partial["started"] is not None and type(partial["started"]) is not bool) or (
        type(partial["side_effect_unknown"]) is not bool
    ):
        raise ValueError("invalid partial execution state")
    for key in ("steps", "checks", "artifacts", "usage", "errors"):
        if not isinstance(partial[key], list) or any(
            not isinstance(item, dict) for item in partial[key]
        ):
            raise ValueError("invalid partial execution observations")
    for key in ("budget_admission", "resource_leases"):
        if partial[key] is not None and not isinstance(partial[key], dict):
            raise ValueError("invalid partial execution accounting")


def _check_error(error: dict, outcome: str) -> None:
    if (
        not is_public_error(error)
        or outcome != _ERROR_OUTCOMES.get(error["code"], "error")
        or not isinstance(error["stage"], str) or not error["stage"]
        or (error["attempt_id"] is not None and not isinstance(error["attempt_id"], str))
        or (error["started"] is not None and not isinstance(error["started"], bool))
        or not isinstance(error["message"], str)
        or not isinstance(error["field_errors"], list)
        or any(not isinstance(item, str) for item in error["field_errors"])
        or not isinstance(error["retained_refs"], list)
    ):
        raise ValueError("invalid Apps PublicError")
    partial = error["partial_record"]
    if partial is not None:
        _check_partial(partial)
    if error["latest_record_ref"] is not None:
        _check_ref(error["latest_record_ref"], *_REF_FIELDS["record_ref"])
    for ref in error["retained_refs"]:
        validate_artifact_ref(ref)


def _check_artifact_ref(ref: dict, payload: dict) -> None:
    artifact = ref["artifact"]
    data = encode(payload)
    if artifact["sha256"] != sha256(data).hexdigest() or artifact["size_bytes"] != len(data):
        raise ValueError("fixed reference does not match the archived bytes")


def _check_record(result: dict, record: dict) -> None:
    ref = result["record_ref"]
    if (
        not isinstance(record, dict)
        or ref is None
        or not _RECORD_OBSERVATIONS <= record.keys()
        or record["record_ref"] != ref
        or not isinstance(record.get("budget_admission"), dict)
        or record["budget_admission"] != result["admission"]
        or record.get("prepared_ref") != result["prepared_ref"]
        or record.get("attempt_id") != result["attempt_id"]
        or not isinstance(record.get("run_id"), str)
        or not record["run_id"]
        or result["attempt_id"] is None
        or ref.get("attempt_id") != result["attempt_id"]
        or type(record.get("record_version")) is not int
        or record["record_version"] < 1
        or ref.get("record_version") != record["record_version"]
    ):
        raise ValueError("inconsistent Apps execution and record reference")
    _check_artifact_ref(
        ref, {field: value for field, value in record.items() if field not in _RECORD_OBSERVATIONS}
    )


def _check_assessment(result: dict, *, output_error: bool) -> None:
    assessment, ref = result["assessment"], result["assessment_ref"]
    if assessment is not None and (
        not isinstance(assessment, dict)
        or any(
            result[field] is None or assessment.get(field) != result[field]
            for field in ("attempt_id", "prepared_ref", "record_ref")
        )
    ):
        raise ValueError("inconsistent Apps assessment")
    error = result["error"]
    if ref is None:
        if error is None:
            raise ValueError("expected an archived assessment")
        return
    if (
        (error is not None and not output_error)
        or assessment is None
        or not assessment.get("assessment_id")
        or assessment.get("assessment_id") != ref.get("assessment_id")
        or assessment.get("schema_version") != ref.get("schema_version")
    ):
        raise ValueError("inconsistent Apps assessment reference")
    _check_artifact_ref(ref, assessment)
    verdict = _selected_verdict(
        assessment, (result["primary_claim_id"], result["primary_scope"])
    )
    if not output_error and _VERDICT_OUTCOMES[verdict] != result["outcome"]:
        raise ValueError("Apps outcome must match the selected primary claim")


def _selected_verdict(assessment: dict, primary: tuple[str, str] | None) -> str:
    if primary is None or any(value is None for value in primary):
        raise ValueError("expected a selected primary claim")
    claims = assessment.get("claims")
    if not isinstance(claims, list):
        raise ValueError("expected assessment claims")
    verdicts = [
        claim.get("verdict")
        for claim in claims
        if isinstance(claim, dict)
        and (claim.get("claim_id"), claim.get("validation_scope")) == primary
    ]
    if (
        len(verdicts) != 1
        or not isinstance(verdicts[0], str)
        or verdicts[0] not in _VERDICT_OUTCOMES
    ):
        raise ValueError("Apps outcome must match the selected primary claim")
    return verdicts[0]


def validate_result(result: object) -> None:
    """Check the Apps envelope and its links to the core responses."""
    if not isinstance(result, dict) or result.keys() != _RESULT_FIELDS:
        raise ValueError("invalid AppsResultV1 fields")
    if type(result["schema_version"]) is not int or result["schema_version"] != 1:
        raise ValueError("unsupported Apps result version")
    operation = result["operation"]
    if operation not in ("prepare", "execute", "assess"):
        raise ValueError("invalid Apps operation")
    outcome, exit_code = result["outcome"], result["exit_code"]
    if (
        not isinstance(outcome, str)
        or type(exit_code) is not int
        or _EXIT_CODES.get(outcome) != exit_code
    ):
        raise ValueError("inconsistent Apps outcome and exit code")
    error = result["error"]
    if error is not None:
        _check_error(error, outcome)
    if result["reason_code"] != (error["code"] if error is not None else None):
        raise ValueError("inconsistent Apps reason and error")
    if error is None and outcome in ("error", "in_progress", "persistence_error"):
        raise ValueError("expected a PublicError for the Apps outcome")
    for field, (id_field, version_field) in _REF_FIELDS.items():
        if result[field] is not None:
            _check_ref(result[field], id_field, version_field)
    for field in ("primary_scope", "primary_claim_id", "attempt_id"):
        if result[field] is not None and (
            not isinstance(result[field], str) or not result[field]
        ):
            raise ValueError("invalid Apps identifier")

    record = result["execution"]
    if record is not None:
        _check_record(result, record)

    output_error = (
        error is not None and error["code"] == "ArchivePersistenceError"
        and error["stage"] == "output_publication"
    )
    if operation == "assess":
        _check_assessment(result, output_error=output_error)
        return

    if any(
        result[field] is not None
        for field in ("assessment", "assessment_ref", "primary_scope", "primary_claim_id")
    ):
        raise ValueError("unexpected assessment fields for the Apps operation")
    if operation == "prepare":
        if error is not None and any(
            error[key] is not None
            for key in ("attempt_id", "started", "latest_record_ref", "partial_record")
        ):
            raise ValueError("unexpected execution facts in prepare error")
        if any(
            result[field] is not None
            for field in ("execution", "record_ref", "attempt_id", "admission")
        ):
            raise ValueError("unexpected execution fields for prepare")
        if (
            not output_error
            and (result["prepared_ref"] is not None) != (error is None)
        ):
            raise ValueError("inconsistent prepare archive reference")
        if error is None and outcome != "success":
            raise ValueError("expected a successful prepare outcome")
    elif record is None:
        if (
            error is None
            or any(
                result[field] is not None
                for field in ("record_ref", "prepared_ref", "attempt_id", "admission")
            )
        ):
            raise ValueError("execute result requires a committed record for execution facts")
    elif not output_error and (error is not None or outcome != _execute_outcome(record)):
        raise ValueError("Apps execute outcome must match the committed record")
