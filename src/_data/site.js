// Build-time settings. Every value here is baked into the static output when the image is built:
// there is no server process to read an environment variable at request time (see README).

export default {
  name: "YipDen",
  line: "Follow people, not platforms.",
  url: process.env.SITE_ORIGIN || "https://yipden.com",
  // Where the "Is this you?" form posts: an n8n webhook. Public by nature (it is in the page), so
  // never a secret. Unset means the form says requests are closed, rather than dropping them.
  removalWebhook: process.env.REMOVAL_WEBHOOK_URL || "",
  // Cloudflare Turnstile's public site key. The secret key lives in n8n, never here.
  turnstileSiteKey: process.env.TURNSTILE_SITE_KEY || "",
  // Shown when the form is closed. Set CONTACT_EMAIL to an empty string to show no address.
  contactEmail: process.env.CONTACT_EMAIL ?? "jamaal@j2it.us",
  repo: "https://github.com/XTREEMMAK/yipden-app",
};
