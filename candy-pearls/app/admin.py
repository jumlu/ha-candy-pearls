"""
HA Ingress admin page — served at GET / and embedded in the HA sidebar.

All data is rendered server-side to avoid JS cross-origin / path-prefix issues
with the Supervisor ingress proxy. No external dependencies.
"""
from __future__ import annotations

import html as _html
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from . import memory

logger = logging.getLogger(__name__)
router = APIRouter()

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, -apple-system, sans-serif; background: #f4f4f5; color: #1a1a1a; padding: 1.5rem; }
.page { max-width: 960px; margin: 0 auto; }
h1 { font-size: 1.3rem; font-weight: 700; margin-bottom: 1.5rem; color: #111; }
h2 { font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: #666; margin-bottom: 0.75rem; }
section { background: #fff; border-radius: 10px; padding: 1.25rem 1.5rem; margin-bottom: 1.25rem; box-shadow: 0 1px 4px rgba(0,0,0,0.07); }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th { text-align: left; padding: 0.45rem 0.75rem; border-bottom: 2px solid #f0f0f0; font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #777; }
td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #f5f5f5; vertical-align: middle; }
tr:last-child td { border-bottom: none; }
.mono { font-family: ui-monospace, 'Cascadia Code', monospace; font-size: 0.78rem; color: #444; word-break: break-all; }
.muted { color: #999; font-size: 0.82rem; font-style: italic; }
.badge { display: inline-block; background: #dbeafe; color: #1d4ed8; border-radius: 4px; padding: 0.1rem 0.45rem; font-size: 0.68rem; font-weight: 700; letter-spacing: 0.05em; margin-left: 0.4rem; vertical-align: middle; }
.ts { color: #aaa; font-size: 0.78rem; white-space: nowrap; }
.copy-btn { background: #f3f4f6; border: 1px solid #e5e7eb; border-radius: 5px; padding: 0.2rem 0.55rem; font-size: 0.75rem; cursor: pointer; color: #444; transition: background 0.12s; }
.copy-btn:hover { background: #e5e7eb; }
.copy-btn.ok { background: #d1fae5; color: #065f46; border-color: #a7f3d0; }
#search { width: 100%; padding: 0.5rem 0.75rem; border: 1px solid #e5e7eb; border-radius: 7px; font-size: 0.85rem; margin-bottom: 0.85rem; outline: none; color: #1a1a1a; }
#search:focus { border-color: #93c5fd; box-shadow: 0 0 0 3px rgba(147,197,253,0.3); }
"""

_JS = """
document.querySelectorAll('.copy-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var text = btn.dataset.copy;
    navigator.clipboard.writeText(text).then(function() {
      btn.textContent = 'Copied';
      btn.classList.add('ok');
      setTimeout(function() { btn.textContent = 'Copy'; btn.classList.remove('ok'); }, 1400);
    });
  });
});

var search = document.getElementById('search');
if (search) {
  search.addEventListener('input', function() {
    var q = search.value.toLowerCase();
    document.querySelectorAll('#ctbody tr').forEach(function(row) {
      row.style.display = row.dataset.q.indexOf(q) !== -1 ? '' : 'none';
    });
  });
}

document.querySelectorAll('[data-ts]').forEach(function(el) {
  var diff = Math.floor((Date.now() - new Date(el.dataset.ts)) / 1000);
  var label;
  if (diff < 60) label = diff + 's ago';
  else if (diff < 3600) label = Math.floor(diff / 60) + 'm ago';
  else if (diff < 86400) label = Math.floor(diff / 3600) + 'h ago';
  else label = Math.floor(diff / 86400) + 'd ago';
  el.textContent = label;
  el.title = new Date(el.dataset.ts).toLocaleString();
});
"""


def _e(s: str) -> str:
    return _html.escape(str(s))


def _copy(text: str, label: str = "Copy") -> str:
    return f'<button class="copy-btn" data-copy="{_e(text)}">{label}</button>'


def _render(
    signal_accounts: list[str],
    contacts: list[dict],
    accounts: list,
    whitelist: set[str],
) -> str:
    # Signal Accounts
    if signal_accounts:
        rows = "".join(
            f"<tr><td>{_e(n)}</td><td>{_copy(n)}</td></tr>"
            for n in signal_accounts
        )
        sa = (
            "<table><thead><tr><th>Number</th><th></th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    else:
        sa = '<p class="muted">No accounts returned — is signal-cli-rest-api running?</p>'

    # Known Contacts
    if contacts:
        rows = []
        for c in contacts:
            uuid, name, ts = c["uuid"], c["name"], c["last_seen"]
            badge = '<span class="badge">ADMIN</span>' if uuid in whitelist else ""
            ts_cell = f'<span class="ts" data-ts="{_e(ts)}">{_e(ts[:10])}</span>'
            rows.append(
                f'<tr data-q="{_e((name + " " + uuid).lower())}">'
                f"<td>{_e(name)}{badge}</td>"
                f'<td class="mono">{_e(uuid)}</td>'
                f"<td>{_copy(uuid)}</td>"
                f"<td>{ts_cell}</td>"
                f"</tr>"
            )
        ct = (
            '<input id="search" type="search" placeholder="Filter by name or UUID…">'
            '<table><thead><tr>'
            "<th>Name</th><th>UUID</th><th></th><th>Last seen</th>"
            "</tr></thead>"
            f'<tbody id="ctbody">{"".join(rows)}</tbody></table>'
        )
    else:
        ct = '<p class="muted">No contacts yet — the first inbound Signal message from each sender is recorded here.</p>'

    # Configured Accounts
    if accounts:
        rows = "".join(
            f"<tr>"
            f"<td>{_e(a.name)}</td>"
            f'<td class="mono">{_e(a.recv_group_id)}</td>'
            f'<td class="mono">{_e(a.send_group_id)}</td>'
            f"<td>{_e(a.balance_entity)}</td>"
            f"<td>{a.daily_refill}</td>"
            f"<td>{a.max_balance}</td>"
            f"</tr>"
            for a in accounts
        )
        ac = (
            "<table><thead><tr>"
            "<th>Name</th><th>Recv Group ID</th><th>Send Group ID</th>"
            "<th>Balance Entity</th><th>Daily Refill</th><th>Max Balance</th>"
            "</tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    else:
        ac = '<p class="muted">No accounts configured yet — add children in the Configuration tab.</p>'

    return (
        "<!DOCTYPE html>"
        '<html lang="en">'
        "<head>"
        '<meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        "<title>Candy Pearls Admin</title>"
        f"<style>{_CSS}</style>"
        "</head>"
        "<body><div class=\"page\">"
        "<h1>Candy Pearls Admin</h1>"
        f"<section><h2>Signal Accounts</h2>{sa}</section>"
        f"<section><h2>Known Contacts</h2>{ct}</section>"
        f"<section><h2>Configured Accounts</h2>{ac}</section>"
        f"<script>{_JS}</script>"
        "</div></body></html>"
    )


@router.get("/", response_class=HTMLResponse)
async def admin_page(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    signal_client = request.app.state.signal

    try:
        signal_accounts = await signal_client.get_accounts()
    except Exception as exc:
        logger.warning("Admin: failed to fetch Signal accounts: %s", exc)
        signal_accounts = []

    contacts = memory.get_contacts()
    whitelist = set(getattr(settings, "whitelist_uuids", []))

    return HTMLResponse(_render(signal_accounts, contacts, settings.accounts, whitelist))
