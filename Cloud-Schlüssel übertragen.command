#!/bin/zsh
# Überträgt die geheimen Schlüssel aus .env als GitHub-Secrets an den Cloud-Radar.
# Doppelklick genügt. Die Werte werden nicht angezeigt.
cd "$(dirname "$0")"
GH="$HOME/.local/bin/gh"
REPO="MaaaTTrss/marketscan"

setsecret () {   # $1 = Name in .env, $2 = Name auf GitHub
  local v
  v=$(grep "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-)
  if [ -n "$v" ]; then
    printf %s "$v" | "$GH" secret set "$2" -R "$REPO" && echo "✓ $2 übertragen"
  else
    echo "– $1 noch nicht vorhanden (übersprungen)"
  fi
}

setsecret NTFY_TOPIC NTFY_TOPIC
setsecret PRIVATE_KEY RADAR_PRIVATE_KEY
setsecret TELEGRAM_BOT_TOKEN TELEGRAM_BOT_TOKEN
setsecret TELEGRAM_CHAT_ID TELEGRAM_CHAT_ID
echo
echo "Fertig. Fenster kann geschlossen werden."
