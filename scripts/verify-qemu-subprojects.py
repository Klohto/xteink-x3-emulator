#!/usr/bin/env python3
"""Verify local Meson dependency checkouts without downloading or changing them."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def git(directory: Path, *arguments: str, index: Path | None = None,
        data: bytes | None = None) -> str:
    environment = dict(os.environ, GIT_NO_REPLACE_OBJECTS="1")
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    result = subprocess.run(
        ["git", "-C", str(directory), *arguments], env=environment,
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return result.stdout.decode().strip()


def overlay(directory: Path, patches: Path, index: Path) -> None:
    for path in sorted(patches.rglob("*")):
        if path.is_symlink():
            content = os.fsencode(os.readlink(path))
            mode = "120000"
        elif path.is_file():
            content = path.read_bytes()
            mode = "100755" if path.stat().st_mode & 0o111 else "100644"
        else:
            continue
        digest = git(directory, "hash-object", "-w", "--stdin", data=content)
        relative = path.relative_to(patches).as_posix()
        git(directory, "update-index", "--add", "--cacheinfo",
            f"{mode},{digest},{relative}", index=index)


def verify(source: Path) -> list[dict]:
    dependencies = []
    for wrap in sorted((source / "subprojects").glob("*.wrap")):
        configuration = configparser.ConfigParser(interpolation=None)
        configuration.read(wrap)
        section = next((name for name in configuration.sections()
                        if name.startswith("wrap-")), None)
        if section is None:
            continue
        settings = configuration[section]
        directory = source / "subprojects" / settings.get("directory", wrap.stem)
        if not directory.exists():
            continue
        if section != "wrap-git":
            raise ValueError(f"unverified non-Git dependency: {directory}; "
                             "use a fresh C3 source directory")
        revision = settings["revision"]
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError(f"dependency revision is not a full commit: {wrap}")
        if not (directory / ".git").exists():
            raise ValueError(f"dependency is not its pinned Git checkout: {directory}")
        actual_revision = git(directory, "rev-parse", "HEAD")
        if actual_revision != revision:
            raise ValueError(f"dependency {wrap.stem} has HEAD {actual_revision}; "
                             f"required {revision}")
        with tempfile.TemporaryDirectory(prefix="x3-qemu-dependency-") as temporary:
            expected_index = Path(temporary) / "expected"
            actual_index = Path(temporary) / "actual"
            git(directory, "read-tree", revision, index=expected_index)
            if "patch_directory" in settings:
                patches = source / "subprojects/packagefiles" / settings["patch_directory"]
                if not patches.is_dir():
                    raise ValueError(f"missing pinned dependency patch directory: {patches}")
                overlay(directory, patches, expected_index)
            expected_tree = git(directory, "write-tree", index=expected_index)
            git(directory, "read-tree", revision, index=actual_index)
            git(directory, "add", "--all", "--force", "--", ".", index=actual_index)
            # This Meson bookkeeping file is not compiled source.
            git(directory, "update-index", "--force-remove", "--",
                ".meson-subproject-wrap-hash.txt", index=actual_index)
            actual_tree = git(directory, "write-tree", index=actual_index)
            if actual_tree != expected_tree:
                raise ValueError(f"dependency {wrap.stem} has local or generated source edits; "
                                 "use a fresh C3 source directory")
        dependencies.append({
            "name": wrap.stem,
            "repository": settings["url"],
            "revision": revision,
            "source_tree": actual_tree,
            "wrap_sha256": hashlib.sha256(wrap.read_bytes()).hexdigest(),
            "patch_directory": settings.get("patch_directory"),
        })
    return dependencies


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        dependencies = verify(arguments.source.resolve())
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"build-qemu: {error}\n")
    if arguments.output is not None:
        arguments.output.write_text(json.dumps(dependencies, indent=2) + "\n")
    print(f"Verified {len(dependencies)} local pinned Meson Git dependencies.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
