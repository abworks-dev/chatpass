"""Allowlisted refresh evidence; never persist remote descriptions or token values."""

import math
import re
import time

_STATUSES = {"started", "not_sent", "uncertain", "rejected", "pending_validation", "validation_failed", "succeeded"}
_STAGES = {"request", "validation", "complete"}
_FIELDS = {"access_token", "refresh_token", "expires_in", "token_type", "earliest_refresh_at"}
_CODES = {
    "upstream_unavailable", "upstream_connect_error", "upstream_connect_timeout",
    "upstream_pool_timeout", "upstream_read_timeout", "upstream_write_timeout",
    "upstream_read_error", "upstream_write_error", "invalid_server_response",
    "invalid_discovery", "invalid_grant", "invalid_client", "invalid_refresh_token",
    "token_expired", "refresh_token_expired", "refresh_token_reused", "refresh_token_invalidated",
    "identity_verification_unavailable", "invalid_id_token", "id_token_not_yet_valid",
    "account_mismatch", "scope_not_authorized", "invalid_token_response", "unknown_error",
}
REJECTED_CODES = {"invalid_grant", "invalid_client", "invalid_refresh_token", "token_expired",
                  "refresh_token_expired", "refresh_token_reused", "refresh_token_invalidated"}


def public_diagnostic(value):
    if not isinstance(value, dict):
        return None
    if not isinstance(value.get("status"), str) or value["status"] not in _STATUSES:
        return None
    if not isinstance(value.get("stage"), str) or value["stage"] not in _STAGES:
        return None
    result = {"status": value["status"], "stage": value["stage"]}
    code = value.get("code")
    if isinstance(code, str):
        result["code"] = code if code in _CODES or re.fullmatch(r"upstream_http_[1-5][0-9]{2}", code) else "unknown_error"
    if isinstance(value.get("field"), str) and value["field"] in _FIELDS:
        result["field"] = value["field"]
    at = value.get("at")
    if isinstance(at, (int, float)) and not isinstance(at, bool) and math.isfinite(at) and at >= 0:
        result["at"] = at
    return result


def record(state, status, stage, error=None):
    value = {"status": status, "stage": stage, "at": time.time()}
    if error is not None:
        value["code"] = getattr(error, "code", "unknown_error")
        value["field"] = getattr(error, "field", None)
    state["refresh_diagnostic"] = public_diagnostic(value)
