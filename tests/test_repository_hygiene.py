from __future__ import annotations

import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]


def _tracked_archives() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths = completed.stdout.decode("utf-8").split("\0")
    return [
        ROOT / path
        for path in paths
        if path and (path.endswith(".zip") or path.endswith((".tar.gz", ".tgz")))
    ]


def _archive_member_names(path: Path) -> list[str]:
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    with tarfile.open(path, mode="r:gz") as archive:
        return archive.getnames()


def test_tracked_archives_do_not_package_runtime_environment_files() -> None:
    violations: list[str] = []
    for archive in _tracked_archives():
        for member in _archive_member_names(archive):
            if PurePosixPath(member.replace("\\", "/")).name.lower() == ".env":
                violations.append(f"{archive.relative_to(ROOT)}:{member}")

    assert violations == [], (
        "Tracked deployment archives must not contain runtime .env files: "
        + ", ".join(violations)
    )
