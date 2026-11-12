"""
<summary>
ESPN: noticias al momento, previews de juegos (últimos 5, matchups anteriores, stats,
líderes, lesiones, línea y precio de Kalshi) y resultados finales.
</summary>
<remarks>
Archivo: espn.py
Autor: Yael Rosado
Propósito: Parte deportiva del bot. Usa la API pública de ESPN (sin key, no oficial).
ESPN_MOCK_DIR=carpeta usa archivos JSON falsos en vez de la API.
</remarks>
"""

import datetime as dt
import html
import json
import os
import re
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

import cards
import notify

BASE = "https://site.api.espn.com/apis/site/v2/sports"
MOCK_DIR = os.getenv("ESPN_MOCK_DIR")
TZ = ZoneInfo("America/Puerto_Rico")

# (deporte, liga, etiqueta, emoji, solo juegos con equipos rankeados top 25)
LEAGUES = [
    ("basketball", "nba", "NBA", "🏀", False),
]
COLORS = {"NFL": 0x013369, "NBA": 0xC9082A, "MLB": 0x041E42, "NHL": 0x111111, "NCAAF": 0x7C3AED, "NCAAB": 0xF97316}

SLATE_HOUR = 10          # hora (PR) en que sale "Juegos de hoy" con las stats de cada equipo
PREVIEW_HOURS = 3        # el preview sale cuando faltan ≤ 3h para el juego
NEWS_PER_LEAGUE = 6      # máximo de noticias por liga en cada corrida
MAX_NEWS_AGE_H = 3       # ignora noticias más viejas que esto
MAX_POSTS_PER_RUN = 15   # tope total para no chocar con el límite de Telegram
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

_budget = {"left": MAX_POSTS_PER_RUN}


# ------------------------------------------------------------- Helpers ----
def get(path, params=None, base=BASE):
    """
    <summary>GET a la API de ESPN (o a un archivo mock). Nunca lanza excepción.</summary>
    <param name="path">Ruta después de /sports, ej. "/basketball/nba/news".</param>
    <param name="params">Query params.</param>
    <param name="base">URL base (BASE o la de site.web.api).</param>
    <returns>JSON (dict vacío si falla).</returns>
    """
    params = params or {}
    if MOCK_DIR:
        key = re.sub(r"[^a-z0-9]+", "_", (path + "?" + urlencode(sorted(params.items()))).lower()).strip("_")
        try:
            with open(os.path.join(MOCK_DIR, key + ".json"), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    for attempt in range(3):
        try:
            r = requests.get(base + path, params=params, timeout=20)
            if r.ok:
                return r.json()
            if r.status_code < 500 and r.status_code != 429:
                break
        except requests.RequestException as e:
            print("ESPN error:", e)
        time.sleep(1 + attempt)
    print("ESPN falló:", path, params)
    return {}


def parse_time(s):
    """
    <summary>Convierte fechas de ESPN ("2026-11-13T01:15Z") a datetime UTC.</summary>
    <param name="s">Texto ISO.</param>
    <returns>datetime o None.</returns>
    """
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def pr_time(t):
    """
    <summary>Hora en PR, ej. "8:15 PM".</summary>
    <param name="t">datetime UTC.</param>
    <returns>Texto.</returns>
    """
    return t.astimezone(TZ).strftime("%I:%M %p").lstrip("0")


def short_date(t):
    """
    <summary>Fecha corta, ej. "12 mar 26".</summary>
    <param name="t">datetime.</param>
    <returns>Texto.</returns>
    """
    t = t.astimezone(TZ)
    return f"{t.day} {MESES[t.month - 1]} {t.strftime('%y')}"


def ago(t, now):
    """
    <summary>Tiempo transcurrido corto, ej. "hace 12m".</summary>
    <param name="t">datetime del evento.</param>
    <param name="now">Hora actual.</param>
    <returns>Texto.</returns>
    """
    m = max(1, int((now - t).total_seconds() // 60))
    return f"hace {m}m" if m < 60 else f"hace {m // 60}h"


def esc(s):
    """
    <summary>Escapa HTML para Telegram.</summary>
    <param name="s">Texto.</param>
    <returns>Texto seguro.</returns>
    """
    return html.escape(str(s or ""))


def score_txt(v):
    """
    <summary>Normaliza un score de ESPN (string, número o {value, displayValue}).</summary>
    <param name="v">Valor crudo.</param>
    <returns>Texto, ej. "112".</returns>
    """
    if isinstance(v, dict):
        v = v.get("displayValue", v.get("value"))
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else str(f)
    except (TypeError, ValueError):
        return str(v or "-")


def can_post():
    """
    <summary>Descuenta del tope de posts de esta corrida.</summary>
    <returns>True si todavía se puede postear.</returns>
    """
    if _budget["left"] <= 0:
        return False
    _budget["left"] -= 1
    return True


def clip(text, n):
    """
    <summary>Corta texto a n caracteres (para límites de Discord).</summary>
    <param name="text">Texto.</param>
    <param name="n">Máximo.</param>
    <returns>Texto cortado.</returns>
    """
    return text if len(text) <= n else text[: n - 1] + "…"


# -------------------------------------------------------------- Noticias ----
def run_news(state, now):
    """
    <summary>Postea noticias nuevas de cada liga. La primera vez solo las marca como vistas.</summary>
    <param name="state">Estado (news_seen).</param>
    <param name="now">Hora UTC.</param>
    """
    seen_all = state.setdefault("news_seen", {})
    for sport, lg, label, emoji, _ in LEAGUES:
        arts = get(f"/{sport}/{lg}/news", {"limit": 15}).get("articles") or []
        ids = [str(a["id"]) for a in arts if a.get("id")]
        if not ids:
            continue
        seen = seen_all.get(lg)
        if seen is None:
            seen_all[lg] = ids
            continue
        fresh = []
        for a in arts:
            pub = parse_time(a.get("published"))
            if str(a.get("id")) in seen or not pub or (now - pub).total_seconds() > MAX_NEWS_AGE_H * 3600:
                continue
            fresh.append((pub, a))
        fresh.sort(key=lambda x: x[0])
        for pub, a in fresh[-NEWS_PER_LEAGUE:]:
            if not can_post():
                break
            post_news(label, emoji, a, pub, now)
        seen_all[lg] = list(dict.fromkeys(ids + seen))[:80]


def post_news(label, emoji, a, pub, now):
    """
    <summary>Publica una noticia con imagen.</summary>
    <param name="label">Liga, ej. "NBA".</param>
    <param name="emoji">Emoji de la liga.</param>
    <param name="a">Artículo de ESPN.</param>
    <param name="pub">Fecha de publicación.</param>
    <param name="now">Hora UTC.</param>
    """
    url = ((a.get("links") or {}).get("web") or {}).get("href") or "https://www.espn.com"
    img = ((a.get("images") or [{}])[0]).get("url")
    head, desc = a.get("headline") or "", a.get("description") or ""
    tg_html = (f"📰 <b>{label} · ÚLTIMA HORA</b> {emoji} <i>{ago(pub, now)}</i>\n\n"
               f"<b>{esc(head)}</b>\n{esc(clip(desc, 600))}\n\n<a href=\"{esc(url)}\">Leer más →</a>")
    embed = {"author": {"name": f"📰 {label} · ÚLTIMA HORA"}, "title": clip(head, 256), "url": url,
             "description": clip(desc, 1000), "color": COLORS.get(label, 0x111827), "timestamp": pub.isoformat(),
             "footer": {"text": "ESPN"}}
    if img:
        embed["image"] = {"url": img}
    notify.post(tg_html, {"embeds": [embed]}, photo=img)


# --------------------------------------------------------------- Juegos ----
def team_info(c):
    """
    <summary>Datos de un competidor del scoreboard.</summary>
    <param name="c">Competitor de ESPN.</param>
    <returns>Dict con id, name, short, abbr, location, logo, record, rank, score, leaders.</returns>
    """
    t = c.get("team") or {}
    rec = next((r.get("summary") for r in c.get("records") or c.get("record") or []
                if r.get("type") == "total" or (r.get("name") or "").lower() in ("overall", "all splits")), "")
    rank = (c.get("curatedRank") or {}).get("current") or 99
    return {"id": str(t.get("id") or c.get("id")), "name": t.get("displayName") or "", "short": t.get("shortDisplayName") or t.get("name") or "",
            "nick": t.get("name") or "", "abbr": t.get("abbreviation") or "", "location": t.get("location") or "",
            "logo": t.get("logo") or ((t.get("logos") or [{}])[0]).get("href"),
            "color": t.get("color"), "alt": t.get("alternateColor"), "record": rec, "rank": rank if rank <= 25 else None,
            "score": score_txt(c.get("score")), "leaders": c.get("leaders") or []}


def game_info(ev):
    """
    <summary>Normaliza un evento del scoreboard.</summary>
    <param name="ev">Evento de ESPN.</param>
    <returns>Dict del juego o None.</returns>
    """
    comp = (ev.get("competitions") or [{}])[0]
    cs = comp.get("competitors") or []
    home = next((c for c in cs if c.get("homeAway") == "home"), None)
    away = next((c for c in cs if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    st = ((comp.get("status") or ev.get("status") or {}).get("type") or {})
    odds = (comp.get("odds") or [{}])[0] if comp.get("odds") else {}
    return {"id": str(ev.get("id")), "start": parse_time(ev.get("date")), "state": st.get("state"),
            "completed": bool(st.get("completed")), "detail": st.get("shortDetail") or "",
            "home": team_info(home), "away": team_info(away), "venue": (comp.get("venue") or {}).get("fullName"),
            "odds": odds, "broadcast": comp.get("broadcast") or ""}


def tname(t):
    """
    <summary>Nombre corto con ranking, ej. "#5 Duke".</summary>
    <param name="t">team_info.</param>
    <returns>Texto.</returns>
    """
    return (f"#{t['rank']} " if t.get("rank") else "") + (t["short"] or t["name"])


def last_five(summ, team_id):
    """
    <summary>Últimos 5 juegos de un equipo desde el summary.</summary>
    <param name="summ">Summary de ESPN.</param>
    <param name="team_id">Id del equipo.</param>
    <returns>(forma "W L W", [líneas]) </returns>
    """
    block = next((b for b in summ.get("lastFiveGames") or [] if str((b.get("team") or {}).get("id")) == team_id), None)
    if not block:
        return "", []
    form, lines = [], []
    for e in block.get("events") or []:
        res = e.get("gameResult") or "-"
        if str(e.get("homeTeamId")) == team_id:
            mine, theirs = e.get("homeTeamScore"), e.get("awayTeamScore")
        else:
            mine, theirs = e.get("awayTeamScore"), e.get("homeTeamScore")
        opp = (e.get("opponent") or {}).get("abbreviation") or "?"
        icon = {"W": "✅", "L": "❌", "T": "➖"}.get(res, "▫️")
        form.append(res)
        lines.append(f"{icon} {res} {score_txt(mine)}-{score_txt(theirs)} {e.get('atVs') or 'vs'} {opp}")
    return " ".join(form), lines


def head_to_head(sport, lg, team, opp):
    """
    <summary>Matchups anteriores entre dos equipos (temporada actual y pasada) usando el calendario del equipo.</summary>
    <param name="sport">Deporte.</param>
    <param name="lg">Liga.</param>
    <param name="team">team_info del equipo local.</param>
    <param name="opp">team_info del rival.</param>
    <returns>Lista de dicts (date, away, as, home, hs, win, line), más reciente primero (máx 5).</returns>
    """
    cur = get(f"/{sport}/{lg}/teams/{team['id']}/schedule")
    pages = [cur]
    year = (cur.get("season") or {}).get("year")
    if year:
        pages.append(get(f"/{sport}/{lg}/teams/{team['id']}/schedule", {"season": int(year) - 1}))
    games = {}
    for page in pages:
        for ev in page.get("events") or []:
            comp = (ev.get("competitions") or [{}])[0]
            if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
                continue
            cs = comp.get("competitors") or []
            ids = {str(c.get("id") or (c.get("team") or {}).get("id")) for c in cs}
            if opp["id"] not in ids:
                continue
            h = next((c for c in cs if c.get("homeAway") == "home"), None)
            a = next((c for c in cs if c.get("homeAway") == "away"), None)
            t = parse_time(ev.get("date"))
            if not h or not a or not t:
                continue
            hs, as_ = score_txt(h.get("score")), score_txt(a.get("score"))
            ha, aa = (h.get("team") or {}).get("abbreviation", "?"), (a.get("team") or {}).get("abbreviation", "?")
            try:
                win = ha if float(hs) > float(as_) else aa if float(as_) > float(hs) else None
            except ValueError:
                win = None
            games[ev.get("id")] = {"t": t, "date": short_date(t), "away": aa, "as": as_, "home": ha, "hs": hs, "win": win,
                                   "line": f"{short_date(t)} · {aa} {as_} @ {ha} {hs}" + (f" → {win}" if win else "")}
    return sorted(games.values(), key=lambda x: x["t"], reverse=True)[:5]


def season_stats(summ, home, away, n=6):
    """
    <summary>Tabla de stats de temporada comparando ambos equipos.</summary>
    <param name="summ">Summary de ESPN.</param>
    <param name="home">team_info local.</param>
    <param name="away">team_info visitante.</param>
    <param name="n">Cantidad de stats.</param>
    <returns>Texto monoespaciado (o "").</returns>
    """
    teams = {str((t.get("team") or {}).get("id")): {s.get("name"): s for s in t.get("statistics") or []}
             for t in (summ.get("boxscore") or {}).get("teams") or []}
    a, h = teams.get(away["id"]), teams.get(home["id"])
    if not a or not h:
        return ""
    rows = []
    for name, s in a.items():
        if name not in h or name == "streak":
            continue
        label = s.get("abbreviation") or s.get("label") or name
        rows.append((clip(label, 16), str(s.get("displayValue", "")), str(h[name].get("displayValue", ""))))
        if len(rows) == n:
            break
    if not rows:
        return ""
    w = max(len(l) for l, _, _ in rows)
    out = [f"{'':{w}} {away['abbr']:>7} {home['abbr']:>7}"]
    out += [f"{l:{w}} {x:>7} {y:>7}" for l, x, y in rows]
    return "\n".join(out)


def leaders_lines(summ, k=2):
    """
    <summary>Líderes de cada equipo (las primeras k categorías).</summary>
    <param name="summ">Summary de ESPN.</param>
    <param name="k">Categorías por equipo.</param>
    <returns>Lista de líneas.</returns>
    """
    lines = []
    for block in summ.get("leaders") or []:
        abbr = (block.get("team") or {}).get("abbreviation", "?")
        parts = []
        for cat in (block.get("leaders") or [])[:k]:
            top = (cat.get("leaders") or [{}])[0]
            ath = (top.get("athlete") or {}).get("shortName") or (top.get("athlete") or {}).get("displayName")
            if ath:
                parts.append(f"{cat.get('displayName') or cat.get('name')}: {ath} {top.get('displayValue', '')}")
        if parts:
            lines.append(f"{abbr} · " + " | ".join(parts))
    return lines


def injury_lines(summ, k=3):
    """
    <summary>Jugadores Out/Doubtful/Questionable por equipo.</summary>
    <param name="summ">Summary de ESPN.</param>
    <param name="k">Máximo por equipo.</param>
    <returns>Lista de líneas.</returns>
    """
    lines = []
    for block in summ.get("injuries") or []:
        abbr = (block.get("team") or {}).get("abbreviation", "?")
        hurt = [f"{(i.get('athlete') or {}).get('shortName') or (i.get('athlete') or {}).get('displayName')} ({i.get('status')})"
                for i in block.get("injuries") or [] if i.get("status") in ("Out", "Doubtful", "Questionable")]
        if hurt:
            lines.append(f"{abbr} · " + ", ".join(hurt[:k]) + (f" +{len(hurt) - k}" if len(hurt) > k else ""))
    return lines


def odds_line(summ, g):
    """
    <summary>Línea de apuestas y proyección de ESPN si existen.</summary>
    <param name="summ">Summary.</param>
    <param name="g">Juego.</param>
    <returns>Texto o "".</returns>
    """
    o = (summ.get("pickcenter") or [None])[0] or g.get("odds") or {}
    parts = []
    if o.get("details"):
        parts.append(f"Línea {o['details']}")
    if o.get("overUnder"):
        parts.append(f"O/U {o['overUnder']}")
    pred = summ.get("predictor") or {}
    try:
        hp = float((pred.get("homeTeam") or {}).get("gameProjection"))
        parts.append(f"ESPN proyecta {g['home']['abbr']} {hp:.0f}%")
    except (TypeError, ValueError):
        pass
    return " · ".join(parts)


def _toks(t):
    """
    <summary>Palabras para reconocer un equipo en un título de Kalshi.</summary>
    <param name="t">team_info.</param>
    <returns>Set de palabras en minúscula.</returns>
    """
    return {x.lower() for x in (t.get("nick"), t.get("short"), t.get("location")) if x and len(x) > 2}


def kalshi_prices(state, g):
    """
    <summary>Precio de Kalshi (¢) de que gane cada equipo, usando solo el mercado de ganador del juego.</summary>
    <param name="state">Estado (kx).</param>
    <param name="g">Juego.</param>
    <returns>{"away": ¢, "home": ¢} (vacío si no hay mercado).</returns>
    """
    ht, at = _toks(g["home"]), _toks(g["away"])
    found = [m for m in state.get("kx") or []
             if any(x in m["t"].lower() for x in ht) and any(x in m["t"].lower() for x in at)]
    winners = [m for m in found if "kxnbagame" in (m.get("u") or "").lower()] or found
    res = {}
    for m in winners:
        side = m["t"].split(" · ")[-1].lower() if " · " in m["t"] else ""
        if side and any(x in side for x in ht) and not any(x in side for x in at):
            res.setdefault("home", m["p"])
        elif side and any(x in side for x in at) and not any(x in side for x in ht):
            res.setdefault("away", m["p"])
    if "home" in res and "away" not in res:
        res["away"] = 100 - res["home"]
    if "away" in res and "home" not in res:
        res["home"] = 100 - res["away"]
    return res


def kalshi_line(state, g):
    """
    <summary>Texto con el precio de Kalshi del juego, ej. "IND 68¢ · WSH 32¢".</summary>
    <param name="state">Estado (kx).</param>
    <param name="g">Juego.</param>
    <returns>Texto o "".</returns>
    """
    p = kalshi_prices(state, g)
    if not p:
        return ""
    return f"{g['away']['abbr']} {p['away']}¢ · {g['home']['abbr']} {p['home']}¢"


def card_info(g, summ, state, h2h=None):
    """
    <summary>Arma los datos que necesita cards.matchup_card.</summary>
    <param name="g">Juego.</param>
    <param name="summ">Summary de ESPN.</param>
    <param name="state">Estado (Kalshi).</param>
    <param name="h2h">Matchups anteriores (opcional).</param>
    <returns>Dict para la tarjeta.</returns>
    """
    import players  # aquí adentro para evitar import circular
    form, last5, keyp, tleaders = {}, {}, {}, {}
    for t in (g["away"], g["home"]):
        f, _ = last_five(summ, t["id"])
        form[t["id"]] = (f.split() if f else [], team_streak(summ, t["id"]))
        try:
            last5[t["id"]] = players.last5_detail(summ, t)
        except Exception as e:
            print("Últimos 5 error:", t["abbr"], repr(e))
        try:
            tleaders[t["id"]] = players.team_leaders(t)
        except Exception as e:
            print("Líderes equipo error:", t["abbr"], repr(e))
        try:
            keyp[t["id"]] = players.key_players(summ, t)
        except Exception as e:
            print("Jugadores clave error:", t["abbr"], repr(e))
    start = g.get("start")
    when = "EN VIVO" if g.get("state") == "in" else (pr_time(start) if start else "")
    local = start.astimezone(TZ) if start else None
    return {"when": when, "sub": "Hora PR" + (f" · {g['broadcast']}" if g.get("broadcast") else ""),
            "date": f"{local.day} {MESES[local.month - 1]} {local.strftime('%Y')}" if local else "",
            "kalshi": kalshi_prices(state, g), "odds": odds_line(summ, g), "h2h": h2h or [], "form": form,
            "last5": last5, "players": keyp, "team_leaders": tleaders}


def caption(g, state, extra=""):
    """
    <summary>Caption corto para la imagen de un juego.</summary>
    <param name="g">Juego.</param>
    <param name="state">Estado (Kalshi).</param>
    <param name="extra">Línea adicional opcional.</param>
    <returns>HTML.</returns>
    """
    a, h = g["away"], g["home"]
    out = f"🏀 <b>{esc(a['short'])} @ {esc(h['short'])}</b>" + (f" · {pr_time(g['start'])} (PR)" if g.get("start") else "")
    kx = kalshi_line(state, g)
    if kx:
        out += f"\n💹 Kalshi: {esc(kx)}"
    return out + (f"\n{extra}" if extra else "")


def run_previews(sport, lg, label, emoji, only_ranked, games, state, now):
    """
    <summary>Postea el preview de cada juego que empieza en ≤ PREVIEW_HOURS.</summary>
    <param name="sport">Deporte.</param>
    <param name="lg">Liga.</param>
    <param name="label">Etiqueta.</param>
    <param name="emoji">Emoji.</param>
    <param name="only_ranked">Solo juegos con rankeados.</param>
    <param name="games">Juegos normalizados del scoreboard.</param>
    <param name="state">Estado (previewed).</param>
    <param name="now">Hora UTC.</param>
    """
    done = state.setdefault("previewed", [])
    for g in games:
        if g["state"] != "pre" or not g["start"] or g["id"] in done:
            continue
        hrs = (g["start"] - now).total_seconds() / 3600
        if not 0 < hrs <= PREVIEW_HOURS or (only_ranked and not (g["home"]["rank"] or g["away"]["rank"])):
            continue
        if not can_post():
            return
        summ = get(f"/{sport}/{lg}/summary", {"event": g["id"]})
        h2h = head_to_head(sport, lg, g["home"], g["away"])
        try:
            png = cards.matchup_card("PREVIEW", g, summ, card_info(g, summ, state, h2h))
            notify.post_photo(png, caption(g, state, "<i>Arranca en menos de 3 horas</i>"))
        except Exception as e:  # si la imagen falla, manda la versión en texto
            print("Card preview error:", repr(e))
            notify.post(*build_preview(label, emoji, g, summ, h2h, state, hrs))
        done.append(g["id"])
    state["previewed"] = done[-500:]


def build_preview(label, emoji, g, summ, h2h, state, hrs):
    """
    <summary>Arma la tarjeta de preview del juego.</summary>
    <param name="label">Liga.</param>
    <param name="emoji">Emoji.</param>
    <param name="g">Juego.</param>
    <param name="summ">Summary de ESPN.</param>
    <param name="h2h">Matchups anteriores (de head_to_head).</param>
    <param name="state">Estado (para el precio de Kalshi).</param>
    <param name="hrs">Horas para el juego.</param>
    <returns>(html de Telegram, payload de Discord).</returns>
    """
    h, a = g["home"], g["away"]
    rec = lambda t: f" ({t['record']})" if t["record"] else ""
    if g.get("state") == "in":
        when = f"🔴 En juego · {g.get('detail') or ''}".rstrip(" ·")
    elif hrs <= 0:
        when = f"🕖 {pr_time(g['start'])} (PR) · por empezar"
    elif hrs < 1:
        when = f"🕖 {pr_time(g['start'])} (PR) · en {max(1, round(hrs * 60))}m"
    elif hrs < 24:
        when = f"🕖 {pr_time(g['start'])} (PR) · en {hrs:.0f}h"
    else:
        when = f"🕖 {short_date(g['start'])} · {pr_time(g['start'])} (PR)"
    extra = " · ".join(x for x in (g.get("venue"), g.get("broadcast")) if x)
    kx, odds = kalshi_line(state, g), odds_line(summ, g)
    fa, la = last_five(summ, a["id"])
    fh, lh = last_five(summ, h["id"])
    stats, leaders, injuries = season_stats(summ, h, a), leaders_lines(summ), injury_lines(summ)

    t = [f"{emoji} <b>{label} · PREVIEW</b>",
         f"<b>{esc(tname(a))}</b>{rec(a)} @ <b>{esc(tname(h))}</b>{rec(h)}",
         when + (f"\n📍 {esc(extra)}" if extra else "")]
    if kx:
        t.append(f"💹 <b>Kalshi:</b> {esc(kx)}")
    if odds:
        t.append(f"🎯 {esc(odds)}")
    if la or lh:
        t.append("\n📅 <b>ÚLTIMOS 5</b>")
        for team, form, lines in ((a, fa, la), (h, fh, lh)):
            if lines:
                t.append(f"<b>{esc(team['abbr'])}</b>  <code>{esc(form)}</code>")
                t += [f"   {esc(x)}" for x in lines]
    t.append("\n🆚 <b>MATCHUPS ANTERIORES</b>")
    t += [f"   {esc(x['line'])}" for x in h2h] or ["   <i>No se han enfrentado recientemente</i>"]
    if stats:
        t.append("\n📊 <b>STATS DE TEMPORADA</b>")
        t.append(f"<pre>{esc(stats)}</pre>")
    if leaders:
        t.append("⭐ <b>LÍDERES</b>")
        t += [f"   {esc(x)}" for x in leaders]
    if injuries:
        t.append("\n🚑 <b>LESIONES</b>")
        t += [f"   {esc(x)}" for x in injuries]
    t.append("\n<i>⚠️ No es consejo financiero.</i>")

    desc = when + (f"\n📍 {extra}" if extra else "") + (f"\n💹 **Kalshi:** {kx}" if kx else "") + (f"\n🎯 {odds}" if odds else "")
    fields = []
    for team, form, lines in ((a, fa, la), (h, fh, lh)):
        if lines:
            fields.append({"name": f"📅 Últimos 5 · {team['abbr']}  `{form}`", "value": clip("\n".join(lines), 1024), "inline": True})
    fields.append({"name": "🆚 Matchups anteriores", "value": clip("\n".join(x["line"] for x in h2h) or "*No se han enfrentado recientemente*", 1024), "inline": False})
    if stats:
        fields.append({"name": "📊 Stats de temporada", "value": clip(f"```\n{stats}\n```", 1024), "inline": False})
    if leaders:
        fields.append({"name": "⭐ Líderes", "value": clip("\n".join(leaders), 1024), "inline": False})
    if injuries:
        fields.append({"name": "🚑 Lesiones", "value": clip("\n".join(injuries), 1024), "inline": False})
    embed = {"author": {"name": f"{emoji} {label} · PREVIEW"}, "title": clip(f"{tname(a)}{rec(a)} @ {tname(h)}{rec(h)}", 256),
             "description": desc, "fields": fields, "color": COLORS.get(label, 0x111827),
             "footer": {"text": "⚠️ No es consejo financiero · Data: ESPN + Kalshi"}}
    if h.get("logo"):
        embed["thumbnail"] = {"url": h["logo"]}
    return "\n".join(t), {"embeds": [embed]}


def run_finals(lg, label, emoji, only_ranked, games, state):
    """
    <summary>Postea resultados finales nuevos. La primera vez por liga solo los marca.</summary>
    <param name="lg">Liga.</param>
    <param name="label">Etiqueta.</param>
    <param name="emoji">Emoji.</param>
    <param name="only_ranked">Solo juegos con rankeados.</param>
    <param name="games">Juegos normalizados.</param>
    <param name="state">Estado (finals, finals_init).</param>
    """
    posted, init = state.setdefault("finals", []), state.setdefault("finals_init", [])
    for g in games:
        if not g["completed"] or g["id"] in posted:
            continue
        if only_ranked and not (g["home"]["rank"] or g["away"]["rank"]):
            continue
        if lg in init:
            if not can_post():
                break
            try:
                local = (g.get("start") or dt.datetime.now(dt.timezone.utc)).astimezone(TZ)
                png = cards.final_card(g, f"{local.day} {MESES[local.month - 1]} {local.strftime('%Y')}")
                a, h = g["away"], g["home"]
                notify.post_photo(png, f"✅ <b>FINAL</b> · {esc(a['short'])} {a['score']} – {h['score']} {esc(h['short'])}")
            except Exception as e:
                print("Card final error:", repr(e))
                post_final(label, emoji, g)
        posted.append(g["id"])
    if lg not in init:
        init.append(lg)
    state["finals"] = posted[-800:]


def post_final(label, emoji, g):
    """
    <summary>Publica la tarjeta de resultado final.</summary>
    <param name="label">Liga.</param>
    <param name="emoji">Emoji.</param>
    <param name="g">Juego.</param>
    """
    h, a = g["home"], g["away"]
    try:
        hw = float(h["score"]) > float(a["score"])
        aw = float(a["score"]) > float(h["score"])
    except ValueError:
        hw = aw = False
    stars = []
    for t in (a, h):
        cat = (t["leaders"] or [{}])[0]
        top = (cat.get("leaders") or [{}])[0]
        ath = (top.get("athlete") or {}).get("shortName") or (top.get("athlete") or {}).get("displayName")
        if ath:
            stars.append(f"{t['abbr']}: {ath} {top.get('displayValue', '')}")
    rows_tg, rows_dc = [], []
    for t, won in ((a, aw), (h, hw)):
        txt = f"{tname(t)}  {t['score']}"
        rows_tg.append((f"<b>{esc(txt)}</b> 🏆" if won else esc(txt)))
        rows_dc.append((f"**{txt}** 🏆" if won else txt))
    star_txt = " · ".join(stars)
    tg_html = f"✅ <b>FINAL · {label}</b> {emoji}\n" + "\n".join(rows_tg) + (f"\n⭐ {esc(star_txt)}" if stars else "")
    desc = "\n".join(rows_dc) + (f"\n⭐ {star_txt}" if stars else "")
    win = h if hw else a if aw else None
    embed = {"author": {"name": f"✅ FINAL · {label}"}, "description": desc, "color": 0x22C55E}
    if win and win.get("logo"):
        embed["thumbnail"] = {"url": win["logo"]}
    notify.post(tg_html, {"embeds": [embed]})


# --------------------------------------------------------- Juegos de hoy ----
def team_streak(summ, team_id):
    """
    <summary>Racha actual del equipo (ej. "W3") desde las stats de temporada.</summary>
    <param name="summ">Summary de ESPN.</param>
    <param name="team_id">Id del equipo.</param>
    <returns>Texto o "".</returns>
    """
    for t in (summ.get("boxscore") or {}).get("teams") or []:
        if str((t.get("team") or {}).get("id")) == team_id:
            s = next((x for x in t.get("statistics") or [] if x.get("name") == "streak"), None)
            return (s or {}).get("displayValue") or ""
    return ""


def run_slate(sport, lg, label, emoji, state, now):
    """
    <summary>Una vez al día (desde SLATE_HOUR en PR) postea "Juegos de hoy" con una tarjeta de stats por juego.</summary>
    <param name="sport">Deporte.</param>
    <param name="lg">Liga.</param>
    <param name="label">Etiqueta.</param>
    <param name="emoji">Emoji.</param>
    <param name="state">Estado (slate_day).</param>
    <param name="now">Hora UTC.</param>
    """
    local = now.astimezone(TZ)
    today = local.date().isoformat()
    if local.hour < SLATE_HOUR or state.get("slate_day") == today:
        return
    events = get(f"/{sport}/{lg}/scoreboard", {"dates": local.strftime("%Y%m%d")}).get("events") or []
    games = sorted((g for g in map(game_info, events) if g and g["start"] and g["state"] == "pre"),
                   key=lambda g: g["start"])
    state["slate_day"] = today
    if not games:
        return

    date_txt = f"{local.day} {MESES[local.month - 1]} {local.strftime('%Y')}"
    for g in games:
        g["when_short"] = pr_time(g["start"])
    n = len(games)
    try:
        # Imagen 1: resumen del día
        notify.post_photo(cards.slate_card(games, date_txt, lambda g: kalshi_prices(state, g)),
                          f"{emoji} <b>JUEGOS DE HOY</b> · {n} juego{'s' if n != 1 else ''} · horas en PR\n"
                          "<i>Las stats de cada juego van abajo 👇</i>",
                          f"**{emoji} JUEGOS DE HOY** · {n} juego{'s' if n != 1 else ''}")
        # Álbum: una tarjeta de stats por juego
        imgs = []
        for g in games:
            try:
                summ = get(f"/{sport}/{lg}/summary", {"event": g["id"]})
                imgs.append(cards.matchup_card("JUEGOS DE HOY", g, summ, card_info(g, summ, state)))
            except Exception as e:
                print("Card slate error:", g["id"], repr(e))
        notify.post_album(imgs, "📊 <b>Stats de cada juego</b> · desliza para verlos todos", "📊 **Stats de cada juego**")
    except Exception as e:  # si las imágenes fallan, manda la versión en texto
        print("Slate imágenes error:", repr(e))
        for g in games:
            summ = get(f"/{sport}/{lg}/summary", {"event": g["id"]})
            notify.post(*build_team_card(label, emoji, g, summ, state))
            time.sleep(2)


def build_team_card(label, emoji, g, summ, state):
    """
    <summary>Arma la tarjeta de stats de los dos equipos de un juego de hoy.</summary>
    <param name="label">Liga.</param>
    <param name="emoji">Emoji.</param>
    <param name="g">Juego.</param>
    <param name="summ">Summary de ESPN.</param>
    <param name="state">Estado (precio de Kalshi).</param>
    <returns>(html de Telegram, payload de Discord).</returns>
    """
    a, h = g["away"], g["home"]
    kx = kalshi_line(state, g)
    stats = season_stats(summ, h, a, n=8)
    leaders, injuries = leaders_lines(summ, k=3), injury_lines(summ)
    form = []
    for t in (a, h):
        f, _ = last_five(summ, t["id"])
        st = team_streak(summ, t["id"])
        form.append((t, f, st))

    head = f"{tname(a)} ({a['record']}) @ {tname(h)} ({h['record']})"
    t = [f"{emoji} <b>{esc(head)}</b>", f"🕖 {pr_time(g['start'])} (PR)" + (f" · {esc(g['broadcast'])}" if g.get("broadcast") else "")]
    if kx:
        t.append(f"💹 <b>Kalshi:</b> {esc(kx)}")
    form_lines = [f"<b>{esc(tm['abbr'])}</b>  últimos 5 <code>{esc(f or '—')}</code>" + (f" · racha <b>{esc(st)}</b>" if st else "")
                  for tm, f, st in form]
    t.append("\n🔥 <b>FORMA</b>")
    t += [f"   {x}" for x in form_lines]
    if stats:
        t.append("\n📊 <b>STATS DE TEMPORADA</b>")
        t.append(f"<pre>{esc(stats)}</pre>")
    if leaders:
        t.append("⭐ <b>LÍDERES</b>")
        t += [f"   {esc(x)}" for x in leaders]
    if injuries:
        t.append("\n🚑 <b>LESIONES</b>")
        t += [f"   {esc(x)}" for x in injuries]

    fields = [{"name": f"🔥 {tm['abbr']}", "value": f"Últimos 5 `{f or '—'}`" + (f"\nRacha **{st}**" if st else ""), "inline": True}
              for tm, f, st in form]
    if stats:
        fields.append({"name": "📊 Stats de temporada", "value": clip(f"```\n{stats}\n```", 1024), "inline": False})
    if leaders:
        fields.append({"name": "⭐ Líderes", "value": clip("\n".join(leaders), 1024), "inline": False})
    if injuries:
        fields.append({"name": "🚑 Lesiones", "value": clip("\n".join(injuries), 1024), "inline": False})
    embed = {"author": {"name": f"{emoji} {label} · JUEGOS DE HOY"}, "title": clip(head, 256),
             "description": f"🕖 {pr_time(g['start'])} (PR)" + (f" · {g['broadcast']}" if g.get("broadcast") else "")
             + (f"\n💹 **Kalshi:** {kx}" if kx else ""),
             "fields": fields, "color": COLORS.get(label, 0x111827), "footer": {"text": "Data: ESPN + Kalshi"}}
    if h.get("logo"):
        embed["thumbnail"] = {"url": h["logo"]}
    return "\n".join(t), {"embeds": [embed]}


def run(state, now):
    """
    <summary>Corrida ESPN completa: juegos de hoy, noticias, previews y finales de todas las ligas.</summary>
    <param name="state">Estado compartido (se modifica).</param>
    <param name="now">Hora UTC.</param>
    """
    _budget["left"] = MAX_POSTS_PER_RUN
    for sport, lg, label, emoji, only_ranked in LEAGUES:
        try:
            run_slate(sport, lg, label, emoji, state, now)
        except Exception as e:
            print(f"Juegos de hoy {label} error:", repr(e))
        try:
            games = [g for g in map(game_info, get(f"/{sport}/{lg}/scoreboard").get("events") or []) if g]
            run_finals(lg, label, emoji, only_ranked, games, state)
            run_previews(sport, lg, label, emoji, only_ranked, games, state, now)
        except Exception as e:  # una liga con data rara no tumba las demás
            print(f"ESPN {label} error:", repr(e))
    try:
        run_news(state, now)
    except Exception as e:
        print("Noticias error:", repr(e))
