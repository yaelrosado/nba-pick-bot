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
