"""
<summary>
Comandos de Telegram: escucha mensajes (long polling) y contesta /hoy, /stats, /juego,
/kalshi, /picks y /ayuda con data de ESPN y Kalshi.
</summary>
<remarks>
Archivo: commands.py
Autor: Yael Rosado
Propósito: Corre en paralelo al bot durante cada turno de GitHub Actions.
Prueba local sin Telegram: DRY_RUN=1 python commands.py --test "/stats lakers"
</remarks>
"""

import datetime as dt
import json
import os
import sys
import time

import espn
import kalshi_board
import notify

SHIFT_MINUTES = int(os.getenv("SHIFT_MINUTES", "55"))
STATE_FILE = os.getenv("STATE_FILE", "state.json")
MAX_AGE_MIN = 10  # ignora comandos más viejos que esto (los que llegaron entre turnos)
SPORT, LG, LABEL, EMOJI = "basketball", "nba", "NBA", "🏀"

ALIASES = {"sixers": "PHI", "76ers": "PHI", "blazers": "POR", "wolves": "MIN", "cavs": "CLE", "mavs": "DAL",
           "dubs": "GS", "warriors": "GS", "clips": "LAC", "lakers": "LAL", "knicks": "NY", "nets": "BKN",
           "spurs": "SA", "pels": "NO", "suns": "PHX", "thunder": "OKC", "okc": "OKC", "nuggets": "DEN",
           "jazz": "UTAH", "wizards": "WSH", "celtics": "BOS", "heat": "MIA", "bucks": "MIL", "bulls": "CHI"}

HELP = (f"{EMOJI} <b>COMANDOS DEL BOT</b>\n\n"
        "/hoy — juegos de hoy, hora en PR y precio en Kalshi\n"
        "/stats <i>equipo</i> — stats de temporada, forma, líderes y lesiones\n"
        "/juego <i>equipo</i> — preview del próximo juego con matchups anteriores\n"
        "/kalshi <i>equipo</i> — precios de Kalshi de ese equipo\n"
        "/picks — los de alta probabilidad ahora mismo\n\n"
        "<i>Ej: /stats lakers · /juego celtics · /kalshi knicks</i>")

_cache = {}


# ---------------------------------------------------------------- Helpers ----
def load_state():
    """
    <summary>Lee state.json (solo lectura; lo escribe bot.py).</summary>
    <returns>Diccionario de estado.</returns>
    """
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def cached(key, seconds, fn):
    """
    <summary>Cache sencillo en memoria para no repetir llamadas pesadas.</summary>
    <param name="key">Llave del cache.</param>
    <param name="seconds">Vida del valor.</param>
    <param name="fn">Función que produce el valor.</param>
    <returns>El valor (del cache o nuevo).</returns>
    """
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def nba_teams():
    """
    <summary>Lista de equipos de la NBA (cache 6h).</summary>
    <returns>Lista de dicts de equipo de ESPN.</returns>
    """
    def load():
        data = espn.get(f"/{SPORT}/{LG}/teams")
        lgs = ((data.get("sports") or [{}])[0].get("leagues") or [{}])[0]
        return [t.get("team") or {} for t in lgs.get("teams") or []]
    return cached("teams", 6 * 3600, load)


def find_team(query):
    """
    <summary>Busca un equipo por nombre, ciudad, abreviatura o apodo.</summary>
    <param name="query">Texto del usuario, ej. "lakers", "LAL", "los angeles lakers".</param>
    <returns>Dict del equipo o None.</returns>
    """
    q = query.strip().lower()
    if not q:
        return None
    teams = nba_teams()
    ab = ALIASES.get(q, q.upper())
    for t in teams:
        if (t.get("abbreviation") or "").upper() == ab:
            return t
    for t in teams:
        names = [t.get(k) or "" for k in ("name", "shortDisplayName", "displayName", "location", "nickname")]
        if any(q == n.lower() for n in names):
            return t
    for t in teams:
        if q in (t.get("displayName") or "").lower():
            return t
    return None


def next_game(team):
    """
    <summary>Busca el próximo juego (o el de hoy) del equipo y su summary.</summary>
    <param name="team">Dict del equipo de ESPN.</param>
    <returns>(juego normalizado, summary) o (None, None).</returns>
    """
    tid = str(team.get("id"))
    info = espn.get(f"/{SPORT}/{LG}/teams/{tid}").get("team") or {}
    ev_id = str(((info.get("nextEvent") or [{}])[0]).get("id") or "")
    if not ev_id:
        now = dt.datetime.now(dt.timezone.utc)
        for ev in espn.get(f"/{SPORT}/{LG}/teams/{tid}/schedule").get("events") or []:
            comp = (ev.get("competitions") or [{}])[0]
            t = espn.parse_time(ev.get("date"))
            if t and not ((comp.get("status") or {}).get("type") or {}).get("completed") and t > now - dt.timedelta(hours=4):
                ev_id = str(ev.get("id"))
                break
    if not ev_id:
        return None, None
    summ = espn.get(f"/{SPORT}/{LG}/summary", {"event": ev_id})
    comp = ((summ.get("header") or {}).get("competitions") or [{}])[0]
    g = espn.game_info({"id": ev_id, "date": comp.get("date"), "competitions": [comp]})
    return g, summ


def team_lines(team_abbr, lines):
    """
    <summary>Filtra líneas "ABR · ..." de un equipo.</summary>
    <param name="team_abbr">Abreviatura.</param>
    <param name="lines">Líneas de leaders_lines/injury_lines.</param>
    <returns>Líneas sin el prefijo.</returns>
    """
    pre = f"{team_abbr} · "
    return [l[len(pre):] for l in lines if l.startswith(pre)]


# --------------------------------------------------------------- Comandos ----
def cmd_hoy(_arg):
    """
    <summary>Juegos de hoy con hora en PR, score si ya empezaron y precio en Kalshi.</summary>
    <param name="_arg">No se usa.</param>
    <returns>HTML.</returns>
    """
    local = dt.datetime.now(espn.TZ)
    events = espn.get(f"/{SPORT}/{LG}/scoreboard", {"dates": local.strftime("%Y%m%d")}).get("events") or []
    games = sorted((g for g in map(espn.game_info, events) if g and g["start"]), key=lambda g: g["start"])
    if not games:
        return f"{EMOJI} No hay juegos de NBA hoy."
    state = load_state()
    out = [f"{EMOJI} <b>JUEGOS DE HOY · {local.day} {espn.MESES[local.month - 1]}</b>\n"]
    for g in games:
        a, h = g["away"], g["home"]
        if g["state"] == "pre":
            head = f"🕖 {espn.pr_time(g['start'])} · {a['abbr']} ({a['record']}) @ {h['abbr']} ({h['record']})"
        elif g["completed"]:
            head = f"✅ Final · {a['abbr']} {a['score']} @ {h['abbr']} {h['score']}"
        else:
            head = f"🔴 {g['detail']} · {a['abbr']} {a['score']} @ {h['abbr']} {h['score']}"
        kx = espn.kalshi_line(state, g) if not g["completed"] else ""
        out.append(f"<b>{espn.esc(head)}</b>" + (f"\n      💹 {espn.esc(kx)}" if kx else ""))
    return "\n".join(out)


def cmd_stats(arg):
    """
    <summary>Stats de un equipo: récord, racha, últimos 5, stats de temporada, líderes, lesiones y próximo juego.</summary>
    <param name="arg">Nombre del equipo.</param>
    <returns>HTML.</returns>
    """
    team = find_team(arg)
    if not team:
        return "No encontré ese equipo. Ej: <code>/stats lakers</code>"
    g, summ = next_game(team)
    if not g:
        return f"No encontré el próximo juego de {espn.esc(team.get('displayName'))}."
    me, opp = (g["home"], g["away"]) if g["home"]["id"] == str(team.get("id")) else (g["away"], g["home"])
    form, l5 = espn.last_five(summ, me["id"])
    streak = espn.team_streak(summ, me["id"])
    stats = {}
    for t in (summ.get("boxscore") or {}).get("teams") or []:
        if str((t.get("team") or {}).get("id")) == me["id"]:
            stats = [(s.get("abbreviation") or s.get("label") or s.get("name"), s.get("displayValue"))
                     for s in t.get("statistics") or [] if s.get("name") != "streak"][:10]
    out = [f"{EMOJI} <b>{espn.esc(me['name'])}</b> ({espn.esc(me['record'])})"]
    out.append(f"🔥 Últimos 5 <code>{espn.esc(form or '—')}</code>" + (f" · racha <b>{espn.esc(streak)}</b>" if streak else ""))
    out += [f"   {espn.esc(x)}" for x in l5]
    if stats:
        w = max(len(str(k)) for k, _ in stats)
        out.append("\n📊 <b>STATS DE TEMPORADA</b>")
        out.append("<pre>" + espn.esc("\n".join(f"{str(k):{w}}  {v:>7}" for k, v in stats)) + "</pre>")
    leaders = team_lines(me["abbr"], espn.leaders_lines(summ, k=4))
    if leaders:
        out.append("⭐ <b>LÍDERES</b>\n   " + espn.esc(leaders[0]).replace(" | ", "\n   "))
    hurt = team_lines(me["abbr"], espn.injury_lines(summ, k=6))
    out.append("\n🚑 <b>LESIONES</b>\n   " + (espn.esc(hurt[0]) if hurt else "Nadie importante fuera"))
    when = f"{espn.short_date(g['start'])} {espn.pr_time(g['start'])} (PR)" if g["start"] else ""
    vs = "vs" if me is g["home"] else "@"
    out.append(f"\n📅 Próximo: {vs} <b>{espn.esc(opp['name'])}</b> · {when}\n<i>Usa /juego {espn.esc(arg)} para el preview completo</i>")
    return "\n".join(out)


def cmd_juego(arg):
    """
    <summary>Preview completo del próximo juego del equipo (incluye matchups anteriores).</summary>
    <param name="arg">Nombre del equipo.</param>
    <returns>HTML.</returns>
    """
    team = find_team(arg)
    if not team:
        return "No encontré ese equipo. Ej: <code>/juego celtics</code>"
    g, summ = next_game(team)
    if not g:
        return f"No encontré el próximo juego de {espn.esc(team.get('displayName'))}."
    hrs = (g["start"] - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600 if g["start"] else 0
    h2h = espn.head_to_head(SPORT, LG, g["home"], g["away"])
    tg_html, _ = espn.build_preview(LABEL, EMOJI, g, summ, h2h, load_state(), hrs)
    return tg_html


def cmd_kalshi(arg):
    """
    <summary>Precios de Kalshi de los mercados que mencionan al equipo.</summary>
    <param name="arg">Nombre del equipo.</param>
    <returns>HTML.</returns>
    """
    team = find_team(arg)
    if not team:
        return "No encontré ese equipo. Ej: <code>/kalshi knicks</code>"
    toks = {x.lower() for x in (team.get("name"), team.get("shortDisplayName"), team.get("location")) if x and len(x) > 2}
    markets = cached("kx_markets", 120, lambda: kalshi_board.sports_markets(
        kalshi_board.fetch_events(), dt.datetime.now(dt.timezone.utc)))
    hits = sorted((m for m in markets if any(t in m["name"].lower() for t in toks)), key=lambda m: -m["vol24"])[:12]
    if not hits:
        return f"No hay mercados abiertos de {espn.esc(team.get('displayName'))} en Kalshi ahora mismo."
    out = [f"💹 <b>KALSHI · {espn.esc(team.get('displayName'))}</b>\n"]
    for m in hits:
        price = f"YES {round(m['mid'])}¢" if m["mid"] is not None else "—"
        out.append(f"• <a href=\"{espn.esc(m['url'])}\">{espn.esc(m['name'])}</a>\n   <b>{price}</b> · Vol {kalshi_board.fmt_vol(m['vol24'])} · cierra en {kalshi_board.fmt_when(m['hours'])}")
    out.append("\n<i>⚠️ No es consejo financiero.</i>")
    return "\n".join(out)


def cmd_picks(_arg):
    """
    <summary>Los picks de alta probabilidad del momento.</summary>
    <param name="_arg">No se usa.</param>
    <returns>HTML.</returns>
    """
    markets = cached("kx_markets", 120, lambda: kalshi_board.sports_markets(
        kalshi_board.fetch_events(), dt.datetime.now(dt.timezone.utc)))
    picks = kalshi_board.high_prob(markets)
    if not picks:
        return "🔒 Ahora mismo no hay picks que cumplan el filtro (85–95¢, líquidos, cierran en &lt;48h)."
    out = ["🔒 <b>ALTA PROBABILIDAD · AHORA</b>\n"]
    for i, p in enumerate(picks, 1):
        out.append(f"{i}. <a href=\"{espn.esc(p['url'])}\">{espn.esc(p['name'])}</a>\n"
                   f"   <b>{p['side']} @ {p['cost']}¢</b> · $10 → +${p['profit10']:.2f} · cierra en {kalshi_board.fmt_when(p['hours'])}")
    out.append("\n<i>⚠️ No es consejo financiero. Ningún bet es seguro.</i>")
    return "\n".join(out)


COMMANDS = {"hoy": cmd_hoy, "stats": cmd_stats, "juego": cmd_juego, "kalshi": cmd_kalshi, "picks": cmd_picks,
            "ayuda": lambda _a: HELP, "help": lambda _a: HELP, "start": lambda _a: HELP}


def answer(text):
    """
    <summary>Interpreta un mensaje y devuelve la respuesta, o None si no es un comando nuestro.</summary>
    <param name="text">Texto del mensaje.</param>
    <returns>HTML o None.</returns>
    """
    if not text or not text.startswith("/"):
        return None
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0][1:].split("@")[0].lower()
    fn = COMMANDS.get(cmd)
    if not fn:
        return None
    if cmd in ("stats", "juego", "kalshi") and len(parts) < 2:
        return f"Dime el equipo. Ej: <code>/{cmd} lakers</code>"
    try:
        return fn(parts[1] if len(parts) > 1 else "")
    except Exception as e:
        print("Comando error:", cmd, repr(e))
        return "Se me complicó buscar eso ahora mismo 😅 Intenta otra vez en un minuto."


def reply(chat_id, msg_id, html_text):
    """
    <summary>Contesta en el mismo chat, partiendo mensajes largos (límite 4096).</summary>
    <param name="chat_id">Chat donde llegó el comando.</param>
    <param name="msg_id">Mensaje a responder.</param>
    <param name="html_text">Respuesta en HTML.</param>
    """
    chunks, cur = [], ""
    for line in html_text.split("\n"):
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for i, c in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": c, "parse_mode": "HTML", "disable_web_page_preview": True}
        if i == 0:
            payload["reply_parameters"] = {"message_id": msg_id, "allow_sending_without_reply": True}
        res = notify.tg("sendMessage", payload)
        if not res.get("ok") and "parse" in (res.get("description") or "").lower():
            payload.pop("parse_mode")  # si el HTML falla, manda texto plano
            notify.tg("sendMessage", payload)


def listen():
    """
    <summary>Escucha comandos con long polling durante el turno (SHIFT_MINUTES).</summary>
    """
    if not notify.TG_TOKEN:
        print("Sin TELEGRAM_BOT_TOKEN; no escucho comandos.")
        return
    notify.tg("setMyCommands", {"commands": [
        {"command": "hoy", "description": "Juegos de hoy y precios en Kalshi"},
        {"command": "stats", "description": "Stats de un equipo (ej. /stats lakers)"},
        {"command": "juego", "description": "Preview del próximo juego (ej. /juego celtics)"},
        {"command": "kalshi", "description": "Precios de Kalshi de un equipo"},
        {"command": "picks", "description": "Picks de alta probabilidad ahora"},
        {"command": "ayuda", "description": "Lista de comandos"}]})
    end, offset = time.time() + SHIFT_MINUTES * 60, None
    print("Escuchando comandos…")
    while time.time() < end:
        try:
            params = {"timeout": 25, "allowed_updates": ["message"]}
            if offset:
                params["offset"] = offset
            res = notify.tg("getUpdates", params)
            for u in res.get("result") or []:
                offset = u["update_id"] + 1
                m = u.get("message") or {}
                if time.time() - m.get("date", 0) > MAX_AGE_MIN * 60:
                    continue
                out = answer(m.get("text") or "")
                if out:
                    print("Comando:", (m.get("text") or "")[:40])
                    reply(m["chat"]["id"], m["message_id"], out)
        except Exception as e:
            print("Listener error:", repr(e))
            time.sleep(5)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--test":
        print(answer(sys.argv[2]))
    else:
        listen()
