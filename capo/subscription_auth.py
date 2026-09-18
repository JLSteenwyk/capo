"""Redacted subscription authentication failures shared by native CLI adapters."""


class LoginRequired(ValueError):
    def __init__(self):
        super().__init__('Subscription login needs attention')


def is_auth_error(value):
    # Inspect diagnostics only in memory. Never return provider messages/tokens.
    text = str(value).lower()
    return any(marker in text for marker in (
        '401', 'unauthorized', 'not authenticated', 'authentication required',
        'not logged in', 'please log in', 'please sign in', 'login required',
        'authentication_error', 'token has expired', 'invalid authentication credentials',
        'invalid_grant', 'token expired', 'token_expired', 'refresh_token_reused',
        'refresh token has expired', 'refresh token has been revoked',
    ))
