from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from importlib.resources import files
from pathlib import Path, PurePosixPath

from ._skill_selection import (
    RELEVANCE_METHODS,
    TASK_RELEVANCE,
    context_view,
    indexed_skill,
    record_context,
    skill_score,
    task_score,
    words,
)
from .catalog import KNOWLEDGE_SCOPES, SCENARIO_ALIASES, knowledge_records

AUTHORITIES = ("package", "checkout", "store")
IDENTITY = r"[a-z0-9][a-z0-9._-]*"


class RevisionConflict(ValueError):
    pass


class IdentityError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or path.is_absolute()
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValueError(f"Unsafe bundle path: {value}")
    return value


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _normalized(value: object, field: str, ancestors: frozenset[int] = frozenset()) -> object:
    # Bundle identity is JSON, so YAML scalars without a JSON form are rejected
    # here instead of failing inside revision serialization.
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        # A YAML alias can make a container its own descendant. Only the active path
        # is tracked, so the same object shared by sibling fields stays accepted.
        identity = id(value)
        if identity in ancestors:
            raise ValueError(
                f"Field {field} contains a reference cycle: {type(value).__name__}"
            )
        ancestors = ancestors | {identity}
        if isinstance(value, list):
            return [
                _normalized(item, f"{field}[{index}]", ancestors)
                for index, item in enumerate(value)
            ]
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"Field {field} has a non-string key: {key!r}")
            result[key] = _normalized(item, f"{field}.{key}" if field else key, ancestors)
        return result
    raise ValueError(
        f"Field {field or '<metadata>'} cannot be represented in the bundle identity: "
        f"{type(value).__name__}"
    )


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sources(records: list[dict]) -> list[dict]:
    result = []
    for record in records:
        source = dict(record)
        kind = source.get("type")
        if kind in ("source", "source_code", "technical_document") and "commit" in source:
            source["original_type"] = kind
            kind = "git"
        if kind is None and "commit" in source:
            kind = "git"
        if kind not in ("git", "paper", "document", "internal", "unknown"):
            # Legacy types keep their original label; nothing is inferred from them.
            source["original_type"] = kind
            kind = "unknown"
        source["type"] = kind
        if kind == "git":
            commit = source.get("commit")
            if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{7,40}", commit):
                raise ValueError("Git source requires a real commit or explicit historical prefix")
            source["revision_precision"] = "full" if len(commit) == 40 else "prefix"
        elif kind == "paper":
            if not source.get("id") or "version" not in source:
                raise ValueError("Paper source requires ID and explicit version (null if unknown)")
            if "commit" in source:
                raise ValueError("Paper versions are not Git commits")
        elif kind == "document":
            if not source.get("url") or not any(k in source for k in ("version", "sha256")):
                raise ValueError("Document source requires URL and version or snapshot identity")
        elif kind == "internal":
            if not source.get("artifact") or not any(k in source for k in ("issue", "run")):
                raise ValueError("Internal source requires artifact and issue/run identity")
        result.append(source)
    return result


@dataclass(frozen=True)
class Bundle:
    metadata: dict
    content: dict[str, bytes]
    dependencies: dict[str, str]

    def __post_init__(self) -> None:
        # Normalize once at ingestion so manifest, revision and views agree.
        object.__setattr__(self, "metadata", _normalized(self.metadata, "metadata"))

    def manifest(self) -> dict:
        record = self.metadata
        if not isinstance(record.get("id"), str) or not re.fullmatch(IDENTITY, record["id"]):
            raise ValueError("Invalid bundle identity")
        if record.get("kind") not in ("skill", "knowledge", "case"):
            raise ValueError("Invalid bundle kind")
        for field in (
            "sources",
            "applicability",
            "state",
            "limitations",
            "attribution",
            "entrypoint",
        ):
            if field not in record:
                raise ValueError(f"Missing normalized field: {field}")
        for field in ("state", "entrypoint"):
            if not isinstance(record[field], str) or not record[field]:
                raise ValueError(f"Normalized {field} must be a nonempty string")
        if not isinstance(record["limitations"], list):
            raise ValueError("Normalized limitations must be a list")
        if not isinstance(record["sources"], list) or not record["sources"]:
            raise ValueError("Bundle requires sources, including explicit unknown provenance")
        _sources(record["sources"])
        for path, data in self.content.items():
            safe_path(path)
            if not isinstance(data, bytes):
                raise ValueError("Bundle content must be bytes")
        if record["entrypoint"] not in self.content:
            raise ValueError("Missing entrypoint in complete bundle")
        for required in record.get("required_resources", []):
            # Present string keys already passed the content path validation.
            if isinstance(required, str) and required in self.content:
                continue
            if safe_path(required) not in self.content:
                raise ValueError(f"Missing required resource: {required}")
        resources = {path: _hash(data) for path, data in sorted(self.content.items())}
        for dependency, revision in self.dependencies.items():
            safe_path(dependency)
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", revision):
                raise ValueError("Dependency requires a resolved content revision")
            if dependency in self.content:
                actual = "sha256:" + resources[dependency]
            else:
                members = {
                    path: digest
                    for path, digest in resources.items()
                    if path.startswith(dependency + "/")
                }
                if not members:
                    raise ValueError(f"Missing dependency: {dependency}")
                actual = "sha256:" + _hash(_json(members))
            if actual != revision:
                raise RevisionConflict(f"Dependency bytes disagree with revision: {dependency}")
        return {
            "metadata": record,
            "resources": resources,
            "dependencies": dict(sorted(self.dependencies.items())),
        }

    @property
    def revision(self) -> str:
        return "sha256:" + _hash(_json(self.manifest()))

    def view(self, *, material: bool = False) -> dict:
        manifest = self.manifest()
        result = {
            **self.metadata,
            "revision": "sha256:" + _hash(_json(manifest)),
            "resources": manifest["resources"],
            "dependencies": manifest["dependencies"],
        }
        if material:
            result["content"] = self.content[self.metadata["entrypoint"]].decode("utf-8")
        return result


INCIDENTAL = ("__pycache__", ".DS_Store")


def _tree(
    node, prefix: str, *, root: Path | None = None, ancestors: frozenset[Path] = frozenset()
) -> dict[str, bytes]:
    if isinstance(node, Path):
        # A junction or symlink resolves away from where it appears; every descended
        # directory must still land inside the root that owns this walk.
        directory = node.resolve()
        if root is None:
            root = directory
        if directory in ancestors:
            raise ValueError(f"Resource cycle inside the skills root: {prefix}")
        ancestors = ancestors | {directory}
    result = {}
    for child in sorted(node.iterdir(), key=lambda p: p.name):
        if child.name in INCIDENTAL or child.name.endswith(".pyc"):
            continue
        path = safe_path(f"{prefix}/{child.name}")
        if isinstance(child, Path) and child.is_symlink():
            raise ValueError(f"Symlink resource is not supported: {path}")
        if child.is_dir():
            if isinstance(child, Path) and root is not None and not child.resolve().is_relative_to(
                root
            ):
                raise ValueError(f"Resource escapes the skills root: {path}")
            result.update(_tree(child, path, root=root, ancestors=ancestors))
        else:
            result[path] = child.read_bytes()
    return result


class KnowledgeReader:
    def __init__(self, *, skill_root: Path | None = None, store=None):
        self.skill_root = skill_root.resolve() if skill_root is not None else None
        if store is not None and store.skill_root != self.skill_root:
            raise ValueError("Store must reserve identities from the same checkout")
        self.store = store
        self._records: dict[str, dict] | None = None
        self._library: tuple[dict[str, bytes], str] | None = None

    def records(self) -> dict[str, dict]:
        """Installed catalog records, read once per reader (one package snapshot)."""
        if self._records is None:
            self._records = {record["id"]: record for record in knowledge_records()}
        return self._records

    def _skill_library(self) -> tuple[dict[str, bytes], str]:
        if self._library is None:
            # K02 moved the physical corpus under the Knowledge module. The bundle
            # key prefix stays "data/skill_library" so resource keys, dependency
            # keys and pinned record hashes keep their existing values.
            root = files("inferrsi").joinpath("knowledge", "data", "skill_library")
            library = _tree(root, "data/skill_library")
            digest = _hash(_json({path: _hash(data) for path, data in sorted(library.items())}))
            self._library = (library, "sha256:" + digest)
        return self._library

    def owners(self) -> dict[str, list[str]]:
        """Map each public ID to every authority that owns it."""
        result: dict[str, list[str]] = {}
        for identity in self.records():
            result.setdefault(identity, []).append("package")
        if self.skill_root is not None:
            # Enumeration reads registered names only. Validating sidecar content
            # here would abort the whole collection over one malformed, possibly
            # unrequested, skill; Collect reports that per item through _read.
            from .skill_catalog import skill_directories

            local = list(skill_directories(self.skill_root))
            local.extend(self._cases())
            if len(set(local)) != len(local):
                raise IdentityError("collision", "Duplicate identity inside the checkout")
            for identity in local:
                result.setdefault(identity, []).append("checkout")
        if self.store is not None:
            for identity in self.store.identities():
                result.setdefault(identity, []).append("store")
        return result

    def identities(self) -> list[str]:
        return sorted(f"{a}:{i}" for i, authorities in self.owners().items() for a in authorities)

    def resolve(self, reference: str, owners: dict[str, list[str]] | None = None) -> str:
        """Return the qualified `authority:id`; ambiguity is an error, never precedence."""
        authority, qualified, identity = reference.rpartition(":")
        if not re.fullmatch(IDENTITY, identity) or (qualified and authority not in AUTHORITIES):
            raise IdentityError("invalid_identity", f"Invalid identity: {reference}")
        found = (self.owners() if owners is None else owners).get(identity, [])
        if qualified:
            if authority not in found:
                raise IdentityError("unknown_identity", f"Unknown identity: {reference}")
            return reference
        if not found:
            raise IdentityError("unknown_identity", f"Unknown identity: {identity}")
        if len(found) > 1:
            choices = ", ".join(f"{a}:{identity}" for a in found)
            raise IdentityError(
                "ambiguous_identity", f"Ambiguous identity {identity}; qualify one of: {choices}"
            )
        return f"{found[0]}:{identity}"

    def _cases(self) -> dict[str, Path]:
        result = {}
        if self.skill_root is not None:
            from .skill_catalog import skill_directories

            for directory in skill_directories(self.skill_root).values():
                for path in sorted(directory.glob("cases/*.json")):
                    if path.is_symlink() or not path.resolve().is_relative_to(self.skill_root):
                        raise ValueError("Case must stay inside the selected skills root")
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if record.get("kind") == "case":
                        identity = record["id"]
                        if not isinstance(identity, str) or not re.fullmatch(IDENTITY, identity):
                            raise ValueError(f"Invalid case identity in {path.name}: {identity!r}")
                        if identity in result:
                            raise ValueError(f"Duplicate case identity: {identity}")
                        result[identity] = path
        return result

    def read(self, reference: str, *, revision: str | None = None) -> Bundle:
        _, bundle = self.read_resolved(reference, revision=revision)
        return bundle

    def read_resolved(self, reference: str, *, revision: str | None = None) -> tuple[str, Bundle]:
        """Read a bundle and return the qualified reference used for that read."""
        qualified = self.resolve(reference)
        return qualified, self._read(qualified, revision)

    def _read(self, qualified: str, revision: str | None) -> Bundle:
        authority, _, identity = qualified.partition(":")
        if authority == "store":
            return self.store.read(identity, revision=revision)
        bundle = self._checkout(identity) if authority == "checkout" else self._knowledge(identity)
        if revision is not None and bundle.revision != revision:
            raise RevisionConflict(
                f"Revision unavailable in this snapshot: {qualified} at {revision}"
            )
        return bundle

    def _checkout(self, identity: str) -> Bundle:
        cases = self._cases()
        if identity in cases:
            path = cases[identity]
            record = json.loads(path.read_text(encoding="utf-8"))
            for field in ("references", "dependencies"):
                if field in record:
                    raise ValueError(f"Case {field} are not supported; declare required_resources")
            declared = record.get("required_resources", [])
            if not isinstance(declared, list) or any(not isinstance(p, str) for p in declared):
                raise ValueError("Case required_resources must be a string list")
            if len(set(declared)) != len(declared):
                raise ValueError("Case required_resources must not repeat a path")
            relative = safe_path(record["content"])
            required = [relative, "case.json", *(p for p in declared if p != relative)]
            content = {"case.json": path.read_bytes()}
            for resource in [relative, *required[2:]]:
                safe_path(resource)
                if resource == "case.json":
                    raise ValueError("Case resource collides with its case.json record")
                resource_path = path.parent / resource
                if resource_path.is_symlink() or not resource_path.resolve().is_relative_to(
                    self.skill_root
                ):
                    raise ValueError(f"Case resource escapes the skills root: {resource}")
                if not resource_path.is_file():
                    raise ValueError(f"Missing case resource: {resource}")
                content[resource] = resource_path.read_bytes()
            return Bundle(
                {
                    **record,
                    "entrypoint": relative,
                    "sources": _sources(record["sources"]),
                    "required_resources": required,
                },
                content,
                {},
            )
        return self._skill(identity)

    def _knowledge(self, identity: str) -> Bundle:
        record = self.records().get(identity)
        if record is None:
            raise IdentityError("unknown_identity", f"Unknown knowledge ID: {identity}")
        content = {"record.json": _json(record)}
        dependencies = {}
        if record.get("skill_documents"):
            # The whole imported library is one explicit dependency so bilingual
            # references and licenses stay a complete closure.
            library, digest = self._skill_library()
            content.update(library)
            dependencies["data/skill_library"] = digest
            documents = record["skill_documents"]
            if not isinstance(documents, dict):
                raise ValueError(f"skill_documents must map editions to paths: {identity}")
            for target in documents.values():
                if not isinstance(target, str) or safe_path(target) not in library:
                    raise ValueError(f"Missing declared skill document: {target}")
        return Bundle(
            {
                "id": identity,
                "kind": "knowledge",
                "entrypoint": "record.json",
                "origin": record.get("origin", ["external"]),
                "sources": _sources(record["sources"]),
                "applicability": record.get("applicability", {}),
                "state": record["status"],
                "limitations": record.get("limitations", [])
                + [
                    "Imported guidance; reading does not validate or execute it.",
                    "Historical revisions require the matching installed snapshot "
                    "or a retained store.",
                ],
                "attribution": record["sources"],
                "verification": record["verification"],
                "tier": record.get("tier", "contribution"),
                "repo": record["repo"],
                "backend": record["backend"],
                "scenario": record["scenario"],
                "title": record["title"],
                "skill_documents": record.get("skill_documents", {}),
                "required_resources": sorted(content),
            },
            content,
            dependencies,
        )

    def _skill(self, identity: str) -> Bundle:
        from .skill_catalog import get_skill, skill_directory, skill_metadata, skill_reference

        root = self.skill_root
        record = get_skill(root, identity)
        directory = skill_directory(root, identity)
        prefix = directory.relative_to(root).as_posix()
        content = _tree(directory, prefix, root=root)
        metadata = skill_metadata(root, identity)
        dependencies = {}
        for reference in metadata.get("references", []):
            path = skill_reference(root, identity, reference)
            key = path.relative_to(root).as_posix()
            content[key] = path.read_bytes()
            dependencies[key] = "sha256:" + _hash(content[key])
        return Bundle(
            {
                "id": identity,
                "kind": "skill",
                "entrypoint": f"{prefix}/SKILL.md",
                "title": record["description"],
                "origin": metadata.get("origin", ["unknown"]),
                "sources": _sources(metadata.get("sources", []))
                or [
                    {
                        "type": "unknown",
                        "note": "Legacy provenance remains in procedure/readsets",
                    }
                ],
                "applicability": metadata.get("applicability", {}),
                "state": record.get("status", "unspecified"),
                "limitations": metadata.get("limitations", [])
                + [
                    "Checkout-only local skill; no automatic execution or promotion.",
                    "Only declared references are required; "
                    "other cross-skill links are navigation.",
                    "Historical revisions require a matching checkout or a retained store.",
                ],
                "attribution": metadata.get("sources", []),
                "empirical_validation": metadata.get("empirical_validation", "unknown"),
                "required_resources": sorted(content),
            },
            content,
            dependencies,
        )

    def resource(self, identity: str, path: str, *, revision: str) -> bytes:
        safe_path(path)
        bundle = self.read(identity, revision=revision)
        if path not in bundle.content:
            raise ValueError(f"Unknown resource: {path}")
        return bundle.content[path]

    def collect(
        self,
        query: str = "",
        *,
        ids: list[str] | None = None,
        revisions: dict[str, str] | None = None,
        kind: str | None = None,
        repo: str | None = None,
        tier: str | None = None,
        backend: str | None = None,
        scenario: str | None = None,
        limit: int = 10,
        max_bytes: int | None = None,
        material: bool = False,
        relevance: str | None = None,
        relevance_method: str = "weighted-lexical-v1",
        context_bytes: int | None = None,
    ) -> dict:
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 1):
            raise ValueError("max_bytes must be a positive integer")
        if ids is not None and (
            not isinstance(ids, list) or any(not isinstance(identity, str) for identity in ids)
        ):
            raise ValueError("ids must be a list of identity strings")
        if kind not in (None, "skill", "knowledge", "case"):
            raise ValueError("Unknown content kind")
        if relevance is not None and (not isinstance(relevance, str) or not words(relevance)):
            raise ValueError("relevance requires at least one word")
        if relevance_method not in RELEVANCE_METHODS:
            raise ValueError("Unknown relevance method")
        if relevance is None and relevance_method != "weighted-lexical-v1":
            raise ValueError("relevance_method requires relevance")
        if context_bytes is not None and (type(context_bytes) is not int or context_bytes < 1):
            raise ValueError("context_bytes must be a positive integer")
        if revisions is None:
            revisions = {}
        if not isinstance(revisions, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in revisions.items()
        ):
            raise ValueError("revisions must map identity strings to revision strings")
        if ids is not None and len(set(ids)) != len(ids):
            raise ValueError("Duplicate requested identity")
        owners = self.owners()
        requested = ids if ids is not None else self.identities()

        def qualified(reference: str) -> str | None:
            # Revision keys may use any spelling the API accepts for an identity.
            try:
                return self.resolve(reference, owners)
            except IdentityError:
                return None

        selected = {identity for identity in map(qualified, requested) if identity is not None}
        pinned: dict[str, str] = {}
        for reference, revision in revisions.items():
            target = qualified(reference)
            if target is None or target not in selected:
                raise ValueError(
                    f"Revision requested for an unselected identity: {reference}"
                )
            if pinned.setdefault(target, revision) != revision:
                raise ValueError(f"Conflicting revisions for the same identity: {reference}")
            pinned[target] = revision
        collisions = sorted(i for i, authorities in owners.items() if len(authorities) > 1)
        if tier is not None and tier not in KNOWLEDGE_SCOPES:
            raise ValueError(f"Unknown knowledge tier: {tier}")
        scenario = SCENARIO_ALIASES.get(scenario, scenario)
        package_filters = any(value is not None for value in (repo, tier, backend, scenario))
        task_ranking = relevance_method == TASK_RELEVANCE

        def matches(metadata: dict, authority: str) -> bool:
            declared = metadata.get("scenario")
            declared = declared if isinstance(declared, str) else ""
            # The catalog records skills.discovery as the discovery operation,
            # not the workload served by a procedure. Let the task cue rank
            # these procedures alongside records declaring the requested workload.
            discovery_skill = (
                task_ranking and ids is None and authority == "package"
                and declared == "skills.discovery"
                and self.records()[metadata["id"]].get("index_type") == "packaged-skill"
            )
            return not (
                (kind and metadata.get("kind") != kind)
                or (package_filters and authority != "package")
                or (repo and metadata.get("repo") != repo)
                or (tier and metadata.get("tier", "contribution") != tier)
                or (backend and metadata.get("backend") not in (backend, "common"))
                or not (
                    scenario is None
                    or declared == scenario
                    or (declared.endswith(".*") and scenario.startswith(declared[:-1]))
                    or discovery_skill
                )
            )

        cases = self._cases() if kind else {}
        selected, omitted, seen = [], [], set()
        used = 0
        ranked, contexts, context_omissions = [], [], []
        context_used = 0
        explanations = {}
        excluded = {"ranking_scope": 0, "filters": 0, "query": 0, "zero_score": 0,
                    "item_limit": 0, "material_bytes": 0}
        matched_count = 0

        def select(reference: str, bundle: Bundle, view: dict) -> None:
            nonlocal used, context_used, matched_count
            matched_count += 1
            # Normal Bundle views were serialized while computing their revision.
            # Preserve custom subclasses' serialization/error behavior below.
            if type(bundle) is Bundle and len(selected) >= limit:
                excluded["item_limit"] += 1
                omitted.append({"id": reference, "reason": "output_limit"})
                return
            size = len(_json(view))
            if len(selected) >= limit or (max_bytes is not None and used + size > max_bytes):
                excluded["item_limit" if len(selected) >= limit else "material_bytes"] += 1
                omitted.append({"id": reference, "reason": "output_limit"})
                return
            selected.append(view)
            used += size
            if context_bytes is None:
                return
            record = self.records().get(view["id"], {})
            is_skill = record.get("index_type") == "packaged-skill"
            if view["authority"] != "package" or (not is_skill and not task_ranking):
                context_omissions.append({"ref": view["ref"], "reason": "unsupported_context"})
                return
            try:
                available = context_bytes - context_used
                if is_skill:
                    context, omission = context_view(
                        record, view, bundle.content, relevance or query, available
                    )
                else:
                    context, omission = record_context(record, view, bundle.content, available)
            except (ValueError, KeyError, TypeError) as exc:
                context_omissions.append({
                    "ref": view["ref"], "reason": "index_unavailable", "detail": str(exc),
                })
                return
            if omission is not None:
                context_omissions.append(omission)
            else:
                contexts.append(context)
                context_used += context["content_bytes"]

        for reference in requested:
            try:
                qualified = self.resolve(reference, owners)
                if qualified in seen:
                    raise IdentityError("duplicate_identity", f"Already selected: {qualified}")
                seen.add(qualified)
                authority, _, identity = qualified.partition(":")
                # Cheap metadata checks first, so filtered items never load resource bytes.
                if authority == "package":
                    early = {**self.records()[identity], "kind": "knowledge"}
                elif authority == "checkout":
                    early = {"kind": "case" if identity in cases else "skill"}
                else:
                    early = None
                if relevance is not None and ids is None and (
                    authority != "package"
                    or (not task_ranking and early.get("index_type") != "packaged-skill")
                ):
                    excluded["ranking_scope"] += 1
                    continue
                if early is not None and not matches(early, authority):
                    excluded["filters"] += 1
                    if ids is not None:
                        omitted.append({"id": reference, "reason": "filters"})
                    continue
                bundle = self._read(qualified, pinned.get(qualified))
                view = {**bundle.view(material=material), "authority": authority, "ref": qualified}
                entry = bundle.content[bundle.metadata["entrypoint"]]
                haystack = (_json(bundle.metadata) + entry).decode("utf-8", "replace").casefold()
            except IdentityError as exc:
                omitted.append({"id": reference, "reason": exc.code, "detail": str(exc)})
                continue
            except RevisionConflict as exc:
                omitted.append(
                    {"id": reference, "reason": "unavailable_revision", "detail": str(exc)}
                )
                continue
            except (ValueError, KeyError, TypeError, AttributeError, OSError, sqlite3.Error) as exc:
                omitted.append({"id": reference, "reason": "invalid", "detail": str(exc)})
                continue
            if not matches(view, authority):
                excluded["filters"] += 1
                if ids is not None:
                    omitted.append({"id": reference, "reason": "filters"})
                continue
            if any(token not in haystack for token in query.casefold().split()):
                excluded["query"] += 1
                if ids is not None:
                    omitted.append({"id": reference, "reason": "query"})
                continue
            if relevance is not None and ids is None:
                record = self.records().get(identity, {})
                try:
                    is_skill = record.get("index_type") == "packaged-skill"
                    if is_skill:
                        core, _, _ = indexed_skill(record, view, bundle.content)
                        body = bundle.content[core]
                    else:
                        body = entry
                    explanation = {}
                    if task_ranking:
                        score = task_score(relevance, record, body, backend,
                                           explanation=explanation)
                    else:
                        score = skill_score(relevance, record, body, method=relevance_method,
                                            explanation=explanation)
                except (ValueError, KeyError, TypeError) as exc:
                    omitted.append({
                        "id": reference, "reason": "index_unavailable", "detail": str(exc),
                    })
                    continue
                if score:
                    explanations[view["ref"]] = explanation
                    ranked.append((score, reference, bundle, view))
                else:
                    excluded["zero_score"] += 1
                continue
            select(reference, bundle, view)
        for _, reference, bundle, view in sorted(
            ranked, key=lambda item: (-item[0], item[3]["ref"])
        ):
            select(reference, bundle, view)
        result = {
            "items": selected,
            "omissions": omitted,
            "no_match": not selected,
            "collisions": collisions,
            "material_bytes": used,
            "limitations": [
                "No crawling, ranking model or conversation state; deterministic identity order.",
                "max_bytes bounds item payloads, not omission/control metadata.",
                "Read references must be resolved with their returned exact revision.",
            ],
        }
        if relevance is not None:
            result["selection"] = {
                "method": relevance_method, "cue": relevance,
                "scope": ("package-skills-and-records" if task_ranking
                          else "generated-packaged-skills"),
                "ranking_applied": ids is None,
            }
            if task_ranking and ids is None:
                result["selection"].update(matched_count=len(ranked), returned_count=len(selected))
            result["limitations"][0] = (
                "Deterministic lexical selection; explicit IDs retain caller order and membership."
            )
        if context_bytes is not None:
            result.update({
                "contexts": contexts, "context_omissions": context_omissions,
                "context_bytes": context_used,
                "context_budget": {
                    "max_bytes": context_bytes, "accounting": "utf8-resource-content",
                    "excludes": "provenance, omission and control metadata",
                },
            })
        from .retrieval import observation

        ranking = relevance is not None and ids is None
        result["retrieval"] = observation(
            entrypoint="collect", request={
                "query": query, "relevance": relevance, "relevance_method": relevance_method,
                "ids": ids, "revisions": revisions, "kind": kind, "repo": repo, "tier": tier,
                "backend": backend, "scenario": scenario, "limit": limit,
                "max_bytes": max_bytes, "context_bytes": context_bytes,
                "sources": ["package"] + (["checkout"] if self.skill_root else [])
                           + (["store"] if self.store is not None else []),
            }, selection={
                "method": relevance_method if relevance is not None else None,
                "scope": (("package-skills-and-records" if task_ranking else
                           "generated-packaged-skills") if relevance is not None else None),
                "ranking_applied": ranking,
                "state": "applied" if ranking else "explicit_ids" if ids is not None
                         else "not_requested",
                "tie_break": "ref_ascending" if ranking else None,
            }, items=[{"ref": item["ref"], "revision": item["revision"], "rank": n,
                       **explanations.get(item["ref"], {})}
                      for n, item in enumerate(selected, 1)],
            counts={"considered": len(requested), "matched": matched_count,
                    "returned": len(selected), "excluded": excluded},
            assembly="not_requested" if context_bytes is None else "performed",
        )
        return result
