# Apps output publication

The [Apps design decision](https://github.com/InferMatrixLab/InferRSI/issues/371#issuecomment-6841315306)
accepts the Apps package path, output contract, CLI and Harness field boundaries.
This document describes the output implementation and its operational limits.

## Atomicity and platform support

Publication requires staging and destination on the same filesystem. Linux uses
libc `renameat2(RENAME_NOREPLACE)`, macOS uses `renamex_np(RENAME_EXCL)`, and
Windows uses Python's no-replacement directory rename. Every destination entry,
including an empty directory, is a conflict. There is no check-then-rename or
copy fallback that could overwrite another publisher's output.

Linux requires a libc exposing renameat2 and a filesystem/kernel combination
supporting RENAME_NOREPLACE. POSIX also requires working directory fsync. Other
POSIX platforms, missing APIs, and filesystems rejecting these operations fail
closed. Local Linux filesystems and local Windows NTFS are the intended targets;
this does not certify every filesystem with that name. NFS, FUSE, WSL drvfs,
macOS and other remote/removable filesystems need environment-specific validation
before any support or crash-durability claim. No network filesystem is implicitly
supported by the Python API.

## Durability and failure reporting

Both JSON files are flushed and fsynced before publication. POSIX additionally
syncs the workspace (which may have gained the output root), the staging directory
and output root before the no-replace rename, then syncs the output root after it.
Atomic visibility and power-loss durability are different properties. Windows
has no portable directory-fsync equivalent here: the implementation guarantees
no-replacement publication on supported local filesystems, not power-loss
persistence of the renamed directory. Storage hardware and mount guarantees
still apply even when POSIX fsync succeeds.

Failures retain ArchivePersistenceError and exit code 6, with safe messages
separating destination conflict, unsupported atomic rename/directory sync,
validation failure, and other I/O failures. Raw paths and arbitrary exception
messages are not included. An EINVAL from the atomic operation or directory sync
is classified as unsupported rather than as a destination conflict.

If rename succeeded but the final sync failed, the output may be visible. The
return explicitly reports uncertain publication durability, preserves underlying
committed references, and does not remove or overwrite that directory. Inspect
the same output ID and its manifest before considering a retry; directory=None
means the call did not confirm success, not proof that no directory exists.

## Port response validation and references

Malformed binder or Harness return values become an InvalidExecutionSpec result
(exit code 2) whose error.stage names the boundary. Apps-generated errors distinguish
binder_validation from harness_response_validation in error.stage. The latter
means the reply contract was violated, not that the submitted spec was invalid
or that execution did not start: started remains null and automatic retry is
not implied. Exit code 2 alone cannot distinguish invalid input from a Harness
reply-contract violation and is not evidence that execution never started;
callers must inspect error.stage and preserve the original attempt when reconciling.
The accepted Apps stage mapping uses the existing v1 error codes.
Valid core PublicError stages are preserved. Raised typed PublicError is handled
as a port error. Other host exceptions remain exceptions: these are programming
or host failures, not automatically trustworthy core responses. The future CLI
must define its own exception presentation; no CLI behavior is implied here.

Non-null error attempt IDs, latest record references and partial execution
identities must belong to the submitted attempt. Partial facts are type checked.
Unknown or invalid values are not invented to make a response pass validation.
Retained artifact references receive metadata validation only. Apps does not
follow arbitrary locators or perform implicit storage I/O while projecting an
error. A consumer must use an authorized storage reader and verify the bytes,
size and digest before treating a referenced artifact as evidence. Validating
the result.json digest does not validate all referenced artifacts.

The Apps design decision selects `inferrsi.harness.execution_spec.request_digest`
as the public Harness digest entry point. `inferrsi.harness.run_store.request_digest`
remains a compatibility export of the same function. Apps can compute the
canonical digest without loading the RunStore implementation.

## Prepare archive

`inferrsi.apps.api.prepare` accepts the v1 AppsPrepareRequest and a separate,
trusted StorageConfig. The host supplies the Benchmark prepare, encode and
validate ports. The prepare port returns the prepared object, canonical protocol
bytes and a reader keyed by resource role. The reader can return bytes or an
open binary stream that Apps closes after consuming it.

The pure encoder returns canonical bytes and the validator returns normally on
success. They raise `ValueError` for invalid content or `PublicError` for a
structured rejection. The host adapts Benchmark-specific rejection exceptions
to this contract. Apps publishes `InvalidRequest` at
`benchmark_response_validation` for `ValueError` and preserves the code and stage
of a valid `PublicError`.
Prepare rejects errors carrying an attempt ID, start state, latest record
reference or partial execution record at `benchmark_response_validation`.

Apps selects the configured local archive directory and allocates the final
logical locations for the prepared object, protocol and resources. The
Benchmark encoder and validator check the relocated prepared object before
publication. Apps checks the original protocol and resource bytes against their
declared sizes and digests, then atomically publishes the complete directory
under a generated ID. A missing or invalid archive root is an `InvalidRequest`
at `storage_config` before Apps calls Benchmark.

On POSIX, confirmation of the closure commit includes syncing the parent
directory after the rename. If that sync fails, the archive may be visible.
Apps returns `ArchivePersistenceError` with exit code 6 and a null
`prepared_ref` because it cannot confirm the commit. If Apps confirms the
closure commit and the separate output publication then fails, the returned
error retains the committed PreparedRef.

`read_prepared` verifies the fixed prepared bytes and identity using the same
storage configuration. `read_archive_artifact` reads protocol or resource bytes
from the archive mount and verifies the reference's size and digest. Both
functions are under `inferrsi.apps.archive`.

## Execute binding

Supply `storage_config` to `inferrsi.apps.api.execute` to read the fixed
`PreparedRef`, verify the protocol bytes and semantic digest, and check the
prepared recipe, scope, and resources against that protocol before binding.
The existing injected binder remains available to hosts. The Apps binder maps
frozen execution resources to fixed artifact inputs and preserves the
candidate declarations and the entire original AttemptContext.

Worker arguments substitute whole input and output role tokens. The worker
uses the attempt root as its working directory and the original attempt
deadline. OperationRecipe v1 has no per-step timeout, so the step timeout
is null. The expected worker code digest comes from the fixed recipe.
The bound spec is handed to the host-supplied Harness execution port.

## Fixed artifact reader

`inferrsi.apps.artifact_reader.ArtifactReader(storage_config)` implements
`open(ArtifactRef)` for hosts composing execution or offline assessment.
Locations select one configured mount by its full prefix and slash boundary.
Directory mounts verify the declared bytes and reject links or traversal.
Harness SQLite mounts use the public read-only RunStore reader for fixed
record versions and retained objects. A directory export can replace a
Harness mount at the same prefix for offline reads.

## Offline assessment

`inferrsi.apps.api.assess` takes an AppsAssessRequest, the trusted storage
configuration and the host-supplied Benchmark assessor. Apps opens the requested
record revision, verifies its preparation and protocol, and checks the requested
claim ID and scope before calling the assessor. The assessor receives the full
committed payload in EvidenceInput and a reader limited to the fixed inputs.
An unavailable individual artifact stays an evidence gap for the assessor.

Apps verifies the returned assessment's identity, archives its canonical bytes,
and publishes the complete result. The selected claim alone determines the exit
code after a confirmed archive commit. Archive failure returns exit code 6 and
keeps the assessment in the result with a null `assessment_ref`. If the archive
commit succeeds and output publication fails, the result retains the committed
`assessment_ref`.
`inferrsi.apps.archive.read_assessment` resolves that fixed reference and checks
the archived bytes, version and identity for reports and other read-only consumers.
