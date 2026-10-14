// The "Is this you?" form: posts JSON to the n8n webhook and reads JSON back. Every check that
// matters (Turnstile, honeypot, timing, rate limit, moderation) happens in n8n; this only sends.
const form = document.getElementById("request");
const status = document.getElementById("status");
const renderedAt = Date.now();

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(form);
  const listing = String(data.get("listing") || "").trim();
  if (!listing) {
    status.textContent = "Add the site, profile or handle you're listed under.";
    return;
  }
  const body = {
    action: data.get("action"),
    listing,
    details: String(data.get("details") || "").slice(0, 2000),
    contact: String(data.get("contact") || ""),
    honeypot: String(data.get("website") || ""),
    elapsed_ms: Date.now() - renderedAt,
    turnstile: String(data.get("cf-turnstile-response") || ""),
  };
  status.textContent = "Sending…";
  try {
    const response = await fetch(form.dataset.webhook, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const reply = await response.json().catch(() => ({}));
    status.textContent = response.ok
      ? "Thanks. Your request is with us, and nothing else is needed from you."
      : reply.message ||
        "That didn't go through. Please try again, or email us.";
    if (response.ok) form.reset();
  } catch {
    status.textContent =
      "That didn't go through. Please try again, or email us.";
  }
});
