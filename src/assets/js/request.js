// The "Is this you?" form: posts JSON to the n8n webhook and reads JSON back. Every check that
// matters (Turnstile, honeypot, timing, rate limit, moderation) happens in n8n; this only sends.
const form = document.getElementById("request");
const status = document.getElementById("status");
const renderedAt = Date.now();

// Arrived from the app's "Is this you?" (`?listing=<entry id>`): fill in the listing from the
// directory, and offer joining IndieNodes when it has a website (members need one).
const listingId = new URLSearchParams(location.search).get("listing");
if (form && listingId && /^[a-z0-9-]{1,81}$/.test(listingId)) {
  form.elements.namedItem("listing_id").value = listingId;
  fetch("/directory/people.json")
    .then((response) => (response.ok ? response.json() : null))
    .then((directory) => {
      const entry = directory?.entries?.find((item) => item.id === listingId);
      if (!entry) return;
      const field = form.elements.namedItem("listing");
      if (!field.value)
        field.value = entry.site_url || entry.feeds?.[0]?.url || entry.name;
      if (entry.site_url) document.getElementById("join").hidden = false;
    })
    .catch(() => {});
}

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
    listing_id: String(data.get("listing_id") || ""),
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
      ? body.action === "confirm"
        ? "Thanks for confirming. Nothing else is needed from you."
        : "Thanks. Your request is with us, and nothing else is needed from you."
      : reply.message ||
        "That didn't go through. Please try again, or email us.";
    if (response.ok) form.reset();
  } catch {
    status.textContent =
      "That didn't go through. Please try again, or email us.";
  }
});
