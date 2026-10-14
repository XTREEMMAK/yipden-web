# yipden-web

The website at **yipden.com**: what YipDen is, where to get it, its privacy policy, and the
creator directory's **"Is this you?"** page. The app itself lives in
[yipden-app](https://github.com/XTREEMMAK/yipden-app).

Built with [11ty](https://www.11ty.dev/) and served by Caddy in Docker, the same way as
indienodes-web. It is a small information site: plain HTML, one stylesheet, and one small script
for the request form. The design is placeholder for now and gets its own pass.

## Run it

```bash
npm install
npm run dev      # http://localhost:8080, rebuilds on save
npm run build    # writes _site/
docker compose up --build
```

## Build-time settings

The output is static: there is no server process to read an environment variable when a page is
requested, so every setting is baked in when the site (or the image) is built. Changing one means
rebuilding. See [.env.example](.env.example).

| Variable              | What it does                                                              |
| --------------------- | ------------------------------------------------------------------------- |
| `SITE_ORIGIN`         | The deployed origin. Default `https://yipden.com`.                        |
| `REMOVAL_WEBHOOK_URL` | Where "Is this you?" posts (n8n). Public. Unset: the form says closed.    |
| `TURNSTILE_SITE_KEY`  | Cloudflare Turnstile's public key. The secret key lives in n8n.           |
| `CONTACT_EMAIL`       | Shown while the form is closed. Default `jamaal@j2it.us`; empty hides it. |

The page's Content-Security-Policy (in the [Caddyfile](Caddyfile)) allows posting to
`n8n.kjnet.us` and Turnstile from Cloudflare, and nothing else off the site.

## Pages

| Path            | What it is                                                                                                          |
| --------------- | ------------------------------------------------------------------------------------------------------------------- |
| `/`             | What YipDen is. Placeholder copy; download links once the app is listed.                                            |
| `/privacy/`     | The privacy policy. **A draft**: written and reviewed before the store links here.                                  |
| `/is-this-you/` | Remove, correct or confirm a directory listing. Contract: [docs/directory-requests.md](docs/directory-requests.md). |

## The creator directory

yipden.com also serves YipDen's creator directory (`/directory/people.json`, `optouts.json` and
their schemas), proxied live from yipden-app's `main` branch. See
[docs/directory-requests.md](docs/directory-requests.md).

## Still to do

- The design.
- The privacy policy, reviewed, before the Play listing points at it.
- The n8n workflow and its runbook (the contract is written).
- Download links once YipDen is on the Play Store.
- `/.well-known/assetlinks.json`, if YipDen (or a separate Realm app) opens yipden.com links.
