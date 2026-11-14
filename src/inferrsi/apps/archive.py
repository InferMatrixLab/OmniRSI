"""Store fixed preparations and standardized Apps output in authorized directories."""

import ctypes
import errno
import os
import re
import sys
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from uuid import uuid4

from inferrsi._json_v1 import decode, encode
from inferrsi.harness.errors import PublicError

from .result import publication_error, validate_artifact_ref, validate_result

_OUTPUT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_RESERVED_NAMES = re.compile(r"CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]", re.IGNORECASE)
_PATH_CONTROL = re.compile(r'[\\:\x00-\x1f\x7f]')
_INVALID_FILE_PART = re.compile(r'[<>"|?*]')
_SEMANTIC_ID = re.compile(r"sha256:[0-9a-f]{64}")
_STORAGE_FIELDS = {"schema_version", "mounts", "archive_namespace", "harness_namespace"}
_MOUNT_FIELDS = {"prefix", "kind", "root", "namespace"}
ResourceReader = Callable[[str], bytes | BinaryIO]


@dataclass(frozen=True)
class RunOutput:
    """The Apps result and the location of its publication, if successful."""

    output_id: str
    directory: Path | None
    result: dict


def _logical_path(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or _PATH_CONTROL.search(value):
        raise ValueError("expected a canonical relative location")
    parts = tuple(value.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("expected a canonical relative location")
    return parts


def _storage_mounts(config: dict) -> dict[str, tuple[str, str, Path]]:
    try:
        if (
            not isinstance(config, dict)
            or config.keys() != _STORAGE_FIELDS
            or type(config["schema_version"]) is not int
            or config["schema_version"] != 1
            or not isinstance(config["mounts"], list)
        ):
            raise ValueError
        mounts = {}
        prefixes = []
        for mount in config["mounts"]:
            if not isinstance(mount, dict) or mount.keys() != _MOUNT_FIELDS:
                raise ValueError
            namespace = mount["namespace"]
            if not isinstance(namespace, str) or not _OUTPUT_ID.fullmatch(namespace):
                raise ValueError
            if namespace in mounts or mount["kind"] not in ("directory", "harness_sqlite"):
                raise ValueError
            prefix = _logical_path(mount["prefix"])
            root_path = mount["root"]
            if not isinstance(root_path, str) or "\0" in root_path:
                raise ValueError
            root = Path(root_path)
            if not root.is_absolute() or root.anchor.startswith(("\\\\", "//")):
                raise ValueError
            if mount["kind"] == "harness_sqlite" and prefix[-1] != namespace:
                raise ValueError
            if any(
                prefix[:len(other)] == other or other[:len(prefix)] == prefix for other in prefixes
            ):
                raise ValueError
            prefixes.append(prefix)
            mounts[namespace] = (mount["kind"], mount["prefix"], root)
        if config["archive_namespace"] not in mounts or config["harness_namespace"] not in mounts:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise PublicError(
            "InvalidRequest", stage="storage_config", message="Invalid Apps storage configuration."
        ) from None
    return mounts


def archive_mount(config: dict, *, for_write: bool = False) -> tuple[str, Path]:
    """Select the trusted local archive mount and check write configuration."""
    mounts = _storage_mounts(config)
    kind, prefix, root = mounts[config["archive_namespace"]]
    if kind != "directory":
        raise PublicError(
            "InvalidRequest", stage="storage_config", message="The Apps archive needs a directory."
        )
    if for_write:
        try:
            root = _directory(root)
        except (OSError, PublicError):
            raise PublicError(
                "InvalidRequest", stage="storage_config",
                message="The Apps archive root must be an existing authorized directory.",
            ) from None
    return prefix, root


def _prepared_identity(prepared: object) -> str:
    if (
        not isinstance(prepared, dict)
        or type(prepared.get("schema_version")) is not int
        or prepared["schema_version"] != 1
        or not isinstance(prepared.get("prepared_id"), str)
        or not _SEMANTIC_ID.fullmatch(prepared["prepared_id"])
    ):
        raise ValueError("invalid prepared identity")
    return prepared["prepared_id"]


def _prepared_error(code: str, message: str) -> PublicError:
    return PublicError(code, stage="prepared_archive", message=message)


def _check_bytes(ref: dict, size: int, digest: str, *, stage: str = "prepared_archive") -> None:
    if ref["size_bytes"] != size or ref["sha256"] != digest:
        raise PublicError(
            "ArtifactIntegrityError", stage=stage, message="Artifact bytes do not match."
        )


def _write_verified(path: Path, ref: dict, source: bytes | BinaryIO) -> None:
    digest = sha256()
    size = 0
    reader = BytesIO(source) if isinstance(source, bytes) else source
    if not callable(getattr(reader, "read", None)):
        raise ValueError("expected bytes or a binary resource stream")
    with reader:
        with path.open("xb") as target:
            while True:
                try:
                    chunk = reader.read(1024 * 1024)
                except OSError:
                    raise _prepared_error(
                        "ArtifactUnavailable", "Could not read a resource."
                    ) from None
                if not isinstance(chunk, bytes):
                    raise ValueError("expected a binary resource")
                if not chunk:
                    break
                size += len(chunk)
                if size > ref["size_bytes"]:
                    raise _prepared_error(
                        "ArtifactIntegrityError", "Prepared artifact is oversized."
                    )
                digest.update(chunk)
                target.write(chunk)
            _check_bytes(ref, size, digest.hexdigest())
            target.flush()
            os.fsync(target.fileno())


def _directory(root: Path) -> Path:
    try:
        resolved = root.resolve(strict=True)
    except RuntimeError:
        raise _prepared_error("UnsafeArtifactPath", "Invalid Apps archive directory.") from None
    if root != resolved or not resolved.is_dir():
        raise _prepared_error("UnsafeArtifactPath", "Invalid Apps archive directory.")
    return resolved


def archive_prepared(
    prepared: dict,
    protocol: bytes,
    read_resource: ResourceReader,
    *,
    mount: tuple[str, Path],
    encode_prepared: Callable[[dict], bytes],
    validate_prepared: Callable[[dict, bytes], None],
) -> dict:
    """Commit a frozen preparation and its complete closure on the selected mount."""
    identity = _prepared_identity(prepared)
    prefix, root = mount
    directory = uuid4().hex
    location = f"{prefix}/{directory}"
    relocated = deepcopy(prepared)
    protocol_ref = relocated["protocol"]
    validate_artifact_ref(protocol_ref)
    _logical_path(protocol_ref["location"])
    if not isinstance(protocol, bytes):
        raise ValueError("expected canonical protocol bytes")
    _check_bytes(protocol_ref, len(protocol), sha256(protocol).hexdigest())
    if not isinstance(decode(protocol), dict):
        raise ValueError("expected a protocol object")
    protocol_ref["location"] = f"{location}/protocol.json"
    resources = relocated["resources"]
    if not isinstance(resources, list) or any(not isinstance(item, dict) for item in resources):
        raise ValueError("expected a role-keyed resource closure")
    roles = [item["role"] for item in resources]
    if any(not isinstance(role, str) or not role for role in roles) or roles != sorted(set(roles)):
        raise ValueError("expected distinct resources sorted by role")
    for index, resource in enumerate(resources):
        validate_artifact_ref(resource["artifact"])
        _logical_path(resource["artifact"]["location"])
        resource["artifact"]["location"] = f"{location}/resource-{index}"

    expected = encode(relocated)
    data = encode_prepared(relocated)
    if not isinstance(data, bytes) or data != expected:
        raise ValueError("Benchmark encoded a different prepared object")
    frozen = decode(data)
    validate_prepared(deepcopy(frozen), protocol)
    ref = {
        "prepared_id": identity,
        "schema_version": 1,
        "artifact": {
            "location": f"{location}/prepared.json",
            "sha256": sha256(data).hexdigest(),
            "size_bytes": len(data),
            "media_type": "application/json",
        },
    }

    published = False
    try:
        root = _directory(root)
        with TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
            staging = Path(temporary)
            _write_file(staging / "prepared.json", data)
            _write_file(staging / "protocol.json", protocol)
            for index, resource in enumerate(frozen["resources"]):
                try:
                    source = read_resource(resource["role"])
                except (KeyError, OSError):
                    source = None
                if source is None:
                    raise _prepared_error("ArtifactUnavailable", "A prepared resource is missing.")
                _write_verified(staging / f"resource-{index}", resource["artifact"], source)
            _sync_directory(staging)
            _publish_directory(staging, root / directory)
            published = True
            _sync_directory(root)
    except OSError:
        message = (
            "Could not confirm the durability of the prepared archive."
            if published else "Could not commit the prepared closure."
        )
        raise _prepared_error("ArchivePersistenceError", message) from None
    return ref


def read_archive_artifact(storage_config: dict, ref: dict) -> bytes:
    """Read and verify an artifact under the selected Apps archive mount."""
    prefix, root = archive_mount(storage_config)
    return _read_directory_artifact(prefix, root, ref)


def _read_directory_artifact(
    prefix: str, root: Path, ref: dict, *, stage: str = "prepared_archive",
) -> bytes:
    try:
        validate_artifact_ref(ref)
        parts = _logical_path(ref["location"])
        mount_parts = _logical_path(prefix)
        suffix = parts[len(mount_parts):]
        if (
            parts[:len(mount_parts)] != mount_parts
            or not suffix
            or suffix[0].startswith(".pending-")
            or any(
                part.rstrip(" .") != part or _INVALID_FILE_PART.search(part)
                or _RESERVED_NAMES.fullmatch(part.split(".")[0])
                for part in suffix
            )
        ):
            raise ValueError
        data = _read_file(_directory(root).joinpath(*suffix))
    except (ValueError, RuntimeError, PublicError):
        raise PublicError(
            "UnsafeArtifactPath", stage=stage, message="Invalid directory artifact location."
        ) from None
    except OSError:
        raise PublicError(
            "ArtifactUnavailable", stage=stage, message="Could not read the directory artifact."
        ) from None
    _check_bytes(ref, len(data), sha256(data).hexdigest(), stage=stage)
    return data


def read_prepared(storage_config: dict, ref: dict) -> dict:
    """Read a fixed PreparedRef and verify its original bytes and identity."""
    try:
        identity = _prepared_identity(ref)
        if ref.keys() != {"prepared_id", "schema_version", "artifact"}:
            raise ValueError
        validate_artifact_ref(ref["artifact"])
        if ref["artifact"]["media_type"] != "application/json":
            raise ValueError
    except ValueError:
        raise _prepared_error("InvalidRequest", "Invalid PreparedRef.") from None
    data = read_archive_artifact(storage_config, ref["artifact"])
    try:
        prepared = decode(data)
        prepared_id = _prepared_identity(prepared)
    except ValueError:
        raise _prepared_error(
            "ArtifactIntegrityError", "Invalid archived prepared bytes."
        ) from None
    if prepared_id != identity:
        raise _prepared_error("IdentityMismatch", "PreparedRef identifies a different preparation.")
    return prepared


def archive_assessment(identity: str, data: bytes, *, mount: tuple[str, Path]) -> dict:
    """Commit canonical assessment bytes and return their fixed reference."""
    prefix, root = mount
    directory = uuid4().hex
    ref = {
        "assessment_id": identity,
        "schema_version": 1,
        "artifact": {
            "location": f"{prefix}/{directory}/assessment.json",
            "sha256": sha256(data).hexdigest(),
            "size_bytes": len(data),
            "media_type": "application/json",
        },
    }
    published = False
    try:
        root = _directory(root)
        with TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
            staging = Path(temporary)
            _write_file(staging / "assessment.json", data)
            _sync_directory(staging)
            _publish_directory(staging, root / directory)
            published = True
            _sync_directory(root)
    except (OSError, PublicError):
        message = (
            "Could not confirm the durability of the assessment archive."
            if published else "Could not commit the assessment."
        )
        raise PublicError(
            "ArchivePersistenceError", stage="assessment_archive", message=message
        ) from None
    return ref


def read_assessment(storage_config: dict, ref: dict) -> dict:
    """Read one archived assessment for reporting or independent replay."""
    try:
        if (
            not isinstance(ref, dict)
            or ref.keys() != {"assessment_id", "schema_version", "artifact"}
        ):
            raise ValueError("invalid AssessmentRef fields")
        validate_artifact_ref(ref["artifact"])
        if (
            type(ref["schema_version"]) is not int or ref["schema_version"] != 1
            or not isinstance(ref["assessment_id"], str)
            or not _SEMANTIC_ID.fullmatch(ref["assessment_id"])
            or ref["artifact"]["media_type"] != "application/json"
        ):
            raise ValueError("invalid AssessmentRef metadata")
    except ValueError:
        raise PublicError(
            "InvalidRequest", stage="assessment_archive", message="Invalid AssessmentRef."
        ) from None
    prefix, root = archive_mount(storage_config)
    data = _read_directory_artifact(prefix, root, ref["artifact"], stage="assessment_archive")
    try:
        assessment = decode(data)
        if not isinstance(assessment, dict):
            raise ValueError("expected an archived assessment object")
    except ValueError:
        raise PublicError(
            "ArtifactIntegrityError", stage="assessment_archive",
            message="Invalid archived assessment bytes.",
        ) from None
    if (
        assessment.get("assessment_id") != ref["assessment_id"]
        or type(assessment.get("schema_version")) is not int or assessment["schema_version"] != 1
    ):
        raise PublicError(
            "IdentityMismatch", stage="assessment_archive",
            message="AssessmentRef identifies a different assessment.",
        )
    return assessment


def validate_output_id(output_id: str) -> None:
    if (
        not isinstance(output_id, str)
        or not _OUTPUT_ID.fullmatch(output_id)
        or output_id.endswith(".")
        or _RESERVED_NAMES.fullmatch(output_id.split(".", 1)[0])
    ):
        raise ValueError("output_id must be a portable directory name")


def _output_root(workspace: Path, *, create: bool) -> Path:
    root = Path(workspace).resolve(strict=True) / "output"
    if root.is_symlink() or root.resolve() != root:
        raise OSError("invalid Apps output root")
    if create:
        root.mkdir(exist_ok=True)
    return root


def _result_entry(data: bytes) -> dict:
    return {
        "path": "result.json",
        "sha256": sha256(data).hexdigest(),
        "size_bytes": len(data),
        "media_type": "application/json",
    }


def _manifest(output_id: str, created_at: str, result: dict, entry: dict) -> dict:
    return {
        "schema_version": 1,
        "output_id": output_id,
        "created_at": created_at,
        "operation": result["operation"],
        "run_id": result["execution"]["run_id"] if result["execution"] is not None else None,
        "attempt_id": result["attempt_id"],
        "result": entry,
    }


def _read_file(path: Path) -> bytes:
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError("expected a regular Apps output file")
    return path.read_bytes()


def _read_output(directory: Path, output_id: str) -> dict:
    if directory.resolve(strict=True) != directory or not directory.is_dir():
        raise ValueError("expected an Apps output directory")
    manifest_data = _read_file(directory / "manifest.json")
    manifest = decode(manifest_data)
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int:
        raise ValueError("invalid Apps output manifest")
    if manifest["schema_version"] != 1:
        raise ValueError("unsupported Apps output manifest version")
    created_at = manifest.get("created_at")
    if not isinstance(created_at, str) or not created_at.endswith("Z") or "T" not in created_at:
        raise ValueError("invalid Apps output creation time")
    datetime.fromisoformat(created_at[:-1] + "+00:00")

    entry = manifest.get("result")
    if not isinstance(entry, dict) or entry.get("path") != "result.json":
        raise ValueError("invalid Apps manifest result path")
    data = _read_file(directory / "result.json")
    expected_entry = _result_entry(data)
    if entry != expected_entry:
        raise ValueError("Apps output manifest does not match result.json bytes")
    result = decode(data)
    validate_result(result)
    if manifest_data != encode(_manifest(output_id, created_at, result, expected_entry)):
        raise ValueError("Apps output manifest does not match result.json")
    return result


def read_result(workspace: Path, output_id: str) -> RunOutput:
    """Read the named publication, checking its version, metadata and result bytes."""
    validate_output_id(output_id)
    directory = _output_root(workspace, create=False) / output_id
    return RunOutput(output_id, directory, _read_output(directory, output_id))


def _write_file(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _publish_directory(staging: Path, destination: Path) -> None:
    """Atomically publish a complete directory without replacing any entry."""
    if os.name == "nt":
        staging.rename(destination)
        return

    libc = ctypes.CDLL(None, use_errno=True)
    source = os.fsencode(staging)
    target = os.fsencode(destination)
    try:
        if sys.platform == "linux":
            at_fdcwd, rename_noreplace = -100, 1
            rename = libc.renameat2
            rename.argtypes = (
                ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
            )
            status = rename(at_fdcwd, source, at_fdcwd, target, rename_noreplace)
        elif sys.platform == "darwin":
            rename_excl = 0x00000004
            rename = libc.renamex_np
            rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
            status = rename(source, target, rename_excl)
        else:
            raise OSError(errno.ENOTSUP, "atomic no-replace directory rename is unavailable")
    except AttributeError as exc:
        raise OSError(errno.ENOTSUP, "atomic no-replace directory rename is unavailable") from exc
    if status != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _sync_directory(path: Path) -> None:
    """Persist directory entries on POSIX; Windows has no portable equivalent."""
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publication_message(error: Exception, *, published: bool) -> str:
    # Never expose local paths or arbitrary exception messages in shared output.
    if published:
        return (
            "Output directory was published, but durability confirmation failed. "
            "Publication state is uncertain; inspect the existing output ID before retrying."
        )
    code = getattr(error, "errno", None)
    if code in (errno.EEXIST, errno.ENOTEMPTY) or getattr(error, "winerror", None) in (80, 183):
        return "Output publication conflict: destination already exists and was not replaced."
    if code in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.EINVAL):
        return ("Output publication unsupported: "
                "atomic no-replace rename or directory sync unavailable.")
    if isinstance(error, ValueError):
        return "Output publication validation failed; no output was published."
    return "Output publication I/O failed; no output was published."


def publish_result(workspace: Path, result: dict, *, output_id: str | None = None) -> RunOutput:
    """Publish an AppsResultV1 after the underlying operation has returned."""
    if output_id is None:
        output_id = uuid4().hex
    validate_output_id(output_id)
    validate_result(result)
    data = encode(result)
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest = encode(_manifest(output_id, created_at, result, _result_entry(data)))

    published = False
    try:
        root = _output_root(workspace, create=True)
        _sync_directory(root.parent)
        destination = root / output_id
        with TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
            staging = Path(temporary)
            _write_file(staging / "result.json", data)
            _write_file(staging / "manifest.json", manifest)
            verified_result = _read_output(staging, output_id)
            _sync_directory(staging)
            _sync_directory(root)
            _publish_directory(staging, destination)
            published = True
            _sync_directory(root)
    except (OSError, ValueError) as error:
        failed = publication_error(result)
        failed["error"]["message"] = _publication_message(error, published=published)
        return RunOutput(output_id, None, failed)
    return RunOutput(output_id, destination, verified_result)
