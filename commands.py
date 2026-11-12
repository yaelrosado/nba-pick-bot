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

import ai
import cards
import espn
import kalshi_board
import notify
import players

SHIFT_MINUTES = int(os.getenv("SHIFT_MINUTES", "55"))
STATE_FILE = os.getenv("STATE_FILE", "state.json")
MAX_AGE_MIN = 10  # ignora comandos más viejos que esto (los que llegaron entre turnos)
SPORT, LG, LABEL, EMOJI = "basketball", "nba", "NBA", "🏀"


HELP = (f"{EMOJI} <b>COMANDOS DEL BOT</b>\n\n"
        "/hoy — juegos de hoy, hora en PR y precio en Kalshi\n"
        "/stats <i>equipo</i> — stats de temporada, forma, líderes y lesiones\n"
        "/juego <i>equipo</i> — preview del próximo juego con matchups anteriores\n"
        "/kalshi <i>equipo</i> — precios de Kalshi de ese equipo\n"
        "/jugador <i>nombre</i> — foto, promedios y últimos 5 juegos con stats\n"
        "/picks — los de alta probabilidad ahora mismo\n"
        "/pregunta <i>lo que sea</i> — pregúntale a la IA (o menciona al bot o respóndele)\n\n"
        "<i>Ej: /jugador flagg · /juego celtics · /pregunta ¿quién tiene ventaja hoy, Knicks o Bucks?</i>")



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


cached, nba_teams, find_team, next_game = players.cached, players.nba_teams, players.find_team, players.next_game


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
    try:
        me = g["home"] if g["home"]["id"] == str(team.get("id")) else g["away"]
        png = cards.matchup_card(f"STATS · {me['abbr']}", g, summ, espn.card_info(g, summ, load_state()))
        return ("photo", png, espn.caption(g, load_state(), f"📊 Stats de <b>{espn.esc(me['name'])}</b> y su próximo rival"))
    except Exception as e:
        print("Card stats error:", repr(e))
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
    try:
        png = cards.matchup_card("PREVIEW", g, summ, espn.card_info(g, summ, load_state(), h2h))
        return ("photo", png, espn.caption(g, load_state()))
    except Exception as e:
        print("Card juego error:", repr(e))
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
    markets = ai.kalshi_markets()
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
    markets = ai.kalshi_markets()
    picks = kalshi_board.high_prob(markets)
    if not picks:
        return "🔒 Ahora mismo no hay picks que cumplan el filtro (85–95¢, líquidos, cierran en &lt;48h)."
    out = ["🔒 <b>ALTA PROBABILIDAD · AHORA</b>\n"]
    for i, p in enumerate(picks, 1):
        out.append(f"{i}. <a href=\"{espn.esc(p['url'])}\">{espn.esc(p['name'])}</a>\n"
                   f"   <b>{p['side']} @ {p['cost']}¢</b> · $10 → +${p['profit10']:.2f} · cierra en {kalshi_board.fmt_when(p['hours'])}")
    out.append("\n<i>⚠️ No es consejo financiero. Ningún bet es seguro.</i>")
    return "\n".join(out)


def cmd_jugador(arg):
    """
    <summary>Tarjeta de un jugador: foto, promedios de temporada, últimos 5 juegos con stats y props de Kalshi.</summary>
    <param name="arg">Nombre del jugador.</param>
    <returns>("photo", png, caption) o HTML.</returns>
    """
    p = players.find_player(arg)
    if not p:
        return "No encontré ese jugador. Ej: <code>/jugador brunson</code>"
    avg = players.player_overview(p["id"])
    rows = players.player_last5(p)
    props = []
    try:
        last = p["last"].lower()
        for m in sorted((m for m in ai.kalshi_markets() if last and last in m["name"].lower() and m["mid"] is not None),
                        key=lambda m: -m["vol24"])[:5]:
            props.append((m["name"], round(m["mid"])))
    except Exception as e:
        print("Props error:", repr(e))
    team = (p.get("team") or {}).get("abbreviation", "")
    cap = f"🏀 <b>{espn.esc(p['name'])}</b> · {espn.esc(team)}"
    if avg.get("PTS"):
        cap += f"\n{avg.get('PTS')} PTS · {avg.get('REB', '-')} REB · {avg.get('AST', '-')} AST por juego"
    try:
        return ("photo", cards.player_card(p, avg, rows, props), cap)
    except Exception as e:
        print("Card jugador error:", repr(e))
    lines = [cap, ""] + [f"{r['date']} {r['atvs']} {r['opp']} {r['res']} {r['score']}: {r['PTS']} PTS, {r['REB']} REB, {r['AST']} AST"
                         for r in rows]
    return "\n".join(espn.esc(x) if i > 1 else x for i, x in enumerate(lines))


def cmd_pregunta(arg, user_id=None):
    """
    <summary>Pregunta libre a la IA con data en vivo.</summary>
    <param name="arg">Pregunta.</param>
    <param name="user_id">Id de quien pregunta (para el tope diario).</param>
    <returns>HTML.</returns>
    """
    if user_id is not None and not ai.allowed(user_id):
        return "Llegaste al máximo de preguntas de hoy 🙏 Mañana sigues."
    txt = ai.ask(arg, load_state()) or "No pude contestar ahora mismo 😅 Intenta en un ratito."
    return kalshi_board.md_to_tg(txt)


COMMANDS = {"hoy": cmd_hoy, "stats": cmd_stats, "juego": cmd_juego, "kalshi": cmd_kalshi, "picks": cmd_picks, "jugador": cmd_jugador, "pregunta": cmd_pregunta, "p": cmd_pregunta,
            "ayuda": lambda _a: HELP, "help": lambda _a: HELP, "start": lambda _a: HELP}


EXAMPLES = {"stats": "lakers", "juego": "celtics", "kalshi": "knicks", "jugador": "brunson",
            "pregunta": "¿quién tiene ventaja hoy?", "p": "¿quién tiene ventaja hoy?"}


def answer(text, user_id=None, bot_username=""):
    """
    <summary>Interpreta un mensaje y devuelve la respuesta, o None si no es para el bot.</summary>
    <param name="text">Texto del mensaje.</param>
    <param name="user_id">Id de quien escribe.</param>
    <param name="bot_username">@ del bot (si lo mencionan, cuenta como /pregunta).</param>
    <returns>HTML, ("photo", png, caption) o None.</returns>
    """
    if not text:
        return None
    mention = f"@{bot_username}".lower() if bot_username else ""
    if not text.startswith("/"):
        if mention and mention in text.lower():
            q = text.lower().replace(mention, "").strip() or text
            return cmd_pregunta(q, user_id)
        return None
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0][1:].split("@")[0].lower()
    fn = COMMANDS.get(cmd)
    if not fn:
        return None
    if cmd in EXAMPLES and len(parts) < 2:
        return f"Te faltó decirme qué. Ej: <code>/{cmd} {EXAMPLES[cmd]}</code>"
    try:
        if fn is cmd_pregunta:
            return fn(parts[1], user_id)
        return fn(parts[1] if len(parts) > 1 else "")
    except Exception as e:
        print("Comando error:", cmd, repr(e))
        return "Se me complicó buscar eso ahora mismo 😅 Intenta otra vez en un minuto."


def reply(chat_id, msg_id, html_text):
    """
    <summary>Contesta en el mismo chat: imagen con caption, o texto partido en mensajes de máx 4096.</summary>
    <param name="chat_id">Chat donde llegó el comando.</param>
    <param name="msg_id">Mensaje a responder.</param>
    <param name="html_text">Respuesta en HTML, o ("photo", png, caption).</param>
    """
    if isinstance(html_text, tuple):
        _, png, cap = html_text
        notify.send_photo(chat_id, png, cap, reply_to=msg_id)
        return
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
        {"command": "jugador", "description": "Stats de un jugador (ej. /jugador brunson)"},
        {"command": "pregunta", "description": "Pregúntale a la IA (ej. /pregunta ¿quién gana hoy?)"},
        {"command": "picks", "description": "Picks de alta probabilidad ahora"},
        {"command": "ayuda", "description": "Lista de comandos"}]})
    me = notify.tg("getMe", {}).get("result") or {}
    bot_username, bot_id = me.get("username", ""), me.get("id")
    end, offset = time.time() + SHIFT_MINUTES * 60, None
    print("Escuchando comandos…")
    try:
        players.roster_index()  # precarga los rosters para que /jugador conteste rápido
    except Exception as e:
        print("Roster error:", repr(e))
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
                text = m.get("text") or ""
                uid = (m.get("from") or {}).get("id")
                to_bot = ((m.get("reply_to_message") or {}).get("from") or {}).get("id") == bot_id
                if to_bot and text and not text.startswith("/"):
                    text = f"/pregunta {text}"  # responderle al bot = preguntarle
                is_ai = text.startswith(("/pregunta", "/p ")) or (bot_username and f"@{bot_username}".lower() in text.lower() and not text.startswith("/"))
                if is_ai:
                    notify.tg("sendChatAction", {"chat_id": m["chat"]["id"], "action": "typing"})
                out = answer(text, uid, bot_username)
                if out:
                    print("Comando:", (m.get("text") or "")[:40])
                    reply(m["chat"]["id"], m["message_id"], out)
        except Exception as e:
            print("Listener error:", repr(e))
            time.sleep(5)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--test":
        out = answer(sys.argv[2])
        if isinstance(out, tuple):
            notify._dry_save([out[1]], out[2])
        else:
            print(out)
    else:
        listen()
