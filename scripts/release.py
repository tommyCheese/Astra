#!/usr/bin/env python3
"""Keep Astra versions aligned and build a reproducible release bundle."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def normalize_version(raw: str) -> str:
    value = raw.removeprefix("v")
    if not SEMVER.fullmatch(value):
        raise SystemExit(f"invalid semantic version: {raw}")
    return value


def replace_toml_version(path: Path, value: str) -> None:
    text = path.read_text()
    updated, count = re.subn(
        r'(?m)^version = "[^"]+"$', f'version = "{value}"', text, count=1
    )
    if count != 1:
        raise SystemExit(f"could not find project version in {path.relative_to(ROOT)}")
    path.write_text(updated)


def read_toml_version(path: Path) -> str:
    match = re.search(r'(?m)^version = "([^"]+)"$', path.read_text())
    if not match:
        raise SystemExit(f"could not find project version in {path.relative_to(ROOT)}")
    return match.group(1)


def update_json_version(path: Path, value: str, *, lockfile: bool = False) -> None:
    data = json.loads(path.read_text())
    data["version"] = value
    if lockfile:
        root_package = data.get("packages", {}).get("")
        if isinstance(root_package, dict):
            root_package["version"] = value
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def version_sources() -> dict[str, str]:
    frontend = json.loads((ROOT / "frontend/package.json").read_text())
    frontend_lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
    runtime = json.loads((ROOT / "runtimes/data-viz/package.json").read_text())
    runtime_lock = json.loads((ROOT / "runtimes/data-viz/package-lock.json").read_text())
    return {
        "VERSION": (ROOT / "VERSION").read_text().strip(),
        "backend/pyproject.toml": read_toml_version(ROOT / "backend/pyproject.toml"),
        "frontend/package.json": frontend["version"],
        "frontend/package-lock.json": frontend_lock["version"],
        "frontend/package-lock.json packages root": frontend_lock["packages"][""]["version"],
        "runtimes/data-viz/pyproject.toml": read_toml_version(
            ROOT / "runtimes/data-viz/pyproject.toml"
        ),
        "runtimes/data-viz/package.json": runtime["version"],
        "runtimes/data-viz/package-lock.json": runtime_lock["version"],
        "runtimes/data-viz/package-lock.json packages root": runtime_lock["packages"][""][
            "version"
        ],
    }


def set_version(value: str) -> None:
    (ROOT / "VERSION").write_text(value + "\n")
    replace_toml_version(ROOT / "backend/pyproject.toml", value)
    replace_toml_version(ROOT / "runtimes/data-viz/pyproject.toml", value)
    update_json_version(ROOT / "frontend/package.json", value)
    update_json_version(ROOT / "frontend/package-lock.json", value, lockfile=True)
    update_json_version(ROOT / "runtimes/data-viz/package.json", value)
    update_json_version(
        ROOT / "runtimes/data-viz/package-lock.json", value, lockfile=True
    )
    verify_version(value)


def verify_version(value: str) -> None:
    mismatches = {
        source: actual for source, actual in version_sources().items() if actual != value
    }
    if mismatches:
        details = "\n".join(
            f"- {source}: expected {value}, found {actual}"
            for source, actual in mismatches.items()
        )
        raise SystemExit(f"version sources are out of sync:\n{details}")
    print(f"all version sources match {value}")


def add_tar_entry(archive: tarfile.TarFile, source: Path, target: str) -> None:
    content = source.read_bytes()
    info = tarfile.TarInfo(target)
    info.size = len(content)
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mode = 0o755 if source.name == "install.sh" else 0o644
    archive.addfile(info, io.BytesIO(content))


def build_bundle(value: str, output_dir: Path) -> Path:
    verify_version(value)
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_name = f"astra-v{value}"
    destination = output_dir / f"{bundle_name}.tar.gz"
    deploy_files = ("compose.yaml", ".env.example", "install.sh", "README.md")

    with tarfile.open(destination, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for name in deploy_files:
            source = ROOT / "deploy" / name
            if name == ".env.example":
                content = re.sub(
                    rb"(?m)^ASTRA_VERSION=.*$",
                    f"ASTRA_VERSION={value}".encode(),
                    source.read_bytes(),
                )
                temporary = output_dir / ".env.example.tmp"
                temporary.write_bytes(content)
                try:
                    add_tar_entry(archive, temporary, f"{bundle_name}/.env.example")
                finally:
                    temporary.unlink(missing_ok=True)
            else:
                add_tar_entry(archive, source, f"{bundle_name}/{name}")
        for name in ("LICENSE", "CHANGELOG.md"):
            add_tar_entry(archive, ROOT / name, f"{bundle_name}/{name}")

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    (output_dir / "SHA256SUMS").write_text(f"{digest}  {destination.name}\n")
    print(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("set", "verify"):
        command = subparsers.add_parser(name)
        command.add_argument("version")
    bundle = subparsers.add_parser("bundle")
    bundle.add_argument("version")
    bundle.add_argument("output", type=Path)
    args = parser.parse_args()

    value = normalize_version(args.version)
    if args.command == "set":
        set_version(value)
    elif args.command == "verify":
        verify_version(value)
    else:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        if output.exists() and not output.is_dir():
            raise SystemExit(f"bundle output is not a directory: {output}")
        build_bundle(value, output)


if __name__ == "__main__":
    main()
