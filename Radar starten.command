#!/bin/zsh
# Doppelklick startet den Markt-Radar und öffnet ihn im Browser.
# Zum Beenden dieses Terminal-Fenster schließen.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Erster Start: installiere Pakete ..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
fi
.venv/bin/python app.py
