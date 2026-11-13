"""Route fixed artifact references through trusted local storage mounts."""

from io import BytesIO

from inferrsi._json_v1 import encode
from inferrsi.harness.errors import PublicError

from .archive import _logical_path, _read_directory_artifact, _storage_mounts
from .result import validate_artifact_ref


class ArtifactReader:
    """Read exact artifact bytes from one matching mount, without write access."""

    def __init__(self, storage_config: dict):
        self._mounts = _storage_mounts(storage_config)
        if any(
            kind == "harness_sqlite" and prefix != f"stores/{namespace}"
            for namespace, (kind, prefix, _) in self._mounts.items()
        ):
            raise PublicError(
                "InvalidRequest", stage="storage_config",
                message="A Harness SQLite mount must use its canonical stores prefix.",
            )

    def open(self, ref: dict) -> BytesIO:
        """Return a stream of verified bytes for a fixed ArtifactRef."""
        try:
            validate_artifact_ref(ref)
        except ValueError:
            raise PublicError(
                "InvalidRequest", stage="open", message="Invalid ArtifactRef."
            ) from None

        location = ref["location"]
        try:
            _logical_path(location)
        except ValueError:
            raise PublicError(
                "UnsafeArtifactPath", stage="open", message="Invalid artifact location."
            ) from None

        for namespace, (kind, prefix, root) in self._mounts.items():
            if location.startswith(prefix + "/"):
                if kind == "directory":
                    return BytesIO(_read_directory_artifact(prefix, root, ref, stage="open"))
                # Archive-only consumers never load the SQLite implementation.
                from inferrsi.harness.run_store import RunStoreReader

                return RunStoreReader.from_mount({
                    "prefix": prefix, "kind": kind, "root": str(root), "namespace": namespace,
                }).open(ref)
        raise PublicError(
            "UnsafeArtifactPath", stage="open", message="No mount matches the artifact location."
        )


class AssessmentArtifacts:
    """Limit an assessor to the frozen protocol and the selected revision's retained bytes."""

    def __init__(self, reader: ArtifactReader, prepared: dict, record: dict):
        self._reader = reader
        refs = [prepared["protocol"], *(item["artifact"] for item in prepared["resources"])]
        for field, key in (("artifacts", "artifact"), ("bound_inputs", "retained_ref")):
            refs.extend(
                item[key] for item in record[field] if item["attempt_id"] == record["attempt_id"]
            )
        refs.extend(
            item["artifact"] for step in record["steps"] for item in step["artifacts"]
            if item["attempt_id"] == record["attempt_id"]
        )
        refs.extend(
            ref for check in record["checks"] if check["attempt_id"] == record["attempt_id"]
            for ref in check["evidence_refs"]
        )
        if record["target_binding"] is not None:
            refs.extend(item["environment_identity"]
                        for item in record["target_binding"]["runtime_bindings_by_step"])
        self._refs = {encode(ref) for ref in refs if ref is not None}

    def open(self, ref: dict) -> BytesIO:
        try:
            validate_artifact_ref(ref)
        except ValueError:
            raise PublicError(
                "InvalidRequest", stage="open", message="Invalid ArtifactRef."
            ) from None
        if encode(ref) not in self._refs:
            raise PublicError(
                "ArtifactUnavailable", stage="open",
                message="The artifact is outside the fixed assessment inputs.",
            )
        return self._reader.open(ref)
