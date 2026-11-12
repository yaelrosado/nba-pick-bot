"""
<summary>
Preguntas libres con IA (API de Anthropic / Claude): arma un contexto con data en vivo de ESPN y Kalshi
según lo que pregunten (equipos, jugadores, juegos de hoy, picks) y Claude contesta en español.
</summary>
<remarks>
Archivo: ai.py
Autor: Yael Rosado
Propósito: Comando /pregunta (o mencionar/responder al bot). Requiere el secret ANTHROPIC_API_KEY.
ANTHROPIC_MODEL cambia el modelo (por defecto Claude Haiku, el más barato).
</remarks>
"""

import datetime as dt
import json
import os

import requests

import cards
import espn
import kalshi_board
import players

API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
MODELS = [m for m in (os.getenv("ANTHROPIC_MODEL", "").strip(), "claude-haiku-5-5", "claude-haiku-4-5") if m]
MAX_TOKENS = 700
MAX_PER_USER_DAY = int(os.getenv("AI_MAX_PER_USER_DAY", "25"))  # tope por persona para controlar el gasto

SYSTEM = (
    "Eres el analista de NBA del grupo de Telegram de apuestas en Kalshi. Contestas en español de Puerto Rico, "
    "relajado pero claro y directo. Usa SOLO los datos del CONTEXTO (ESPN y Kalshi en tiempo real, hora de Puerto Rico). "
    "Si algo no está en el contexto, dilo; nunca inventes stats, lesiones ni precios. "
    "Los precios de Kalshi en ¢ equivalen a probabilidad implícita en % (68¢ = 68%). "
    "Responde en máximo ~170 palabras salvo que pidan más detalle. Formato: texto plano para Telegram; puedes usar "
    "**negritas** y viñetas con '•'. No uses tablas, encabezados ni enlaces. "
    "Si preguntan qué apostar o quién gana: da tu lectura con números (precio en Kalshi, forma de los últimos 5, "
    "stats de temporada, jugadores clave, lesiones, matchups anteriores), menciona el riesgo, nunca digas que algo es "
    "seguro y termina con: 'No es consejo financiero.'"
)

_usage = {}


def kalshi_markets():
    """
    <summary>Mercados NBA abiertos en Kalshi (cache 2 min).</summary>
    <returns>Lista de mercados normalizados.</returns>
    """
    return players.cached("kx_markets", 120, lambda: kalshi_board.sports_markets(
        kalshi_board.fetch_events(), dt.datetime.now(dt.timezone.utc)))


def allowed(user_id):
    """
    <summary>Controla el tope diario de preguntas por persona.</summary>
    <param name="user_id">Id de Telegram.</param>
    <returns>True si todavía puede preguntar hoy.</returns>
    """
    today = dt.datetime.now(espn.TZ).date().isoformat()
    day, n = _usage.get(user_id, (today, 0))
    if day != today:
        n = 0
    if n >= MAX_PER_USER_DAY:
        return False
    _usage[user_id] = (today, n + 1)
    return True


def team_block(team, state):
    """
    <summary>Resumen compacto de un equipo para el contexto.</summary>
    <param name="team">Equipo de ESPN.</param>
    <param name="state">Estado (Kalshi).</param>
    <returns>Dict.</returns>
    """
    g, summ = players.next_game(team)
    if not g:
        return {"equipo": team.get("displayName"), "nota": "sin próximo juego"}
    me = g["home"] if g["home"]["id"] == str(team.get("id")) else g["away"]
    opp = g["away"] if me is g["home"] else g["home"]
    d5 = players.last5_detail(summ, me)
    out, doubt = cards.team_injuries(summ, me)
    return {
        "equipo": me["name"], "record": me["record"], "racha": espn.team_streak(summ, me["id"]),
        "ultimos_5": {"record": d5["record"], "prom_anotados": round(d5["avg_for"], 1),
                      "prom_permitidos": round(d5["avg_against"], 1),
                      "juegos": [f"{r['date']} {r['atvs']} {r['opp']} {r['res']} {r['for']}-{r['against']}"
                                 + (f" (mejor: {r['top']['name']} {r['top']['pts']} pts, {r['top']['reb']} reb, {r['top']['ast']} ast)" if r.get("top") else "")
                                 for r in d5["rows"]]},
        "stats_temporada": {k: v for k, v in cards.team_stats(summ, me["id"]).items() if k},
        "jugadores_clave": [{"nombre": p["name"], "pos": p["pos"],
                             "promedios": {k: p["avg"].get(k) for k in ("PTS", "REB", "AST", "FG%", "3P%", "MIN") if p["avg"].get(k)}}
                            for p in players.key_players(summ, me, k=3)],
        "lideres_por_juego": {key: [f"{x['name']} {x['value']}" for x in rows]
                              for key, rows in players.team_leaders(me).items()},
        "lesiones": {"fuera": out, "en_duda": doubt},
        "proximo_juego": {"rival": opp["name"], "rival_record": opp["record"], "local": me is g["home"],
                          "fecha_hora_PR": f"{espn.short_date(g['start'])} {espn.pr_time(g['start'])}" if g.get("start") else "",
                          "estado": g.get("detail"), "kalshi": espn.kalshi_line(state, g), "linea_espn": espn.odds_line(summ, g),
                          "matchups_anteriores": [x["line"] for x in espn.head_to_head(players.SPORT, players.LG, g["home"], g["away"])][:4]},
    }


def player_block(p):
    """
    <summary>Resumen compacto de un jugador para el contexto.</summary>
    <param name="p">Jugador (roster_index).</param>
    <returns>Dict.</returns>
    """
    return {"jugador": p["name"], "equipo": (p.get("team") or {}).get("displayName"), "pos": p["pos"],
            "lesion": p.get("injury") or "activo", "promedios_temporada": players.player_overview(p["id"]),
            "ultimos_5": players.player_last5(p)}


def build_context(question, state):
    """
    <summary>Contexto en JSON con la data relevante a la pregunta.</summary>
    <param name="question">Pregunta.</param>
    <param name="state">Estado (Kalshi).</param>
    <returns>Texto JSON.</returns>
    """
    local = dt.datetime.now(espn.TZ)
    ctx = {"fecha_hora_PR": local.strftime("%Y-%m-%d %I:%M %p"), "juegos_hoy": []}
    events = espn.get(f"/{players.SPORT}/{players.LG}/scoreboard", {"dates": local.strftime("%Y%m%d")}).get("events") or []
    for g in sorted((x for x in map(espn.game_info, events) if x and x["start"]), key=lambda x: x["start"]):
        a, h = g["away"], g["home"]
        ctx["juegos_hoy"].append({"partido": f"{a['name']} ({a['record']}) @ {h['name']} ({h['record']})",
                                  "hora_PR": espn.pr_time(g["start"]), "estado": g["detail"],
                                  "score": f"{a['abbr']} {a['score']} - {h['abbr']} {h['score']}" if g["state"] != "pre" else "",
                                  "kalshi": espn.kalshi_line(state, g)})
    teams, pls = players.teams_in_text(question), players.players_in_text(question)
    ctx["equipos"] = []
    for t in teams:
        try:
            ctx["equipos"].append(team_block(t, state))
        except Exception as e:
            print("AI equipo error:", repr(e))
    ctx["jugadores"] = []
    for p in pls:
        try:
            ctx["jugadores"].append(player_block(p))
        except Exception as e:
            print("AI jugador error:", repr(e))
    try:
        markets = kalshi_markets()
        ctx["kalshi_picks_alta_probabilidad"] = [f"{x['name']}: {x['side']} @ {x['cost']}¢ (cierra en {kalshi_board.fmt_when(x['hours'])})"
                                                 for x in kalshi_board.high_prob(markets)]
        toks = set()
        for t in teams:
            toks |= {w.lower() for w in (t.get("name"), t.get("location")) if w}
        for p in pls:
            toks.add(p["last"].lower())
        if toks:
            rel = sorted((m for m in markets if any(w in m["name"].lower() for w in toks)), key=lambda m: -m["vol24"])[:15]
            ctx["kalshi_mercados_relacionados"] = [f"{m['name']}: YES {round(m['mid'])}¢" for m in rel if m["mid"] is not None]
    except Exception as e:
        print("AI Kalshi error:", repr(e))
    return json.dumps(ctx, ensure_ascii=False)[:60000]


def ask(question, state):
    """
    <summary>Hace la pregunta a Claude con el contexto en vivo.</summary>
    <param name="question">Pregunta del usuario.</param>
    <param name="state">Estado (Kalshi).</param>
    <returns>Texto de respuesta (con **negritas**).</returns>
    """
    if not API_KEY:
        return "La IA no está prendida todavía: falta el secret ANTHROPIC_API_KEY en GitHub."
    ctx = build_context(question, state)
    body = {"max_tokens": MAX_TOKENS, "system": SYSTEM,
            "messages": [{"role": "user", "content": f"CONTEXTO (JSON):\n{ctx}\n\nPREGUNTA: {question}"}]}
    headers = {"x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    for model in MODELS:
        r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json={**body, "model": model}, timeout=90)
        if r.status_code == 404 or (r.status_code == 400 and "model" in r.text.lower()):
            print("Modelo no disponible:", model)
            continue
        if not r.ok:
            print("Anthropic error:", r.status_code, r.text[:300])
            return "No pude contestar ahora mismo 😅 Intenta en un ratito."
        data = r.json()
        u = data.get("usage") or {}
        print(f"IA {model}: {u.get('input_tokens')} in / {u.get('output_tokens')} out tokens")
        return "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text").strip()
    return "No encontré un modelo disponible; revisa ANTHROPIC_MODEL."
