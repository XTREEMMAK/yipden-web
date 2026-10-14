# "Is this you?" requests: the webhook contract

The form at `/is-this-you/` posts JSON to `REMOVAL_WEBHOOK_URL` (an n8n workflow) and reads JSON
back. The site does nothing else: every check lives in n8n, because a static site has nowhere to
keep a secret. Modelled on indienodes-app's forms.

## Request

```json
{
  "action": "remove" | "correct" | "confirm",
  "listing": "the site, profile or handle the person is listed under",
  "listing_id": "the people.json entry id, when they came from the app's link; may be empty",
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
5. On approval of a removal: dispatch yipden-app's `directory-optout` workflow with the entry id
   (`listing_id`, or the one the maintainer finds for `listing`). It runs `pnpm directory:optout
--id <id>`, which hashes every form of the person into `directory/optouts.json`, removes the
   entry from `directory/people.json`, and opens a pull request. Only the id reaches GitHub.
6. A **confirm** ("Yes, that's me"), once the maintainer has checked it is really them, becomes
   `"listing": "confirmed"` on the entry, by hand for now. A **correction** is handled by hand.
7. Keep nothing longer than handling needs: the request is not stored in either repository.

## The directory files

`/directory/people.json`, `/directory/optouts.json` and their two schemas are served from
yipden-app's `main` branch through Caddy (see the [Caddyfile](../Caddyfile)), so a change there is
live within five minutes with no rebuild here. Only those four paths: the test seed is never
served. They carry one `Access-Control-Allow-Origin: *`, which the app's web build needs.
