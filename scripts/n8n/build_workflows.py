#!/usr/bin/env python3
"""
Generator for YipDen's n8n workflows: the creator directory's "Is this you?" requests.

Modelled on indienodes-app's `scripts/n8n/build_workflows.py`, and built the same way on purpose:
the workflows are *generated*, never hand-edited (an edit in the n8n UI is reverted by the next
push), signed review links go through one signature helper, every request is kept in a Data Table
until a decision, and the maintainer approves from a page whose only side-effecting action is a
POST, so a link previewer or mail scanner that opens a URL cannot approve anything.

    python3 scripts/n8n/build_workflows.py --list
    python3 scripts/n8n/build_workflows.py --emit            # writes n8n/workflows/*.json, no API key
    python3 scripts/n8n/build_workflows.py --create-tables
    python3 scripts/n8n/build_workflows.py --push [--only NAME]

The API key is read from ~/.n8n-api-key (the same file indienodes-app uses) and is never written
anywhere. Nothing here is a secret: the HMAC key, the rate-limit salt, the Turnstile secret, the
Gotify token and the GitHub token all live in n8n credentials, named below. See
docs/n8n-runbook.md for setting them up.
"""

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent

# --- Environment-specific configuration -------------------------------------
# IDs on one n8n instance, not portable constants. Credential IDs are filled in once the
# credentials exist (docs/n8n-runbook.md); --push refuses while any is empty.

N8N_BASE = "https://n8n.kjnet.us"
API = f"{N8N_BASE}/api/v1"

# Filled in from --create-tables.
TABLE_REQUESTS = "jQPC5vmy2nlUNVl1"
TABLE_RATE_LIMITS = "TBw8H8Cq8OgWhY3m"

DATA_TABLES_SCHEMA = json.loads((HERE / "data-tables-schema.json").read_text())

CRYPTO_CREDENTIAL = {"id": "JYMNbKffktAQll4k", "name": "YipDen - Review Link HMAC"}
RATE_LIMIT_CREDENTIAL = {"id": "JkbuIsOLvTKzx2A6", "name": "YipDen - Rate Limit Salt"}
TURNSTILE_CREDENTIAL = {"id": "Xg8iC0nkUnK0nwTX", "name": "YipDen - Turnstile Secret"}
GOTIFY_CREDENTIAL = {"id": "HHn5tGkeTH4JaGhj", "name": "YipDen Notifications"}
GITHUB_CREDENTIAL = {"id": "fy3nhsblMq8H1cHP", "name": "Github PAT - YipDen"}

# The repository whose `directory-optout` workflow an approved removal runs.
GITHUB_REPO = "XTREEMMAK/yipden-app"
OPTOUT_WORKFLOW_FILE = "directory-optout.yml"

TURNSTILE_ENABLED = True
ALLOWED_ORIGINS = "https://yipden.com"

REQUEST_WEBHOOK_PATH = "yipden-directory-request"
REVIEW_WEBHOOK_PATH = "yipden-directory-review"
REVIEW_CONFIRM_WEBHOOK_PATH = f"{REVIEW_WEBHOOK_PATH}-confirm"
REVIEW_WEBHOOK_BASE = f"{N8N_BASE}/webhook/{REVIEW_WEBHOOK_PATH}"
REVIEW_CONFIRM_WEBHOOK_BASE = f"{N8N_BASE}/webhook/{REVIEW_CONFIRM_WEBHOOK_PATH}"

MIN_DWELL_MS = 1500
RATE_LIMIT_WINDOW_SECONDS = 10 * 60
REVIEW_LINK_TTL_SECONDS = 7 * 24 * 60 * 60
STALE_CLAIM_SECONDS = 5 * 60

ERROR_WORKFLOW_NAME = "YipDen - Error Workflow"
SIGNATURE_NAME = "YipDen - Helper - Review Link Signature"
REQUEST_NAME = "YipDen - Directory Request"
REVIEW_NAME = "YipDen - Directory Review"


# --- Node and workflow builders ---------------------------------------------

def node(name, type_, type_version, position, parameters=None, **extra):
    n = {"parameters": parameters or {}, "name": name, "type": type_,
         "typeVersion": type_version, "position": list(position)}
    n.update(extra)
    return n


def code_node(name, position, js, **extra):
    return node(name, "n8n-nodes-base.code", 2, position, {"jsCode": js.strip() + "\n"}, **extra)


def link(*targets):
    """One main output, to each target."""
    return {"main": [[{"node": t, "type": "main", "index": 0} for t in targets]]}


def branches(*targets):
    """One main output per target, in order (an IF's true/false, a Switch's rules)."""
    return {"main": [[{"node": t, "type": "main", "index": 0}] if t else [] for t in targets]}


def settings(error_workflow_id=None, caller_ids=None, no_persist=False):
    s = {"executionOrder": "v1", "binaryMode": "separate"}
    if error_workflow_id:
        s["errorWorkflow"] = error_workflow_id
    if caller_ids:
        s["callerPolicy"] = "workflowsFromAList"
        s["callerIds"] = ",".join(caller_ids)
    if no_persist:
        s["saveDataSuccessExecution"] = "none"
        s["saveDataErrorExecution"] = "none"
        s["saveManualExecutions"] = False
    return s


def ifn(name, pos, left, right, idx):
    return node(name, "n8n-nodes-base.if", 2.3, pos, {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                   "typeValidation": "loose", "version": 3},
                       "conditions": [{"id": "y0000000-0000-4000-8000-%012d" % idx,
                                       "leftValue": left, "rightValue": right,
                                       "operator": {"type": "string", "operation": "equals"}}],
                       "combinator": "and"},
        "options": {}})


def switch(name, pos, values):
    rules = []
    for i, value in enumerate(values):
        rules.append({"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                                 "typeValidation": "loose", "version": 3},
                                     "conditions": [{"id": f"s{i}", "leftValue": "={{ $json.route }}",
                                                     "rightValue": value,
                                                     "operator": {"type": "string",
                                                                  "operation": "equals"}}],
                                     "combinator": "and"}})
    return node(name, "n8n-nodes-base.switch", 3.4, pos, {
        "rules": {"values": rules}, "options": {"fallbackOutput": "extra"}})


def table(table_id, name):
    return {"__rl": True, "value": table_id, "mode": "list", "cachedResultName": name}


def dt_schema(name):
    return [{"id": c["name"], "displayName": c["name"], "required": False,
             "defaultMatch": False, "display": True, "type": c["type"], "readOnly": False,
             "removed": False} for c in DATA_TABLES_SCHEMA[name]["columns"]]


def columns(name, values):
    return {"mappingMode": "defineBelow", "value": values, "matchingColumns": [],
            "schema": dt_schema(name), "attemptToConvertTypes": False,
            "convertFieldsToString": False}


def call_signature(name, pos, ctx, values):
    return node(name, "n8n-nodes-base.executeWorkflow", 1.3, pos, {
        "workflowId": {"__rl": True, "value": ctx.get("signature_id", ""), "mode": "list",
                       "cachedResultName": SIGNATURE_NAME},
        "workflowInputs": {"mappingMode": "defineBelow",
                           "value": {"request_id": "", "decision": "", "exp": "", "sig": "",
                                     **values},
                           "matchingColumns": [""], "schema": [],
                           "attemptToConvertTypes": False, "convertFieldsToString": True},
        "options": {}})


def turnstile_nodes(token_expr, verify_pos, verdict_pos):
    """Cloudflare Siteverify and its verdict. Fails closed, as indienodes-app's does."""
    return [
        node("verify turnstile", "n8n-nodes-base.httpRequest", 4.5, verify_pos, {
            "method": "POST", "url": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            "sendBody": True, "specifyBody": "json",
            "authentication": "genericCredentialType", "genericAuthType": "httpCustomAuth",
            # The credential injects `secret` into the body: it is never workflow data.
            "jsonBody": "={{ JSON.stringify({ response: %s }) }}" % token_expr,
            "options": {"timeout": 8000,
                        "response": {"response": {"neverError": True, "fullResponse": True,
                                                  "responseFormat": "json"}}}},
             credentials={"httpCustomAuth": TURNSTILE_CREDENTIAL},
             onError="continueErrorOutput"),
        code_node("turnstile verdict", verdict_pos,
                  "const s = Number($json.statusCode || 0);\n"
                  "const b = $json.body || {};\n"
                  "const passed = s >= 200 && s < 300 && b && b.success === true;\n"
                  "return [{ json: { ok: passed ? 'yes' : 'no' } }];"),
    ]


# --- Workflow: Error Workflow ------------------------------------------------

def wf_error_workflow(ctx):
    """Records operational metadata for a failure: never a request's contents or contact."""
    js = """
const e = $json.execution || {};
const w = $json.workflow || {};
const err = e.error || {};
return [{ json: {
  workflow_name: w.name || '',
  workflow_id:   w.id || '',
  execution_id:  e.id || '',
  failed_node:   err.node?.name || e.lastNodeExecuted || '',
  error_class:   err.name || 'UnknownError',
  timestamp:     new Date().toISOString(),
  mode:          e.mode || ''
} }];
"""
    return {
        "name": ERROR_WORKFLOW_NAME,
        "settings": settings(),
        "nodes": [
            node("Error Trigger", "n8n-nodes-base.errorTrigger", 1, (0, 0)),
            code_node("shape safe metadata", (240, 0), js),
        ],
        "connections": {"Error Trigger": link("shape safe metadata")},
    }


# --- Workflow: Review Link Signature helper ----------------------------------

def wf_signature_helper(ctx):
    """HMAC-SHA256 sign and verify for the approve/reject/view links, as indienodes-app's."""
    build_js = f"""
const inp = $input.first().json;
const mode = (inp.mode || '').toString();
const id = (inp.request_id || '').toString();
if (!/^[a-z0-9]{{8,64}}$/.test(id)) throw new Error('signature helper: invalid request_id');

if (mode === 'sign') {{
  const exp = Math.floor(Date.now() / 1000) + {REVIEW_LINK_TTL_SECONDS};
  return ['approve', 'reject', 'view'].map((d) => ({{
    json: {{ mode, request_id: id, decision: d, exp, message: `${{id}}|${{d}}|${{exp}}` }}
  }}));
}}

if (mode === 'verify') {{
  const decision = (inp.decision || '').toString();
  const expRaw = inp.exp === undefined || inp.exp === null ? '' : inp.exp.toString();
  const sig = (inp.sig || '').toString().toLowerCase();
  const shape_ok =
    (decision === 'approve' || decision === 'reject' || decision === 'view') &&
    /^[0-9]{{1,12}}$/.test(expRaw) && /^[0-9a-f]{{64}}$/.test(sig);
  const exp = shape_ok ? parseInt(expRaw, 10) : 0;
  return [{{ json: {{ mode, request_id: id, decision, exp, sig, shape_ok,
    message: shape_ok ? `${{id}}|${{decision}}|${{exp}}` : '\\u0000invalid' }} }}];
}}

throw new Error('signature helper: unknown mode ' + JSON.stringify(mode));
"""
    emit_js = f"""
const items = $input.all();
const mode = items[0].json.mode;

if (mode === 'sign') {{
  const out = {{ ok: true }};
  for (const it of items) {{
    const j = it.json;
    const sig = (j.data || '').toLowerCase();
    if (!/^[0-9a-f]{{64}}$/.test(sig)) throw new Error('signature helper: no sha256 digest');
    const qs = ['request_id=' + encodeURIComponent(j.request_id),
      'decision=' + encodeURIComponent(j.decision),
      'exp=' + encodeURIComponent(j.exp), 'sig=' + encodeURIComponent(sig)].join('&');
    out[j.decision + '_link'] = {json.dumps(REVIEW_WEBHOOK_BASE)} + '?' + qs;
    out[j.decision + '_sig'] = sig;
    out.exp = j.exp;
  }}
  return [{{ json: out }}];
}}

const j = items[0].json;
const expected = (j.data || '').toLowerCase();
const got = (j.sig || '').toLowerCase();
let ok = j.shape_ok === true && expected.length === 64 && got.length === 64;
if (ok) {{
  let diff = 0;
  for (let i = 0; i < 64; i++) diff |= expected.charCodeAt(i) ^ got.charCodeAt(i);
  ok = diff === 0;
}}
// Expiry only once the signature is trusted: otherwise "expired" would say the id was real.
const expired = ok && Math.floor(Date.now() / 1000) > j.exp;
return [{{ json: {{ valid: ok ? 'yes' : 'no', expired: expired ? 'yes' : 'no',
  decision: ok ? j.decision : '' }} }}];
"""
    return {
        "name": SIGNATURE_NAME,
        "settings": settings(error_workflow_id=ctx.get("error_workflow_id"),
                             caller_ids=ctx.get("signature_callers", []), no_persist=True),
        "nodes": [
            node("Trigger", "n8n-nodes-base.executeWorkflowTrigger", 1.2, (0, 0), {
                "workflowInputs": {"values": [
                    {"name": "mode", "type": "string"}, {"name": "request_id", "type": "string"},
                    {"name": "decision", "type": "string"}, {"name": "exp", "type": "string"},
                    {"name": "sig", "type": "string"}]}}),
            code_node("build canonical message", (240, 0), build_js),
            node("hmac sha256", "n8n-nodes-base.crypto", 2, (480, 0),
                 {"action": "hmac", "type": "SHA256", "value": "={{ $json.message }}",
                  "encoding": "hex"},
                 credentials={"crypto": CRYPTO_CREDENTIAL}),
            code_node("emit links or verdict", (720, 0), emit_js),
        ],
        "connections": {"Trigger": link("build canonical message"),
                        "build canonical message": link("hmac sha256"),
                        "hmac sha256": link("emit links or verdict")},
    }


# --- Workflow: Directory Request (the form's webhook) ------------------------

def wf_request(ctx):
    """`/is-this-you/` posts here. Checked, rate limited, stored, and sent to the maintainer.

    Nothing is decided here: a request waits in the Data Table for the maintainer's signed
    decision (Directory Review). The response never says whether a listing exists.
    """
    validate = """
const body = $json.body;
const err = (message, retryable) =>
  [{ json: { route: 'error', payload: { ok: false, message, retryable } } }];
if (!body || typeof body !== 'object' || Array.isArray(body)) return err('That request was not valid.', false);

// The bot gate first, before any validation can tell a bot which field it got wrong.
const honeypot = typeof body.honeypot === 'string' ? body.honeypot : '';
const elapsed = Number(body.elapsed_ms);
if (honeypot !== '' || !Number.isFinite(elapsed) || elapsed < %(dwell)d) {
  return [{ json: { route: 'dropped' } }];
}

const str = (v, max) => (typeof v === 'string' ? v.trim().slice(0, max) : '');
const action = str(body.action, 16);
if (!['remove', 'correct', 'confirm'].includes(action)) return err('Choose what you would like.', false);
const listing = str(body.listing, 500);
if (!listing) return err('Add the site, profile or handle you are listed under.', false);
const listing_id = str(body.listing_id, 81);
const contact = str(body.contact, 320);
if (contact && !/^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/.test(contact)) {
  return err('That email address does not look right.', false);
}
const token = str(body.turnstile, 2048);
if (%(ts)s && !token) return err('Spam check failed - please try again.', true);

const headers = $('Webhook').first().json.headers || {};
const ip = String(headers['cf-connecting-ip'] || headers['x-real-ip'] ||
  String(headers['x-forwarded-for'] || '').split(',')[0] || '').trim();

const request_id = (Date.now().toString(36) + Math.random().toString(36).slice(2, 12)).slice(0, 24);
return [{ json: { route: 'store', request_id, action, listing,
  listing_id: /^[a-z0-9][a-z0-9-]{0,80}$/.test(listing_id) ? listing_id : '',
  details: str(body.details, 2000), contact, turnstile: token, rate_key: ip || 'unknown' } }];
""" % {"dwell": MIN_DWELL_MS, "ts": "true" if TURNSTILE_ENABLED else "false"}

    rate_js = """
const rows = $input.all().map((i) => i.json).filter((r) => r && r.created_at);
let newest = 0;
for (const r of rows) {
  const t = Date.parse(r.created_at);
  if (!Number.isNaN(t) && t > newest) newest = t;
}
// A removal is never held back: someone asking to be unlisted must not have to wait.
const removal = $('validate').first().json.action === 'remove';
const blocked = !removal && newest > 0 && Date.now() - newest < %(win)d * 1000;
return [{ json: { blocked: blocked ? 'yes' : 'no' } }];
""" % {"win": RATE_LIMIT_WINDOW_SECONDS}

    notify_js = r"""
const r = $('validate').first().json;
const links = $('sign review links').first().json;
const what = { remove: 'Remove', correct: 'Correct', confirm: 'Confirm (yes, that is me)' }[r.action];
const lines = [
  'Request: ' + what,
  'Listing: ' + r.listing,
  'Entry id: ' + (r.listing_id || '(none given)'),
  r.contact ? 'Reply to: ' + r.contact : 'No reply address',
  '',
  r.details || '(no details)',
  '',
  'Review: ' + links.view_link
];
return [{ json: { title: 'YipDen directory: ' + what, body: lines.join('\n') } }];
"""

    nodes = [
        node("Webhook", "n8n-nodes-base.webhook", 2.1, (-1100, 0), {
            "httpMethod": "POST", "path": REQUEST_WEBHOOK_PATH, "responseMode": "responseNode",
            "options": {"allowedOrigins": ALLOWED_ORIGINS}}),
        code_node("validate", (-880, 0), validate),
        switch("route", (-660, 0), ["store", "dropped"]),
        node("rate: hash", "n8n-nodes-base.crypto", 2, (-200, -100),
             {"action": "hmac", "type": "SHA256", "value": "={{ $('validate').first().json.rate_key }}",
              "encoding": "hex", "dataPropertyName": "hash"},
             credentials={"crypto": RATE_LIMIT_CREDENTIAL}),
        node("rate: get rows", "n8n-nodes-base.dataTable", 1.1, (20, -100), {
            "operation": "get", "dataTableId": table(TABLE_RATE_LIMITS, "yipden_rate_limits"),
            "filters": {"conditions": [{"keyName": "ip_hash", "keyValue": "={{ $json.hash }}"}]}},
             alwaysOutputData=True),
        code_node("rate: decide", (240, -100), rate_js),
        ifn("rate limited?", (460, -100), "={{ $json.blocked }}", "yes", 1),
        code_node("shape rate limited", (680, 60),
                  "return [{ json: { ok: false, message: 'Please wait a few minutes before sending another request.', retryable: true } }];"),
        node("store request", "n8n-nodes-base.dataTable", 1.1, (680, -200), {
            "dataTableId": table(TABLE_REQUESTS, "yipden_directory_requests"),
            "columns": columns("yipden_directory_requests", {
                "request_id": "={{ $('validate').first().json.request_id }}",
                "action": "={{ $('validate').first().json.action }}",
                "listing": "={{ $('validate').first().json.listing }}",
                "listing_id": "={{ $('validate').first().json.listing_id }}",
                "details": "={{ $('validate').first().json.details }}",
                "contact": "={{ $('validate').first().json.contact }}",
                "status": "pending_review",
                "created_at": "={{ new Date().toISOString() }}"}),
            "options": {}}),
        node("rate: record", "n8n-nodes-base.dataTable", 1.1, (900, -200), {
            "dataTableId": table(TABLE_RATE_LIMITS, "yipden_rate_limits"),
            "columns": columns("yipden_rate_limits", {
                "ip_hash": "={{ $('rate: hash').first().json.hash }}",
                "created_at": "={{ new Date().toISOString() }}"}),
            "options": {}}),
        call_signature("sign review links", (1120, -200), ctx, {
            "mode": "sign", "request_id": "={{ $('validate').first().json.request_id }}"}),
        code_node("build notification", (1340, -200), notify_js),
        node("notify: gotify", "n8n-nodes-base.gotify", 1, (1560, -200), {
            "message": "={{ $json.body }}",
            "additionalFields": {"title": "={{ $json.title }}", "priority": 7}},
             credentials={"gotifyApi": GOTIFY_CREDENTIAL}, onError="continueRegularOutput"),
        # Stored before notifying: a failed notification still leaves the request in the table.
        code_node("shape received", (1780, -200), "return [{ json: { ok: true } }];"),
        code_node("shape fake success", (-440, 120),
                  "// Indistinguishable from a real answer, so a bot learns nothing.\n"
                  "return [{ json: { ok: true } }];"),
        code_node("shape client error", (-440, 260), "return [{ json: $json.payload }];"),
        node("Respond", "n8n-nodes-base.respondToWebhook", 1.5, (2000, 0),
             {"respondWith": "json", "responseBody": "={{ $json }}", "options": {}}),
    ]
    connections = {
        "Webhook": link("validate"),
        "validate": link("route"),
        "route": branches("verify turnstile" if TURNSTILE_ENABLED else "rate: hash",
                          "shape fake success", "shape client error"),
        "rate: hash": link("rate: get rows"),
        "rate: get rows": link("rate: decide"),
        "rate: decide": link("rate limited?"),
        "rate limited?": branches("shape rate limited", "store request"),
        "store request": link("rate: record"),
        "rate: record": link("sign review links"),
        "sign review links": link("build notification"),
        "build notification": link("notify: gotify"),
        "notify: gotify": link("shape received"),
        "shape received": link("Respond"),
        "shape rate limited": link("Respond"),
        "shape fake success": link("Respond"),
        "shape client error": link("Respond"),
    }
    if TURNSTILE_ENABLED:
        nodes += [
            *turnstile_nodes("$('validate').first().json.turnstile", (-440, -100), (-220, -260)),
            ifn("turnstile passed?", (-20, -260), "={{ $json.ok }}", "yes", 2),
            code_node("shape turnstile failure", (200, -360),
                      "return [{ json: { ok: false, message: 'Spam check failed - please try again.', retryable: true } }];"),
        ]
        connections.update({
            "verify turnstile": branches("turnstile verdict", "turnstile verdict"),
            "turnstile verdict": link("turnstile passed?"),
            "turnstile passed?": branches("rate: hash", "shape turnstile failure"),
            "shape turnstile failure": link("Respond"),
        })
    return {
        "name": REQUEST_NAME,
        # A request's contact address and words live in the Data Table until a decision, then are
        # scrubbed; n8n must not keep a second copy in its execution history.
        "settings": settings(error_workflow_id=ctx.get("error_workflow_id"), no_persist=True),
        "nodes": nodes,
        "connections": connections,
    }


# --- Workflow: Directory Review (signed links, confirmed by POST) -------------

REVIEW_STYLE = (
    ":root{color-scheme:light dark;--bg:#f7f2ee;--ink:#1d1410;--muted:#6b5a52;--brand:#c2410c;"
    "--panel:#fff;--line:#e8ddd6}"
    "@media (prefers-color-scheme:dark){:root{--bg:#1a120e;--ink:#f3ebe6;--muted:#b9a79e;"
    "--panel:#26190f;--line:#3a2a20}}"
    "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);"
    "font:16px/1.5 system-ui,sans-serif}main{max-width:36rem;margin:0 auto;padding:1.25rem}"
    ".brand{font-weight:700;color:var(--brand);text-decoration:none}"
    ".panel{margin-top:1rem;padding:1.25rem;border:1px solid var(--line);border-radius:16px;"
    "background:var(--panel)}h1{font-size:1.4rem;margin:.25rem 0 .75rem}"
    "dl{display:grid;grid-template-columns:auto 1fr;gap:.35rem .9rem;margin:0 0 1rem}"
    "dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}"
    ".actions{display:flex;gap:.6rem;flex-wrap:wrap}form{margin:0}"
    "button{min-height:44px;padding:0 1.2rem;border-radius:999px;font:inherit;font-weight:600;"
    "border:1px solid var(--line);background:var(--panel);color:var(--ink)}"
    "button.approve{background:var(--brand);border-color:var(--brand);color:#fff}"
    "code{background:var(--bg);padding:.1rem .35rem;border-radius:6px}"
)


def page(body, title="YipDen directory review"):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light dark"><title>' + title + '</title>'
            '<style>' + REVIEW_STYLE + '</style></head><body><main>'
            '<a class="brand" href="https://yipden.com/">YipDen · private review</a>'
            '<article class="panel">' + body + '</article></main></body></html>')


def wf_review(ctx):
    """Signed approve/reject/view links. A GET only ever shows a page; the page's buttons POST.

    Approving a removal with an entry id dispatches yipden-app's `directory-optout` workflow with
    that id alone, which opens a pull request; nothing the person wrote reaches GitHub. A removal
    without an id, a correction and a confirmation are approved here and done by hand, with the
    page saying exactly what to run. The contact address and details are scrubbed on any decision.
    """
    validate_q = """
const req = $input.first().json || {};
const isConfirm = req._review_request === 'confirm';
const q = isConfirm ? (req.body || {}) : (req.query || {});
const S = (v, max) => { const s = v === undefined || v === null ? '' : v.toString(); return s.length > max ? '' : s; };
return [{ json: {
  request_id: S(q.request_id, 64), decision: S(q.decision, 16), exp: S(q.exp, 12), sig: S(q.sig, 128),
  // Only the POST webhook can set this: a crafted query on the GET link stays read-only.
  confirmed: isConfirm && S(q.confirmed, 3) === 'yes' ? 'yes' : 'no'
} }];
"""
    auth_verdict = """
const v = $json;
if (v.valid !== 'yes') return [{ json: { route: 'invalid' } }];
if (v.expired === 'yes') return [{ json: { route: 'expired' } }];
return [{ json: { route: 'valid', decision: v.decision } }];
"""
    esc = (
        "const esc = (v) => (v === undefined || v === null ? '' : v.toString())\n"
        "  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')\n"
        "  .replace(/\"/g, '&quot;').replace(/'/g, '&#39;');\n"
    )
    confirm_page = esc + """
const row = $('get request row').first().json || {};
const q = $('validate query').first().json;
const decision = $('auth verdict').first().json.decision;
const what = { remove: 'Remove the listing', correct: 'Correct the listing', confirm: 'Confirm: yes, that is me' }[row.action] || row.action;
if (!row.request_id) {
  return [{ json: { html: __PAGE__('<h1>This request no longer exists.</h1>') } }];
}
const pending = String(row.status || '') === 'pending_review' || String(row.status || '') === 'approval_failed';
const form = (d, label, cls) =>
  '<form method="post" action="' + __CONFIRM__ + '">' +
  '<input type="hidden" name="request_id" value="' + esc(q.request_id) + '">' +
  '<input type="hidden" name="decision" value="' + esc(d.decision) + '">' +
  '<input type="hidden" name="exp" value="' + esc(d.exp) + '">' +
  '<input type="hidden" name="sig" value="' + esc(d.sig) + '">' +
  '<input type="hidden" name="confirmed" value="yes">' +
  '<button class="' + cls + '" type="submit">' + label + '</button></form>';
const links = $('sign fresh links').first().json;
const body =
  '<p>' + esc(row.status) + '</p><h1>' + esc(what) + '</h1>' +
  '<dl><dt>Listing</dt><dd>' + esc(row.listing) + '</dd>' +
  '<dt>Entry id</dt><dd>' + (row.listing_id ? '<code>' + esc(row.listing_id) + '</code>' : 'none given') + '</dd>' +
  '<dt>Reply to</dt><dd>' + (esc(row.contact) || 'no address') + '</dd>' +
  '<dt>Details</dt><dd>' + (esc(row.details) || 'none') + '</dd></dl>' +
  (pending
    ? '<div class="actions">' +
      form({ decision: 'approve', exp: links.exp, sig: links.approve_sig }, 'Approve', 'approve') +
      form({ decision: 'reject', exp: links.exp, sig: links.reject_sig }, 'Reject', '') + '</div>'
    : '<p>Already decided.</p>');
return [{ json: { html: __PAGE__(body) } }];
"""
    page_fn = ("(b) => " + json.dumps(page("@@BODY@@")).replace("@@BODY@@", '" + b + "'))
    confirm_page = (confirm_page.replace("__PAGE__", "(" + page_fn + ")")
                    .replace("__CONFIRM__", json.dumps(REVIEW_CONFIRM_WEBHOOK_BASE)))

    precheck = """
const row = $('get request row').first().json || {};
const decision = $('auth verdict').first().json.decision;
const bad = (m) => [{ json: { ok: 'no', message: m } }];
if (!row.request_id) return bad('This request no longer exists.');
const st = String(row.status || '');
let open = st === 'pending_review' || st === 'approval_failed';
if (!open && st.indexOf('reviewing-') === 0) {
  const stamped = Number(st.split('-').pop());
  open = Number.isFinite(stamped) && Date.now() - stamped > %(stale)d;
  if (!open) return bad('This request is being handled right now. Try again shortly.');
}
if (!open) return bad('This request was already decided.');
if (decision !== 'approve' && decision !== 'reject') return bad('This link is invalid.');
let route = 'reject';
if (decision === 'approve') {
  route = row.action === 'remove' && /^[a-z0-9][a-z0-9-]{0,80}$/.test(String(row.listing_id || ''))
    ? 'dispatch' : 'manual';
}
return [{ json: { ok: 'yes', route, action: row.action, listing: row.listing, listing_id: row.listing_id || '' } }];
""" % {"stale": STALE_CLAIM_SECONDS * 1000}

    manual_page = esc + """
const p = $('precheck').first().json;
const steps = {
  remove: 'There was no entry id. Find the entry for <code>' + esc(p.listing) + '</code>, then run <code>pnpm directory:optout --id &lt;id&gt;</code> in yipden-app, or <code>pnpm directory:optout ' + esc(p.listing) + '</code>.',
  correct: 'Edit the entry in yipden-app\\'s <code>directory/people.json</code>, then <code>pnpm directory:resolve &lt;id&gt;</code> and <code>pnpm directory:check</code>.',
  confirm: 'Once you are sure it is really them, set <code>"listing": "confirmed"</code> on their entry in <code>directory/people.json</code>.'
}[p.action] || 'Handle it by hand.';
return [{ json: { html: __PAGE__('<h1>Approved. One step is yours.</h1><p>' + steps + '</p>') } }];
""".replace("__PAGE__", "(" + page_fn + ")")

    dispatch_body = ("={{ JSON.stringify({ ref: 'main', inputs: { id: "
                     "$('precheck').first().json.listing_id } }) }}")
    dispatch_verdict = """
const s = Number($json.statusCode || 0);
// workflow_dispatch answers 204 No Content when the run is queued.
return [{ json: { ok: s === 204 || (s >= 200 && s < 300) ? 'yes' : 'no' } }];
"""
    rid = "={{ $('validate query').first().json.request_id }}"
    rid_filter = {"conditions": [{"keyName": "request_id", "keyValue": rid}]}
    req_table = table(TABLE_REQUESTS, "yipden_directory_requests")

    def set_status(name, pos, status, scrub=False):
        values = {"status": status}
        if scrub:
            values.update({"contact": "", "details": "",
                           "resolved_at": "={{ new Date().toISOString() }}"})
        return node(name, "n8n-nodes-base.dataTable", 1.1, pos, {
            "operation": "update", "dataTableId": req_table, "filters": rid_filter,
            "columns": columns("yipden_directory_requests", values), "options": {}},
            alwaysOutputData=True)

    def respond(name, pos, body):
        return node(name, "n8n-nodes-base.respondToWebhook", 1.5, pos, {
            "respondWith": "text", "responseBody": body,
            "options": {"responseHeaders": {"entries": [
                {"name": "Content-Type", "value": "text/html; charset=utf-8"},
                {"name": "Cache-Control", "value": "no-store"},
                {"name": "Referrer-Policy", "value": "no-referrer"}]}}})

    def static(html_body):
        # A literal (no leading "="), so n8n never reads the page's CSS braces as an expression.
        return page(html_body)

    nodes = [
        node("Webhook", "n8n-nodes-base.webhook", 2.1, (-1500, -80),
             {"path": REVIEW_WEBHOOK_PATH, "responseMode": "responseNode", "options": {}}),
        node("Webhook Confirm", "n8n-nodes-base.webhook", 2.1, (-1500, 80),
             {"httpMethod": "POST", "path": REVIEW_CONFIRM_WEBHOOK_PATH,
              "responseMode": "responseNode", "options": {}}),
        code_node("mark link request", (-1280, -80),
                  "return [{ json: Object.assign({}, $json, { _review_request: 'link' }) }];"),
        code_node("mark confirmed request", (-1280, 80),
                  "return [{ json: Object.assign({}, $json, { _review_request: 'confirm' }) }];"),
        code_node("validate query", (-1060, 0), validate_q),
        call_signature("verify signature", (-840, 0), ctx, {
            "mode": "verify", "request_id": "={{ $json.request_id }}",
            "decision": "={{ $json.decision }}", "exp": "={{ $json.exp }}", "sig": "={{ $json.sig }}"}),
        code_node("auth verdict", (-620, 0), auth_verdict),
        switch("auth route", (-400, 0), ["valid", "expired"]),
        respond("respond expired", (-180, 160), static(
            "<h1>This link has expired.</h1><p>The request is still in the table; ask for a fresh "
            "review link from n8n.</p>")),
        respond("respond invalid", (-180, 300), static("<h1>This link is not valid.</h1>")),
        node("get request row", "n8n-nodes-base.dataTable", 1.1, (-180, -40), {
            "operation": "get", "dataTableId": req_table, "filters": rid_filter},
             alwaysOutputData=True),
        ifn("confirmed request?", (40, -40), "={{ $('validate query').first().json.confirmed }}",
            "yes", 10),
        # A GET (any decision) only shows the page, with fresh signatures for its buttons.
        call_signature("sign fresh links", (260, -260), ctx, {
            "mode": "sign", "request_id": "={{ $('validate query').first().json.request_id }}"}),
        code_node("build confirm page", (480, -260), confirm_page),
        respond("respond page", (700, -260), "={{ $json.html }}"),
        # POST from the page's buttons: claim the row (last write wins; only the run whose
        # marker survives the read-back acts), then decide.
        code_node("precheck", (260, -40), precheck),
        ifn("can act?", (480, -40), "={{ $json.ok }}", "yes", 11),
        code_node("build cannot act page", (700, 100), esc +
                  "return [{ json: { html: (" + page_fn + ")('<h1>' + esc($json.message) + '</h1>') } }];"),
        respond("respond cannot act", (920, 100), "={{ $json.html }}"),
        set_status("claim: stamp", (700, -40), "=reviewing-{{ $execution.id }}-{{ Date.now() }}"),
        node("claim: read back", "n8n-nodes-base.dataTable", 1.1, (920, -40), {
            "operation": "get", "dataTableId": req_table, "filters": rid_filter},
             alwaysOutputData=True),
        code_node("claim: won?", (1140, -40),
                  "const mine = 'reviewing-' + $execution.id + '-';\n"
                  "const status = $json && $json.status ? String($json.status) : '';\n"
                  "return [{ json: { ok: status.indexOf(mine) === 0 ? 'yes' : 'no',\n"
                  "  route: $('precheck').first().json.route } }];"),
        ifn("claimed?", (1360, -40), "={{ $json.ok }}", "yes", 12),
        respond("respond busy", (1580, 100), static(
            "<h1>Someone else is deciding this right now.</h1><p>Reload in a moment.</p>")),
        switch("decision route", (1580, -40), ["dispatch", "manual"]),
        node("dispatch opt-out", "n8n-nodes-base.httpRequest", 4.5, (1800, -260), {
            "method": "POST",
            "url": f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{OPTOUT_WORKFLOW_FILE}/dispatches",
            "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
            "sendHeaders": True,
            "headerParameters": {"parameters": [
                {"name": "Accept", "value": "application/vnd.github+json"},
                {"name": "X-GitHub-Api-Version", "value": "2022-11-28"}]},
            "sendBody": True, "specifyBody": "json", "jsonBody": dispatch_body,
            "options": {"timeout": 10000,
                        "response": {"response": {"neverError": True, "fullResponse": True,
                                                  "responseFormat": "text"}}}},
             credentials={"httpHeaderAuth": GITHUB_CREDENTIAL}, onError="continueErrorOutput"),
        code_node("dispatch verdict", (2020, -260), dispatch_verdict),
        ifn("dispatched?", (2240, -260), "={{ $json.ok }}", "yes", 13),
        set_status("mark approved", (2460, -360), "approved", scrub=True),
        respond("respond dispatched", (2680, -360), static(
            "<h1>Approved.</h1><p>yipden-app's opt-out workflow is running: it hashes every form of "
            "the person into <code>optouts.json</code>, removes the entry, and opens a pull request. "
            "Merge it, and the app drops them on its next check.</p>")),
        # Resumable: the same link can be used again.
        set_status("mark approval_failed", (2460, -160), "approval_failed"),
        respond("respond dispatch failed", (2680, -160), static(
            "<h1>GitHub did not take the request.</h1><p>Nothing changed. Check the "
            "<code>Github PAT - YipDen</code> credential, then use the same link again.</p>")),
        set_status("mark approved (manual)", (1800, -40), "approved_manual", scrub=True),
        code_node("build manual page", (2020, -40), manual_page),
        respond("respond manual", (2240, -40), "={{ $json.html }}"),
        set_status("mark rejected", (1800, 160), "rejected", scrub=True),
        respond("respond rejected", (2020, 160), static(
            "<h1>Rejected.</h1><p>Nothing changes in the directory. The request's contact address "
            "and details have been cleared.</p>")),
    ]
    connections = {
        "Webhook": link("mark link request"),
        "Webhook Confirm": link("mark confirmed request"),
        "mark link request": link("validate query"),
        "mark confirmed request": link("validate query"),
        "validate query": link("verify signature"),
        "verify signature": link("auth verdict"),
        "auth verdict": link("auth route"),
        "auth route": branches("get request row", "respond expired", "respond invalid"),
        "get request row": link("confirmed request?"),
        "confirmed request?": branches("precheck", "sign fresh links"),
        "sign fresh links": link("build confirm page"),
        "build confirm page": link("respond page"),
        "precheck": link("can act?"),
        "can act?": branches("claim: stamp", "build cannot act page"),
        "build cannot act page": link("respond cannot act"),
        "claim: stamp": link("claim: read back"),
        "claim: read back": link("claim: won?"),
        "claim: won?": link("claimed?"),
        "claimed?": branches("decision route", "respond busy"),
        "decision route": branches("dispatch opt-out", "mark approved (manual)", "mark rejected"),
        "dispatch opt-out": branches("dispatch verdict", "dispatch verdict"),
        "dispatch verdict": link("dispatched?"),
        "dispatched?": branches("mark approved", "mark approval_failed"),
        "mark approved": link("respond dispatched"),
        "mark approval_failed": link("respond dispatch failed"),
        "mark approved (manual)": link("build manual page"),
        "build manual page": link("respond manual"),
        "mark rejected": link("respond rejected"),
    }
    return {
        "name": REVIEW_NAME,
        "settings": settings(error_workflow_id=ctx.get("error_workflow_id"), no_persist=True),
        "nodes": nodes,
        "connections": connections,
    }


BUILDERS = [
    ("error", wf_error_workflow),
    ("signature", wf_signature_helper),
    ("request", wf_request),
    ("review", wf_review),
]


# --- n8n API ----------------------------------------------------------------

def api_key():
    path = os.path.expanduser("~/.n8n-api-key")
    if not os.path.exists(path):
        sys.exit(f"missing {path} -- see docs/n8n-runbook.md")
    return open(path).read().strip()


def request(method, path, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                 headers={"X-N8N-API-KEY": key,
                                          "Content-Type": "application/json",
                                          # Cloudflare fronts the host and refuses Python's agent.
                                          "User-Agent": "yipden-workflow-generator/1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:400]}")


def existing_by_name(key):
    out = {}
    for w in request("GET", "/workflows?limit=250", key).get("data", []):
        out.setdefault(w["name"], w["id"])
    return out


def missing_configuration():
    problems = []
    for name, cred in [("CRYPTO_CREDENTIAL", CRYPTO_CREDENTIAL),
                       ("RATE_LIMIT_CREDENTIAL", RATE_LIMIT_CREDENTIAL),
                       ("GOTIFY_CREDENTIAL", GOTIFY_CREDENTIAL),
                       ("GITHUB_CREDENTIAL", GITHUB_CREDENTIAL)] + (
            [("TURNSTILE_CREDENTIAL", TURNSTILE_CREDENTIAL)] if TURNSTILE_ENABLED else []):
        if not cred["id"]:
            problems.append(f"{name} ({cred['name']!r}) has no id")
    if not TABLE_REQUESTS:
        problems.append("TABLE_REQUESTS is empty (run --create-tables)")
    if not TABLE_RATE_LIMITS:
        problems.append("TABLE_RATE_LIMITS is empty (run --create-tables)")
    return problems


def push(payload, key, existing):
    for n in payload["nodes"]:
        if n["type"] == "n8n-nodes-base.executeWorkflow":
            if not (n["parameters"].get("workflowId") or {}).get("value"):
                sys.exit(f"{payload['name']}: node {n['name']!r} has no workflowId yet")
    body = {k: payload[k] for k in ("name", "nodes", "connections", "settings")}
    wid = existing.get(payload["name"])
    if wid:
        saved = request("PUT", f"/workflows/{wid}", key, body)
        action = "updated"
    else:
        saved = request("POST", "/workflows", key, body)
        wid, action = saved["id"], "created"
    # A helper (only an Execute Workflow trigger) is published at once: it exposes nothing. A
    # workflow with a public webhook is never activated here; turning it on stays deliberate.
    triggers = {n["type"] for n in payload["nodes"]
                if n["type"].endswith("Trigger") or n["type"] == "n8n-nodes-base.webhook"}
    if triggers == {"n8n-nodes-base.executeWorkflowTrigger"} and not saved.get("active"):
        request("POST", f"/workflows/{wid}/activate", key)
        action += "+published"
    return wid, action


def create_tables():
    key = api_key()
    live = {t["name"]: t["id"] for t in request("GET", "/data-tables", key).get("data", [])}
    for name, spec in DATA_TABLES_SCHEMA.items():
        if name in live:
            print(f"  exists   {live[name]}  {name}")
            continue
        new_id = request("POST", "/data-tables", key, {"name": name, "columns": spec["columns"]})["id"]
        const = "TABLE_REQUESTS" if name == "yipden_directory_requests" else "TABLE_RATE_LIMITS"
        print(f"  created  {new_id}  {name}")
        print(f'           paste into build_workflows.py: {const} = "{new_id}"')


def emit(ctx):
    out = ROOT / "n8n" / "workflows"
    out.mkdir(parents=True, exist_ok=True)
    for slug, build in BUILDERS:
        payload = build(ctx)
        (out / f"{slug}.json").write_text(json.dumps(payload, indent=2) + "\n")
        print(f"  wrote n8n/workflows/{slug}.json  ({payload['name']})")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--list", action="store_true", help="the workflows this builds")
    parser.add_argument("--emit", action="store_true", help="write JSON, no API key needed")
    parser.add_argument("--create-tables", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--only", choices=[slug for slug, _ in BUILDERS])
    args = parser.parse_args()

    if args.list:
        for slug, build in BUILDERS:
            print(f"  {slug:10} {build({})['name']}")
        return
    if args.emit:
        emit({})
        return
    if args.create_tables:
        create_tables()
        return
    if args.push:
        problems = missing_configuration()
        if problems:
            sys.exit("not pushing:\n  " + "\n  ".join(problems))
        key = api_key()
        existing = existing_by_name(key)
        ctx = {}
        # Order matters: the error workflow and the signature helper must exist (and the helper
        # be published) before the workflows that call them are pushed.
        for slug, build in BUILDERS:
            if args.only and slug != args.only and slug not in ("error", "signature"):
                continue
            if slug == "signature":
                ctx["signature_callers"] = [i for i in (existing.get(REQUEST_NAME),
                                                        existing.get(REVIEW_NAME)) if i]
            wid, action = push(build(ctx), key, existing)
            existing[build(ctx)["name"]] = wid
            if slug == "error":
                ctx["error_workflow_id"] = wid
            if slug == "signature":
                ctx["signature_id"] = wid
            print(f"  {action:18} {wid}  {build(ctx)['name']}")
        # The helper's caller allowlist names the callers, which only now all have ids.
        ctx["signature_callers"] = [i for i in (existing.get(REQUEST_NAME),
                                                existing.get(REVIEW_NAME)) if i]
        push(wf_signature_helper(ctx), key, existing)
        print("  signature helper's caller list updated")
        return
    parser.print_help()


if __name__ == "__main__":
    main()
