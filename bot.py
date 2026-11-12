"""
<summary>
Kalshi Sports Bot: punto de entrada. Cada corrida publica noticias, previews y finales
(ESPN) y, cada BOARD_EVERY_MIN minutos, actualiza el Live Board de Kalshi y las alertas.
</summary>
<remarks>
Archivo: bot.py
Autor: Yael Rosado
Propósito: Correr en GitHub Actions en loop cada ~2 min. Guarda su memoria en state.json.
Variables de entorno:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  -> Telegram (opcional)
  DISCORD_WEBHOOK_URL                   -> Discord (opcional)
  DRY_RUN=1                             -> imprime en consola, no postea
</remarks>
"""

import datetime as dt
import json
import os

import espn
import kalshi_board

STATE_FILE = os.getenv("STATE_FILE", "state.json")
BOARD_EVERY_MIN = int(os.getenv("BOARD_EVERY_MIN", "30"))


def load_state():
    """
    <summary>Lee state.json (o un estado vacío).</summary>
    <returns>Diccionario de estado.</returns>
    """
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    """
    <summary>Guarda state.json.</summary>
    <param name="state">Diccionario de estado.</param>
    """
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, separators=(",", ":"), ensure_ascii=False)


def main():
    """
    <summary>Una corrida completa del bot.</summary>
    """
    now = dt.datetime.now(dt.timezone.utc)
    state = load_state()

    last = state.get("board_at")
    if not last or (now - dt.datetime.fromisoformat(last)).total_seconds() >= (BOARD_EVERY_MIN - 1) * 60:
        try:
            kalshi_board.run(state, now)
            state["board_at"] = now.isoformat()
        except Exception as e:  # si Kalshi falla, ESPN sigue
            print("Kalshi error:", repr(e))

    espn.run(state, now)
    save_state(state)


if __name__ == "__main__":
    main()
