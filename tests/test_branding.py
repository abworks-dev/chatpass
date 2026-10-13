"""Branding assets stay local and preserve the static-file allowlist."""

from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import pytest
from test_console import setup as setup  # Reuse isolated console; never touch real credentials.

STATIC = Path(__file__).resolve().parents[1] / "src" / "chatpass" / "static"
BRAND_ASSETS = (
    "branding.css",
    "chatpass-icon.svg",
    "chatpass-mark.svg",
    "chatpass-icon-512.png",
    "chatpass-icon-32.png",
    "chatpass.ico",
)


class BrandReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()
        self.icon_attributes = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for attribute in ("href", "src"):
            path = attrs.get(attribute, "")
            if path.startswith("/assets/brand/"):
                self.paths.add(path)
        if tag == "img" and "brand-icon" in attrs.get("class", "").split():
            self.icon_attributes = attrs


async def test_console_shell_references_local_ticket_icon(setup):
    shell = await setup.client.get("/admin/")
    assert shell.status_code == 200
    references = BrandReferences()
    references.feed(shell.text)
    assert references.paths == {
        "/assets/brand/branding.css",
        "/assets/brand/chatpass-icon.svg",
        "/assets/brand/chatpass.ico",
    }
    assert references.icon_attributes is not None
    assert references.icon_attributes["width"] == "42"
    assert references.icon_attributes["height"] == "42"
    # The adjacent ChatPass text supplies the accessible name; avoid duplicate announcements.
    assert references.icon_attributes["alt"] == ""
    assert references.icon_attributes["aria-hidden"] == "true"
    assert setup.credentials.calls == 0
    assert not setup.calls


@pytest.mark.parametrize("name", BRAND_ASSETS)
async def test_brand_assets_are_public_local_and_byte_exact(setup, name):
    response = await setup.client.get("/assets/brand/" + name)
    assert response.status_code == 200
    assert response.content == (STATIC / "brand" / name).read_bytes()
    content_type = response.headers["content-type"]
    if name.endswith(".svg"):
        assert content_type.startswith("image/svg+xml")
        root = ElementTree.fromstring(response.content)
        assert root.attrib["viewBox"] == "0 0 128 128"
        assert not root.findall(".//{http://www.w3.org/2000/svg}script")
        assert not root.findall(".//{http://www.w3.org/2000/svg}image")
    elif name.endswith(".png"):
        assert content_type.startswith("image/png")
    elif name.endswith(".ico"):
        assert content_type.startswith("image/")
    else:
        assert content_type.startswith("text/css")
    assert setup.credentials.calls == 0
    assert not setup.calls


@pytest.mark.parametrize("path", [
    "/assets/brand/not-an-asset.svg",
    "/assets/brand/console.py",
    "/assets/brand/%2e%2e/%2e%2e/console.py",
])
async def test_branding_does_not_allow_arbitrary_static_paths(setup, path):
    assert (await setup.client.get(path)).status_code == 404


def test_both_readmes_reference_the_same_packaged_icon():
    root = STATIC.parents[2]
    for name in ("README.md", "README.en.md"):
        readme = (root / name).read_text(encoding="utf-8")
        assert 'src="src/chatpass/static/brand/chatpass-icon.svg"' in readme
        assert 'width="96" height="96"' in readme
