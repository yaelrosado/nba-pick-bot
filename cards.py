"""
<summary>
Tarjetas en imagen (PNG) estilo app deportiva: juegos de hoy, preview de juego y final.
Usa logos y colores de cada equipo, barras comparando stats y probabilidad de Kalshi.
</summary>
<remarks>
Archivo: cards.py
Autor: Yael Rosado
Propósito: Que lo que postea el bot se vea profesional en Telegram y Discord.
Fuente: Inter (paquete fonts-inter en el runner); si no está, usa DejaVu.
</remarks>
"""

import io
import os

import requests
from PIL import Image, ImageDraw, ImageFont

S = 2            # dibuja al doble y reduce al final (bordes suaves)
W = 1080         # ancho final en px
BG = "#EEF1F5"
CARD = "#FFFFFF"
INK = "#0F172A"
MUTED = "#64748B"
LINE = "#E2E8F0"
HEADER = "#0B1220"
ACCENT = "#F97316"
WIN, LOSS = "#16A34A", "#DC2626"
GREY_BAR = "#CBD5E1"
FOOTER_TXT = "Data: ESPN · Kalshi   •   No es consejo financiero"

FONT_DIRS = ["/usr/share/fonts/opentype/inter", os.path.join(os.path.dirname(__file__), "fonts")]
FONT_FILES = {"regular": "Inter-Regular.otf", "medium": "Inter-Medium.otf", "semibold": "Inter-SemiBold.otf",
              "bold": "Inter-Bold.otf", "black": "Inter-ExtraBold.otf"}
FALLBACK = {"regular": "DejaVuSans.ttf", "medium": "DejaVuSans.ttf", "semibold": "DejaVuSans-Bold.ttf",
            "bold": "DejaVuSans-Bold.ttf", "black": "DejaVuSans-Bold.ttf"}

STAT_LABELS = {"PTS": "Puntos", "OPP PPG": "Pts permitidos", "FG%": "FG%", "3P%": "3P%", "FT%": "TL%",
               "REB": "Rebotes", "AST": "Asistencias", "BLK": "Tapones", "STL": "Robos", "TO": "Pérdidas"}
LOWER_IS_BETTER = {"OPP PPG", "TO", "PF"}
LEADER_ABBR = {"points": "PTS", "assists": "AST", "rebounds": "REB", "steals": "ROB", "blocks": "TAP"}

_fonts, _logos = {}, {}


# --------------------------------------------------------------- Helpers ----
def U(v):
    """
    <summary>Convierte unidades de diseño (base 1080) a píxeles del lienzo (x S).</summary>
    <param name="v">Valor en unidades.</param>
    <returns>Píxeles enteros.</returns>
    """
    return int(round(v * S))


def font(weight, size):
    """
    <summary>Carga una fuente (Inter o DejaVu) con cache.</summary>
    <param name="weight">regular, medium, semibold, bold o black.</param>
    <param name="size">Tamaño en unidades de diseño.</param>
    <returns>ImageFont.</returns>
    """
    key = (weight, size)
    if key not in _fonts:
        f = None
        for d in FONT_DIRS:
            p = os.path.join(d, FONT_FILES[weight])
            if os.path.exists(p):
                f = ImageFont.truetype(p, U(size))
                break
        if f is None:
            try:
                f = ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{FALLBACK[weight]}", U(size))
            except OSError:
                f = ImageFont.load_default(U(size))
        _fonts[key] = f
    return _fonts[key]


def hex_rgb(h, default="#334155"):
    """
    <summary>Convierte "1D428A" o "#1D428A" a tupla RGB.</summary>
    <param name="h">Color hex.</param>
    <param name="default">Color si viene vacío o malo.</param>
    <returns>(r, g, b).</returns>
    """
    h = (h or default).lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return hex_rgb(default)


def team_color(t):
    """
    <summary>Color principal del equipo, usando el alterno si el principal es muy claro.</summary>
    <param name="t">team_info.</param>
    <returns>(r, g, b).</returns>
    """
    for c in (t.get("color"), t.get("alt")):
        rgb = hex_rgb(c, "#334155")
        lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255
        if c and lum < 0.8:
            return rgb
    return hex_rgb("#334155")


def pair_colors(a, h):
    """
    <summary>Colores para visitante y local; si se parecen mucho, el local usa su color alterno (o gris oscuro).</summary>
    <param name="a">team_info visitante.</param>
    <param name="h">team_info local.</param>
    <returns>(color visitante, color local).</returns>
    """
    ca, ch = team_color(a), team_color(h)
    close = lambda x, y: sum(abs(i - j) for i, j in zip(x, y)) < 120
    if close(ca, ch):
        alt = hex_rgb(h.get("alt"), "#475569")
        lum = (0.299 * alt[0] + 0.587 * alt[1] + 0.114 * alt[2]) / 255
        ch = alt if (h.get("alt") and lum < 0.8 and not close(ca, alt)) else hex_rgb("#475569")
        if close(ca, ch):
            ch = hex_rgb(ACCENT)
    return ca, ch


def logo(t, size):
    """
    <summary>Logo del equipo (descargado de ESPN, con cache). Si falla, un círculo con la abreviatura.</summary>
    <param name="t">team_info.</param>
    <param name="size">Tamaño en unidades.</param>
    <returns>Imagen RGBA.</returns>
    """
    url = t.get("logo") or f"https://a.espncdn.com/i/teamlogos/nba/500/{(t.get('abbr') or '').lower()}.png"
    if url not in _logos:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            _logos[url] = Image.open(io.BytesIO(r.content)).convert("RGBA")
        except Exception:
            _logos[url] = None
    src, px = _logos[url], U(size)
    if src is None:
        im = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse([0, 0, px - 1, px - 1], fill=team_color(t))
        d.text((px / 2, px / 2), t.get("abbr", "?"), font=font("black", size * 0.3), fill="white", anchor="mm")
        return im
    im = src.copy()
    im.thumbnail((px, px), Image.LANCZOS)
    return im


def headshot(url, size, fallback_text="?"):
    """
    <summary>Foto del jugador recortada en círculo (cache). Si falla, un círculo gris con iniciales.</summary>
    <param name="url">URL del headshot de ESPN.</param>
    <param name="size">Tamaño en unidades.</param>
    <param name="fallback_text">Iniciales si no hay foto.</param>
    <returns>Imagen RGBA.</returns>
    """
    px = U(size)
    if url and url not in _logos:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            _logos[url] = Image.open(io.BytesIO(r.content)).convert("RGBA")
        except Exception:
            _logos[url] = None
    src = _logos.get(url) if url else None
    base = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, px - 1, px - 1], fill=255)
    bg = Image.new("RGBA", (px, px), hex_rgb("#E2E8F0") + (255,))
    if src is not None:
        w, h = src.size
        side = min(w, h)
        crop = src.crop(((w - side) // 2, 0, (w + side) // 2, side)).resize((px, px), Image.LANCZOS)
        bg.alpha_composite(crop)
    else:
        ImageDraw.Draw(bg).text((px / 2, px / 2), fallback_text, font=font("black", size * 0.32), fill=MUTED, anchor="mm")
    base.paste(bg, (0, 0), mask)
    return base


def initials(name):
    """
    <summary>Iniciales de un nombre, ej. "Cooper Flagg" -> "CF".</summary>
    <param name="name">Nombre.</param>
    <returns>Texto.</returns>
    """
    return "".join(w[0] for w in str(name or "?").replace(".", " ").split()[:2]).upper() or "?"


def fit(d, text, f, maxw):
    """
    <summary>Corta texto con "…" para que quepa en maxw.</summary>
    <param name="d">ImageDraw.</param>
    <param name="text">Texto.</param>
    <param name="f">Fuente.</param>
    <param name="maxw">Ancho máximo en unidades.</param>
    <returns>Texto que cabe.</returns>
    """
    text = str(text or "")
    if d.textlength(text, font=f) <= U(maxw):
        return text
    while text and d.textlength(text + "…", font=f) > U(maxw):
        text = text[:-1]
    return text.rstrip() + "…"


def wrap(d, text, f, maxw, max_lines=3):
    """
    <summary>Parte texto en líneas que quepan en maxw.</summary>
    <param name="d">ImageDraw.</param>
    <param name="text">Texto.</param>
    <param name="f">Fuente.</param>
    <param name="maxw">Ancho máximo en unidades.</param>
    <param name="max_lines">Máximo de líneas.</param>
    <returns>Lista de líneas.</returns>
    """
    lines, cur = [], ""
    for word in str(text).split():
        test = f"{cur} {word}".strip()
        if d.textlength(test, font=f) <= U(maxw):
            cur = test
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = fit(d, lines[-1] + " …", f, maxw)
    return lines


def num(v):
    """
    <summary>Convierte un displayValue ("48.3", "47%") a float.</summary>
    <param name="v">Texto.</param>
    <returns>float o None.</returns>
    """
    try:
        return float(str(v).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


class Card:
    """
    <summary>Lienzo alto que se recorta al final; las secciones se dibujan de arriba hacia abajo.</summary>
    """

    def __init__(self, max_h=4400):
        """
        <summary>Crea el lienzo.</summary>
        <param name="max_h">Altura máxima en unidades.</param>
        """
        self.img = Image.new("RGB", (U(W), U(max_h)), BG)
        self.d = ImageDraw.Draw(self.img)
        self.y = 0

    def t(self, x, y, s, f, fill=INK, anchor="la"):
        """
        <summary>Dibuja texto en unidades de diseño.</summary>
        <param name="x">X.</param>
        <param name="y">Y.</param>
        <param name="s">Texto.</param>
        <param name="f">Fuente.</param>
        <param name="fill">Color.</param>
        <param name="anchor">Ancla de PIL (la, mm, rm, lm…).</param>
        """
        if isinstance(fill, tuple) and (0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]) / 255 > 0.55:
            fill = tuple(int(v * 0.62) for v in fill)  # colores claros (ej. amarillo) se oscurecen para que se lean
        self.d.text((U(x), U(y)), str(s), font=f, fill=fill, anchor=anchor)

    def rr(self, x0, y0, x1, y1, r, fill=None, outline=None, width=1):
        """
        <summary>Rectángulo redondeado en unidades de diseño.</summary>
        <param name="x0">Izquierda.</param>
        <param name="y0">Arriba.</param>
        <param name="x1">Derecha.</param>
        <param name="y1">Abajo.</param>
        <param name="r">Radio.</param>
        <param name="fill">Relleno.</param>
        <param name="outline">Borde.</param>
        <param name="width">Grosor del borde.</param>
        """
        if x1 - x0 < 1:
            return
        self.d.rounded_rectangle([U(x0), U(y0), U(x1), U(y1)], radius=U(min(r, (x1 - x0) / 2, (y1 - y0) / 2)),
                                 fill=fill, outline=outline, width=U(width) if outline else 0)

    def paste(self, im, cx, cy):
        """
        <summary>Pega una imagen RGBA centrada en (cx, cy).</summary>
        <param name="im">Imagen.</param>
        <param name="cx">Centro X.</param>
        <param name="cy">Centro Y.</param>
        """
        self.img.paste(im, (U(cx) - im.width // 2, U(cy) - im.height // 2), im)

    def header(self, title, right):
        """
        <summary>Barra superior oscura con etiqueta NBA, título y fecha.</summary>
        <param name="title">Título, ej. "PREVIEW".</param>
        <param name="right">Texto a la derecha (fecha/hora).</param>
        """
        self.d.rectangle([0, 0, U(W), U(112)], fill=HEADER)
        self.rr(40, 36, 122, 76, 10, fill=ACCENT)
        self.t(81, 56, "NBA", font("black", 24), "white", "mm")
        self.t(142, 56, title, font("black", 32), "white", "lm")
        self.t(W - 40, 56, right, font("medium", 24), "#94A3B8", "rm")
        self.y = 112 + 24

    def section(self, h, title=None):
        """
        <summary>Dibuja el fondo blanco de una sección y devuelve dónde empieza su contenido.</summary>
        <param name="h">Altura de la sección.</param>
        <param name="title">Título opcional en mayúsculas.</param>
        <returns>Y donde empieza el contenido.</returns>
        """
        y0 = self.y
        self.rr(28, y0, W - 28, y0 + h, 22, fill=CARD, outline=LINE, width=1)
        self.y = y0 + h + 18
        if title:
            self.t(60, y0 + 38, title, font("bold", 20), MUTED, "lm")
            return y0 + 70
        return y0

    def footer(self):
        """
        <summary>Pie con fuentes y aviso.</summary>
        """
        self.t(W / 2, self.y + 22, FOOTER_TXT, font("medium", 20), MUTED, "mm")
        self.y += 56

    def png(self):
        """
        <summary>Recorta a la altura usada, reduce a 1080 px y devuelve los bytes PNG.</summary>
        <returns>bytes.</returns>
        """
        im = self.img.crop((0, 0, U(W), U(self.y))).resize((W, int(self.y)), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "PNG", optimize=True)
        return out.getvalue()


# --------------------------------------------------------- Data helpers ----
def team_stats(summ, team_id):
    """
    <summary>Stats de temporada de un equipo, por abreviatura.</summary>
    <param name="summ">Summary de ESPN.</param>
    <param name="team_id">Id.</param>
    <returns>{abbr: displayValue}.</returns>
    """
    for t in (summ.get("boxscore") or {}).get("teams") or []:
        if str((t.get("team") or {}).get("id")) == team_id:
            return {(s.get("abbreviation") or s.get("label") or s.get("name")): s.get("displayValue")
                    for s in t.get("statistics") or []}
    return {}


def team_leaders(summ, team, k=3):
    """
    <summary>Líderes del equipo: [(PTS, nombre, valor)].</summary>
    <param name="summ">Summary.</param>
    <param name="team">team_info.</param>
    <param name="k">Máximo.</param>
    <returns>Lista de tuplas.</returns>
    """
    for b in summ.get("leaders") or []:
        bt = b.get("team") or {}
        if str(bt.get("id")) == team["id"] or bt.get("abbreviation") == team["abbr"]:
            out = []
            for cat in (b.get("leaders") or [])[:k]:
                top = (cat.get("leaders") or [{}])[0]
                ath = top.get("athlete") or {}
                name = ath.get("shortName") or ath.get("displayName")
                if name:
                    key = (cat.get("name") or cat.get("displayName") or "").lower()
                    out.append((LEADER_ABBR.get(key, (cat.get("displayName") or "")[:3].upper()), name,
                                top.get("displayValue", "")))
            return out
    return []


def team_injuries(summ, team):
    """
    <summary>Jugadores fuera o en duda del equipo.</summary>
    <param name="summ">Summary.</param>
    <param name="team">team_info.</param>
    <returns>(fuera, en duda) como listas de nombres.</returns>
    """
    for b in summ.get("injuries") or []:
        bt = b.get("team") or {}
        if str(bt.get("id")) == team["id"] or bt.get("abbreviation") == team["abbr"]:
            out, doubt = [], []
            for i in b.get("injuries") or []:
                n = (i.get("athlete") or {}).get("shortName") or (i.get("athlete") or {}).get("displayName")
                if i.get("status") == "Out":
                    out.append(n)
                elif i.get("status") in ("Doubtful", "Questionable"):
                    doubt.append(n)
            return out, doubt
    return [], []


# ---------------------------------------------------------------- Cards ----
def matchup_card(kind, g, summ, info):
    """
    <summary>Tarjeta completa de un juego: matchup, Kalshi, forma, stats, líderes, lesiones y (opcional) matchups anteriores.</summary>
    <param name="kind">Título del header, ej. "JUEGOS DE HOY" o "PREVIEW".</param>
    <param name="g">Juego normalizado (espn.game_info).</param>
    <param name="summ">Summary de ESPN.</param>
    <param name="info">Dict con when, sub, date, kalshi {away, home}, odds, h2h, form {id: (lista, racha)}.</param>
    <returns>bytes PNG.</returns>
    """
    a, h = g["away"], g["home"]
    ca, ch = pair_colors(a, h)
    c = Card()
    c.header(kind, info.get("date", ""))

    # --- Matchup
    kx = info.get("kalshi") or {}
    has_kx = kx.get("away") is not None and kx.get("home") is not None
    top_h = 340 + (104 if has_kx else 0) + (46 if info.get("odds") else 0)
    y = c.section(top_h)
    for t, cx in ((a, 280), (h, 800)):
        c.paste(logo(t, 170), cx, y + 125)
        c.t(cx, y + 255, fit(c.d, t.get("short") or t.get("name"), font("black", 40), 400), font("black", 40), INK, "mm")
        st = info.get("form", {}).get(t["id"], ([], ""))[1]
        meta = " · ".join(x for x in (t.get("record"), (f"Racha {st}" if st else "")) if x)
        c.t(cx, y + 298, meta, font("medium", 24), MUTED, "mm")
        if t.get("rank"):
            c.rr(cx - 34, y + 22, cx + 34, y + 52, 15, fill=INK)
            c.t(cx, y + 37, f"#{t['rank']}", font("bold", 18), "white", "mm")
    c.t(W / 2, y + 95, "@", font("bold", 30), MUTED, "mm")
    c.t(W / 2, y + 140, info.get("when", ""), font("black", 34), INK, "mm")
    c.t(W / 2, y + 180, fit(c.d, info.get("sub", ""), font("medium", 20), 220), font("medium", 20), MUTED, "mm")
    yy = y + 350
    if has_kx:
        pa, ph = kx["away"], kx["home"]
        tot = max(1, pa + ph)
        c.t(W / 2, yy, "PROBABILIDAD EN KALSHI", font("bold", 18), MUTED, "mm")
        x0, x1 = 64, W - 64
        split = x0 + (x1 - x0) * pa / tot
        c.rr(x0, yy + 24, x1, yy + 76, 26, fill=ch)
        c.rr(x0, yy + 24, split, yy + 76, 26, fill=ca)
        if split - x0 > 30:
            c.d.rectangle([U(max(x0, split - 26)), U(yy + 24), U(split), U(yy + 76)], fill=ca)
        c.t(x0 + 22, yy + 50, f"{a['abbr']}  {pa}¢", font("black", 26), "white", "lm")
        c.t(x1 - 22, yy + 50, f"{ph}¢  {h['abbr']}", font("black", 26), "white", "rm")
        yy += 100
    if info.get("odds"):
        c.t(W / 2, yy + 8, fit(c.d, info["odds"], font("semibold", 22), 900), font("semibold", 22), MUTED, "mm")

    # --- Últimos 5 (con mejor jugador de cada juego)
    for t, col in ((a, ca), (h, ch)):
        d5 = (info.get("last5") or {}).get(t["id"])
        if not d5 or not d5.get("rows"):
            continue
        rows5 = d5["rows"][:5]
        y = c.section(70 + len(rows5) * 52 + 12, f"ÚLTIMOS 5 · {t['abbr']}")
        c.t(W - 60, y - 32, f"{d5['record']}  ·  {d5['avg_for']:.1f} anotados  ·  {d5['avg_against']:.1f} permitidos",
            font("semibold", 20), MUTED, "rm")
        for i, r in enumerate(rows5):
            cy = y + 22 + i * 52
            c.t(60, cy, r["date"], font("medium", 20), MUTED, "lm")
            c.t(180, cy, f"{'vs' if r['atvs'] == 'vs' else '@'} {r['opp']}", font("bold", 23), INK, "lm")
            chip = WIN if r["res"] == "W" else LOSS if r["res"] == "L" else GREY_BAR
            c.rr(300, cy - 17, 344, cy + 17, 9, fill=chip)
            c.t(322, cy, r["res"], font("black", 20), "white", "mm")
            c.t(362, cy, f"{r['for']}-{r['against']}", font("black", 24), INK, "lm")
            tp = r.get("top")
            if tp:
                name = fit(c.d, tp["name"], font("semibold", 22), 210)
                c.t(500, cy, name, font("semibold", 22), INK, "lm")
                c.t(W - 60, cy, f"{tp['pts']} PTS · {tp['reb']} REB · {tp['ast']} AST", font("bold", 20), col, "rm")
            if i < len(rows5) - 1:
                c.d.line([U(60), U(cy + 26), U(W - 60), U(cy + 26)], fill=LINE, width=U(1))

    # --- Stats
    sa, sh = team_stats(summ, a["id"]), team_stats(summ, h["id"])
    rows = []
    for key, label in STAT_LABELS.items():
        va, vh = num(sa.get(key)), num(sh.get(key))
        if va is not None and vh is not None:
            rows.append((key, label, sa[key], sh[key], va, vh))
    rows = rows[:7]
    if rows:
        y = c.section(70 + 40 + len(rows) * 58 + 10, "STATS DE TEMPORADA")
        c.t(60, y + 14, a["abbr"], font("black", 24), ca, "lm")
        c.t(W - 60, y + 14, h["abbr"], font("black", 24), ch, "rm")
        y += 44
        for key, label, ta, th, va, vh in rows:
            cy = y + 29
            low = key in LOWER_IS_BETTER
            a_best = (va < vh) if low else (va > vh)
            h_best = (vh < va) if low else (vh > va)
            top = max(abs(va), abs(vh)) or 1
            maxlen = 250
            la, lh = maxlen * (0.25 + 0.75 * abs(va) / top), maxlen * (0.25 + 0.75 * abs(vh) / top)
            if low:
                la, lh = lh, la
            c.t(60, cy, ta, font("black" if a_best else "semibold", 28), INK if a_best else MUTED, "lm")
            c.t(W - 60, cy, th, font("black" if h_best else "semibold", 28), INK if h_best else MUTED, "rm")
            c.rr(430 - la, cy - 7, 430, cy + 7, 7, fill=ca if a_best else GREY_BAR)
            c.rr(650, cy - 7, 650 + lh, cy + 7, 7, fill=ch if h_best else GREY_BAR)
            c.t(W / 2, cy, label, font("semibold", 21), MUTED, "mm")
            if key != rows[-1][0]:
                c.d.line([U(60), U(y + 58), U(W - 60), U(y + 58)], fill=LINE, width=U(1))
            y += 58

    # --- Jugadores clave (foto + promedios de temporada)
    kp = info.get("players") or {}
    pa_, ph_ = kp.get(a["id"]) or [], kp.get(h["id"]) or []
    if pa_ or ph_:
        n = max(len(pa_), len(ph_))
        y = c.section(70 + 34 + n * 100 + 6, "JUGADORES CLAVE")
        for lst, x, t, col in ((pa_, 60, a, ca), (ph_, 560, h, ch)):
            c.t(x, y + 10, t["abbr"], font("black", 22), col, "lm")
            for i, pl in enumerate(lst):
                cy = y + 80 + i * 100
                c.paste(headshot(pl.get("headshot"), 80, initials(pl.get("name"))), x + 40, cy)
                nm = pl.get("short") or pl.get("name")
                c.t(x + 96, cy - 18, fit(c.d, f"{nm}" + (f" · {pl['pos']}" if pl.get("pos") else ""), font("bold", 23), 360),
                    font("bold", 23), INK, "lm")
                av = pl.get("avg") or {}
                line1 = "  ·  ".join(f"{av[k]} {lbl}" for k, lbl in (("PTS", "PTS"), ("REB", "REB"), ("AST", "AST")) if av.get(k))
                line2 = "  ·  ".join(f"{lbl} {av[k]}{'%' if '%' in k else ''}" for k, lbl in (("FG%", "FG"), ("3P%", "3P"), ("MIN", "MIN")) if av.get(k))
                c.t(x + 96, cy + 12, line1 or "Sin promedios", font("black", 20), col, "lm")
                if line2:
                    c.t(x + 96, cy + 38, line2, font("medium", 18), MUTED, "lm")

    # --- Líderes del equipo (puntos, triples, rebotes, asistencias por juego)
    tl = info.get("team_leaders") or {}
    la_, lh_ = tl.get(a["id"]) or {}, tl.get(h["id"]) or {}
    cats = [(key, lbl) for key, lbl in (("PTS", "PUNTOS POR JUEGO"), ("3PM", "TRIPLES POR JUEGO"),
                                       ("REB", "REBOTES POR JUEGO"), ("AST", "ASISTENCIAS POR JUEGO"))
            if la_.get(key) or lh_.get(key)]
    if cats:
        y = c.section(70 + 30 + len(cats) * 118, "LÍDERES DEL EQUIPO")
        c.t(60, y + 6, a["abbr"], font("black", 22), ca, "lm")
        c.t(560, y + 6, h["abbr"], font("black", 22), ch, "lm")
        y += 30
        for i, (key, lbl) in enumerate(cats):
            yb = y + i * 118
            c.rr(W / 2 - 130, yb + 2, W / 2 + 130, yb + 30, 14, fill="#F1F5F9")
            c.t(W / 2, yb + 16, lbl, font("bold", 17), MUTED, "mm")
            for lst, x, col in ((la_.get(key) or [], 60, ca), (lh_.get(key) or [], 560, ch)):
                if not lst:
                    c.t(x, yb + 70, "Sin data", font("medium", 20), MUTED, "lm")
                    continue
                top = lst[0]
                c.paste(headshot(top.get("headshot"), 60, initials(top.get("name"))), x + 30, yb + 70)
                c.t(x + 74, yb + 58, fit(c.d, top.get("short") or top.get("name"), font("bold", 23), 250),
                    font("bold", 23), INK, "lm")
                rest = "  ·  ".join(f"{p.get('short')} {p.get('value')}" for p in lst[1:3])
                if rest:
                    c.t(x + 74, yb + 86, fit(c.d, rest, font("medium", 17), 300), font("medium", 17), MUTED, "lm")
                c.t(x + 460, yb + 64, top.get("value") or "-", font("black", 36), col, "rm")
            if i < len(cats) - 1:
                c.d.line([U(60), U(yb + 112), U(W - 60), U(yb + 112)], fill=LINE, width=U(1))

    # --- Lesiones
    cols = []
    for t in (a, h):
        out, doubt = team_injuries(summ, t)
        lines = []
        if out:
            lines += wrap(c.d, "Fuera: " + ", ".join(out), font("medium", 21), 440, 3)
        if doubt:
            lines += wrap(c.d, "En duda: " + ", ".join(doubt), font("medium", 21), 440, 2)
        cols.append(lines or ["Sin bajas importantes"])
    n = max(len(x) for x in cols)
    y = c.section(70 + 34 + n * 30 + 12, "LESIONES")
    for t, lines, x, col in ((a, cols[0], 60, ca), (h, cols[1], 560, ch)):
        c.t(x, y + 12, t["abbr"], font("black", 22), col, "lm")
        for i, ln in enumerate(lines):
            c.t(x, y + 46 + i * 30, ln, font("medium", 21), INK if ln != "Sin bajas importantes" else MUTED, "lm")

    # --- Matchups anteriores
    h2h = info.get("h2h") or []
    if h2h:
        y = c.section(70 + len(h2h) * 46 + 10, "MATCHUPS ANTERIORES")
        for i, row in enumerate(h2h[:4]):
            cy = y + 18 + i * 46
            c.t(60, cy, row["date"], font("medium", 22), MUTED, "lm")
            c.t(W / 2, cy, f"{row['away']} {row['as']}  @  {row['home']} {row['hs']}", font("semibold", 24), INK, "mm")
            if row.get("win"):
                col = ca if row["win"] == a["abbr"] else ch if row["win"] == h["abbr"] else INK
                c.rr(W - 160, cy - 16, W - 60, cy + 16, 16, fill=col)
                c.t(W - 110, cy, f"{row['win']} W", font("bold", 18), "white", "mm")

    c.footer()
    return c.png()


def final_card(g, date_txt):
    """
    <summary>Tarjeta de resultado final con logos, score y mejor jugador de cada equipo.</summary>
    <param name="g">Juego normalizado.</param>
    <param name="date_txt">Fecha para el header.</param>
    <returns>bytes PNG.</returns>
    """
    a, h = g["away"], g["home"]
    sa, sh = num(a.get("score")) or 0, num(h.get("score")) or 0
    c = Card(1000)
    c.header("FINAL", date_txt)
    y = c.section(360)
    c.rr(W / 2 - 60, y + 30, W / 2 + 60, y + 66, 18, fill=INK)
    c.t(W / 2, y + 48, "FINAL", font("black", 20), "white", "mm")
    for t, cx, mine, theirs in ((a, 200, sa, sh), (h, 880, sh, sa)):
        won = mine > theirs
        c.paste(logo(t, 140), cx, y + 120)
        c.t(cx, y + 222, fit(c.d, t.get("short") or t["name"], font("bold", 30), 360), font("bold", 30), INK, "mm")
        c.t(cx, y + 262, t.get("record", ""), font("medium", 22), MUTED, "mm")
        cat = (t.get("leaders") or [{}])[0]
        top = (cat.get("leaders") or [{}])[0]
        ath = (top.get("athlete") or {}).get("shortName") or (top.get("athlete") or {}).get("displayName")
        if ath:
            c.t(cx, y + 312, fit(c.d, f"{ath} · {top.get('displayValue', '')}", font("semibold", 22), 380),
                font("semibold", 22), MUTED, "mm")
        sx = W / 2 - 125 if t is a else W / 2 + 125
        c.t(sx, y + 170, str(int(mine)), font("black", 88), INK if won else "#94A3B8", "mm")
        if won:
            c.rr(sx - 30, y + 230, sx + 30, y + 238, 4, fill=team_color(t))
    c.t(W / 2, y + 170, "–", font("black", 60), "#94A3B8", "mm")
    c.footer()
    return c.png()


def slate_card(games, date_txt, kalshi_of):
    """
    <summary>Tarjeta resumen con todos los juegos del día.</summary>
    <param name="games">Juegos normalizados (en orden).</param>
    <param name="date_txt">Fecha.</param>
    <param name="kalshi_of">Función juego -> {away, home} en ¢ (o {}).</param>
    <returns>bytes PNG.</returns>
    """
    c = Card(200 + len(games) * 140)
    c.header("JUEGOS DE HOY", date_txt)
    for g in games:
        a, h = g["away"], g["home"]
        y = c.section(118)
        cy = y + 59
        for t, x in ((a, 60), (h, 360)):
            c.paste(logo(t, 70), x + 35, cy)
            c.t(x + 86, cy - 14, t["abbr"], font("black", 32), INK, "lm")
            c.t(x + 86, cy + 22, t.get("record", ""), font("medium", 20), MUTED, "lm")
        c.t(300, cy, "@", font("bold", 28), MUTED, "mm")
        c.t(W - 60, cy - 14, g.get("when_short", ""), font("black", 30), INK, "rm")
        kx = kalshi_of(g) or {}
        if kx.get("away") is not None and kx.get("home") is not None:
            fav, p = (a, kx["away"]) if kx["away"] >= kx["home"] else (h, kx["home"])
            txt = f"Kalshi: {fav['abbr']} {p}¢"
            c.t(W - 60, cy + 22, txt, font("bold", 20), team_color(fav), "rm")
        elif g.get("broadcast"):
            c.t(W - 60, cy + 22, g["broadcast"], font("medium", 20), MUTED, "rm")
    c.footer()
    return c.png()


def player_card(p, avg, rows, props=None):
    """
    <summary>Tarjeta de un jugador: foto, equipo, promedios de temporada, últimos 5 juegos y props de Kalshi.</summary>
    <param name="p">Jugador (players.roster_index).</param>
    <param name="avg">Promedios de temporada {PTS, REB, …}.</param>
    <param name="rows">Últimos 5 (players.player_last5).</param>
    <param name="props">Líneas de Kalshi [(texto, ¢)] opcional.</param>
    <returns>bytes PNG.</returns>
    """
    team = p.get("team") or {}
    tinfo = {"color": team.get("color"), "alt": team.get("alternateColor"), "abbr": team.get("abbreviation", ""),
             "logo": ((team.get("logos") or [{}])[0]).get("href")}
    col = team_color(tinfo)
    c = Card()
    c.header("JUGADOR", team.get("displayName", ""))

    y = c.section(250)
    c.paste(headshot(p.get("headshot"), 190, initials(p.get("name"))), 160, y + 125)
    c.t(285, y + 82, fit(c.d, p.get("name"), font("black", 44), 640), font("black", 44), INK, "lm")
    sub = " · ".join(x for x in (f"#{p['jersey']}" if p.get("jersey") else "", p.get("pos"), team.get("displayName")) if x)
    c.t(285, y + 128, sub, font("medium", 24), MUTED, "lm")
    if p.get("injury"):
        c.rr(285, y + 158, 285 + 24 + c.d.textlength(p["injury"], font=font("bold", 20)) / S, y + 194, 18, fill=LOSS)
        c.t(297, y + 176, p["injury"], font("bold", 20), "white", "lm")
    else:
        c.rr(285, y + 158, 385, y + 194, 18, fill=WIN)
        c.t(335, y + 176, "Activo", font("bold", 20), "white", "mm")
    c.paste(logo(tinfo, 90), W - 110, y + 70)

    tiles = [(k, lbl) for k, lbl in (("PTS", "PUNTOS"), ("REB", "REBOTES"), ("AST", "ASIST."), ("FG%", "FG%"),
                                    ("3P%", "3P%"), ("MIN", "MIN")) if avg.get(k)]
    if tiles:
        y = c.section(200, "PROMEDIOS DE TEMPORADA")
        if avg.get("GP"):
            c.t(W - 60, y - 32, f"{avg['GP']} juegos", font("semibold", 20), MUTED, "rm")
        tw = (W - 120) / len(tiles)
        for i, (k, lbl) in enumerate(tiles):
            x0 = 60 + i * tw
            c.rr(x0 + 6, y, x0 + tw - 6, y + 104, 16, fill="#F8FAFC", outline=LINE)
            c.t(x0 + tw / 2, y + 44, avg[k], font("black", 38), col if i < 3 else INK, "mm")
            c.t(x0 + tw / 2, y + 82, lbl, font("bold", 17), MUTED, "mm")

    if rows:
        cols = [("FECHA", 60, "lm"), ("RIVAL", 175, "lm"), ("RES", 330, "mm"), ("MIN", 470, "mm"), ("PTS", 570, "mm"),
                ("REB", 660, "mm"), ("AST", 750, "mm"), ("FG", 865, "mm"), ("3PT", 975, "mm")]
        y = c.section(70 + 40 + len(rows) * 54 + 10, "ÚLTIMOS 5 JUEGOS")
        for lbl, x, an in cols:
            c.t(x, y + 10, lbl, font("bold", 17), MUTED, an)
        for i, r in enumerate(rows):
            cy = y + 58 + i * 54
            if i % 2 == 0:
                c.rr(44, cy - 25, W - 44, cy + 25, 10, fill="#F8FAFC")
            c.t(60, cy, r["date"], font("medium", 20), MUTED, "lm")
            c.t(175, cy, f"{'vs' if r['atvs'] == 'vs' else '@'} {r['opp']}", font("bold", 22), INK, "lm")
            chip = WIN if r["res"] == "W" else LOSS if r["res"] == "L" else GREY_BAR
            c.rr(290, cy - 16, 370, cy + 16, 9, fill=chip)
            c.t(330, cy, f"{r['res']} {r['score']}" if len(r["score"]) <= 7 else r["res"], font("black", 15), "white", "mm")
            for key, x in (("MIN", 470), ("PTS", 570), ("REB", 660), ("AST", 750), ("FG", 865), ("3PT", 975)):
                c.t(x, cy, r.get(key, "-"), font("black" if key == "PTS" else "semibold", 26 if key == "PTS" else 22),
                    col if key == "PTS" else INK, "mm")
        pts = [num(r.get("PTS")) for r in rows if num(r.get("PTS")) is not None]
        if pts:
            c.t(W - 60, y - 32, f"Prom. {sum(pts) / len(pts):.1f} PTS", font("semibold", 20), MUTED, "rm")

    if props:
        y = c.section(70 + len(props[:5]) * 44 + 10, "PROPS EN KALSHI")
        for i, (txt, cents) in enumerate(props[:5]):
            cy = y + 18 + i * 44
            c.t(60, cy, fit(c.d, txt, font("semibold", 22), 780), font("semibold", 22), INK, "lm")
            c.rr(W - 160, cy - 17, W - 60, cy + 17, 17, fill=col)
            c.t(W - 110, cy, f"{cents}¢", font("black", 20), "white", "mm")

    c.footer()
    return c.png()
