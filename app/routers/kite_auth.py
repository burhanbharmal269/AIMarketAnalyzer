"""Kite Connect OAuth2 routes.

  GET  /api/kite/login     → returns Zerodha login URL (open in browser)
  GET  /api/kite/callback  → exchanges request_token → access_token (set as redirect)
  GET  /api/kite/status    → session status + expiry info
  POST /api/kite/logout    → invalidates the current session

Daily flow:
  1. User opens  http://localhost:8000/api/kite/login  in browser
  2. Browser redirects to Zerodha; user logs in
  3. Zerodha redirects to  /api/kite/callback?request_token=XXX&status=success
  4. We exchange the token and store it; browser shows success page
  5. Server is now fully operational until 6 AM IST next day
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.data_sources.kite import KITE_AVAILABLE, kite_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["kite-auth"])

# ── Simple HTML templates ─────────────────────────────────────────────────────

def _page(title: str, body: str, color: str = "#22c55e") -> HTMLResponse:
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title} — Options Research Desk</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: system-ui, sans-serif; background: #0f172a;
           color: #e2e8f0; display: flex; align-items: center;
           justify-content: center; min-height: 100vh; }}
    .card {{ background: #1e293b; border: 1px solid #334155;
             border-radius: 12px; padding: 2rem 2.5rem; max-width: 480px;
             width: 100%; text-align: center; }}
    h1 {{ font-size: 1.5rem; margin-bottom: 0.5rem; color: {color}; }}
    p  {{ color: #94a3b8; margin: 0.75rem 0; line-height: 1.6; }}
    a  {{ color: {color}; text-decoration: none; font-weight: 600; }}
    a:hover {{ text-decoration: underline; }}
    .badge {{ display: inline-block; background: #0f172a; border: 1px solid #334155;
              border-radius: 6px; padding: 0.25rem 0.75rem; font-family: monospace;
              font-size: 0.85rem; margin: 0.25rem 0; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    {body}
    <p style="margin-top:1.5rem"><a href="/">← Back to dashboard</a></p>
  </div>
</body>
</html>""")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/kite/login")
def kite_login():
    """Redirect the browser to Zerodha's OAuth login page."""
    if not KITE_AVAILABLE:
        return _page(
            "Kite Not Configured",
            "<p>Set <span class='badge'>KITE_API_KEY</span> and "
            "<span class='badge'>KITE_API_SECRET</span> in your .env file.</p>",
            color="#ef4444",
        )
    url = kite_session.login_url()
    return RedirectResponse(url)


@router.get("/kite/callback")
def kite_callback(request_token: str = "", status: str = "", message: str = ""):
    """Zerodha redirects here after the user logs in.

    Query params (set by Zerodha):
      request_token  — one-time token to exchange for access_token
      status         — "success" | "error"
      message        — error message from Zerodha (on failure)
    """
    if status == "error" or not request_token:
        return _page(
            "Login Failed",
            f"<p>Zerodha reported an error:</p>"
            f"<p class='badge'>{message or 'No request token received'}</p>"
            "<p>Please try <a href='/api/kite/login'>logging in</a> again.</p>",
            color="#ef4444",
        )
    try:
        data = kite_session.exchange_token(request_token)
        user = data.get("user_name") or data.get("user_id", "")
        return _page(
            "Login Successful",
            f"<p>Welcome, <strong>{user}</strong>!</p>"
            "<p>Your Kite session is now active and will remain valid until "
            "<strong>6:00 AM IST</strong> tomorrow.</p>"
            "<p>You can close this tab and return to the dashboard.</p>",
        )
    except Exception as exc:
        logger.error("Kite token exchange failed: %s", exc)
        return _page(
            "Token Exchange Failed",
            f"<p>Could not exchange the request token:</p>"
            f"<p class='badge'>{exc}</p>"
            "<p>Please try <a href='/api/kite/login'>logging in</a> again.</p>",
            color="#ef4444",
        )


@router.get("/kite/status")
def kite_status():
    """Return current Kite session status as JSON."""
    if not KITE_AVAILABLE:
        return JSONResponse({
            "configured": False,
            "connected":  False,
            "message":    "KITE_API_KEY / KITE_API_SECRET not set in .env",
        })
    return kite_session.status()


@router.post("/kite/logout")
def kite_logout():
    """Invalidate the current session. User must re-login afterwards."""
    if not KITE_AVAILABLE:
        return {"ok": False, "message": "Kite not configured"}
    kite_session.invalidate()
    # Remove session file
    try:
        from app.data_sources.kite import _SESSION_FILE
        if _SESSION_FILE.exists():
            _SESSION_FILE.unlink()
    except Exception:
        pass
    return {"ok": True, "message": "Session invalidated — visit /api/kite/login to re-authorise"}
