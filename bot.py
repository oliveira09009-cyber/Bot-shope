import requests
import json
import os
import time
import threading
import re
import html
from urllib.parse import urljoin
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# CONFIGURAÇÕES
# ==========================================

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID", "-1003905816059")

REDIS_URL = os.environ["UPSTASH_REDIS_REST_URL"]
REDIS_TOKEN = os.environ["UPSTASH_REDIS_REST_TOKEN"]

ADMIN_ID = os.environ.get("ADMIN_ID")

# 5 produtos por hora
INTERVALO = 720


# ==========================================
# HEADERS
# ==========================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Connection": "keep-alive",
}


# ==========================================
# BANCO REDIS
# ==========================================

def redis_cmd(*args):

    resposta = requests.post(
        REDIS_URL,
        headers={
            "Authorization": f"Bearer {REDIS_TOKEN}"
        },
        json=list(args),
        timeout=15
    )

    resposta.raise_for_status()

    dados = resposta.json()

    return dados.get("result")


def carregar_fila():

    dado = redis_cmd("GET", "fila")

    if not dado:
        return []

    try:
        return json.loads(dado)
    except Exception:
        print("⚠️ Erro ao ler fila do Redis.")
        return []


def salvar_fila(fila):

    redis_cmd(
        "SET",
        "fila",
        json.dumps(fila, ensure_ascii=False)
    )


def adicionar_na_fila(nome, link, imagem=None):

    fila = carregar_fila()

    for produto in fila:

        if produto.get("link") == link:
            return False, len(fila)

    fila.append({
        "nome": nome,
        "link": link,
        "imagem": imagem
    })

    salvar_fila(fila)

    return True, len(fila)


# ==========================================
# TELEGRAM
# ==========================================

def telegram_request(metodo, dados=None, files=None):

    url = f"https://api.telegram.org/bot{TOKEN}/{metodo}"

    try:

        resposta = requests.post(
            url,
            data=dados,
            files=files,
            timeout=40
        )

        try:
            resultado = resposta.json()
        except Exception:
            resultado = {
                "ok": False,
                "description": resposta.text
            }

        return resultado

    except Exception as erro:

        print(f"❌ Erro Telegram ({metodo}):", erro)

        return {
            "ok": False,
            "description": str(erro)
        }


def enviar_mensagem(chat_id, texto):

    return telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": texto
        }
    )


# ==========================================
# RESOLVER LINK SHOPEE
# ==========================================

def resolver_link(link):

    if not link:
        return link

    try:

        print("🔗 Resolvendo link:", link)

        resposta = requests.get(
            link,
            headers=HEADERS,
            allow_redirects=True,
            timeout=20
        )

        final = resposta.url

        print("🔗 Link final:", final)

        return final

    except Exception as erro:

        print("⚠️ Não foi possível resolver link:", erro)

        return link


# ==========================================
# BAIXAR PÁGINA DA SHOPEE
# ==========================================

def baixar_pagina(link):

    try:

        resposta = requests.get(
            link,
            headers=HEADERS,
            timeout=30,
            allow_redirects=True
        )

        print("🌐 Status Shopee:", resposta.status_code)
        print("📄 Tamanho HTML:", len(resposta.text))

        if resposta.status_code != 200:
            return None, resposta.url

        return resposta.text, resposta.url

    except Exception as erro:

        print("❌ Erro ao acessar Shopee:", erro)

        return None, link


# ==========================================
# LIMPAR HTML
# ==========================================

def limpar_texto(texto):

    if not texto:
        return ""

    texto = html.unescape(texto)

    texto = re.sub(
        r"<[^>]+>",
        " ",
        texto
    )

    texto = re.sub(
        r"\s+",
        " ",
        texto
    )

    return texto.strip()


# ==========================================
# EXTRAIR PREÇO
# ==========================================

def extrair_preco(html_text):

    if not html_text:
        return None

    texto = html.unescape(html_text)

    # --------------------------------------
    # 1. PREÇOS NO FORMATO R$ 42,94
    # --------------------------------------

    padrao = r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})"

    encontrados = re.findall(
        padrao,
        texto,
        flags=re.IGNORECASE
    )

    precos = []

    for valor in encontrados:

        try:

            numero = float(
                valor.replace(".", "")
                .replace(",", ".")
            )

            if numero > 0 and numero < 100000:
                precos.append(numero)

        except Exception:
            pass

    # --------------------------------------
    # 2. FORMATO SEM R$
    # Exemplo: "42,94"
    # --------------------------------------

    if not precos:

        padrao2 = r"\b([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})\b"

        encontrados2 = re.findall(
            padrao2,
            texto
        )

        for valor in encontrados2:

            try:

                numero = float(
                    valor.replace(".", "")
                    .replace(",", ".")
                )

                if 5 <= numero < 100000:
                    precos.append(numero)

            except Exception:
                pass

    # --------------------------------------
    # 3. REMOVER VALORES PROVÁVEIS DE
    # TELEFONE / DATA / COISAS ESTRANHAS
    # --------------------------------------

    precos = [
        p for p in precos
        if p >= 1
    ]

    if not precos:
        return None

    # --------------------------------------
    # ESCOLHER O MENOR PREÇO
    # --------------------------------------

    menor = min(precos)

    # Formata BR
    valor_formatado = f"{menor:,.2f}"

    valor_formatado = (
        valor_formatado
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )

    print("💰 Preços encontrados:", precos)
    print("💰 Menor preço:", valor_formatado)

    return f"R$ {valor_formatado}"


# ==========================================
# EXTRAIR NOME
# ==========================================

def extrair_nome(html_text):

    if not html_text:
        return None

    # --------------------------------------
    # OG TITLE
    # --------------------------------------

    padroes = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
    ]

    for padrao in padroes:

        encontrado = re.search(
            padrao,
            html_text,
            re.IGNORECASE
        )

        if encontrado:

            nome = html.unescape(
                encontrado.group(1)
            ).strip()

            if len(nome) > 5:

                print("🛍️ Nome encontrado:", nome)

                return nome

    # --------------------------------------
    # TITLE
    # --------------------------------------

    encontrado = re.search(
        r"<title[^>]*>(.*?)</title>",
        html_text,
        re.IGNORECASE | re.DOTALL
    )

    if encontrado:

        nome = limpar_texto(
            encontrado.group(1)
        )

        if len(nome) > 5:

            print("🛍️ Nome encontrado pelo title:", nome)

            return nome

    return None


# ==========================================
# EXTRAIR IMAGEM
# ==========================================

def extrair_imagem(html_text, link_base):

    if not html_text:
        return None

    imagens = []

    # --------------------------------------
    # OG IMAGE
    # --------------------------------------

    padroes = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'"og:image"\s*:\s*"([^"]+)"',
        r'"image"\s*:\s*"([^"]+)"',
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            html_text,
            re.IGNORECASE
        )

        for imagem in encontrados:

            imagem = html.unescape(
                imagem
            )

            imagem = imagem.replace(
                "\\/",
                "/"
            )

            imagem = imagem.replace(
                "\\u002F",
                "/"
            )

            if imagem.startswith("//"):
                imagem = "https:" + imagem

            elif imagem.startswith("/"):
                imagem = urljoin(
                    link_base,
                    imagem
                )

            if imagem.startswith("http"):

                imagens.append(imagem)

    # --------------------------------------
    # IMAGENS SUSERCONTENT
    # --------------------------------------

    encontrados = re.findall(
        r'https?://[^"\']*susercontent\.com[^"\']*',
        html_text,
        re.IGNORECASE
    )

    for imagem in encontrados:

        imagem = imagem.replace(
            "\\/",
            "/"
        )

        if imagem.startswith("http"):
            imagens.append(imagem)

    # --------------------------------------
    # FILTRAR IMAGENS
    # --------------------------------------

    ignorar = [
        "logo",
        "avatar",
        "icon",
        "favicon",
        "placeholder",
        "banner"
    ]

    final = []

    for imagem in imagens:

        imagem_lower = imagem.lower()

        if any(
            palavra in imagem_lower
            for palavra in ignorar
        ):
            continue

        if imagem not in final:
            final.append(imagem)

    if final:

        print("🖼️ Imagem encontrada.")

        return final[0]

    print("⚠️ Nenhuma imagem encontrada.")

    return None


# ==========================================
# OBTER INFORMAÇÕES DO PRODUTO
# ==========================================

def obter_informacoes_shopee(link):

    link_original = link

    # Primeiro resolve o link curto
    link_final = resolver_link(link)

    # Depois baixa a página
    html_text, url_real = baixar_pagina(link_final)

    if not html_text:

        return {
            "nome": None,
            "preco": None,
            "imagem": None,
            "link": link_original
        }

    nome = extrair_nome(html_text)

    preco = extrair_preco(html_text)

    imagem = extrair_imagem(
        html_text,
        url_real
    )

    print("--------------------------------")
    print("🔎 RESULTADO SHOPEE")
    print("🛍️ Nome:", nome)
    print("💰 Preço:", preco)
    print("🖼️ Imagem:", "SIM" if imagem else "NÃO")
    print("--------------------------------")

    return {
        "nome": nome,
        "preco": preco,
        "imagem": imagem,
        "link": link_original
    }


# ==========================================
# MONTAR LEGENDA
# ==========================================

def montar_legenda(nome, preco):

    if not nome:
        nome = "Achadinho da Shopee"

    if preco:

        linha_preco = f"💰 A PARTIR DE {preco}"

    else:

        linha_preco = "💰 OFERTA ESPECIAL"

    legenda = f"""🔥 ACHADINHO DA SHOPEE 🔥

🛍️ {nome}

{linha_preco}
⚡ Aproveite enquanto estiver disponível!

👇 Clique abaixo para conferir:

🛒 COMPRAR AGORA"""

    return legenda


# ==========================================
# PUBLICAR PRODUTO
# ==========================================

def publicar_proximo():

    fila = carregar_fila()

    if not fila:

        print("📭 Fila vazia.")

        return False

    produto = fila[0]

    nome = produto.get("nome") or "Achadinho da Shopee"
    link = produto.get("link")
    imagem = produto.get("imagem")

    # --------------------------------------
    # TENTAR OBTER DADOS DA SHOPEE
    # --------------------------------------

    dados_shopee = obter_informacoes_shopee(link)

    nome_shopee = dados_shopee.get("nome")
    preco = dados_shopee.get("preco")
    imagem_shopee = dados_shopee.get("imagem")

    if nome_shopee:
        nome = nome_shopee

    if imagem_shopee:
        imagem = imagem_shopee

    legenda = montar_legenda(
        nome,
        preco
    )

    teclado = {
        "inline_keyboard": [
            [
                {
                    "text": "🛒 COMPRAR AGORA",
                    "url": link
                }
            ]
        ]
    }

    # ======================================
    # PRIMEIRA TENTATIVA: IMAGEM
    # ======================================

    if imagem:

        print("🖼️ Tentando publicar com imagem...")

        dados = {
            "chat_id": CHAT_ID,
            "caption": legenda,
            "reply_markup": json.dumps(teclado)
        }

        resposta = telegram_request(
            "sendPhoto",
            dados={
                **dados,
                "photo": imagem
            }
        )

        # ----------------------------------
        # SE DEU CERTO
        # ----------------------------------

        if resposta.get("ok"):

            fila.pop(0)

            salvar_fila(fila)

            print("================================")
            print("✅ PRODUTO PUBLICADO COM IMAGEM")
            print("🛒", nome)
            print("💰", preco or "não encontrado")
            print("📦 Restam:", len(fila))
            print("================================")

            return True

        print("⚠️ Telegram não aceitou a imagem.")
        print(resposta)

    # ======================================
    # SEGUNDA TENTATIVA: TEXTO
    # ======================================

    print("📝 Publicando como texto...")

    resposta = telegram_request(
        "sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": legenda,
            "reply_markup": json.dumps(teclado)
        }
    )

    if resposta.get("ok"):

        fila.pop(0)

        salvar_fila(fila)

        print("================================")
        print("✅ PRODUTO PUBLICADO COMO TEXTO")
        print("🛒", nome)
        print("💰", preco or "não encontrado")
        print("📦 Restam:", len(fila))
        print("================================")

        return True

    print("❌ Telegram recusou publicação.")
    print(resposta)

    print("⚠️ Produto continua na fila.")

    return False


# ==========================================
# PUBLICADOR AUTOMÁTICO
# ==========================================

def publicador_automatico():

    print("🚀 Publicador automático iniciado.")
    print("⏰ Intervalo:", INTERVALO, "segundos")

    while True:

        try:

            ultimo = float(
                redis_cmd(
                    "GET",
                    "ultimo_post"
                ) or 0
            )

            if time.time() - ultimo >= INTERVALO:

                if publicar_proximo():

                    redis_cmd(
                        "SET",
                        "ultimo_post",
                        str(time.time())
                    )

        except Exception as erro:

            print(
                "❌ Erro no publicador:",
                erro
            )

        time.sleep(60)


# ==========================================
# MOSTRAR FILA
# ==========================================

def mostrar_fila(chat_id):

    fila = carregar_fila()

    if not fila:

        enviar_mensagem(
            chat_id,
            "📭 A fila está vazia."
        )

        return

    mensagem = "📦 PRODUTOS NA FILA\n\n"

    for numero, produto in enumerate(
        fila,
        start=1
    ):

        mensagem += (
            f"{numero}. 🛒 {produto.get('nome', 'Produto')}\n"
            f"🔗 {produto.get('link', '')}\n\n"
        )

    enviar_mensagem(
        chat_id,
        mensagem[:4000]
    )


# ==========================================
# LIMPAR FILA
# ==========================================

def limpar_fila(chat_id):

    salvar_fila([])

    enviar_mensagem(
        chat_id,
        "🗑️ Fila limpa com sucesso!"
    )


# ==========================================
# PROCESSAR MENSAGEM
# ==========================================

def processar_mensagem(mensagem):

    chat_id = mensagem["chat"]["id"]

    # --------------------------------------
    # ADMIN
    # --------------------------------------

    if ADMIN_ID:

        usuario_id = str(
            mensagem.get(
                "from",
                {}
            ).get(
                "id"
            )
        )

        if usuario_id != str(ADMIN_ID):

            return

    # ======================================
    # FOTO + LEGENDA
    # ======================================

    if "photo" in mensagem:

        file_id = mensagem["photo"][-1]["file_id"]

        legenda = mensagem.get(
            "caption",
            ""
        ).strip()

        linhas = [
            linha.strip()
            for linha in legenda.split("\n")
            if linha.strip()
        ]

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

            enviar_mensagem(
                chat_id,
                "❌ O link informado não é válido."
            )

            return

        adicionado, posicao = adicionar_na_fila(
            nome,
            link,
            file_id
        )

        if not adicionado:

            enviar_mensagem(
                chat_id,
                "⚠️ Esse produto já está na fila."
            )

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

    # ======================================
    # TEXTO
    # ======================================

    texto = mensagem.get(
        "text",
        ""
    ).strip()

    # --------------------------------------
    # START
    # --------------------------------------

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

    # --------------------------------------
    # FILA
    # --------------------------------------

    if texto == "/fila":

        mostrar_fila(chat_id)

        return

    # --------------------------------------
    # LIMPAR
    # --------------------------------------

    if texto == "/limparfila":

        limpar_fila(chat_id)

        return

    # --------------------------------------
    # PRODUTO POR TEXTO
    # --------------------------------------

    linhas = [
        linha.strip()
        for linha in texto.split("\n")
        if linha.strip()
    ]

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

        enviar_mensagem(
            chat_id,
            "❌ Link inválido."
        )

        return

    adicionado, posicao = adicionar_na_fila(
        nome,
        link
    )

    if not adicionado:

        enviar_mensagem(
            chat_id,
            "⚠️ Esse produto já está na fila."
        )

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

            url = (
                f"https://api.telegram.org/"
                f"bot{TOKEN}/getUpdates"
            )

            parametros = {
                "timeout": 30
            }

            if ultimo_update is not None:

                parametros["offset"] = (
                    ultimo_update + 1
                )

            resposta = requests.get(
                url,
                params=parametros,
                timeout=35
            )

            dados = resposta.json()

            if not dados.get("ok"):

                print(
                    "❌ Erro Telegram:",
                    dados
                )

                time.sleep(5)

                continue

            for update in dados["result"]:

                ultimo_update = update[
                    "update_id"
                ]

                if "message" in update:

                    try:

                        processar_mensagem(
                            update["message"]
                        )

                    except Exception as erro:

                        print(
                            "❌ Erro ao processar:",
                            erro
                        )

                        enviar_mensagem(
                            update["message"]["chat"]["id"],
                            "⚠️ Erro temporário. Tente enviar de novo."
                        )

        except Exception as erro:

            print(
                "❌ Erro:",
                erro
            )

            time.sleep(5)


# ==========================================
# MINI SERVIDOR
# ==========================================

class Saude(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.end_headers()

        self.wfile.write(
            b"ok"
        )

    def do_HEAD(self):

        self.send_response(200)

        self.end_headers()

    def log_message(self, *args):

        pass


def servidor_saude():

    porta = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    servidor = HTTPServer(
        ("0.0.0.0", porta),
        Saude
    )

    servidor.serve_forever()


# ==========================================
# INICIAR
# ==========================================

if __name__ == "__main__":

    threading.Thread(
        target=servidor_saude,
        daemon=True
    ).start()

    threading.Thread(
        target=publicador_automatico,
        daemon=True
    ).start()

    print(
        "======================================"
    )

    print(
        "✅ SISTEMA COMPLETO ATIVO"
    )

    print(
        "⏰ 5 PRODUTOS POR HORA"
    )

    print(
        "💰 LEITOR DE PREÇOS ATIVO"
    )

    print(
        "🖼️ LEITOR DE IMAGENS ATIVO"
    )

    print(
        "======================================"
    )

    iniciar_bot()
