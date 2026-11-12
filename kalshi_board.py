"""
<summary>
Kalshi: lee mercados de deportes abiertos (API pública, sin login), mantiene el
"Live Board" que se edita solo y manda alertas cuando un mercado se mueve fuerte.
</summary>
<remarks>
Archivo: kalshi_board.py
Autor: Yael Rosado
Propósito: Parte Kalshi del bot. KALSHI_MOCK_FILE=ruta.json usa data falsa.
</remarks>
"""

import datetime as dt
import html
import json
import os
import time
from zoneinfo import ZoneInfo

import requests

import notify

# ---------------------------------------------------------------- Config ----
API = os.getenv("KALSHI_API", "https://api.elections.kalshi.com/trade-api/v2")
MOCK_FILE = os.getenv("KALSHI_MOCK_FILE")
TZ = ZoneInfo("America/Puerto_Rico")

SAFE_MIN, SAFE_MAX = 85, 95   # rango de precio (¢) para "alta probabilidad"
SAFE_MAX_HOURS = 48           # solo mercados que cierran pronto
MIN_VOL_24H = 500             # liquidez mínima (contratos en 24h)
MAX_SPREAD = 3                # spread máximo bid/ask en ¢
MOVE_ALERT = 10               # ¢ de movimiento entre corridas para alertar
ALERT_COOLDOWN_MIN = 120      # no repetir alerta del mismo mercado antes de esto
MAX_ALERTS_PER_RUN = 5
MAX_PAGES = 40
SERIES_PREFIXES = ("KXNBA",)  # solo NBA (juegos, spreads, totales, props, campeón)
TOP_N = 5

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DISCLAIMER = "No es consejo financiero. Ningún bet es seguro. Precios antes de fees."


# ---------------------------------------------------------- Kalshi data ----
def cents(m, key):
    """
    <summary>Lee un precio en centavos aceptando el formato viejo (int ¢) o nuevo (*_dollars).</summary>
    <param name="m">Diccionario del mercado.</param>
    <param name="key">Campo base, ej. "yes_bid".</param>
    <returns>Precio en centavos (int) o None.</returns>
    """
    v = m.get(f"{key}_dollars")
    if v not in (None, ""):
        try:
            return round(float(v) * 100)
        except (TypeError, ValueError):
            pass
    v = m.get(key)
    return int(v) if isinstance(v, (int, float)) else None


def num(m, key):
    """
    <summary>Lee un número aceptando el campo normal o su versión *_fp (string).</summary>
    <param name="m">Diccionario del mercado.</param>
    <param name="key">Nombre del campo, ej. "volume_24h".</param>
    <returns>Valor float (0.0 si no existe).</returns>
    """
    for k in (key, f"{key}_fp"):
        v = m.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def kalshi_get(path, params):
    """
    <summary>GET a la API de Kalshi con reintentos para 429/5xx.</summary>
    <param name="path">Ruta del endpoint, ej. "/events".</param>
    <param name="params">Query params.</param>
    <returns>JSON de la respuesta.</returns>
    """
    for attempt in range(5):
        r = requests.get(API + path, params=params, timeout=30)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def fetch_events():
    """
    <summary>Baja todos los eventos abiertos con sus mercados (o la data mock).</summary>
    <returns>Lista de eventos.</returns>
    """
    if MOCK_FILE:
        with open(MOCK_FILE, encoding="utf-8") as f:
            return json.load(f)["events"]
    events, cursor = [], None
    for _ in range(MAX_PAGES):
        params = {"status": "open", "with_nested_markets": "true", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = kalshi_get("/events", params)
        events += data.get("events") or []
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(0.15)
    return events


def parse_time(s):
    """
    <summary>Convierte un timestamp ISO de Kalshi a datetime UTC.</summary>
    <param name="s">Texto ISO-8601.</param>
    <returns>datetime con zona UTC, o None.</returns>
    """
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def sports_markets(events, now):
    """
    <summary>Filtra los mercados de deportes activos y los normaliza.</summary>
    <param name="events">Eventos crudos de Kalshi.</param>
    <param name="now">Hora actual (UTC).</param>
    <returns>Lista de mercados normalizados.</returns>
    """
    out = []
    for ev in events:
        if (ev.get("category") or "").lower() != "sports":
            continue
        series = ev.get("series_ticker") or ""
        if not series.upper().startswith(SERIES_PREFIXES):  # solo NBA; deja fuera parlays y otras ligas
            continue
        for m in ev.get("markets") or []:
            if m.get("status") not in ("active", "open", None):
                continue
            close = parse_time(m.get("close_time"))
            if not close or close <= now:
                continue
            yb, ya, last = cents(m, "yes_bid"), cents(m, "yes_ask"), cents(m, "last_price")
            mid = (yb + ya) / 2 if yb and ya else last
            side = m.get("yes_sub_title") or m.get("subtitle") or m.get("title") or ""
            title = ev.get("title") or m.get("title") or m.get("ticker")
            name = f"{title} · {side}" if side and side.strip().lower() != title.strip().lower() else title
            out.append({
                "ticker": m.get("ticker"),
                "event": ev.get("event_ticker") or m.get("event_ticker") or m.get("ticker"),
                "name": name,
                "url": f"https://kalshi.com/markets/{series.lower()}" if series else "https://kalshi.com/sports/basketball",
                "yes_bid": yb, "yes_ask": ya, "last": last, "mid": mid,
                "vol24": num(m, "volume_24h"),
                "close": close,
                "hours": (close - now).total_seconds() / 3600,
            })
    return out


# ---------------------------------------------------------------- Picks ----
def one_per_event(items, n=TOP_N):
    """
    <summary>Deja solo el primer mercado de cada evento (evita repetir el mismo juego).</summary>
    <param name="items">Lista ya ordenada.</param>
    <param name="n">Máximo a devolver.</param>
    <returns>Lista sin eventos repetidos.</returns>
    """
    seen, out = set(), []
    for it in items:
        if it["event"] in seen:
            continue
        seen.add(it["event"])
        out.append(it)
        if len(out) == n:
            break
    return out


def high_prob(markets):
    """
    <summary>Busca lados (YES o NO) entre SAFE_MIN y SAFE_MAX ¢, líquidos, con spread corto y que cierran pronto.</summary>
    <param name="markets">Mercados normalizados.</param>
    <returns>Top picks ordenados por precio y volumen.</returns>
    """
    picks = []
    for m in markets:
        yb, ya = m["yes_bid"], m["yes_ask"]
        if not yb or not ya or ya - yb > MAX_SPREAD:
            continue
        if m["vol24"] < MIN_VOL_24H or m["hours"] > SAFE_MAX_HOURS:
            continue
        for side, cost in (("YES", ya), ("NO", 100 - yb)):
            if SAFE_MIN <= cost <= SAFE_MAX:
                picks.append({**m, "side": side, "cost": cost,
                              "profit10": 10 * (100 - cost) / cost})
    picks.sort(key=lambda p: (-p["cost"], -p["vol24"]))
    return one_per_event(picks)


def hot(markets):
    """
    <summary>Mercados con más volumen en 24h.</summary>
    <param name="markets">Mercados normalizados.</param>
    <returns>Top TOP_N por volumen.</returns>
    """
    return one_per_event(sorted((m for m in markets if m["vol24"] > 0), key=lambda m: (-m["vol24"], -(m["mid"] or 0))))


def closing_soon(markets):
    """
    <summary>Mercados líquidos que cierran en las próximas 6 horas.</summary>
    <param name="markets">Mercados normalizados.</param>
    <returns>Top TOP_N por volumen.</returns>
    """
    soon = [m for m in markets if m["hours"] <= 6 and m["vol24"] >= MIN_VOL_24H]
    return one_per_event(sorted(soon, key=lambda m: (-m["vol24"], -(m["mid"] or 0))))


def movers(markets, baseline):
    """
    <summary>Mercados que más se han movido desde el inicio del día.</summary>
    <param name="markets">Mercados normalizados.</param>
    <param name="baseline">Precios mid al inicio del día {ticker: mid}.</param>
    <returns>Top TOP_N por cambio absoluto.</returns>
    """
    out = []
    for m in markets:
        old = baseline.get(m["ticker"])
        if old is None or m["mid"] is None or m["vol24"] < MIN_VOL_24H:
            continue
        delta = m["mid"] - old
        if abs(delta) >= 3:
            out.append({**m, "from": old, "delta": delta})
    return one_per_event(sorted(out, key=lambda m: -abs(m["delta"])))


# ------------------------------------------------------------ Formatting ----
def fmt_when(hours):
    """
    <summary>Texto corto de tiempo restante.</summary>
    <param name="hours">Horas hasta el cierre.</param>
    <returns>Ej. "45m", "5h", "2d".</returns>
    """
    if hours < 1:
        return f"{max(1, int(hours * 60))}m"
    if hours < 48:
        return f"{int(hours)}h"
    return f"{int(hours // 24)}d"


def fmt_vol(v):
    """
    <summary>Formatea volumen: 1234 -> 1.2K.</summary>
    <param name="v">Volumen.</param>
    <returns>Texto corto.</returns>
    """
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return str(int(v))


def fmt_stamp(now):
    """
    <summary>Fecha/hora en hora de PR, ej. "12 nov, 3:45 PM".</summary>
    <param name="now">Hora UTC.</param>
    <returns>Texto.</returns>
    """
    t = now.astimezone(TZ)
    return f"{t.day} {MESES[t.month - 1]}, {t.strftime('%I:%M %p').lstrip('0')}"


def price_txt(m):
    """
    <summary>Precio YES visible (mid o last).</summary>
    <param name="m">Mercado.</param>
    <returns>Ej. "62¢".</returns>
    """
    return f"{round(m['mid'])}¢" if m["mid"] is not None else "—"


def build_sections(markets, baseline):
    """
    <summary>Arma las secciones del board como líneas neutrales (luego se pintan por plataforma).</summary>
    <param name="markets">Mercados normalizados.</param>
    <param name="baseline">Precios al inicio del día.</param>
    <returns>Lista de (emoji, título, [(nombre, url, detalle)]).</returns>
    """
    s = []
    s.append(("🔒", "ALTA PROBABILIDAD", [
        (p["name"], p["url"],
         f"**{p['side']} @ {p['cost']}¢** · $10 → +${p['profit10']:.2f} · cierra en {fmt_when(p['hours'])}")
        for p in high_prob(markets)]))
    s.append(("🔥", "MÁS ACTIVOS (24h)", [
        (m["name"], m["url"], f"YES {price_txt(m)} · Vol {fmt_vol(m['vol24'])}") for m in hot(markets)]))
    s.append(("📈", "MOVERS HOY", [
        (m["name"], m["url"],
         f"{round(m['from'])}¢ → **{round(m['mid'])}¢** ({'+' if m['delta'] > 0 else ''}{round(m['delta'])}¢)")
        for m in movers(markets, baseline)]))
    s.append(("⏰", "CIERRAN PRONTO", [
        (m["name"], m["url"], f"YES {price_txt(m)} · en {fmt_when(m['hours'])} · Vol {fmt_vol(m['vol24'])}")
        for m in closing_soon(markets)]))
    return s


def md_to_tg(text):
    """
    <summary>Convierte **bold** a HTML de Telegram escapando el resto.</summary>
    <param name="text">Texto con **bold**.</param>
    <returns>HTML seguro.</returns>
    """
    parts = text.split("**")
    return "".join(f"<b>{html.escape(p)}</b>" if i % 2 else html.escape(p) for i, p in enumerate(parts))


def render_telegram(sections, now):
    """
    <summary>Pinta el board en HTML de Telegram (máx 4096 chars).</summary>
    <param name="sections">Secciones de build_sections.</param>
    <param name="now">Hora UTC.</param>
    <returns>Texto HTML.</returns>
    """
    out = ["🏀 <b>KALSHI NBA · LIVE BOARD</b>",
           f"<i>🔄 Actualizado {fmt_stamp(now)} (PR)</i>", ""]
    for emoji, title, rows in sections:
        out.append(f"{emoji} <b>{title}</b>")
        if not rows:
            out.append("<i>Nada por ahora</i>")
        for i, (name, url, detail) in enumerate(rows, 1):
            out.append(f"{i}. <a href=\"{html.escape(url)}\">{html.escape(name)}</a>")
            out.append(f"     {md_to_tg(detail)}")
        out.append("")
    out.append(f"⚠️ <i>{DISCLAIMER}</i>")
    text = "\n".join(out)
    return text if len(text) <= 4096 else text[:4000] + "\n…"


def dc_escape(s):
    """
    <summary>Escapa caracteres que rompen links markdown en Discord.</summary>
    <param name="s">Texto.</param>
    <returns>Texto seguro.</returns>
    """
    return s.replace("[", "(").replace("]", ")").replace("*", "").replace("_", " ")


def render_discord(sections, now):
    """
    <summary>Pinta el board como embeds de Discord (límite 6000 chars totales).</summary>
    <param name="sections">Secciones de build_sections.</param>
    <param name="now">Hora UTC.</param>
    <returns>Payload JSON para el webhook.</returns>
    """
    colors = [0x22C55E, 0xF97316, 0x3B82F6, 0xEAB308]
    embeds = [{
        "title": "🏀 KALSHI NBA · LIVE BOARD",
        "description": f"🔄 Se actualiza solo · último update **{fmt_stamp(now)}** (PR)",
        "color": 0x111827,
    }]
    budget = 5500 - len(embeds[0]["title"]) - len(embeds[0]["description"])
    for (emoji, title, rows), color in zip(sections, colors):
        lines = [f"`{i}` [{dc_escape(n)}]({u})\n┗ {d}" for i, (n, u, d) in enumerate(rows, 1)] or ["*Nada por ahora*"]
        desc = ""
        for ln in lines:
            if len(desc) + len(ln) + 1 > min(1800, budget - len(title) - 10):
                break
            desc += ln + "\n"
        budget -= len(desc) + len(title) + 10
        embeds.append({"title": f"{emoji} {title}", "description": desc.strip() or "*Nada por ahora*", "color": color})
    embeds[-1]["footer"] = {"text": f"⚠️ {DISCLAIMER}"}
    embeds[-1]["timestamp"] = now.isoformat()
    return {"username": "Kalshi Sports Bot", "embeds": embeds, "allowed_mentions": {"parse": []}}


def find_alerts(markets, state, now):
    """
    <summary>Detecta mercados que se movieron ≥ MOVE_ALERT ¢ desde la corrida anterior.</summary>
    <param name="markets">Mercados normalizados.</param>
    <param name="state">Estado guardado (last_mid, alerted).</param>
    <param name="now">Hora UTC.</param>
    <returns>Lista de alertas.</returns>
    """
    last, alerted, out = state.get("last_mid", {}), state.get("alerted", {}), []
    for m in markets:
        old = last.get(m["ticker"])
        if old is None or m["mid"] is None or m["vol24"] < MIN_VOL_24H:
            continue
        delta = m["mid"] - old
        prev = alerted.get(m["ticker"])
        if prev and (now - dt.datetime.fromisoformat(prev)).total_seconds() < ALERT_COOLDOWN_MIN * 60:
            continue
        if abs(delta) >= MOVE_ALERT:
            out.append({**m, "from": old, "delta": delta})
    out.sort(key=lambda a: (-abs(a["delta"]), -a["mid"]))
    return one_per_event(out, MAX_ALERTS_PER_RUN)


# ------------------------------------------------------------- Posting ----
def post_board(sections, now, state, new_day):
    """
    <summary>Edita el board de hoy en Telegram y Discord, o postea uno nuevo si es día nuevo.</summary>
    <param name="sections">Secciones de build_sections.</param>
    <param name="now">Hora UTC.</param>
    <param name="state">Estado (guarda tg_board_id y dc_board_id).</param>
    <param name="new_day">True si cambió el día.</param>
    """
    text, payload = render_telegram(sections, now), render_discord(sections, now)
    if notify.DRY_RUN:
        print("\n===== BOARD TELEGRAM =====\n" + text)
        print("===== BOARD DISCORD =====\n" + json.dumps(payload, ensure_ascii=False)[:1200])
        return
    if notify.has_tg():
        base = {"chat_id": notify.TG_CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        mid = state.get("tg_board_id")
        res = notify.tg("editMessageText", {**base, "message_id": mid}) if mid and not new_day else {}
        if not (res.get("ok") or "not modified" in res.get("description", "")):
            res = notify.tg("sendMessage", base)
            if res.get("ok"):
                state["tg_board_id"] = res["result"]["message_id"]
                notify.tg("pinChatMessage", {"chat_id": notify.TG_CHAT, "message_id": state["tg_board_id"],
                                             "disable_notification": True})
    if notify.has_dc():
        mid = state.get("dc_board_id")
        r = notify.dc(payload, message_id=mid) if mid and not new_day else None
        if r is None or not r.ok:
            r = notify.dc(payload, wait=True)
            if r.ok:
                state["dc_board_id"] = r.json()["id"]


def post_alert(a):
    """
    <summary>Manda una alerta de movimiento a Telegram y Discord.</summary>
    <param name="a">Alerta (mercado + from + delta).</param>
    """
    up = a["delta"] > 0
    arrow, sign = ("🟢⬆️", "+") if up else ("🔴⬇️", "")
    move = f"{round(a['from'])}¢ → {round(a['mid'])}¢ ({sign}{round(a['delta'])}¢)"
    tg_text = (f"🚨 <b>MOVIMIENTO FUERTE</b> {arrow}\n<a href=\"{html.escape(a['url'])}\">{html.escape(a['name'])}</a>\n"
               f"YES <b>{move}</b>\nVol 24h {fmt_vol(a['vol24'])} · cierra en {fmt_when(a['hours'])}")
    dc_payload = {"embeds": [{
        "title": f"🚨 MOVIMIENTO FUERTE {arrow}", "url": a["url"],
        "description": f"**{dc_escape(a['name'])}**\nYES **{move}**\nVol 24h {fmt_vol(a['vol24'])} · cierra en {fmt_when(a['hours'])}",
        "color": 0x22C55E if up else 0xEF4444}]}
    notify.post(tg_text, dc_payload)


def run(state, now):
    """
    <summary>Corrida Kalshi: baja mercados, actualiza el board, manda alertas y guarda un índice de precios para los previews.</summary>
    <param name="state">Estado compartido (se modifica).</param>
    <param name="now">Hora UTC.</param>
    """
    markets = sports_markets(fetch_events(), now)
    print(f"Kalshi: {len(markets)} mercados NBA abiertos")
    today = now.astimezone(TZ).date().isoformat()
    new_day = state.get("day") != today
    liquid = {m["ticker"]: m["mid"] for m in markets if m["mid"] is not None and m["vol24"] >= MIN_VOL_24H}
    if new_day:
        state["day"], state["baseline"] = today, liquid

    post_board(build_sections(markets, state.get("baseline", {})), now, state, new_day)

    for a in find_alerts(markets, state, now):
        post_alert(a)
        state.setdefault("alerted", {})[a["ticker"]] = now.isoformat()

    cutoff = now - dt.timedelta(minutes=ALERT_COOLDOWN_MIN)
    state["alerted"] = {k: v for k, v in state.get("alerted", {}).items() if dt.datetime.fromisoformat(v) > cutoff}
    state["last_mid"] = liquid
    # Índice compacto para que los previews de ESPN muestren el precio de Kalshi del juego.
    state["kx"] = [{"t": m["name"], "p": round(m["mid"]), "u": m["url"]}
                   for m in markets if m["mid"] is not None and m["vol24"] >= MIN_VOL_24H and m["hours"] <= 72][:600]
