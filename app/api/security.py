"""Who may spend the stored session.

The server holds logged-in TikTok and Instagram sessions, so the exposure was
never that a stranger reads the register — it is that they act as the account
holder. This gates that, and only that.

Two dependencies, because there are two different answers:

- `require_key` guards the session *itself* — saving it, clearing it, importing
  it from a browser. There is no sensible degraded behaviour for those, so they
  401.
- `session_allowed` guards the routes that *spend* the session. They stay open;
  without the key they simply run cookie-less and reach public content only.
  That path is not new code — it is exactly what happens today when no session
  has been saved.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status


API_KEY_HEADER = "X-API-Key"


def _configured_key(request: Request) -> str:
    return (request.app.state.settings.api_key or "").strip()


def _key_matches(request: Request) -> bool:
    configured = _configured_key(request)
    provided = (request.headers.get(API_KEY_HEADER) or "").strip()
    if not provided:
        return False
    # compare_digest, not ==, so a wrong key cannot be found one byte at a time.
    return hmac.compare_digest(provided, configured)


def require_key(request: Request) -> None:
    """401 unless the caller holds the key. Applied to session management."""
    if not _configured_key(request):
        return
    if not _key_matches(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This action needs the server's API key. Add it in Sessions.",
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )


def session_allowed(request: Request) -> bool:
    """Whether this request may run as the account holder. Never raises."""
    if not _configured_key(request):
        return True
    return _key_matches(request)
