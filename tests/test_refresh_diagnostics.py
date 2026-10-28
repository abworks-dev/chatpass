import json

import pytest
from test_credentials import FakeOAuth, make_store

from chatpass.console import credential_status
from chatpass.credentials import Credentials
from chatpass.oauth import OAuthError, RefreshNotSent
from chatpass.refresh_diagnostics import public_diagnostic
from chatpass.storage import StateStore


@pytest.mark.parametrize('error,status,uncertain', [
    (RefreshNotSent('upstream_unavailable'), 'not_sent', False),
    (OAuthError('upstream_unavailable'), 'uncertain', True),
    (OAuthError('upstream_http_503'), 'uncertain', True),
    (OAuthError('invalid_grant'), 'rejected', False),
    (OAuthError('refresh_token_expired'), 'rejected', False),
])
def test_request_failure_evidence_survives_restart(tmp_path, error, status, uncertain):
    store = make_store(tmp_path)
    class Failed(FakeOAuth):
        def refresh(self, *args):
            raise error
    with pytest.raises(OAuthError):
        Credentials(store, Failed())._get_access_token()
    reopened = StateStore(tmp_path, backend=store._backend)
    result = credential_status(reopened.read())
    assert result['refresh_diagnostic']['status'] == status
    assert result['refresh_diagnostic']['stage'] == 'request'
    assert result['refresh_diagnostic']['code'] == error.code
    assert result['refresh_uncertain'] is uncertain
    assert result['login_required'] is (status != 'not_sent')


@pytest.mark.parametrize('code,field', [
    ('invalid_token_response', 'refresh_token'),
    ('invalid_id_token', None),
    ('id_token_not_yet_valid', None),
    ('scope_not_authorized', None),
])
def test_validation_failure_is_not_ambiguous_submission(tmp_path, code, field):
    store = make_store(tmp_path)
    class Invalid(FakeOAuth):
        def validate_tokens(self, *args, **kwargs):
            raise OAuthError(code, field=field)
    with pytest.raises(OAuthError):
        Credentials(store, Invalid())._get_access_token()
    reopened = StateStore(tmp_path, backend=store._backend)
    state = reopened.read()
    result = credential_status(state)
    assert result['login_required'] is True
    assert result['refresh_uncertain'] is False
    assert result['refresh_diagnostic']['status'] == 'validation_failed'
    assert result['refresh_diagnostic']['code'] == code
    if field:
        assert result['refresh_diagnostic']['field'] == field
    oauth = FakeOAuth()
    with pytest.raises(OAuthError, match='login_required'):
        Credentials(reopened, oauth)._get_access_token()
    assert oauth.calls == 0
    assert 'pending_tokens' not in state


def test_diagnostic_never_exposes_remote_content_or_tokens(tmp_path):
    store = make_store(tmp_path)
    secret = 'remote-description-or-secret-token'
    class Unsafe(FakeOAuth):
        def refresh(self, *args):
            raise OAuthError(secret, field=secret)
    with pytest.raises(OAuthError):
        Credentials(store, Unsafe())._get_access_token()
    state = store.read()
    serialized = json.dumps(credential_status(state))
    assert secret not in serialized
    assert secret not in json.dumps(state['refresh_diagnostic'])
    assert state['refresh_diagnostic']['code'] == 'unknown_error'
    assert '"access_token":' not in serialized
    assert 'rotating-old' not in serialized


@pytest.mark.parametrize('value', [None, [], {'status': []}, {'status': 'uncertain', 'stage': {}},
                                  {'status': 'uncertain', 'stage': 'request', 'field': {}, 'code': {}}])
def test_corrupt_diagnostic_is_safe(value):
    result = public_diagnostic(value)
    assert result is None or result == {'status': 'uncertain', 'stage': 'request'}


def test_old_validation_flags_do_not_claim_transport_uncertainty():
    result = credential_status({'credentials': {'expires_at': 0}, 'login_required': True,
                                'refresh_in_progress': True})
    assert result['login_required'] is True
    assert result['refresh_uncertain'] is False
    assert result['refresh_diagnostic'] is None
