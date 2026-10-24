import asyncio
import json
import logging
import os
import re
import sqlite3
from html import escape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# ---------------- CONFIG ----------------
BOT_TOKEN = os.environ["BOT_TOKEN"]        # token do @BotFather
OWNER_ID = int(os.environ["OWNER_ID"])     # seu ID numérico (use o @userinfobot)
CHANNEL_ID = os.environ["CHANNEL_ID"]      # @seucanal ou -100xxxxxxxxxx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}
URL_RE = re.compile(r"https?://\S+")
PRICE_RE = re.compile(r"R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}")

logging.basicConfig(level=logging.INFO)

# ---------------- BANCO ----------------
db = sqlite3.connect("produtos.db", check_same_thread=False)
db.execute(
    """CREATE TABLE IF NOT EXISTS produtos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT, titulo TEXT, preco TEXT, imagem TEXT,
        criado_em TEXT DEFAULT CURRENT_TIMESTAMP
    )"""
)
db.commit()


# ---------------- EXTRAÇÃO ----------------
def meta(soup, *nomes):
    for n in nomes:
        tag = soup.find("meta", attrs={"property": n}) or soup.find(
            "meta", attrs={"name": n}
        )
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def achar_preco_json(obj):
    if isinstance(obj, dict):
        for k in ("price", "lowPrice"):
            if obj.get(k) not in (None, ""):
                return obj[k]
        for v in obj.values():
            r = achar_preco_json(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = achar_preco_json(v)
            if r:
                return r
    return None


def formatar_preco(valor):
    """Converte 1234.5 / '1.234,50' / '99,9' em 'R$ 1.234,50'."""
    try:
        s = str(valor).strip()
        s = re.sub(r"[^\d.,]", "", s)
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        n = float(s)
        return "R$ " + f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return None


def extrair(url):
    r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    final_url = r.url

    titulo = meta(soup, "og:title", "twitter:title") or (
        soup.title.string.strip() if soup.title and soup.title.string else "Produto"
    )

    imagem = meta(soup, "og:image", "twitter:image")
    if not imagem:
        img = soup.find("img", src=True)
        imagem = img["src"] if img else None
    if imagem:
        imagem = urljoin(final_url, imagem)

    # preço: meta tags -> JSON-LD -> regex "R$ 00,00"
    preco = meta(
        soup, "product:price:amount", "og:price:amount", "twitter:data1"
    )
    if not preco:
        for s in soup.find_all("script", type="application/ld+json"):
            try:
                preco = achar_preco_json(json.loads(s.string or ""))
            except Exception:
                continue
            if preco:
                break
    if preco:
        preco = formatar_preco(preco)
    if not preco:
        m = PRICE_RE.search(soup.get_text(" ", strip=True))
        preco = m.group(0) if m else None

    return {"url": final_url, "titulo": titulo, "preco": preco, "imagem": imagem}


# ---------------- HANDLER ----------------
async def receber_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    texto = msg.text or ""
    m = URL_RE.search(texto)
    if not m:
        return

    url = m.group(0)
    # opcional: "link 99,90" para forçar o preço manualmente
    resto = texto.replace(url, "").strip()
    preco_manual = formatar_preco(resto) if re.search(r"\d", resto) else None

    await msg.reply_text("🔎 Lendo o link...")

    try:
        dados = await asyncio.to_thread(extrair, url)
    except Exception as e:
        await msg.reply_text(f"❌ Não consegui ler o link: {e}")
        return

    if preco_manual:
        dados["preco"] = preco_manual

    # salva no banco
    db.execute(
        "INSERT INTO produtos (url, titulo, preco, imagem) VALUES (?, ?, ?, ?)",
        (dados["url"], dados["titulo"], dados["preco"], dados["imagem"]),
    )
    db.commit()

    legenda = f"<b>{escape(dados['titulo'][:200])}</b>\n\n"
    legenda += f"💰 {escape(dados['preco'])}\n\n" if dados["preco"] else ""
    legenda += f"🛒 <a href=\"{escape(dados['url'], quote=True)}\">Ver produto</a>"

    try:
        if dados["imagem"]:
            await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=dados["imagem"],
                caption=legenda[:1024],
                parse_mode="HTML",
            )
        else:
            await context.bot.send_message(
                chat_id=CHANNEL_ID, text=legenda, parse_mode="HTML"
            )
    except Exception as e:
        await msg.reply_text(f"❌ Erro ao publicar: {e}")
        return

    aviso = "✅ Publicado!"
    if not dados["preco"]:
        aviso += "\n⚠️ Preço não encontrado. Da próxima vez mande: link 99,90"
    await msg.reply_text(aviso)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    # só aceita mensagens de texto vindas de você
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.User(user_id=OWNER_ID),
            receber_link,
        )
    )
    app.run_polling()


if __name__ == "__main__":
    main()
