import pytest

from chatpass.cli import main


@pytest.mark.parametrize('command', ['serve', 'web'])
def test_concurrency_and_timeout_reach_settings(monkeypatch, command):
    from chatpass.config import Settings
    seen = []

    def capture(**kwargs):
        seen.append(Settings(**kwargs))
        raise ValueError('stop before accessing credentials')

    monkeypatch.setattr('chatpass.cli.Settings', capture)
    capture.max_concurrency = Settings.max_concurrency
    capture.timeout_seconds = Settings.timeout_seconds
    capture.connect_timeout_seconds = Settings.connect_timeout_seconds
    capture.response_header_timeout_seconds = Settings.response_header_timeout_seconds
    capture.stream_idle_timeout_seconds = Settings.stream_idle_timeout_seconds
    capture.stream_total_timeout_seconds = Settings.stream_total_timeout_seconds
    assert main([command, '--max-concurrency', '2', '--connect-timeout', '30']) == 1
    assert seen[0].max_concurrency == 2
    assert seen[0].connect_timeout_seconds == 30


@pytest.mark.parametrize('command', ['serve', 'web'])
@pytest.mark.parametrize('value', ['0', '65'])
def test_invalid_concurrency_fails_before_storage(monkeypatch, command, value):
    monkeypatch.setattr('chatpass.cli.StateStore', lambda *_: pytest.fail('must not access storage'))
    assert main([command, '--max-concurrency', value]) == 1
