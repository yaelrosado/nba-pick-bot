"""
<summary>
Data de equipos y jugadores de la NBA (ESPN): búsqueda de equipos y jugadores, próximo juego,
promedios de temporada, game log, box scores y "últimos 5" con el mejor jugador de cada juego.
</summary>
<remarks>
Archivo: players.py
Autor: Yael Rosado
Propósito: Funciones compartidas por las tarjetas, los comandos y la IA. Todo con cache en memoria.
</remarks>
"""

import datetime as dt
import time

import espn

SPORT, LG = "basketball", "nba"
WEB = "https://site.web.api.espn.com/apis/common/v3/sports"

ALIASES = {"sixers": "PHI", "76ers": "PHI", "blazers": "POR", "wolves": "MIN", "cavs": "CLE", "mavs": "DAL",
           "dubs": "GS", "warriors": "GS", "clips": "LAC", "lakers": "LAL", "knicks": "NY", "nets": "BKN",
           "spurs": "SA", "pels": "NO", "suns": "PHX", "thunder": "OKC", "okc": "OKC", "nuggets": "DEN",
           "jazz": "UTAH", "wizards": "WSH", "celtics": "BOS", "heat": "MIA", "bucks": "MIL", "bulls": "CHI"}

_cache = {}


def cached(key, seconds, fn):
    """
    <summary>Cache sencillo en memoria.</summary>
    <param name="key">Llave.</param>
    <param name="seconds">Vida del valor.</param>
    <param name="fn">Función que produce el valor.</param>
    <returns>Valor.</returns>
    """
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def summary(event_id, ttl=600):
    """
    <summary>Summary de un juego (cache; los juegos terminados se guardan por 6h).</summary>
    <param name="event_id">Id del juego.</param>
    <param name="ttl">Vida en segundos para juegos no terminados.</param>
    <returns>JSON del summary.</returns>
    """
    key = f"summ:{event_id}"
    hit = _cache.get(key)
    if hit and (hit[2] or time.time() - hit[0] < ttl):
        return hit[1]
    s = espn.get(f"/{SPORT}/{LG}/summary", {"event": event_id})
    comp = ((s.get("header") or {}).get("competitions") or [{}])[0]
    done = bool(((comp.get("status") or {}).get("type") or {}).get("completed"))
    _cache[key] = (time.time(), s, done)
    return s


# --------------------------------------------------------------- Equipos ----
def nba_teams():
    """
    <summary>Lista de equipos de la NBA (cache 6h).</summary>
    <returns>Lista de dicts de equipo.</returns>
    """
    def load():
        data = espn.get(f"/{SPORT}/{LG}/teams")
        lgs = ((data.get("sports") or [{}])[0].get("leagues") or [{}])[0]
        return [t.get("team") or {} for t in lgs.get("teams") or []]
    return cached("teams", 6 * 3600, load)


def find_team(query):
    """
    <summary>Busca un equipo por nombre, ciudad, abreviatura o apodo.</summary>
    <param name="query">Texto, ej. "lakers", "LAL".</param>
    <returns>Dict del equipo o None.</returns>
    """
    q = (query or "").strip().lower()
    if len(q) < 2:
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
    if len(q) >= 4:
        for t in teams:
            if q in (t.get("displayName") or "").lower():
                return t
    return None


def teams_in_text(text, k=2):
    """
    <summary>Encuentra hasta k equipos mencionados en un texto libre.</summary>
    <param name="text">Pregunta del usuario.</param>
    <param name="k">Máximo.</param>
    <returns>Lista de equipos.</returns>
    """
    words = [w.strip("¿?¡!.,;:()\"'").lower() for w in (text or "").split()]
    found = []
    for i in range(len(words)):
        for cand in (" ".join(words[i:i + 2]), words[i]):
            if len(cand) < 3 or cand in ("los", "las", "the", "que", "hoy", "con", "vs", "por", "para"):
                continue
            t = find_team(cand)
            if t and t not in found:
                found.append(t)
                break
        if len(found) >= k:
            break
    return found


def next_game(team):
    """
    <summary>Próximo juego (o el de hoy) del equipo y su summary.</summary>
    <param name="team">Dict del equipo.</param>
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
    summ = summary(ev_id)
    comp = ((summ.get("header") or {}).get("competitions") or [{}])[0]
    g = espn.game_info({"id": ev_id, "date": comp.get("date"), "competitions": [comp]})
    return g, summ


# ------------------------------------------------------------- Box score ----
def box_players(summ):
    """
    <summary>Stats de cada jugador en un box score.</summary>
    <param name="summ">Summary de un juego.</param>
    <returns>{team_id: [ {id, name, short, headshot, pos, starter, stats{PTS, REB…}} ]}.</returns>
    """
    out = {}
    for p in (summ.get("boxscore") or {}).get("players") or []:
        tid = str((p.get("team") or {}).get("id"))
        st = (p.get("statistics") or [{}])[0]
        names = st.get("names") or st.get("labels") or []
        rows = []
        for a in st.get("athletes") or []:
            if a.get("didNotPlay") or not a.get("stats"):
                continue
            ath = a.get("athlete") or {}
            rows.append({"id": str(ath.get("id")), "name": ath.get("displayName"), "short": ath.get("shortName"),
                         "headshot": (ath.get("headshot") or {}).get("href"),
                         "pos": (ath.get("position") or {}).get("abbreviation"), "starter": a.get("starter"),
                         "stats": dict(zip(names, a.get("stats")))})
        out[tid] = rows
    return out


def to_int(v):
    """
    <summary>Convierte "32" o "-5" a int (0 si no se puede).</summary>
    <param name="v">Valor.</param>
    <returns>int.</returns>
    """
    try:
        return int(float(str(v).replace("+", "")))
    except (TypeError, ValueError):
        return 0


def last5_detail(summ, team):
    """
    <summary>Últimos 5 juegos del equipo con score, rival y el mejor jugador de cada juego, más promedios.</summary>
    <param name="summ">Summary del próximo juego (trae lastFiveGames).</param>
    <param name="team">team_info (id, abbr).</param>
    <returns>{"rows": [...], "avg_for", "avg_against", "record"}.</returns>
    """
    block = next((b for b in summ.get("lastFiveGames") or [] if str((b.get("team") or {}).get("id")) == team["id"]), None)
    rows = []
    for e in (block or {}).get("events") or []:
        home = str(e.get("homeTeamId")) == team["id"]
        mine = to_int(e.get("homeTeamScore") if home else e.get("awayTeamScore"))
        theirs = to_int(e.get("awayTeamScore") if home else e.get("homeTeamScore"))
        t = espn.parse_time(e.get("gameDate"))
        top = None
        if e.get("id"):
            try:
                ps = box_players(summary(str(e["id"]))).get(team["id"]) or []
                if ps:
                    b = max(ps, key=lambda p: to_int(p["stats"].get("PTS")))
                    s = b["stats"]
                    top = {"name": b["short"] or b["name"], "pts": to_int(s.get("PTS")),
                           "reb": to_int(s.get("REB")), "ast": to_int(s.get("AST"))}
            except Exception as ex:
                print("Box error:", e.get("id"), repr(ex))
        rows.append({"date": espn.short_date(t) if t else "", "res": e.get("gameResult") or "-",
                     "atvs": e.get("atVs") or "vs", "opp": (e.get("opponent") or {}).get("abbreviation") or "?",
                     "for": mine, "against": theirs, "top": top})
    n = len(rows) or 1
    w = sum(1 for r in rows if r["res"] == "W")
    return {"rows": rows, "avg_for": sum(r["for"] for r in rows) / n, "avg_against": sum(r["against"] for r in rows) / n,
            "record": f"{w}-{len(rows) - w}"}


# ------------------------------------------------------------- Jugadores ----
def player_overview(athlete_id):
    """
    <summary>Promedios de temporada regular de un jugador (cache 1h).</summary>
    <param name="athlete_id">Id ESPN.</param>
    <returns>{"PTS": "25.2", "REB": …, "GP": …} o {}.</returns>
    """
    def load():
        data = espn.get(f"/{SPORT}/{LG}/athletes/{athlete_id}/overview", base=WEB)
        st = data.get("statistics") or {}
        labels = st.get("labels") or []
        split = next((s for s in st.get("splits") or [] if s.get("displayName") == "Regular Season"), None)
        return dict(zip(labels, split.get("stats") or [])) if split else {}
    return cached(f"ov:{athlete_id}", 3600, load)


def key_players(summ, team, k=3):
    """
    <summary>Jugadores clave del equipo (los líderes de ESPN) con foto y promedios de temporada.</summary>
    <param name="summ">Summary del juego.</param>
    <param name="team">team_info.</param>
    <param name="k">Máximo de jugadores.</param>
    <returns>Lista de {name, short, pos, headshot, avg{…}}.</returns>
    """
    out, seen = [], set()
    for b in summ.get("leaders") or []:
        bt = b.get("team") or {}
        if not (str(bt.get("id")) == team["id"] or bt.get("abbreviation") == team["abbr"]):
            continue
        for cat in b.get("leaders") or []:
            for top in (cat.get("leaders") or [])[:1]:
                ath = top.get("athlete") or {}
                aid = str(ath.get("id") or "")
                if not aid or aid in seen:
                    continue
                seen.add(aid)
                avg = player_overview(aid)
                out.append({"id": aid, "name": ath.get("displayName"), "short": ath.get("shortName") or ath.get("displayName"),
                            "pos": (ath.get("position") or {}).get("abbreviation") or "",
                            "headshot": (ath.get("headshot") or {}).get("href")
                            or f"https://a.espncdn.com/i/headshots/nba/players/full/{aid}.png", "avg": avg})
    out.sort(key=lambda p: -espn_float(p["avg"].get("PTS")))
    return out[:k]


def espn_float(v):
    """
    <summary>displayValue a float (0 si no se puede).</summary>
    <param name="v">Texto.</param>
    <returns>float.</returns>
    """
    try:
        return float(str(v).replace("%", ""))
    except (TypeError, ValueError):
        return 0.0


def roster_index():
    """
    <summary>Índice de todos los jugadores de la NBA (rosters de los 30 equipos, cache 6h).</summary>
    <returns>Lista de {id, name, last, short, pos, jersey, headshot, injury, team}.</returns>
    """
    def load():
        idx = []
        for t in nba_teams():
            data = espn.get(f"/{SPORT}/{LG}/teams/{t.get('id')}/roster")
            for a in data.get("athletes") or []:
                inj = (a.get("injuries") or [{}])[0].get("status") if a.get("injuries") else ""
                idx.append({"id": str(a.get("id")), "name": a.get("displayName") or a.get("fullName") or "",
                            "last": a.get("lastName") or "", "short": a.get("shortName") or "",
                            "pos": (a.get("position") or {}).get("abbreviation") or "", "jersey": a.get("jersey") or "",
                            "headshot": (a.get("headshot") or {}).get("href"), "injury": inj or "", "team": t})
            time.sleep(0.1)
        return idx
    return cached("roster", 6 * 3600, load)


def find_player(query):
    """
    <summary>Busca un jugador por nombre completo, apellido o parte del nombre.</summary>
    <param name="query">Texto, ej. "lebron", "doncic", "jalen brunson".</param>
    <returns>Dict del jugador o None.</returns>
    """
    q = (query or "").strip().lower()
    if len(q) < 3:
        return None
    idx = roster_index()
    for test in (lambda p: p["name"].lower() == q, lambda p: p["last"].lower() == q,
                 lambda p: p["name"].lower().split()[0] == q if p["name"] else False,
                 lambda p: q in p["name"].lower()):
        hits = [p for p in idx if test(p)]
        if hits:
            return hits[0]
    return None


def players_in_text(text, k=2):
    """
    <summary>Encuentra hasta k jugadores mencionados en un texto libre (por apellido o nombre completo).</summary>
    <param name="text">Pregunta.</param>
    <param name="k">Máximo.</param>
    <returns>Lista de jugadores.</returns>
    """
    low = " " + (text or "").lower() + " "
    found = []
    for p in roster_index():
        last, full = p["last"].lower(), p["name"].lower()
        if (full and full in low) or (len(last) >= 4 and f" {last} " in low.replace("?", " ").replace(",", " ")):
            if p not in found:
                found.append(p)
        if len(found) >= k:
            break
    return found


def player_last5(player):
    """
    <summary>Últimos 5 juegos de un jugador con sus stats (game log de ESPN; si falta algo, usa el box score).</summary>
    <param name="player">Dict del jugador (de roster_index).</param>
    <returns>Lista de {date, atvs, opp, res, score, MIN, PTS, REB, AST, FG, 3PT}.</returns>
    """
    gl = espn.get(f"/{SPORT}/{LG}/athletes/{player['id']}/gamelog", base=WEB)
    labels = gl.get("labels") or []
    by_event = {}
    for stype in gl.get("seasonTypes") or []:
        for cat in stype.get("categories") or []:
            for e in cat.get("events") or []:
                if e.get("eventId") and e.get("stats"):
                    by_event[str(e["eventId"])] = dict(zip(labels, e["stats"]))
    events = sorted((gl.get("events") or {}).values(), key=lambda e: e.get("gameDate") or "", reverse=True)
    rows = []
    for e in events:
        eid = str(e.get("id"))
        st = by_event.get(eid)
        if st is None:
            ps = box_players(summary(eid)).get(str(player["team"].get("id"))) or []
            me = next((p for p in ps if p["id"] == player["id"]), None)
            st = me["stats"] if me else None
        if not st:
            continue
        t = espn.parse_time((e.get("gameDate") or "").replace(".000+00:00", "Z"))
        home = str(e.get("homeTeamId")) == str(player["team"].get("id"))
        mine, theirs = (e.get("homeTeamScore"), e.get("awayTeamScore")) if home else (e.get("awayTeamScore"), e.get("homeTeamScore"))
        rows.append({"date": espn.short_date(t) if t else "", "atvs": e.get("atVs") or "vs",
                     "opp": (e.get("opponent") or {}).get("abbreviation") or "?", "res": e.get("gameResult") or "",
                     "score": f"{to_int(mine)}-{to_int(theirs)}" if mine is not None else (e.get("score") or ""), **{k: st.get(k, "-") for k in ("MIN", "PTS", "REB", "AST", "FG", "3PT")}})
        if len(rows) == 5:
            break
    return rows


# ------------------------------------------------------- Líderes equipo ----
CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
LEADER_CATS = [("pointsPerGame", "PTS", "PUNTOS"), ("3PointMadePerGame", "3PM", "TRIPLES"),
               ("reboundsPerGame", "REB", "REBOTES"), ("assistsPerGame", "AST", "ASISTENCIAS")]


def season_year(now=None):
    """
    <summary>Año de temporada NBA según ESPN (la 2026-27 es 2027).</summary>
    <param name="now">Fecha (opcional).</param>
    <returns>int.</returns>
    """
    now = now or dt.datetime.now(espn.TZ)
    return now.year + 1 if now.month >= 9 else now.year


def team_roster(team_id):
    """
    <summary>Roster de un equipo como {athlete_id: {name, short, headshot, pos}} (cache 6h).</summary>
    <param name="team_id">Id del equipo.</param>
    <returns>Dict.</returns>
    """
    def load():
        data = espn.get(f"/{SPORT}/{LG}/teams/{team_id}/roster")
        return {str(a.get("id")): {"name": a.get("displayName"), "short": a.get("shortName") or a.get("displayName"),
                                   "headshot": (a.get("headshot") or {}).get("href"),
                                   "pos": (a.get("position") or {}).get("abbreviation") or ""}
                for a in data.get("athletes") or []}
    return cached(f"roster:{team_id}", 6 * 3600, load)


def team_leaders(team, k=3):
    """
    <summary>Top k del equipo en puntos, triples, rebotes y asistencias por juego (temporada actual).</summary>
    <param name="team">team_info o equipo de ESPN (necesita id).</param>
    <param name="k">Jugadores por categoría.</param>
    <returns>{"PTS": [{name, short, headshot, pos, value}], "3PM": [...], "REB": [...], "AST": [...]}.</returns>
    """
    tid = str(team.get("id"))

    def load():
        data = {}
        for year in (season_year(), season_year() - 1):
            data = espn.get(f"/seasons/{year}/types/2/teams/{tid}/leaders", base=CORE)
            if data.get("categories"):
                break
        roster = team_roster(tid)
        out = {}
        for name, key, _ in LEADER_CATS:
            cat = next((c for c in data.get("categories") or [] if c.get("name") == name), None)
            rows = []
            for item in (cat or {}).get("leaders") or []:
                ref = (item.get("athlete") or {}).get("$ref") or ""
                aid = ref.split("/athletes/")[-1].split("?")[0] if "/athletes/" in ref else ""
                p = roster.get(aid)
                if not p:
                    continue  # jugador que ya no está en el roster
                rows.append({**p, "id": aid, "value": item.get("displayValue")})
                if len(rows) == k:
                    break
            out[key] = rows
        return out
    return cached(f"leaders:{tid}", 1800, load)
