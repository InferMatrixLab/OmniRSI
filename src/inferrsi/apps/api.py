"""Python entry points for assembling and publishing Apps operations."""

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Protocol

from inferrsi._json_v1 import decode, encode
from inferrsi.harness.errors import PublicError
from inferrsi.harness.execution_spec import request_digest

from .archive import (
    ResourceReader,
    RunOutput,
    archive_assessment,
    archive_mount,
    archive_prepared,
    publish_result,
    read_archive_artifact,
    read_prepared,
    validate_output_id,
)
from .artifact_reader import ArtifactReader, AssessmentArtifacts
from .bridge import bind_execution as _bind_prepared_execution
from .bridge import check_assessment_identity, execution_fields, to_evidence, verify_protocol
from .result import (
    assessment_result,
    execution_result,
    is_public_error,
    preparation_result,
    validate_result,
)

_PREPARE_FIELDS = {"schema_version", "request"}
_BENCHMARK_REQUEST_FIELDS = {
    "schema_version", "task", "operation", "validation_scope", "input_pins", "task_parameters",
    "comparison_conditions", "expected_candidate", "budget_policy_refs",
}


def _validation_error(code: str, message: str, *, stage: str = "validation") -> dict:
    return {
        "code": code,
        "stage": stage,
        "attempt_id": None,
        "started": None,
        "latest_record_ref": None,
        "partial_record": None,
        "retained_refs": [],
        "field_errors": [],
        "message": message,
    }


def _preserves_request(spec: dict, fields: dict, inputs: list) -> bool:
    if not fields.keys() <= spec.keys() or encode(fields) != encode(
        {field: spec[field] for field in fields}
    ):
        return False
    bound_inputs = spec.get("inputs")
    if not isinstance(bound_inputs, list):
        return False
    # The binder can add inputs from the frozen preparation.
    bound_bytes = {encode(item) for item in bound_inputs}
    return all(encode(item) in bound_bytes for item in inputs)


def _port_call(port: Callable[[dict], object], value: dict) -> object:
    # Programming/host exceptions are not port replies: do not silently swallow them.
    try:
        return port(value)
    except PublicError as error:
        return error.to_json()


def _port_error(
    error: dict, fields: dict, digest: str | None = None, *, stage: str
) -> dict:
    result = execution_result(error=error)
    validate_result(result)
    partial = error["partial_record"]
    refs = [error["latest_record_ref"]] if error["latest_record_ref"] is not None else []
    if (error["attempt_id"] is not None and error["attempt_id"] != fields["attempt_id"]) or any(
        ref["attempt_id"] != fields["attempt_id"] for ref in refs
    ) or (partial is not None and (
        any(partial[key] != fields[key] for key in ("request_id", "run_id", "attempt_id"))
        or (digest is not None and partial["request_digest"] is not None
            and partial["request_digest"] != digest)
    )):
        return execution_result(error=_validation_error(
            "IdentityMismatch", "Port error facts belong to a different execution request.",
            stage=stage,
        ))
    return result


def _invalid_port_result(stage: str) -> dict:
    return execution_result(error=_validation_error(
        "InvalidExecutionSpec", "The execution port returned a malformed response.", stage=stage
    ))


def _execute_saved(
    request: dict,
    fields: dict,
    bind_execution: Callable[[dict], dict],
    execute_harness: Callable[[dict], dict],
) -> dict:
    spec = _port_call(bind_execution, deepcopy(request))
    try:
        if is_public_error(spec):
            return _port_error(spec, fields, stage="binder_validation")
        if not isinstance(spec, dict) or not _preserves_request(
            spec, fields, request["proposal"]["inputs"]
        ):
            return _invalid_port_result("binder_validation")
        submitted_digest = request_digest(spec)
    except (ValueError, TypeError, KeyError):
        return _invalid_port_result("binder_validation")

    response = _port_call(execute_harness, spec)
    try:
        if is_public_error(response):
            return _port_error(
                response, fields, submitted_digest, stage="harness_response_validation"
            )
        if not isinstance(response, dict) or "request_digest" not in response:
            return _invalid_port_result("harness_response_validation")
        if response["request_digest"] != submitted_digest or any(
            response.get(field) != fields[field]
            for field in ("request_id", "run_id", "attempt_id", "prepared_ref")
        ):
            return execution_result(error=_validation_error(
                "IdentityMismatch", "Harness returned a record for a different request.",
                stage="harness_response_validation",
            ))
        result = execution_result(record=response)
        validate_result(result)
        return result
    except (ValueError, TypeError, KeyError):
        return _invalid_port_result("harness_response_validation")


def _prepare_request(request: dict) -> dict:
    encode(request)
    benchmark_request = request.get("request") if isinstance(request, dict) else None
    envelopes = (request, _PREPARE_FIELDS), (benchmark_request, _BENCHMARK_REQUEST_FIELDS)
    for value, fields in envelopes:
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
            raise ValueError("expected a numeric schema version")
        if value["schema_version"] != 1:
            raise PublicError(
                "UnsupportedSchema", stage="validation",
                message="Unsupported prepare request version.",
            )
        if value.keys() != fields:
            raise ValueError("unexpected prepare request fields")
    return benchmark_request


def _prepare_port_error(error: dict) -> dict:
    try:
        result = preparation_result(error=error)
        validate_result(result)
    except (ValueError, TypeError, KeyError):
        return preparation_result(error=_validation_error(
            "InvalidRequest", "Benchmark returned a malformed prepare error.",
            stage="benchmark_response_validation",
        ))
    return result


def prepare(
    request: dict,
    *,
    workspace: Path,
    storage_config: dict,
    prepare_benchmark: Callable[[dict], tuple[dict, bytes, ResourceReader] | dict],
    encode_prepared: Callable[[dict], bytes],
    validate_prepared: Callable[[dict, bytes], None],
    output_id: str | None = None,
) -> RunOutput:
    """Freeze a Benchmark request, archive its closure, and publish the Apps result.

    The host adapts the Benchmark prepare port to return the prepared object,
    protocol bytes and a role-keyed reader. Each stream returned by the reader
    is owned by Apps for that read. The pure encoder and validator raise
    ValueError for invalid content or PublicError for a structured rejection.
    The host adapts any Benchmark-specific rejection exception to those types.
    No Harness port is called during preparation.
    """
    if output_id is not None:
        validate_output_id(output_id)
    try:
        benchmark_request = _prepare_request(request)
        mount = archive_mount(storage_config, for_write=True)
    except ValueError:
        result = preparation_result(error=_validation_error(
            "InvalidRequest", "Expected a v1 Apps prepare request."
        ))
    except PublicError as error:
        result = preparation_result(error=error.to_json())
    else:
        response = _port_call(prepare_benchmark, deepcopy(benchmark_request))
        try:
            if is_public_error(response):
                result = _prepare_port_error(response)
            else:
                if not isinstance(response, tuple) or len(response) != 3:
                    raise ValueError("expected a prepared closure")
                prepared, protocol, read_resource = response
                if not isinstance(prepared, dict) or not callable(read_resource):
                    raise ValueError("expected a prepared object and resource reader")
                if any(encode(prepared[key]) != encode(benchmark_request[key]) for key in (
                    "task", "operation", "validation_scope", "expected_candidate"
                )):
                    raise PublicError(
                        "IdentityMismatch", stage="benchmark_response_validation",
                        message="Benchmark prepared a different request.",
                    )
                prepared_ref = archive_prepared(
                    prepared, protocol, read_resource, mount=mount,
                    encode_prepared=encode_prepared, validate_prepared=validate_prepared,
                )
                result = preparation_result(prepared_ref=prepared_ref)
        except PublicError as error:
            result = _prepare_port_error(error.to_json())
        except (ValueError, TypeError, KeyError):
            result = preparation_result(error=_validation_error(
                "InvalidRequest", "Benchmark returned a malformed prepared closure.",
                stage="benchmark_response_validation",
            ))
    return publish_result(workspace, result, output_id=output_id)


def execute(
    request: dict,
    *,
    workspace: Path,
    execute_harness: Callable[[dict], dict],
    bind_execution: Callable[[dict], dict] | None = None,
    storage_config: dict | None = None,
    output_id: str | None = None,
) -> RunOutput:
    """Bind the saved request and submit its original attempt to Harness.

    The trusted host supplies the Harness port and a storage configuration for
    the Apps binder, or provides an existing custom binder. Either port may
    raise PublicError or return its wire form. Workspace and output_id locate
    Apps output. Harness owns the RunStore, request identity and replay decisions.
    """
    if output_id is not None:
        validate_output_id(output_id)
    try:
        encode(request)
        fields = execution_fields(request)
        if (bind_execution is None) == (storage_config is None):
            raise ValueError("expected one storage configuration or binder")
    except ValueError:
        result = execution_result(error=_validation_error(
            "InvalidRequest", "Expected a v1 execute request with a pre-allocated attempt."
        ))
        return publish_result(workspace, result, output_id=output_id)

    binder = bind_execution
    if binder is None:
        def bind_from_archive(saved: dict) -> dict:
            prepared = read_prepared(storage_config, saved["prepared_ref"])
            if "protocol" not in prepared:
                raise PublicError(
                    "InvalidExecutionSpec", stage="binder_validation",
                    message="The fixed preparation has no protocol reference.",
                )
            protocol = read_archive_artifact(storage_config, prepared["protocol"])
            verify_protocol(prepared, protocol)
            return _bind_prepared_execution(saved, prepared)

        binder = bind_from_archive
    result = _execute_saved(request, fields, binder, execute_harness)
    return publish_result(workspace, result, output_id=output_id)


_ASSESS_FIELDS = {"schema_version", "record_ref", "primary_scope", "primary_claim_id"}
_ASSESSMENT_FIELDS = {
    "schema_version", "assessment_id", "task", "grader", "assessor", "prepared_ref",
    "record_ref", "protocol_digest", "candidate", "attempt_id", "claims", "metrics",
    "eligibility", "comparison", "evidence_artifacts", "assessed_at", "supersedes",
    "derived_from", "limitations",
}


class Assessor(Protocol):
    """Benchmark's offline evaluation port."""

    def __call__(
        self, prepared: dict, evidence: dict, /, *, artifacts: AssessmentArtifacts,
    ) -> dict: ...


def _assessment_request(request: dict) -> tuple[str, str]:
    if (
        not isinstance(request, dict) or request.keys() != _ASSESS_FIELDS
        or type(request["schema_version"]) is not int
    ):
        raise ValueError("expected AppsAssessRequest fields")
    if request["schema_version"] != 1:
        raise PublicError(
            "UnsupportedSchema", stage="validation", message="Unknown assess version."
        )
    claim, scope = request["primary_claim_id"], request["primary_scope"]
    ref = request["record_ref"]
    if (
        not isinstance(claim, str) or not claim or not isinstance(scope, str) or not scope
        or not isinstance(ref, dict) or ref.keys() != {"attempt_id", "record_version", "artifact"}
    ):
        raise ValueError("expected an exact primary claim key and RecordRef")
    return claim, scope


def _assess_fixed(
    request: dict, storage_config: dict, assess_benchmark: Assessor,
    validate_assessment: Callable[[dict], None], encode_assessment: Callable[[dict], bytes],
) -> dict:
    try:
        encode(request)
        primary = _assessment_request(request)
        record_ref = request["record_ref"]
        mount = archive_mount(storage_config, for_write=True)
        artifacts = ArtifactReader(storage_config)
        with artifacts.open(record_ref["artifact"]) as record_bytes:
            record = decode(record_bytes.read())
        prepared_ref = record["prepared_ref"]
        prepared = read_prepared(storage_config, prepared_ref)
        protocol = read_archive_artifact(storage_config, prepared["protocol"])
        verify_protocol(prepared, protocol)
        evidence = to_evidence(prepared, prepared_ref, record_ref, record)
        claim_keys = [
            (claim["claim_id"], claim["validation_scope"])
            for claim in decode(protocol)["claim_definitions"]
        ]
        if primary not in claim_keys:
            raise PublicError(
                "InvalidRequest", stage="primary_claim", message="Unknown primary claim key."
            )
        allowed_artifacts = AssessmentArtifacts(artifacts, prepared, record)
    except (KeyError, TypeError, ValueError):
        raise PublicError(
            "InvalidRequest", stage="assessment_binding",
            message="Expected a fixed assessment request and record.",
        ) from None
    try:
        assessment = assess_benchmark(
            deepcopy(prepared), deepcopy(evidence), artifacts=allowed_artifacts,
        )
    except PublicError as error:
        assessment = error.to_json()
    try:
        if is_public_error(assessment):
            if (
                assessment["attempt_id"] not in (None, record_ref["attempt_id"])
                or any(assessment[key] is not None
                       for key in ("started", "latest_record_ref", "partial_record"))
                or assessment["retained_refs"]
            ):
                raise ValueError("unexpected execution facts in Benchmark error")
            result = assessment_result(error=assessment)
            validate_result(result)
            return result
        expected = {
            "task": prepared["task"], "grader": prepared["grader"],
            "prepared_ref": prepared_ref, "record_ref": record_ref,
            **{field: record[field] for field in ("protocol_digest", "candidate", "attempt_id")},
        }
        if (
            not isinstance(assessment, dict) or assessment.keys() != _ASSESSMENT_FIELDS
            or type(assessment["schema_version"]) is not int or assessment["schema_version"] != 1
            or any(encode(assessment[key]) != encode(value) for key, value in expected.items())
        ):
            raise ValueError("assessor returned a different evaluation")
        returned_keys = [
            (claim["claim_id"], claim["validation_scope"]) for claim in assessment["claims"]
        ]
        returned_set = set(returned_keys)
        if returned_keys != [key for key in claim_keys if key in returned_set]:
            raise ValueError("assessor claim keys must follow the frozen protocol")
        validate_assessment(deepcopy(assessment))
        check_assessment_identity(assessment)
        data = encode_assessment(deepcopy(assessment))
        if not isinstance(data, bytes) or data != encode(assessment):
            raise ValueError("Benchmark encoder returned different assessment bytes")
        # Check the selected claim before making the assessment durable.
        assessment_result(primary=primary, assessment=assessment)
    except (KeyError, TypeError, ValueError, AttributeError, PublicError):
        raise PublicError(
            "AssessorError", stage="assessor_response_validation",
            message="Benchmark returned an invalid assessment or omitted the selected claim.",
        ) from None
    try:
        ref = archive_assessment(assessment["assessment_id"], data, mount=mount)
    except PublicError as error:
        return assessment_result(primary=primary, assessment=assessment, error=error.to_json())
    return assessment_result(primary=primary, assessment=assessment, assessment_ref=ref)


def assess(
    request: dict,
    *,
    workspace: Path,
    storage_config: dict,
    assess_benchmark: Assessor,
    validate_assessment: Callable[[dict], None],
    encode_assessment: Callable[[dict], bytes],
    output_id: str | None = None,
) -> RunOutput:
    """Assess one fixed record with the supplied offline Benchmark port.

    The port receives the frozen preparation, EvidenceInput and a reader scoped
    to those inputs. It can return or raise a structured public rejection.
    The pure Benchmark validator and encoder check and serialize the result.
    """
    if output_id is not None:
        validate_output_id(output_id)
    try:
        result = _assess_fixed(
            request, storage_config, assess_benchmark, validate_assessment, encode_assessment,
        )
    except PublicError as error:
        result = assessment_result(error=error.to_json())
    return publish_result(workspace, result, output_id=output_id)
