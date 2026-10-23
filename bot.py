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

    print("🔎 ABRINDO SHOPEE NO NAVEGADOR:")
    print(link)

    nome = None
    preco = None
    imagem = None

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:

            navegador = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu"
                ]
            )

            pagina = navegador.new_page(
                viewport={
                    "width": 1280,
                    "height": 900
                },
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                locale="pt-BR"
            )

            pagina.set_default_timeout(15000)

            print("🌐 Carregando página...")

            resposta = pagina.goto(
                link,
                wait_until="domcontentloaded",
                timeout=30000
            )

            if resposta:
                print(
                    "📡 Status:",
                    resposta.status
                )

            # Dá tempo para a Shopee carregar os dados
            pagina.wait_for_timeout(7000)

            # ==========================================
            # TEXTO VISÍVEL DA PÁGINA
            # ==========================================

            try:
                texto = pagina.locator("body").inner_text(
                    timeout=10000
                )
            except Exception:
                texto = ""

            print(
                "📄 Texto carregado:",
                len(texto),
                "caracteres"
            )

            # ==========================================
            # HTML COMPLETO APÓS JAVASCRIPT
            # ==========================================

            try:
                html = pagina.content()
            except Exception:
                html = ""

            print(
                "📄 HTML final:",
                len(html),
                "caracteres"
            )

            # ==========================================
            # NOME
            # ==========================================

            try:

                titulo = pagina.locator(
                    'meta[property="og:title"]'
                ).get_attribute("content")

                if titulo:
                    titulo = unescape(titulo).strip()

                    if titulo.lower() not in [
                        "shopee",
                        "shopee brasil",
                        "ofertas incríveis. melhores preços do mercado"
                    ]:
                        nome = titulo

            except Exception:
                pass

            # Tenta títulos visíveis se o meta não funcionar
            if not nome:

                seletores_nome = [
                    "h1",
                    '[class*="product-title"]',
                    '[class*="item-title"]',
                    '[class*="name"]'
                ]

                for seletor in seletores_nome:

                    try:

                        elementos = pagina.locator(
                            seletor
                        )

                        quantidade = elementos.count()

                        for i in range(
                            min(quantidade, 10)
                        ):

                            valor = elementos.nth(i).inner_text(
                                timeout=3000
                            ).strip()

                            valor = re.sub(
                                r"\s+",
                                " ",
                                valor
                            )

                            if (
                                len(valor) >= 5
                                and "shopee" not in valor.lower()
                            ):

                                nome = valor
                                break

                        if nome:
                            break

                    except Exception:
                        continue

            # ==========================================
            # IMAGEM
            # ==========================================

            try:

                imagem_meta = pagina.locator(
                    'meta[property="og:image"]'
                ).get_attribute("content")

                if imagem_meta:
                    imagem = imagem_meta

            except Exception:
                pass

            # ==========================================
            # PREÇO
            # ==========================================

            # Primeiro procura preços VISÍVEIS
            # na página carregada pelo navegador.

            padroes_preco = [

                # R$ 29,90
                r'R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})',

                # R$ 29.90
                r'R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*\.[0-9]{2})',

                # R$29,90
                r'R\$\s*([0-9]+,[0-9]{2})',

                # BRL 29,90
                r'BRL\s*([0-9]+,[0-9]{2})',

                # números com formato decimal
                r'\b([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})\b'
            ]

            for padrao in padroes_preco:

                resultado = re.search(
                    padrao,
                    texto,
                    re.IGNORECASE
                )

                if resultado:

                    candidato = resultado.group(1)

                    try:

                        valor = float(
                            candidato
                            .replace(".", "")
                            .replace(",", ".")
                        )

                        if 0.01 <= valor < 1000000:

                            preco = candidato

                            print(
                                "💰 PREÇO ENCONTRADO NA PÁGINA:",
                                preco
                            )

                            break

                    except Exception:
                        continue

            # ==========================================
            # SEGUNDA TENTATIVA:
            # HTML APÓS JAVASCRIPT
            # ==========================================

            if not preco and html:

                padroes_html = [

                    r'"price"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',
                    r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

                    r'"salePrice"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',
                    r'"salePrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

                    r'"currentPrice"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',
                    r'"currentPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

                    r'"minPrice"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',
                    r'"minPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

                    r'"priceMin"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',
                    r'"priceMin"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
                ]

                for padrao in padroes_html:

                    resultado = re.search(
                        padrao,
                        html,
                        re.IGNORECASE
                    )

                    if resultado:

                        candidato = resultado.group(1)

                        try:

                            valor = float(
                                candidato.replace(",", ".")
                            )

                            if 0.01 <= valor < 1000000:

                                preco = candidato

                                print(
                                    "💰 PREÇO ENCONTRADO NO HTML DINÂMICO:",
                                    preco
                                )

                                break

                        except Exception:
                            continue

            # ==========================================
            # TERCEIRA TENTATIVA:
            # JSON-LD APÓS CARREGAMENTO
            # ==========================================

            if not preco and html:

                jsonld = extrair_jsonld(html)

                (
                    nome_json,
                    imagem_json,
                    preco_json
                ) = procurar_produto_jsonld(jsonld)

                if not nome and nome_json:
                    nome = str(nome_json).strip()

                if not imagem and imagem_json:

                    if isinstance(
                        imagem_json,
                        list
                    ):

                        if imagem_json:
                            imagem = imagem_json[0]

                    else:
                        imagem = imagem_json

                if preco_json:

                    preco = str(
                        preco_json
                    ).strip()

                    print(
                        "💰 PREÇO ENCONTRADO NO JSON-LD:",
                        preco
                    )

            # ==========================================
            # FECHAR NAVEGADOR
            # ==========================================

            navegador.close()

    except Exception as erro:

        print(
            "❌ Erro no leitor Shopee:",
            repr(erro)
        )

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
    # LIMPAR PREÇO
    # ==========================================

    if preco:

        preco = str(preco).strip()

        try:

            valor = float(
                preco
                .replace("R$", "")
                .replace("BRL", "")
                .replace(" ", "")
                .replace(".", "")
                .replace(",", ".")
            )

            preco = (
                f"R$ {valor:.2f}"
                .replace(".", ",")
            )

        except Exception as erro:

            print(
                "⚠️ Não consegui formatar preço:",
                preco,
                erro
            )

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
    print("======================================")
    print("🔎 RESULTADO DO LEITOR SHOPEE")
    print("🛒 Nome:", nome)
    print("💰 Preço:", preco)
    print("🖼️ Imagem:", imagem)
    print("======================================")
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
