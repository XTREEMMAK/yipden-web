# "Is this you?" requests: the webhook contract

The form at `/is-this-you/` posts JSON to `REMOVAL_WEBHOOK_URL` (an n8n workflow) and reads JSON
back. The site does nothing else: every check lives in n8n, because a static site has nowhere to
keep a secret. Modelled on indienodes-app's forms.

## Request

```json
{
  "action": "remove" | "correct",
  "listing": "the site, profile or handle the person is listed under",
  "details": "free text, at most 2000 characters, may be empty",
  "contact": "an email for a reply, may be empty",
  "honeypot": "must be empty; a bot fills it",
  "elapsed_ms": 4210,
  "turnstile": "Cloudflare Turnstile token, empty when Turnstile is off"
}
```

## Response

`200` with `{ "ok": true }` when the request is queued. Any other status may carry
`{ "message": "…" }`, which the page shows as is.

## What the workflow does (to build, in this repo)

1. Drop silently (still `200`): a filled honeypot, or `elapsed_ms` under a few seconds.
2. Verify the Turnstile token with the secret key (`TURNSTILE_SECRET_KEY`, held in n8n only).
3. Rate limit per IP.
4. **Moderated, never automatic** (decided 2026-10-14): notify the maintainer with the request.
5. On approval of a removal: run yipden-app's `directory-optout` workflow (`pnpm directory:optout
<listing>`), which adds the hashes to `directory/optouts.json`, removes the entry from
   `directory/people.json`, and opens a pull request. A correction is handled by hand.
6. Keep nothing longer than handling needs: the request is not stored in either repository.
