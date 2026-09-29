"""Full Optimization Review alert e-mail, sent directly through the Gmail API (OAuth 2.0).

No SMTP, no n8n, no Resend, no Gmail password. Credentials live OUTSIDE the repository:
    WTMA_GMAIL_CLIENT_FILE  Desktop OAuth client JSON downloaded from Google Cloud
    WTMA_GMAIL_TOKEN_FILE   authorized token written by scripts/gmail_authorize.py
Scope: https://www.googleapis.com/auth/gmail.send is the only Gmail scope; openid/email are
used solely to prove the authorized account is SENDER. Client secrets, access and refresh tokens
are never printed, logged or written into the repo; every string that leaves this module
passes through redact().

Sender and recipients are fixed by business instruction; both recipients are in To of one message:
    from apiramedigit@gmail.com  ->  apiramedigit@gmail.com, roshandigitweb@gmail.com
"""
import base64
import html
import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

REPO = pathlib.Path(__file__).resolve().parent.parent
SENDER = "apiramedigit@gmail.com"
RECIPIENTS = ("apiramedigit@gmail.com", "roshandigitweb@gmail.com")
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
OAUTH_SCOPES = [GMAIL_SEND, "openid", "https://www.googleapis.com/auth/userinfo.email"]
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
SUBJECT = "⚠️ Full Optimization Review Required – ASIN {asin} – 2 Consecutive 7D Performance Drops"
NO_VISIBLE_CHANGE = ("Full Optimization Review is required. The automation will NOT automatically modify title, "
                     "bullets, images, description, A+ content, or other visible listing content. Full Optimization "
                     "must be performed manually by the user/business team.")
_EMAIL = re.compile(r"^[^@\s<>]+@([A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+)$")
_SECRETS = re.compile(r"ya29\.[A-Za-z0-9._-]+|1//[A-Za-z0-9._-]{10,}|GOCSPX-[A-Za-z0-9_-]+|re_[A-Za-z0-9_]{8,}"
                      r'|("(?:access_token|refresh_token|client_secret|token)"\s*:\s*)"[^"]*"')


class AlertConfigError(Exception):
    """Configuration problem. The message never contains a secret."""


def redact(text):
    return _SECRETS.sub(lambda m: (m.group(1) + '"[REDACTED]"') if m.group(1) else "[REDACTED]", str(text))


def _outside_repo(p):
    try:
        p.resolve().relative_to(REPO)
        return False
    except ValueError:
        return True


def gmail_paths(env=None):
    env = os.environ if env is None else env
    out = []
    for var in ("WTMA_GMAIL_CLIENT_FILE", "WTMA_GMAIL_TOKEN_FILE"):
        v = (env.get(var) or "").strip()
        if not v:
            raise AlertConfigError(f"{var} is not set (a file path outside the repository).")
        p = pathlib.Path(v)
        if not p.is_absolute():
            raise AlertConfigError(f"{var} must be an absolute path.")
        if not _outside_repo(p):
            raise AlertConfigError(f"{var} points inside the repository; credentials must be stored outside it.")
        out.append(p)
    if not out[0].is_file():
        raise AlertConfigError("WTMA_GMAIL_CLIENT_FILE does not exist.")
    return out[0], out[1]


def load_config(env=None):
    client, token = gmail_paths(env)
    if not token.is_file():
        raise AlertConfigError("Gmail token not found at WTMA_GMAIL_TOKEN_FILE: run scripts/gmail_authorize.py once.")
    validate_recipients(RECIPIENTS)
    return {"sender": SENDER, "client_file": str(client), "token_file": str(token)}


def validate_recipients(recipients):
    rec = list(recipients)
    if sorted(r.lower() for r in rec) != sorted(RECIPIENTS):
        raise AlertConfigError(f"Recipients must be exactly {list(RECIPIENTS)}; got {rec}")
    for r in rec:
        if not _EMAIL.match(r):
            raise AlertConfigError(f"Invalid recipient address: {r!r}")
    return rec


def access_token(cfg):
    """Fresh access token from the saved OAuth token (refreshed and re-saved when expired).
    Tests pass cfg['get_token'] instead, so no Google library or network is needed."""
    if cfg.get("get_token"):
        return cfg["get_token"]()
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(cfg["token_file"])
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise AlertConfigError("Gmail token refresh failed (revoked or expired): re-run "
                                   f"scripts/gmail_authorize.py. {redact(e)}") from None
        pathlib.Path(cfg["token_file"]).write_text(creds.to_json(), encoding="utf-8")
    return creds.token


def _http(method, url, headers, body, transport=None):
    if transport:
        status, text = transport(method, url, headers, body)
    else:
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                status, text = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as e:
            status, text = None, f"request error: {e}"
    text = redact(text)
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text[:500]


def token_identity(token, transport=None):
    """Google tokeninfo (token sent in the POST body, never in a URL). Returns email + granted scopes."""
    status, js = _http("POST", TOKENINFO, {"Content-Type": "application/x-www-form-urlencoded"},
                       urllib.parse.urlencode({"access_token": token}).encode(), transport)
    if status != 200 or not isinstance(js, dict):
        raise AlertConfigError(f"Google rejected the access token (tokeninfo HTTP {status}).")
    return {"email": (js.get("email") or "").lower(), "email_verified": str(js.get("email_verified")) == "true",
            "scopes": set((js.get("scope") or "").split())}


def validate_gmail(cfg, transport=None):
    """Read-only; sends nothing. ready=True only if the token authenticates, belongs to SENDER, has
    gmail.send, is stored outside the repo, and the Gmail API accepts it."""
    rep = {"sender_expected": SENDER, "authorized_account": None, "email_verified": None,
           "gmail_send_scope": None, "scopes_granted": None, "token_outside_repo": None,
           "gmail_api_reachable": None, "recipients": list(RECIPIENTS), "ready": False, "blocker": None}
    if cfg.get("token_file"):
        rep["token_outside_repo"] = _outside_repo(pathlib.Path(cfg["token_file"]))
    try:
        tok = access_token(cfg)
        who = token_identity(tok, transport)
    except AlertConfigError as e:
        rep["blocker"] = redact(e)
        return rep
    rep.update(authorized_account=who["email"], email_verified=who["email_verified"],
               gmail_send_scope=GMAIL_SEND in who["scopes"], scopes_granted=sorted(who["scopes"]))
    # Non-sending Gmail API probe: with gmail.send only, reading the profile must be refused for
    # *insufficient scope* (403) - proving the API is enabled and the token accepted - and must
    # not be 401 (auth failed) or SERVICE_DISABLED (Gmail API not enabled in the project).
    status, js = _http("GET", f"{GMAIL_API}/profile", {"Authorization": f"Bearer {tok}"}, None, transport)
    blob = json.dumps(js) if isinstance(js, dict) else str(js)
    rep["gmail_api_probe_http"] = status
    rep["gmail_api_reachable"] = status == 200 or (status == 403 and "SERVICE_DISABLED" not in blob
                                                   and "accessNotConfigured" not in blob)
    if who["email"] != SENDER or not who["email_verified"]:
        rep["blocker"] = f"Authorized account is {who['email']!r}, not {SENDER}."
    elif not rep["gmail_send_scope"]:
        rep["blocker"] = "gmail.send scope not granted."
    elif rep["token_outside_repo"] is False:
        rep["blocker"] = "Token file is inside the repository."
    elif not rep["gmail_api_reachable"]:
        rep["blocker"] = f"Gmail API not usable (HTTP {status}): {blob[:200]}"
    rep["ready"] = rep["blocker"] is None
    return rep


def _fmt(v, pct=False):
    if v is None or v == "":
        return "—"
    return f"{v:+.1f}%" if pct else str(v)


def build_alert(a):
    """a: alert context dict (see performance_alert.alert_context). Returns subject, text, html."""
    rows = [
        ("ASIN", a["asin"]), ("SKU", a["sku"]), ("Amazon account", a["account"]), ("Marketplace", a["marketplace"]),
        ("Current 7D", f'{a["current_7d_start"]} → {a["current_7d_end"]}'),
        ("Previous 7D", f'{a["previous_7d_start"]} → {a["previous_7d_end"]}'),
        ("Current 7D orders", _fmt(a["current_orders"])), ("Previous 7D orders", _fmt(a["previous_orders"])),
        ("Order Change %", _fmt(a["order_change_pct"], True)),
        ("Current / previous impressions", f'{_fmt(a["current_impressions"])} / {_fmt(a["previous_impressions"])}'),
        ("Impression Change %", _fmt(a["impression_change_pct"], True)),
        ("CTR Change %", _fmt(a["ctr_change_pct"], True)), ("CVR Change %", _fmt(a["cvr_change_pct"], True)),
        ("Consecutive decline count", _fmt(a["consecutive_decline_count"])),
        ("Last backend keyword fine-tuning date", _fmt(a["last_fine_tuning_date"])),
        ("Last live verification date", _fmt(a["last_live_verification_date"])),
        ("Current backend keyword status", _fmt(a["backend_keyword_status"])),
        ("Duplicate words removed", _fmt(a["duplicate_words_removed"])),
        ("Dashboard / report", _fmt(a["dashboard_ref"])),
    ]
    subject = SUBJECT.format(asin=a["asin"])
    text = ("⚠️ FULL OPTIMIZATION REVIEW REQUIRED\n\n"
            "This ASIN has shown performance decline for 2 consecutive 7-day monitoring cycles after backend "
            "keyword fine-tuning. Full Optimization Review is required.\n\n"
            + "\n".join(f"{k}: {v}" for k, v in rows) + f"\n\n{NO_VISIBLE_CHANGE}\n\nCycle: {a['cycle_id']}\n")
    trs = "".join(f'<tr><th align="left" style="padding:4px 12px 4px 0">{html.escape(k)}</th>'
                  f'<td style="padding:4px 0">{html.escape(str(v))}</td></tr>' for k, v in rows)
    body = (f'<div style="font-family:Arial,sans-serif;font-size:14px;color:#222">'
            f'<h2 style="color:#b45309">⚠️ FULL OPTIMIZATION REVIEW REQUIRED</h2>'
            f'<p>This ASIN has shown performance decline for 2 consecutive 7-day monitoring cycles after backend '
            f'keyword fine-tuning. Full Optimization Review is required.</p><table>{trs}</table>'
            f'<p style="margin-top:16px"><b>{html.escape(NO_VISIBLE_CHANGE)}</b></p>'
            f'<p style="color:#666;font-size:12px">Cycle {html.escape(a["cycle_id"])} · Weekly Top-Moving ASIN '
            f'Backend Keyword Fine-Tuning</p></div>')
    return {"subject": subject, "text": text, "html": body}


def build_mime(alert, idempotency_key):
    msg = EmailMessage()
    msg["From"] = SENDER
    msg["To"] = ", ".join(validate_recipients(RECIPIENTS))
    msg["Subject"] = alert["subject"]
    msg["X-WTMA-Alert-Key"] = idempotency_key
    msg.set_content(alert["text"])
    msg.add_alternative(alert["html"], subtype="html")
    return msg


def send_alert(cfg, alert, idempotency_key, transport=None):
    """One Gmail message with BOTH recipients in To. Returns {'ok', 'http_status', 'message_id', 'error'}.
    Duplicate protection is the alert log in performance_alert (Gmail has no idempotency key);
    the key is carried in the X-WTMA-Alert-Key header for traceability."""
    raw = base64.urlsafe_b64encode(build_mime(alert, idempotency_key).as_bytes()).decode("ascii")
    try:
        tok = access_token(cfg)
    except AlertConfigError as e:
        return {"ok": False, "http_status": None, "message_id": None, "error": redact(e)}
    status, js = _http("POST", f"{GMAIL_API}/messages/send",
                       {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                       json.dumps({"raw": raw}).encode("utf-8"), transport)
    ok = status == 200 and isinstance(js, dict) and bool(js.get("id"))
    return {"ok": ok, "http_status": status, "message_id": js.get("id") if ok else None,
            "error": None if ok else redact(json.dumps(js, ensure_ascii=False) if isinstance(js, dict) else js)}
