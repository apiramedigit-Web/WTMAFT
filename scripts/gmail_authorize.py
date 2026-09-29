"""One-time Gmail OAuth authorization for the Full Optimization Review alert sender.

Opens the browser consent for the Desktop OAuth client at WTMA_GMAIL_CLIENT_FILE and saves the
authorized token to WTMA_GMAIL_TOKEN_FILE. Both paths must be OUTSIDE this repository.

Scopes: https://www.googleapis.com/auth/gmail.send (the only Gmail scope) + openid/email, which
are used solely to prove the authorizing account is the approved sender. The token is saved
only if the Google account is exactly email_alert.SENDER; otherwise it is discarded.
Nothing secret (client secret, access/refresh token, file contents) is ever printed.
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import email_alert  # noqa: E402


def main():
    # Let a partial grant (e.g. the gmail.send checkbox left unticked) reach the explicit
    # scope check below instead of raising inside oauthlib.
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    from google_auth_oauthlib.flow import InstalledAppFlow

    client, token = email_alert.gmail_paths()
    old_rt_hash = None
    if token.exists():   # only a hash, to report whether a fresh refresh token was issued (never printed)
        import hashlib
        old_rt = json.loads(token.read_text(encoding="utf-8")).get("refresh_token") or ""
        old_rt_hash = hashlib.sha256(old_rt.encode()).hexdigest()
    flow = InstalledAppFlow.from_client_secrets_file(str(client), scopes=email_alert.OAUTH_SCOPES)
    print(f"Opening the browser: sign in as {email_alert.SENDER} and allow 'Send email on your behalf'.",
          flush=True)
    creds = flow.run_local_server(port=0, open_browser=True, login_hint=email_alert.SENDER,
                                  prompt="consent", access_type="offline",
                                  # the consent URL holds only the public client id + state (no secret);
                                  # printed so it can be opened manually if the browser tab does not appear
                                  authorization_prompt_message="If no browser tab opened, open this link:\n{url}\n",
                                  success_message=(
                                      "Authorization received. You can close this tab and return to the terminal."))
    who = email_alert.token_identity(creds.token)
    if who["email"] != email_alert.SENDER or not who["email_verified"]:
        print(f'REFUSED: authorized account is {who["email"]!r}, not {email_alert.SENDER}. Token NOT saved.')
        return 2
    if email_alert.GMAIL_SEND not in who["scopes"]:
        print("REFUSED: gmail.send was not granted (tick 'Send email on your behalf' on the consent screen). "
              "Token NOT saved.")
        return 2
    if not creds.refresh_token:
        print("REFUSED: Google returned no refresh token. Token NOT saved.")
        return 2
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(token, 0o600)
    except OSError:
        pass
    # Google returns refresh_token_expires_in only for refresh tokens with a limited lifetime
    # (e.g. 604799 s = 7 days for apps in "Testing" publishing status); absent = no fixed expiry.
    rt_exp = (flow.oauth2session.token or {}).get("refresh_token_expires_in")
    import hashlib
    rotated = old_rt_hash is None or hashlib.sha256(creds.refresh_token.encode()).hexdigest() != old_rt_hash
    print(json.dumps({"saved": True, "sender": who["email"], "scopes": sorted(who["scopes"]),
                      "token_file": str(token), "refresh_token_rotated": rotated,
                      "refresh_token_expires_in_seconds": rt_exp,
                      "limited_lifetime_refresh_token": rt_exp is not None}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except email_alert.AlertConfigError as e:
        print("CONFIG ERROR:", email_alert.redact(e))
        sys.exit(2)
