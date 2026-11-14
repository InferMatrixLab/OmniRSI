"""Plan daily versioned releases and validate their packages without publishing."""

import argparse
import ast
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def release_version(tag: str) -> str:
    number = r"(?:0|[1-9][0-9]*)"
    if not re.fullmatch(rf"v{number}\.{number}\.{number}", tag):
        raise ValueError("Release tags must have the stable form vMAJOR.MINOR.PATCH")
    return tag[1:]


def version_tuple(tag: str) -> tuple[int, ...]:
    return tuple(map(int, release_version(tag).split(".")))


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, stderr=subprocess.PIPE).strip()


def plan_release(default_branch: str, releases: list[dict], initial_version: str,
                 prerelease: bool = True, *, release_v020: bool = False) -> dict:
    if type(prerelease) is not bool:
        raise ValueError("Release prerelease setting must be a boolean")
    initial = version_tuple(f"v{initial_version}")
    source = git("rev-parse", "--verify", f"refs/remotes/origin/{default_branch}^{{commit}}")
    stable = {}
    for tag in git("tag", "--list").splitlines():
        try:
            stable[tag] = version_tuple(tag)
        except ValueError:
            continue
    published = []
    reserved = []
    for release in releases:
        tag = release["tag_name"]
        try:
            value = version_tuple(tag)
        except ValueError:
            continue
        reserved.append(value)
        if release["draft"] is False:
            published.append((value, tag))
    if release_v020:
        baseline = git("rev-parse", "--verify", "refs/tags/v0.1.1^{commit}")
        subprocess.run(["git", "merge-base", "--is-ancestor", baseline, source],
                       check=True, capture_output=True)
        target = (0, 2, 0)
        if any(value >= target for value in [*stable.values(), *reserved]):
            raise ValueError("One-off v0.2.0 requires an unused target newer than "
                             "every reserved version")
    previous_tag = max(published)[1] if published else ""
    if previous_tag:
        previous = git("rev-parse", "--verify", f"refs/tags/{previous_tag}^{{commit}}")
        subprocess.run(["git", "merge-base", "--is-ancestor", previous, source],
                       check=True, capture_output=True)
        if (not release_v020 and
                git("rev-parse", f"{previous}^{{tree}}") == git("rev-parse", f"{source}^{{tree}}")):
            return {"release": "false"}
    used = [*stable.values(), *reserved]
    if used:
        major, minor, patch = max(used)
        version = max(initial, (major, minor, patch + 1))
    else:
        version = initial
    if release_v020:
        version = (0, 2, 0)
        prerelease = False
    return {"release": "true", "tag": "v" + ".".join(map(str, version)),
            "version": ".".join(map(str, version)),
            "prerelease": str(prerelease).lower(),
            "commit": source, "previous_tag": previous_tag}


def check_versions(tag: str, distribution: str, runtime: str) -> None:
    version = release_version(tag)
    if distribution != version or runtime != version:
        raise ValueError("Tag, installed distribution and runtime versions must agree exactly")


def check_sdist(path: Path) -> None:
    # setuptools-scm's file finder must not expand the release to all tracked files.
    allowed = {"src", "pyproject.toml", "README.md", "LICENSE", "MANIFEST.in",
               "PKG-INFO", "setup.cfg"}
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            if ".." in parts or Path(member.name).is_absolute():
                raise ValueError("Unsafe source-distribution path")
            if len(parts) > 1 and parts[1] not in allowed:
                raise ValueError("Source distribution includes repository-only material")


def _standalone_entries(archive: zipfile.ZipFile) -> list[str]:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError("Plugin ZIP must contain one standalone inferrsi plugin")
    roots = set()
    destinations = set()
    spellings = {}
    for member in archive.infolist():
        name = PurePosixPath(member.filename)
        canonical = name.as_posix() + ("/" if member.is_dir() else "")
        if (not name.parts or name.is_absolute() or ".." in name.parts
                or "\\" in member.filename or ":" in member.filename
                or member.filename != canonical
                or name.as_posix().casefold() in destinations
                or any(part.endswith((".", " ")) or re.search(r'[<>"|?*]', part)
                       or re.fullmatch(r"con|prn|aux|nul|com[1-9]|lpt[1-9]",
                                       part.split(".")[0], re.IGNORECASE)
                       for part in name.parts)
                or stat.S_ISLNK(member.external_attr >> 16)):
            raise ValueError("Unsafe plugin ZIP entry")
        destinations.add(name.as_posix().casefold())
        for depth in range(1, len(name.parts) + 1):
            prefix = "/".join(name.parts[:depth])
            if spellings.setdefault(prefix.casefold(), prefix) != prefix:
                raise ValueError("Unsafe plugin ZIP directory alias")
        roots.add(name.parts[0])
    if roots != {"inferrsi"}:
        raise ValueError("Plugin ZIP must contain one standalone inferrsi plugin")
    return names


def check_plugin(path: Path, tag: str, host: str = "codex") -> None:
    version = release_version(tag)
    if host not in {"codex", "workswarm"}:
        raise ValueError("Unknown plugin host")
    with zipfile.ZipFile(path) as archive:
        names = _standalone_entries(archive)
        plugin = "inferrsi"
        metadata_file = "plugin.json" if host == "codex" else "manifest.json"
        allowed = {metadata_file, "skills", "runtime", "LICENSE", "README.md"}
        if host == "workswarm":
            allowed.add("tools")
        if any(len(PurePosixPath(name).parts) > 1
               and PurePosixPath(name).parts[1] not in allowed for name in names):
            raise ValueError("Plugin ZIP includes a marketplace or unrelated wrapper")
        metadata = json.loads(archive.read(f"{plugin}/{metadata_file}"))
        manifest = json.loads(archive.read(f"{plugin}/runtime/manifest.json"))
        if host == "codex":
            if metadata.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json":
                raise ValueError("Plugin manifest must use the portable plugin schema")
            identity = metadata["name"]
        else:
            if (metadata.get("package_type") != "plugin"
                    or metadata.get("skills") != [{"dir": "./skills/inferrsi", "mode": "all"}]
                    or len(metadata.get("tools", [])) != 1
                    or metadata["tools"][0].get("file") != "tools/runtime_info_tool.py"
                    or metadata["tools"][0].get("class") != "InferRSIRuntimeInfoTool"):
                raise ValueError("WorkSwarm manifest must declare the native skill and locator")
            try:
                locator = ast.parse(archive.read(f"{plugin}/tools/runtime_info_tool.py"))
            except (SyntaxError, ValueError) as error:
                raise ValueError("WorkSwarm locator must be valid Python") from error
            if not any(isinstance(node, ast.ClassDef) and node.name == "InferRSIRuntimeInfoTool"
                       for node in locator.body):
                raise ValueError("WorkSwarm locator must define the declared tool class")
            identity = metadata["id"]
        archive.getinfo(f"{plugin}/skills/inferrsi/SKILL.md")
        archive.getinfo(f"{plugin}/README.md")
        archive.getinfo(f"{plugin}/LICENSE")
        check_versions(tag, metadata["version"], manifest["runtime_version"])
        if identity != "inferrsi" or manifest["plugin_version"] != version:
            raise ValueError("Plugin identity/version does not match the release")
        prefix = f"{plugin}/runtime/"
        actual = {name[len(prefix):] for name in names
                  if name.startswith(prefix) and name != prefix + "manifest.json"
                  and not name.endswith("/")}
        if host == "workswarm":
            if actual != {"payload.zip", "launcher.py"}:
                raise ValueError("Unexpected file outside the bundled runtime package")
            payload_bytes = archive.read(prefix + "payload.zip")
            if (hashlib.sha256(payload_bytes).hexdigest() != manifest["payload_sha256"]
                    or hashlib.sha256(archive.read(prefix + "launcher.py")).hexdigest()
                    != manifest["launcher_sha256"]):
                raise ValueError("Plugin bundled runtime checksum mismatch")
            with zipfile.ZipFile(io.BytesIO(payload_bytes)) as payload:
                payload_names = _standalone_entries(payload)
                runtime_bytes = {name: payload.read(name) for name in payload_names
                                 if not name.endswith("/")}
        else:
            runtime_bytes = {name: archive.read(prefix + name) for name in actual}
        actual = set(runtime_bytes)
        if any(not name.startswith("inferrsi/") for name in actual):
            raise ValueError("Unexpected file outside the bundled runtime package")
        hashes = manifest["files"]
        if not hashes or set(hashes) != actual:
            raise ValueError("Plugin manifest must cover every bundled runtime file")
        for name, digest in hashes.items():
            if hashlib.sha256(runtime_bytes[name]).hexdigest() != digest:
                raise ValueError("Plugin bundled runtime checksum mismatch")
        identity = "\n".join(f"{name}\0{digest}" for name, digest in sorted(hashes.items()))
        if hashlib.sha256(identity.encode("utf-8")).hexdigest() != manifest["content_sha256"]:
            raise ValueError("Plugin content identity mismatch")
        # Cleanup must use the same extended path as the deep runtime cache.
        temp_root = Path(tempfile.gettempdir()).resolve()
        value = str(temp_root)
        if os.name == "nt" and not value.startswith("\\\\?\\"):
            temp_root = Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\")
                             else "\\\\?\\" + value)
        with tempfile.TemporaryDirectory(prefix="irsi-", dir=temp_root) as staging:
            destination = Path(staging).resolve()
            archive.extractall(destination)
            launcher = destination / plugin / "skills/inferrsi/scripts/inferrsi.py"
            command = [sys.executable, "-I", "-S", "-X", "utf8", str(launcher)]

            def invoke(*args):
                try:
                    result = subprocess.run(command + list(args), cwd=staging, check=True,
                                            capture_output=True, text=True, encoding="utf-8",
                                            timeout=45,
                                            env=dict(os.environ, LOCALAPPDATA=str(
                                                destination / "runtime-cache")))
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    raise ValueError(
                        f"Extracted {host} plugin failed isolated validation"
                    ) from error
                return json.loads(result.stdout)

            info = invoke("--plugin-info")
            check_versions(tag, info["plugin_version"], info["runtime_version"])
            if info["runtime"] != "bundled" or info["content_sha256"] != manifest["content_sha256"]:
                raise ValueError("Plugin launcher did not select the verified bundled runtime")
            items = invoke("collect", "--limit", "1", "--json")["items"]
            if not items:
                raise ValueError("Plugin has no bundled knowledge")
            item = items[0]
            material = invoke("read", item["ref"], "--revision", item["revision"], "--material")
            if material["revision"] != item["revision"]:
                raise ValueError("Plugin knowledge read lost its revision identity")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--default-branch", required=True)
    plan.add_argument("--releases", type=Path, required=True)
    plan.add_argument("--release-v020", action="store_true")
    installed = commands.add_parser("installed")
    installed.add_argument("--tag", required=True)
    sdist = commands.add_parser("sdist")
    sdist.add_argument("path", type=Path)
    plugin = commands.add_parser("plugin")
    plugin.add_argument("path", type=Path)
    plugin.add_argument("--tag", required=True)
    plugin.add_argument("--host", choices=("codex", "workswarm"), default="codex")
    args = parser.parse_args()
    try:
        if args.command == "plan":
            # The workflow uses Python 3.11; importing these helpers remains 3.10-safe.
            import tomllib

            config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
            pages = json.loads(args.releases.read_text(encoding="utf-8"))
            releases = [release for page in pages for release in page]
            settings = config["tool"]["inferrsi"]["release"]
            result = plan_release(args.default_branch, releases,
                                  settings["initial_version"], settings["prerelease"],
                                  release_v020=args.release_v020)
            for key, value in result.items():
                print(f"{key}={value}")
        elif args.command == "sdist":
            check_sdist(args.path)
        elif args.command == "plugin":
            check_plugin(args.path, args.tag, args.host)
        else:
            from importlib.metadata import version

            from inferrsi import __version__

            check_versions(args.tag, version("inferrsi"), __version__)
            print("Installed release versions agree")
    except (ValueError, KeyError, OSError, zipfile.BadZipFile,
            subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release validation failed: {error}\n")


if __name__ == "__main__":
    main()
