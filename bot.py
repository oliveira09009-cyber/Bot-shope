import requests
import json
import os
import time
import threading
import re
from html import unescape
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
# BANCO - UPSTASH REDIS
# ==========================================

def redis_cmd(*args):
    resp = requests.post(
        REDIS_URL,
        headers={
            "Authorization": f"Bearer {REDIS_TOKEN}"
        },
        json=list(args),
        timeout=15
    )

    resp.raise_for_status()

    return resp.json().get("result")


def carregar_fila():

    dado = redis_cmd("GET", "fila")

    return json.loads(dado) if dado else []


def salvar_fila(fila):

    redis_cmd(
        "SET",
        "fila",
        json.dumps(fila, ensure_ascii=False)
    )


def adicionar_na_fila(nome, link, imagem=None, preco=None):

    fila = carregar_fila()

    for produto in fila:

        if produto["link"] == link:

            return False, len(fila)

    produto = {
        "nome": nome,
        "link": link,
        "imagem": imagem,
        "preco": preco
    }

    fila.append(produto)

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
            data={
                "chat_id": chat_id,
                "text": texto
            },
            timeout=30
        )

        return resposta.json()

    except Exception as erro:

        print("❌ Erro Telegram:", erro)

        return {
            "ok": False
        }


# ==========================================
# CAPTURA DE DADOS DA SHOPEE
# ==========================================

def limpar_html(texto):

    texto = unescape(texto)

    texto = re.sub(
        r"<[^>]+>",
        "",
        texto
    )

    return texto.strip()


def extrair_meta(html, propriedade):

    padroes = [

        rf'<meta[^>]+property=["\']{re.escape(propriedade)}["\'][^>]+content=["\']([^"\']+)["\']',

        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(propriedade)}["\']',

        rf'<meta[^>]+name=["\']{re.escape(propriedade)}["\'][^>]+content=["\']([^"\']+)["\']',

        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(propriedade)}["\']'
    ]

    for padrao in padroes:

        resultado = re.search(
            padrao,
            html,
            re.IGNORECASE
        )

        if resultado:

            return limpar_html(resultado.group(1))

    return None


def extrair_jsonld(html):

    resultados = []

    blocos = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL
    )

    for bloco in blocos:

        try:

            dados = json.loads(
                unescape(bloco.strip())
            )

            if isinstance(dados, list):

                resultados.extend(dados)

            else:

                resultados.append(dados)

        except Exception:

            continue

    return resultados


def procurar_produto_jsonld(dados):

    nome = None
    imagem = None
    preco = None

    for item in dados:

        if not isinstance(item, dict):
            continue

        tipo = item.get("@type")

        if isinstance(tipo, list):

            eh_produto = "Product" in tipo

        else:

            eh_produto = tipo == "Product"

        if eh_produto:

            nome = item.get("name")

            imagem = item.get("image")

            oferta = item.get("offers")

            if isinstance(oferta, list) and oferta:

                oferta = oferta[0]

            if isinstance(oferta, dict):

                preco = (
                    oferta.get("price")
                    or oferta.get("lowPrice")
                )

            break

    return nome, imagem, preco


def extrair_dados_shopee(link):

    print("🔎 Analisando Shopee:")
    print(link)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; K) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120.0 Mobile Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,"
            "image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Referer": "https://shopee.com.br/"
    }

    try:

        resposta = requests.get(
            link,
            headers=headers,
            timeout=30,
            allow_redirects=True
        )

        print("📡 Status:", resposta.status_code)
        print("🔗 URL final:", resposta.url)

        if resposta.status_code != 200:

            return None, None, None

        html = resposta.text

        print("📄 HTML:", len(html), "caracteres")

    except Exception as erro:

        print("❌ Erro ao acessar Shopee:", erro)

        return None, None, None


    # ==========================================
    # OPEN GRAPH
    # ==========================================

    nome = extrair_meta(
        html,
        "og:title"
    )

    imagem = extrair_meta(
        html,
        "og:image"
    )


    # ==========================================
    # JSON-LD
    # ==========================================

    jsonld = extrair_jsonld(html)

    nome_json, imagem_json, preco_json = (
        procurar_produto_jsonld(jsonld)
    )


    if nome_json:

        nome = nome_json


    if imagem_json:

        if isinstance(imagem_json, list):

            if imagem_json:

                imagem = imagem_json[0]

        else:

            imagem = imagem_json


    preco = preco_json

    # ==========================================
    # TENTAR ENCONTRAR PREÇO
    # ==========================================

    if not preco:

        padroes_preco = [

            r'"price"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',

            r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

            r'"salePrice"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',

            r'"sale_price"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',

            r'"current_price"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',

            r'"currentPrice"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',

            r'"minPrice"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',

            r'"maxPrice"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',

            r'"priceMin"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',

            r'"priceMax"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"'
        ]

        for padrao in padroes_preco:

            resultado = re.search(
                padrao,
                html,
                re.IGNORECASE
            )

            if resultado:

                preco = resultado.group(1)

                print(
                    "💰 Preço encontrado no HTML:",
                    preco
                )

                break


    # ==========================================
    # LIMPAR NOME
    # ==========================================

    if nome:

        nome = unescape(str(nome))

        nome = re.sub(
            r"\s+",
            " ",
            nome
        ).strip()


    # ==========================================
    # NÃO ACEITAR NOME DA PÁGINA INICIAL
    # ==========================================

    nomes_invalidos = [

        "Shopee Brasil | Ofertas incríveis. Melhores preços do mercado",

        "Shopee Brasil",

        "Ofertas incríveis. Melhores preços do mercado",

        "Shopee"

    ]

    if nome:

        nome_normalizado = nome.lower().strip()

        for nome_invalido in nomes_invalidos:

            if nome_normalizado == nome_invalido.lower():

                print(
                    "⚠️ Nome encontrado é da página inicial."
                )

                nome = None

                break


    # ==========================================
    # LIMPAR PREÇO
    # ==========================================

    if preco:

        preco = str(preco).strip()

        try:

            valor = float(
                preco.replace("R$", "")
                     .replace(" ", "")
                     .replace(",", ".")
            )

            preco = (
                f"R$ {valor:.2f}"
                .replace(".", ",")
            )

        except Exception:

            pass


    # ==========================================
    # VALIDAR IMAGEM
    # ==========================================

    if imagem:

        imagem = str(imagem).strip()

        if not imagem.startswith("http"):

            imagem = None


    # ==========================================
    # RESULTADO
    # ==========================================

    print("")
    print("🔎 DADOS ENCONTRADOS:")

    print("🛒 Nome:", nome)
    print("💰 Preço:", preco)
    print("🖼️ Imagem:", imagem)

    print("")

    return nome, preco, imagem  


# ==========================================
# PUBLICAR PRODUTO
# ==========================================

def publicar_proximo():

    fila = carregar_fila()

    if not fila:

        print("📭 Fila vazia.")

        return False

    produto = fila[0]

    nome = produto["nome"]
    link = produto["link"]
    imagem = produto.get("imagem")
    preco = produto.get("preco")


    # ------------------------------------------
    # LEGENDA
    # ------------------------------------------

    if preco:

        oferta = f"💰 {preco}"

    else:

        oferta = "💰 OFERTA ESPECIAL"


    legenda = f"""🔥 ACHADINHO DA SHOPEE 🔥

🛍️ {nome}

{oferta}

⚡ Aproveite enquanto estiver disponível!

👇 Clique abaixo para conferir:

🛒 COMPRAR AGORA"""


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


    # ------------------------------------------
    # PUBLICAR COM FOTO
    # ------------------------------------------

    if imagem:

        url = (
            f"https://api.telegram.org/"
            f"bot{TOKEN}/sendPhoto"
        )

        dados = {

            "chat_id": CHAT_ID,

            "photo": imagem,

            "caption": legenda,

            "reply_markup": json.dumps(
                teclado
            )

        }

    else:

        url = (
            f"https://api.telegram.org/"
            f"bot{TOKEN}/sendMessage"
        )

        dados = {

            "chat_id": CHAT_ID,

            "text": legenda,

            "reply_markup": json.dumps(
                teclado
            )

        }


    try:

        resposta = requests.post(
            url,
            data=dados,
            timeout=30
        )

        resultado = resposta.json()

    except Exception as erro:

        print(
            "❌ Erro ao publicar:",
            erro
        )

        return False


    if resultado.get("ok"):

        fila.pop(0)

        salvar_fila(fila)

        print(
            "================================"
        )

        print(
            "✅ PRODUTO PUBLICADO"
        )

        print(
            "🛒",
            nome
        )

        print(
            "📦 Restam:",
            len(fila)
        )

        print(
            "================================"
        )

        return True


    print(
        "❌ Telegram recusou a publicação."
    )

    print(resultado)

    print(
        "⚠️ Produto continua na fila."
    )

    return False


# ==========================================
# PUBLICADOR AUTOMÁTICO
# ==========================================

def publicador_automatico():

    print(
        "🚀 Publicador automático iniciado."
    )

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


    mensagem = (
        "📦 PRODUTOS NA FILA\n\n"
    )


    for numero, produto in enumerate(
        fila,
        start=1
    ):

        mensagem += (
            f"{numero}. 🛒 "
            f"{produto['nome']}\n"
            f"🔗 {produto['link']}\n\n"
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


    # ------------------------------------------
    # SEGURANÇA
    # ------------------------------------------

    if ADMIN_ID and str(
        mensagem.get(
            "from",
            {}
        ).get("id")
    ) != ADMIN_ID:

        return


    # ==========================================
    # FOTO + LEGENDA
    # ==========================================

    if "photo" in mensagem:

        file_id = mensagem[
            "photo"
        ][-1]["file_id"]


        legenda = mensagem.get(
            "caption",
            ""
        ).strip()


        linhas = [

            l.strip()

            for l in legenda.split("\n")

            if l.strip()

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


        # --------------------------------------
        # TENTA PEGAR PREÇO DA SHOPEE
        # --------------------------------------

        nome_shopee, preco, imagem_shopee = (
            extrair_dados_shopee(link)
        )


        if nome_shopee:

            nome = nome_shopee


        # A foto enviada pelo usuário tem prioridade
        imagem = file_id


        adicionado, posicao = (
            adicionar_na_fila(
                nome,
                link,
                imagem,
                preco
            )
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

💰 Preço: {preco or 'não encontrado'}

📦 Posição na fila: {posicao}

🖼️ Foto salva.
⏰ Publicação automática ativa."""
        )

        return


    # ==========================================
    # TEXTO
    # ==========================================

    texto = mensagem.get(
        "text",
        ""
    ).strip()


    # ------------------------------------------
    # START
    # ------------------------------------------

    if texto == "/start":

        enviar_mensagem(
            chat_id,
            """🤖 ACHADOS DA SHOPEE

📸 Para adicionar produto:

Envie uma FOTO com a legenda:

Nome do produto
Link da Shopee

Ou envie somente:

Link da Shopee

O bot tentará encontrar automaticamente:
🛒 Nome
💰 Preço
🖼️ Imagem

Comandos:

/fila
Ver fila

/limparfila
Limpar fila"""
        )

        return


    # ------------------------------------------
    # FILA
    # ------------------------------------------

    if texto == "/fila":

        mostrar_fila(chat_id)

        return


    # ------------------------------------------
    # LIMPAR FILA
    # ------------------------------------------

    if texto == "/limparfila":

        limpar_fila(chat_id)

        return


    # ==========================================
    # SOMENTE LINK DA SHOPEE
    # ==========================================

    if texto.startswith("http"):

        link = texto.split()[0]


        print(
            "🔎 Link recebido:",
            link
        )


        nome, preco, imagem = (
            extrair_dados_shopee(link)
        )


        if not nome:

            enviar_mensagem(
                chat_id,
                """⚠️ Não consegui identificar o nome do produto automaticamente.

Envie uma FOTO com a legenda:

Nome do produto
Link da Shopee"""
            )

            return


        adicionado, posicao = (
            adicionar_na_fila(
                nome,
                link,
                imagem,
                preco
            )
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

💰 Preço: {preco or 'não encontrado'}

🖼️ Imagem: {'encontrada ✅' if imagem else 'não encontrada ⚠️'}

📦 Posição: {posicao}

⏰ Será publicado automaticamente."""
        )

        return


    # ==========================================
    # TEXTO COM NOME + LINK
    # ==========================================

    linhas = [

        l.strip()

        for l in texto.split("\n")

        if l.strip()

    ]


    if len(linhas) < 2:

        enviar_mensagem(
            chat_id,
            """❌ Formato incorreto.

Você pode enviar:

1️⃣ Somente o link da Shopee

OU

2️⃣

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


    # Tenta complementar os dados
    nome_shopee, preco, imagem = (
        extrair_dados_shopee(link)
    )


    if nome_shopee:

        nome = nome_shopee


    adicionado, posicao = (
        adicionar_na_fila(
            nome,
            link,
            imagem,
            preco
        )
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

💰 Preço: {preco or 'não encontrado'}

🖼️ Imagem: {'encontrada ✅' if imagem else 'não encontrada ⚠️'}

📦 Posição: {posicao}

⏰ Será publicado automaticamente."""
    )


# ==========================================
# RECEBER TELEGRAM
# ==========================================

def iniciar_bot():

    print(
        "📥 Sistema de recebimento iniciado."
    )

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

                parametros[
                    "offset"
                ] = ultimo_update + 1


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
# MINI SERVIDOR DO RENDER
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

    HTTPServer(
        ("0.0.0.0", porta),
        Saude
    ).serve_forever()


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
        "🔎 CAPTURA AUTOMÁTICA SHOPEE ATIVA"
    )

    print(
        "======================================"
    )


    iniciar_bot()
