"""Validate local distributions before handing them to the isolated publisher."""

import configparser
import email.parser
import os
from pathlib import Path
import re
import sys
import tarfile
import tomllib
import zipfile


def check(directory: Path) -> str:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("release version must be a stable major.minor.patch")
    tag = os.environ.get("RELEASE_TAG", "")
    if tag and tag != f"v{version}":
        raise ValueError("release tag does not match package version")
    wheels, sources = list(directory.glob("*.whl")), list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1 or len(list(directory.iterdir())) != 2:
        raise ValueError("expected exactly one wheel and one source distribution in a clean dist directory")
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        metadata_path = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = email.parser.Parser().parsestr(wheel.read(metadata_path).decode())
        if metadata["Name"] != "agent-safe-runner" or metadata["Version"] != version:
            raise ValueError("wheel metadata disagrees with pyproject.toml")
        runtime = wheel.read("agent_safe_runner/__init__.py").decode()
        if f'__version__ = "{version}"' not in runtime:
            raise ValueError("runtime version disagrees with metadata")
        requirements = metadata.get_all("Requires-Dist", [])
        if any("extra ==" not in requirement for requirement in requirements):
            raise ValueError("core package must remain dependency-free")
        entries = configparser.ConfigParser()
        entries.read_string(wheel.read(metadata_path.replace("METADATA", "entry_points.txt")).decode())
        if entries["console_scripts"]["agent-safe"] != "agent_safe_runner.cli:main":
            raise ValueError("missing pipx console entry point")
        if "agent_safe_runner/mcp_server.py" not in names:
            raise ValueError("MCP adapter is missing from wheel")
    with tarfile.open(sources[0], "r:gz") as source:
        source_names = source.getnames()
        if not any(name.endswith("/pyproject.toml") for name in source_names):
            raise ValueError("source distribution is incomplete")
    for name in [*names, *source_names]:
        parts = Path(name).parts
        if ".git" in parts or ".venv" in parts or name.endswith((".sqlite3", ".jsonl", ".env")):
            raise ValueError("runtime/private file found in distribution")
    return version


if __name__ == "__main__":
    print(f"Release artifacts verified: {check(Path(sys.argv[1]))}")
