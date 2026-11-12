"""
<summary>
Envío de mensajes a Telegram y Discord (o a la consola en DRY_RUN).
</summary>
<remarks>
Archivo: notify.py
Autor: Yael Rosado
Propósito: Centralizar los posts, con pausas para no chocar con los rate limits.
</remarks>
"""

import json
import os
import time

import requests

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "").strip().split("?")[0]
DRY_RUN = os.getenv("DRY_RUN") == "1"
BOT_NAME = "Kalshi NBA Bot"


def has_tg():
    """
    <summary>Indica si Telegram está configurado.</summary>
    <returns>True si hay token y chat id.</returns>
    """
    return bool(TG_TOKEN and TG_CHAT)


def has_dc():
    """
    <summary>Indica si Discord está configurado.</summary>
    <returns>True si hay webhook.</returns>
    """
    return bool(DISCORD_WEBHOOK)


def tg(method, payload):
    """
    <summary>Llama la Bot API de Telegram y respeta el retry_after si hay 429.</summary>
    <param name="method">Método, ej. "sendMessage".</param>
    <param name="payload">Datos JSON.</param>
    <returns>JSON de respuesta.</returns>
    """
    for _ in range(3):
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", json=payload, timeout=30)
        res = r.json()
        if r.status_code == 429:
            time.sleep(res.get("parameters", {}).get("retry_after", 3) + 1)
            continue
        if not res.get("ok") and "not modified" not in res.get("description", ""):
            print(f"TG {method} falló:", res.get("description"))
        return res
    return {"ok": False}


def dc(payload, message_id=None, wait=False):
    """
    <summary>Postea (o edita) un mensaje del webhook de Discord, con reintento si hay 429.</summary>
    <param name="payload">Contenido/embeds.</param>
    <param name="message_id">Si se da, edita ese mensaje.</param>
    <param name="wait">True para recibir el mensaje creado (y su id).</param>
    <returns>Response de requests.</returns>
    """
    payload = {"username": BOT_NAME, "allowed_mentions": {"parse": []}, **payload}
    for _ in range(3):
        if message_id:
            r = requests.patch(f"{DISCORD_WEBHOOK}/messages/{message_id}", json=payload, timeout=30)
        else:
            r = requests.post(DISCORD_WEBHOOK + ("?wait=true" if wait else ""), json=payload, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        if not r.ok:
            print("Discord falló:", r.status_code, r.text[:200])
        return r
    return r


def tg_files(method, data, files):
    """
    <summary>Llama la Bot API de Telegram subiendo archivos (multipart), con reintento si hay 429.</summary>
    <param name="method">Método, ej. "sendPhoto".</param>
    <param name="data">Campos del formulario.</param>
    <param name="files">Archivos {campo: (nombre, bytes, tipo)}.</param>
    <returns>JSON de respuesta.</returns>
    """
    for _ in range(3):
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", data=data, files=files, timeout=60)
        res = r.json()
        if r.status_code == 429:
            time.sleep(res.get("parameters", {}).get("retry_after", 3) + 1)
            continue
        if not res.get("ok"):
            print(f"TG {method} falló:", res.get("description"))
        return res
    return {"ok": False}


def _dry_save(images, caption):
    """
    <summary>En DRY_RUN guarda las imágenes en ./out_cards para verlas.</summary>
    <param name="images">Lista de bytes PNG.</param>
    <param name="caption">Caption.</param>
    """
    os.makedirs("out_cards", exist_ok=True)
    for png in images:
        n = len(os.listdir("out_cards")) + 1
        path = os.path.join("out_cards", f"card_{n:02d}.png")
        with open(path, "wb") as f:
            f.write(png)
        print(f"[imagen] {path}")
    print("caption:", caption)


def send_photo(chat_id, png, caption="", reply_to=None):
    """
    <summary>Manda una imagen con caption HTML a un chat de Telegram.</summary>
    <param name="chat_id">Chat.</param>
    <param name="png">Bytes PNG.</param>
    <param name="caption">Caption HTML (máx 1024).</param>
    <param name="reply_to">Mensaje a responder (opcional).</param>
    <returns>JSON de respuesta.</returns>
    """
    data = {"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
    if reply_to:
        data["reply_parameters"] = json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})
    return tg_files("sendPhoto", data, {"photo": ("card.png", png, "image/png")})


def post_photo(png, caption_html, dc_text=""):
    """
    <summary>Publica una imagen en Telegram (con caption) y en Discord (como adjunto en un embed).</summary>
    <param name="png">Bytes PNG.</param>
    <param name="caption_html">Caption HTML para Telegram.</param>
    <param name="dc_text">Texto corto (markdown) para Discord.</param>
    """
    if DRY_RUN:
        _dry_save([png], caption_html)
        return
    if has_tg():
        send_photo(TG_CHAT, png, caption_html)
    if has_dc():
        payload = {"username": BOT_NAME, "allowed_mentions": {"parse": []},
                   "embeds": [{"image": {"url": "attachment://card.png"}, "color": 0xF97316,
                               **({"description": dc_text} if dc_text else {})}]}
        for _ in range(3):
            r = requests.post(DISCORD_WEBHOOK, data={"payload_json": json.dumps(payload)},
                              files={"files[0]": ("card.png", png, "image/png")}, timeout=60)
            if r.status_code != 429:
                break
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
    time.sleep(1.5)


def post_album(images, caption_html, dc_text=""):
    """
    <summary>Publica varias imágenes juntas: álbum en Telegram (10 por álbum) y galería en Discord.</summary>
    <param name="images">Lista de bytes PNG.</param>
    <param name="caption_html">Caption del álbum (va en la primera foto).</param>
    <param name="dc_text">Texto para Discord.</param>
    """
    if not images:
        return
    if len(images) == 1:
        post_photo(images[0], caption_html, dc_text)
        return
    if DRY_RUN:
        _dry_save(images, caption_html)
        return
    for start in range(0, len(images), 10):
        chunk = images[start:start + 10]
        if len(chunk) == 1:
            post_photo(chunk[0], "", "")
            continue
        if has_tg():
            media = [{"type": "photo", "media": f"attach://p{i}"} for i in range(len(chunk))]
            if start == 0 and caption_html:
                media[0].update({"caption": caption_html[:1024], "parse_mode": "HTML"})
            tg_files("sendMediaGroup", {"chat_id": TG_CHAT, "media": json.dumps(media)},
                     {f"p{i}": (f"p{i}.png", png, "image/png") for i, png in enumerate(chunk)})
        if has_dc():
            payload = {"username": BOT_NAME, "content": dc_text if start == 0 else "", "allowed_mentions": {"parse": []}}
            requests.post(DISCORD_WEBHOOK, data={"payload_json": json.dumps(payload)},
                          files={f"files[{i}]": (f"card{i}.png", png, "image/png") for i, png in enumerate(chunk)},
                          timeout=90)
        time.sleep(3)


def post(tg_html, dc_payload, photo=None):
    """
    <summary>Publica un mensaje nuevo en ambas plataformas.</summary>
    <param name="tg_html">Texto HTML para Telegram.</param>
    <param name="dc_payload">Payload (embeds) para Discord.</param>
    <param name="photo">URL de imagen opcional (Telegram la manda como foto con caption).</param>
    """
    if DRY_RUN:
        print("\n----- TELEGRAM -----\n" + tg_html)
        print("----- DISCORD -----\n" + json.dumps(dc_payload, ensure_ascii=False)[:1500])
        return
    if has_tg():
        if photo and len(tg_html) <= 1024:
            res = tg("sendPhoto", {"chat_id": TG_CHAT, "photo": photo, "caption": tg_html, "parse_mode": "HTML"})
            if not res.get("ok"):
                photo = None
        if not photo or len(tg_html) > 1024:
            tg("sendMessage", {"chat_id": TG_CHAT, "text": tg_html[:4096], "parse_mode": "HTML",
                               "disable_web_page_preview": True})
    if has_dc():
        dc(dc_payload)
    time.sleep(1.2)
