"""Fast license checks plus an opt-in wheel/sdist verification without real credentials."""

import hashlib
import os
import shutil
import subprocess
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LICENSE_FILES = (
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "src/chatpass/static/vendor/LICENSE.tabler",
)
TABLER_SHA256 = "370a2d4e608ae518351d10528fc7dbe19b354ae5062c51b2a9cb294850d65403"


def test_project_declares_mit_and_distribution_notices():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == "MIT"
    assert tuple(project["license-files"]) == LICENSE_FILES
    assert project["authors"] == [{"name": "ChatPass contributors"}]
    assert project["urls"]["Repository"] == "https://github.com/abworks-dev/chatpass"
    # PEP 639 license expressions replace deprecated license classifiers.
    assert not any(value.startswith("License ::") for value in project["classifiers"])
    for filename in LICENSE_FILES:
        assert (ROOT / filename).is_file()


def test_project_and_tabler_keep_separate_copyright_notices():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n\nCopyright (c) 2026 ChatPass contributors\n")
    assert "Permission is hereby granted, free of charge" in license_text
    assert 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND' in license_text
    tabler = (ROOT / LICENSE_FILES[2]).read_text(encoding="utf-8")
    assert "Copyright (c) 2018-2026 The Tabler Authors" in tabler
    assert (
        hashlib.sha256(
            (ROOT / "src/chatpass/static/vendor/tabler.min.css").read_bytes()
        ).hexdigest()
        == TABLER_SHA256
    )


def _assert_metadata(content):
    metadata = BytesParser().parsebytes(content)
    assert metadata["Name"] == "chatpass"
    assert metadata["Version"] == "0.1.0"
    assert metadata["License-Expression"] == "MIT"
    assert set(metadata.get_all("License-File", [])) == set(LICENSE_FILES)
    assert metadata["Author"] == "ChatPass contributors"
    assert "Repository, https://github.com/abworks-dev/chatpass" in metadata.get_all(
        "Project-URL", []
    )
    assert "text/markdown" in metadata["Description-Content-Type"]
    assert metadata["Requires-Python"] == ">=3.11"
    assert "许可证" in content.decode("utf-8")


def _assert_no_runtime_state(names):
    prohibited = {
        ".env",
        ".venv",
        "data",
        "dist",
        "state.enc",
        "state.lock",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
    }
    for name in names:
        assert not (set(Path(name).parts) & prohibited), name


@pytest.mark.skipif(
    os.environ.get("CHATPASS_PACKAGE_TEST") != "1",
    reason="set CHATPASS_PACKAGE_TEST=1 to build and inspect wheel and sdist with uv",
)
def test_built_distributions_include_license_metadata_and_original_notices(tmp_path):
    uv = shutil.which("uv")
    assert uv is not None, "uv is required for the opted-in packaging check"
    output = tmp_path / "artifacts"
    subprocess.run(
        [uv, "build", str(ROOT), "--out-dir", str(output), "--system-certs"],
        check=True,
        timeout=120,
    )
    (wheel,) = output.glob("*.whl")
    (sdist,) = output.glob("*.tar.gz")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        _assert_no_runtime_state(names)
        (metadata_name,) = (name for name in names if name.endswith(".dist-info/METADATA"))
        _assert_metadata(archive.read(metadata_name))
        prefix = metadata_name.removesuffix("METADATA") + "licenses/"
        for filename in LICENSE_FILES:
            assert archive.read(prefix + filename) == (ROOT / filename).read_bytes()
        assert (
            archive.read("chatpass/static/vendor/LICENSE.tabler")
            == (ROOT / LICENSE_FILES[2]).read_bytes()
        )
        for filename in (ROOT / "src/chatpass/static").rglob("*"):
            if filename.is_file():
                packaged_name = str(filename.relative_to(ROOT / "src")).replace("\\", "/")
                assert archive.read(packaged_name) == filename.read_bytes()
    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
        _assert_no_runtime_state(names)
        prefix = names[0].split("/")[0] + "/"
        metadata = archive.extractfile(prefix + "PKG-INFO")
        assert metadata is not None
        _assert_metadata(metadata.read())
        for filename in LICENSE_FILES:
            member = archive.extractfile(prefix + filename)
            assert member is not None
            assert member.read() == (ROOT / filename).read_bytes()
        for filename in ("README.md", "README.en.md", "pyproject.toml"):
            member = archive.extractfile(prefix + filename)
            assert member is not None
            assert member.read() == (ROOT / filename).read_bytes()
