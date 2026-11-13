// MarketScan – Schnell-Antworten für Telegram (Cloudflare Worker, kostenlos)
//
// Telegram schickt jede Nachricht an deinen Bot sofort hierher. Der Worker
//  1) prüft, dass sie wirklich von Telegram und aus DEINEM Chat kommt,
//  2) antwortet sofort "⏳ Moment …",
//  3) startet bei GitHub den Lauf, der die eigentliche Antwort schickt (~1 Minute).
//
// Einstellungen (Cloudflare → Worker → Settings → Variables and Secrets):
//   TELEGRAM_BOT_TOKEN  – Token deines Bots
//   CHAT_ID             – deine Chat-ID (steht in .env als TELEGRAM_CHAT_ID)
//   GITHUB_TOKEN        – GitHub-Zugriffsschlüssel, nur für das Projekt marketscan, Recht "Contents: Read and write"
//   WEBHOOK_SECRET      – ein selbst ausgedachtes Passwort (nur Buchstaben/Zahlen)
//   GITHUB_REPO         – MaaaTTrss/marketscan

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("MarketScan-Worker läuft ✓");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET)
      return new Response("nein", { status: 403 });
    const update = await request.json();
    const msg = update.message || {};
    if (String(msg.chat?.id) !== String(env.CHAT_ID) || !(msg.text || "").startsWith("/"))
      return new Response("ok");   // fremde Chats / normale Texte ignorieren

    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendChatAction`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: env.CHAT_ID, action: "typing" }),
    });
    const gh = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`, "Accept": "application/vnd.github+json",
        "User-Agent": "marketscan-worker", "Content-Type": "application/json",
      },
      body: JSON.stringify({ event_type: "telegram", client_payload: { update } }),
    });
    if (!gh.ok) {
      await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: env.CHAT_ID, text: `⚠️ GitHub antwortet nicht (${gh.status}). Prüf den GITHUB_TOKEN im Worker.` }),
      });
    }
    return new Response("ok");
  },
};
