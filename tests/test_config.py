from pathlib import Path

import pytest

from chatpass.config import Settings


def test_local_defaults():
    s = Settings()
    assert s.host == '127.0.0.1'
    assert s.port == 19840
    assert s.upstream_base_url == 'https://api.openai.com/v1'
    assert isinstance(s.data_dir, Path)


@pytest.mark.parametrize('kwargs', [
    {'host': '0.0.0.0'}, {'host': 'example.com'}, {'port': 0},
    {'max_concurrency': 0}, {'timeout_seconds': 0}, {'max_body_bytes': -1},
])
def test_invalid_or_public_runtime_rejected(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)
