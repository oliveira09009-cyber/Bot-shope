import requests
import json
import os
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# CONFIGURAÇÕES (vêm das variáveis do Render)
# ==========================================

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID", "-1003905816059")
REDIS_URL = os.environ["UPSTASH_REDIS_REST_URL"]
REDIS_TOKEN = os.environ["UPSTASH_REDIS_REST_TOKEN"]
ADMIN_ID = os.environ.get("ADMIN_ID")  # opcional: só você pode usar o bot

INTERVALO = 3600


# ==========================================
# BANCO (Upstash Redis) - a fila não some mais
# ==========================================

def redis_cmd(*args):
    resp = requests.post(
        REDIS_URL,
        headers={"Authorization": f"Bearer {REDIS_TOKEN}"},
        json=list(args),
        timeout=15
    )
    resp.raise_for_status()
    return resp.json().get("result")


def carregar_fila():
    # Se der erro de rede, deixa a exceção subir
    # (evita apagar a fila sem querer)
    dado = redis_cmd("GET", "fila")
    return json.loads(dado) if dado else []


def salvar_fila(fila):
    redis_cmd("SET", "fila", json.dumps(fila, ensure_ascii=False))


def adicionar_na_fila(nome, link, imagem=None):
    fila = carregar_fila()

    for produto in fila:
        if produto["link"] == link:
            return False, len(fila)

    fila.append({"nome": nome, "link": link, "imagem": imagem})
    salvar_fila(fila)

    return True, len(fila)


# ==========================================
# TELEGRAM
# ==========================================

def enviar_mensagem(chat_id, texto):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    try:
        resposta = requests.post(
            url,
            data={"chat_id": chat_id, "text": texto},
            timeout=30
        )
        return resposta.json()

    except Exception as erro:
        print("❌ Erro Telegram:", erro)
        return {"ok": False}


# ==========================================
# PUBLICAR PRODUTO
# ==========================================

def publicar_proximo():
    """Retorna True se publicou."""

    fila = carregar_fila()

    if not fila:
        print("📭 Fila vazia.")
        return False

    produto = fila[0]

    nome = produto["nome"]
    link = produto["link"]
    imagem = produto.get("imagem")

    legenda = f"""🔥 ACHADINHO DA SHOPEE

🛒 {nome}

💰 Confira o preço e as opções disponíveis!"""

    teclado = {
        "inline_keyboard": [
            [{"text": "🛒 COMPRAR AGORA", "url": link}]
        ]
    }

    if imagem:
        url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
        dados = {
            "chat_id": CHAT_ID,
            "photo": imagem,
            "caption": legenda,
            "reply_markup": json.dumps(teclado)
        }
    else:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        dados = {
            "chat_id": CHAT_ID,
            "text": legenda,
            "reply_markup": json.dumps(teclado)
        }

    try:
        resposta = requests.post(url, data=dados, timeout=30)
        resultado = resposta.json()

    except Exception as erro:
        print("❌ Erro ao publicar:", erro)
        return False

    if resultado.get("ok"):
        fila.pop(0)
        salvar_fila(fila)

        print("================================")
        print("✅ PRODUTO PUBLICADO")
        print("🛒", nome)
        print("📦 Restam:", len(fila))
        print("================================")
        return True

    print("❌ Telegram recusou a publicação.")
    print(resultado)
    print("⚠️ Produto continua na fila.")
    return False


# ==========================================
# PUBLICADOR AUTOMÁTICO
# (guarda a hora do último post, então
#  reiniciar o servidor não posta de novo)
# ==========================================

def publicador_automatico():

    print("🚀 Publicador automático iniciado.")

    while True:

        try:
            ultimo = float(redis_cmd("GET", "ultimo_post") or 0)

            if time.time() - ultimo >= INTERVALO:
                if publicar_proximo():
                    redis_cmd("SET", "ultimo_post", str(time.time()))

        except Exception as erro:
            print("❌ Erro no publicador:", erro)

        time.sleep(60)


# ==========================================
# MOSTRAR / LIMPAR FILA
# ==========================================

def mostrar_fila(chat_id):

    fila = carregar_fila()

    if not fila:
        enviar_mensagem(chat_id, "📭 A fila está vazia.")
        return

    mensagem = "📦 PRODUTOS NA FILA\n\n"

    for numero, produto in enumerate(fila, start=1):
        mensagem += (
            f"{numero}. 🛒 {produto['nome']}\n"
            f"🔗 {produto['link']}\n\n"
        )

    enviar_mensagem(chat_id, mensagem[:4000])


def limpar_fila(chat_id):
    salvar_fila([])
    enviar_mensagem(chat_id, "🗑️ Fila limpa com sucesso!")


# ==========================================
# PROCESSAR MENSAGEM
# ==========================================

def processar_mensagem(mensagem):

    chat_id = mensagem["chat"]["id"]

    if ADMIN_ID and str(mensagem.get("from", {}).get("id")) != ADMIN_ID:
        return

    # ---------- FOTO + LEGENDA ----------

    if "photo" in mensagem:

        file_id = mensagem["photo"][-1]["file_id"]

        legenda = mensagem.get("caption", "").strip()

        linhas = [l.strip() for l in legenda.split("\n") if l.strip()]

        if len(linhas) < 2:
            enviar_mensagem(
                chat_id,
                """❌ Formato incorreto.

Envie a FOTO com esta legenda:

Nome do produto
Link da Shopee"""
            )
            return

        nome = linhas[0]
        link = linhas[1]

        if not link.startswith("http"):
            enviar_mensagem(chat_id, "❌ O link informado não é válido.")
            return

        adicionado, posicao = adicionar_na_fila(nome, link, file_id)

        if not adicionado:
            enviar_mensagem(chat_id, "⚠️ Esse produto já está na fila.")
            return

        enviar_mensagem(
            chat_id,
            f"""✅ PRODUTO ADICIONADO!

🛒 {nome}

📦 Posição na fila: {posicao}

🖼️ Foto salva.
⏰ Publicação automática ativa."""
        )
        return

    # ---------- TEXTO ----------

    texto = mensagem.get("text", "").strip()

    if texto == "/start":
        enviar_mensagem(
            chat_id,
            """🤖 ACHADOS DA SHOPEE

📸 Para adicionar produto:

Envie uma FOTO com a legenda:

Nome do produto
Link da Shopee

Comandos:

/fila
Ver fila

/limparfila
Limpar fila"""
        )
        return

    if texto == "/fila":
        mostrar_fila(chat_id)
        return

    if texto == "/limparfila":
        limpar_fila(chat_id)
        return

    linhas = [l.strip() for l in texto.split("\n") if l.strip()]

    if len(linhas) < 2:
        enviar_mensagem(
            chat_id,
            """❌ Formato incorreto.

Envie uma FOTO com:

Nome do produto
Link da Shopee"""
        )
        return

    nome = linhas[0]
    link = linhas[1]

    if not link.startswith("http"):
        enviar_mensagem(chat_id, "❌ Link inválido.")
        return

    adicionado, posicao = adicionar_na_fila(nome, link)

    if not adicionado:
        enviar_mensagem(chat_id, "⚠️ Esse produto já está na fila.")
        return

    enviar_mensagem(
        chat_id,
        f"""✅ PRODUTO ADICIONADO!

🛒 {nome}

📦 Posição: {posicao}

⏰ Será publicado automaticamente."""
    )


# ==========================================
# RECEBER TELEGRAM
# ==========================================

def iniciar_bot():

    print("📥 Sistema de recebimento iniciado.")

    ultimo_update = None

    while True:

        try:
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"

            parametros = {"timeout": 30}

            if ultimo_update is not None:
                parametros["offset"] = ultimo_update + 1

            resposta = requests.get(url, params=parametros, timeout=35)
            dados = resposta.json()

            if not dados.get("ok"):
                print("❌ Erro Telegram:", dados)
                time.sleep(5)
                continue

            for update in dados["result"]:

                ultimo_update = update["update_id"]

                if "message" in update:
                    try:
                        processar_mensagem(update["message"])
                    except Exception as erro:
                        print("❌ Erro ao processar:", erro)
                        enviar_mensagem(
                            update["message"]["chat"]["id"],
                            "⚠️ Erro temporário. Tente enviar de novo."
                        )

        except Exception as erro:
            print("❌ Erro:", erro)
            time.sleep(5)


# ==========================================
# MINI SERVIDOR (para Web Service do Render)
# ==========================================

class Saude(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def servidor_saude():
    porta = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", porta), Saude).serve_forever()


# ==========================================
# INICIAR
# ==========================================

if __name__ == "__main__":

    threading.Thread(target=servidor_saude, daemon=True).start()
    threading.Thread(target=publicador_automatico, daemon=True).start()

    print("======================================")
    print("✅ SISTEMA COMPLETO ATIVO")
    print("⏰ 1 PRODUTO POR HORA")
    print("======================================")

    # O bot roda na thread principal: o programa NÃO encerra mais
    iniciar_bot()
