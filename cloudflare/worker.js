// MarketScan – Schnell-Antworten für Telegram (Cloudflare Worker, kostenlos)
//
// Telegram schickt jede Nachricht an deinen Bot sofort hierher. Der Worker prüft das
// geheime Passwort (kommt nur von Telegram) und startet bei GitHub den Lauf, der die
// Antwort schickt (~1 Minute). Ob die Nachricht aus DEINEM Chat kommt, prüft der GitHub-Lauf.
//
// Nötige Einstellungen (Worker → Settings → Variables and Secrets, Typ "Secret"):
//   GITHUB_TOKEN    – GitHub-Zugriffsschlüssel (nur Projekt marketscan, "Contents: Read and write")
//   WEBHOOK_SECRET  – Passwort, das dir das Skript "Telegram-Schnellantwort aktivieren" anzeigt

const REPO = "MaaaTTrss/marketscan";

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("MarketScan-Worker läuft ✓");
    if (!env.WEBHOOK_SECRET || request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET)
      return new Response("nein", { status: 403 });
    const update = await request.json();
    const text = update.message?.text || "";
    if (!text.startsWith("/")) return new Response("ok");   // nur Befehle

    const gh = await fetch(`https://api.github.com/repos/${REPO}/dispatches`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`, "Accept": "application/vnd.github+json",
        "User-Agent": "marketscan-worker", "Content-Type": "application/json",
      },
      body: JSON.stringify({ event_type: "telegram", client_payload: { update } }),
    });
    // Status zurückgeben, damit Probleme in Telegrams "getWebhookInfo" sichtbar werden
    return new Response(gh.ok ? "ok" : `github ${gh.status}`, { status: gh.ok ? 200 : 502 });
  },
};
