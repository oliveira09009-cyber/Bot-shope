import requests
import json
import os
import time
import threading
import re
import html as html_lib
from urllib.parse import urljoin
from http.server import BaseHTTPRequestHandler, HTTPServer

# ============================================================
# CONFIGURAÇÕES
# ============================================================

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID", "-1003905816059")

REDIS_URL = os.environ["UPSTASH_REDIS_REST_URL"]
REDIS_TOKEN = os.environ["UPSTASH_REDIS_REST_TOKEN"]

ADMIN_ID = os.environ.get("ADMIN_ID")

# 5 produtos por hora
INTERVALO = 720

# ============================================================
# HEADERS SHOPEE
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,image/webp,"
        "*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Referer": "https://shopee.com.br/"
}

# ============================================================
# SESSÃO HTTP
# ============================================================

def nova_sessao():
    sessao = requests.Session()
    sessao.headers.update(HEADERS)
    return sessao


# ============================================================
# REDIS
# ============================================================

def redis_cmd(*args, timeout=8):

    resposta = requests.post(
        REDIS_URL,
        headers={
            "Authorization": f"Bearer {REDIS_TOKEN}",
            "Content-Type": "application/json"
        },
        json=list(args),
        timeout=timeout
    )

    resposta.raise_for_status()

    dados = resposta.json()

    return dados.get("result")


def carregar_fila():

    try:

        dado = redis_cmd("GET", "fila")

        if not dado:
            return []

        fila = json.loads(dado)

        if isinstance(fila, list):
            return fila

    except Exception as erro:

        print(
            "❌ Erro carregando fila:",
            repr(erro)
        )

    return []


def salvar_fila(fila):

    redis_cmd(
        "SET",
        "fila",
        json.dumps(
            fila,
            ensure_ascii=False
        )
    )


def adicionar_na_fila(
    nome,
    link,
    imagem=None,
    preco=None
):

    fila = carregar_fila()

    for produto in fila:

        if produto.get("link") == link:

            return False, len(fila)

    produto = {
        "nome": nome or "Produto Shopee",
        "link": link,
        "imagem": imagem,
        "preco": preco
    }

    fila.append(produto)

    salvar_fila(fila)

    return True, len(fila)


def atualizar_produto(link, nome=None, imagem=None, preco=None):

    fila = carregar_fila()

    alterou = False

    for produto in fila:

        if produto.get("link") != link:
            continue

        if nome and (
            not produto.get("nome")
            or produto.get("nome") == "Produto Shopee"
        ):
            produto["nome"] = nome
            alterou = True

        if imagem and not produto.get("imagem"):
            produto["imagem"] = imagem
            alterou = True

        if preco and not produto.get("preco"):
            produto["preco"] = preco
            alterou = True

        break

    if alterou:
        salvar_fila(fila)

    return alterou


# ============================================================
# TELEGRAM
# ============================================================

def telegram_post(endpoint, data=None, files=None, timeout=15):

    try:

        resposta = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/{endpoint}",
            data=data,
            files=files,
            timeout=timeout
        )

        return resposta.json()

    except Exception as erro:

        print(
            "❌ Erro Telegram:",
            repr(erro)
        )

        return {
            "ok": False,
            "error": str(erro)
        }


def enviar_mensagem(chat_id, texto):

    return telegram_post(
        "sendMessage",
        data={
            "chat_id": chat_id,
            "text": texto
        },
        timeout=12
    )


# ============================================================
# LIMPEZA
# ============================================================

def limpar_texto(valor):

    if valor is None:
        return None

    valor = html_lib.unescape(
        str(valor)
    )

    valor = valor.replace(
        "\\/",
        "/"
    )

    valor = valor.replace(
        "\\u002F",
        "/"
    )

    valor = valor.replace(
        "\\u003A",
        ":"
    )

    valor = valor.replace(
        "\\u0026",
        "&"
    )

    valor = re.sub(
        r"<[^>]+>",
        " ",
        valor
    )

    valor = re.sub(
        r"\s+",
        " ",
        valor
    )

    return valor.strip()


def normalizar_url(url):

    if not url:
        return None

    url = limpar_texto(url)

    if not url:
        return None

    if url.startswith("//"):
        url = "https:" + url

    elif url.startswith("/"):
        url = urljoin(
            "https://shopee.com.br/",
            url
        )

    if not url.startswith(
        ("http://", "https://")
    ):
        return None

    return url


# ============================================================
# META TAG
# ============================================================

def extrair_meta(html, nome):

    if not html:
        return None

    padroes = [

        rf'<meta[^>]+property=["\']'
        rf'{re.escape(nome)}'
        rf'["\'][^>]+content=["\']'
        rf'([^"\']+)["\']',

        rf'<meta[^>]+content=["\']'
        rf'([^"\']+)["\'][^>]+property=["\']'
        rf'{re.escape(nome)}'
        rf'["\']',

        rf'<meta[^>]+name=["\']'
        rf'{re.escape(nome)}'
        rf'["\'][^>]+content=["\']'
        rf'([^"\']+)["\']',

        rf'<meta[^>]+content=["\']'
        rf'([^"\']+)["\'][^>]+name=["\']'
        rf'{re.escape(nome)}'
        rf'["\']'
    ]

    for padrao in padroes:

        encontrado = re.search(
            padrao,
            html,
            re.IGNORECASE
        )

        if encontrado:

            return limpar_texto(
                encontrado.group(1)
            )

    return None


# ============================================================
# JSON-LD
# ============================================================

def extrair_jsonld(html):

    resultados = []

    blocos = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>'
        r'(.*?)'
        r'</script>',
        html,
        re.IGNORECASE | re.DOTALL
    )

    for bloco in blocos:

        try:

            dados = json.loads(
                html_lib.unescape(
                    bloco.strip()
                )
            )

            if isinstance(dados, list):

                resultados.extend(dados)

            elif isinstance(dados, dict):

                resultados.append(dados)

        except Exception:

            continue

    return resultados


def procurar_jsonld_produto(dados):

    nome = None
    imagem = None
    preco = None

    for item in dados:

        if not isinstance(item, dict):
            continue

        tipo = item.get("@type")

        if isinstance(tipo, list):

            eh_produto = any(
                str(x).lower() == "product"
                for x in tipo
            )

        else:

            eh_produto = (
                str(tipo).lower() == "product"
            )

        if not eh_produto:
            continue

        nome = item.get("name")

        imagem = item.get("image")

        ofertas = item.get("offers")

        if isinstance(ofertas, list):

            if ofertas:
                ofertas = ofertas[0]

        if isinstance(ofertas, dict):

            preco = (
                ofertas.get("price")
                or ofertas.get("lowPrice")
                or ofertas.get("highPrice")
            )

        break

    if isinstance(imagem, list):

        imagem = (
            imagem[0]
            if imagem
            else None
        )

    return (
        limpar_texto(nome),
        normalizar_url(imagem),
        preco
    )


# ============================================================
# PREÇO
# ============================================================

def converter_preco(valor):

    if valor is None:
        return None

    try:

        texto = str(valor).strip()

        if not texto:
            return None

        texto = (
            texto
            .replace("R$", "")
            .replace("BRL", "")
            .replace(" ", "")
        )

        if "," in texto:

            texto = texto.replace(
                ".",
                ""
            )

            texto = texto.replace(
                ",",
                "."
            )

            numero = float(texto)

        else:

            numero = float(texto)

        if numero <= 0:
            return None

        # Valores internos da Shopee
        if numero >= 1000000:
            numero /= 100000

        elif numero >= 10000:
            numero /= 100

        elif numero >= 1000 and numero.is_integer():
            numero /= 100

        if numero <= 0 or numero > 500000:
            return None

        return round(
            numero,
            2
        )

    except Exception:

        return None


def preco_valido(valor):

    numero = converter_preco(valor)

    return (
        numero is not None
        and 0.01 <= numero <= 500000
    )


def precos_do_html(html):

    if not html:
        return []

    candidatos = []

    padroes = [

        r'"price"\s*:\s*"([^"]+)"',

        r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"price_min"\s*:\s*"([^"]+)"',

        r'"price_min"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"priceMin"\s*:\s*"([^"]+)"',

        r'"priceMin"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"salePrice"\s*:\s*"([^"]+)"',

        r'"salePrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"currentPrice"\s*:\s*"([^"]+)"',

        r'"currentPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"current_price"\s*:\s*"([^"]+)"',

        r'"current_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"final_price"\s*:\s*"([^"]+)"',

        r'"final_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"sale_price"\s*:\s*"([^"]+)"',

        r'"sale_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"display_price"\s*:\s*"([^"]+)"',

        r'"display_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            html,
            re.IGNORECASE
        )

        for valor in encontrados:

            numero = converter_preco(
                valor
            )

            if preco_valido(numero):

                candidatos.append(
                    numero
                )

    return candidatos


# ============================================================
# IMAGEM
# ============================================================

def encontrar_imagem_html(html):

    if not html:
        return None

    candidatos = []

    def adicionar(url):

        if not url:
            return

        url = normalizar_url(url)

        if not url:
            return

        baixo = url.lower()

        ignorar = (
            "logo",
            "favicon",
            "avatar",
            "icon",
            "sprite",
            "placeholder",
            "loading",
            "default_image"
        )

        if any(
            palavra in baixo
            for palavra in ignorar
        ):
            return

        candidatos.append(url)

    # PRIMEIRO: OG IMAGE
    adicionar(
        extrair_meta(
            html,
            "og:image"
        )
    )

    adicionar(
        extrair_meta(
            html,
            "og:image:secure_url"
        )
    )

    # SEGUNDO: SUSERCONTENT
    padroes = [

        r'https?://[^"\'<>\s]+susercontent\.com/file/[^"\'<>\s]+',

        r'https?:\\?/\\?/[^"\'<>\s]+susercontent\.com/file/[^"\'<>\s]+',

        r'https?://[^"\'<>\s]*img\.susercontent\.com/[^"\'<>\s]+',

        r'https?:\\?/\\?/[^"\'<>\s]*img\.susercontent\.com/[^"\'<>\s]+',

        r'"image"\s*:\s*"([^"]+)"',

        r'"imageUrl"\s*:\s*"([^"]+)"',

        r'"image_url"\s*:\s*"([^"]+)"',

        r'"coverImage"\s*:\s*"([^"]+)"',

        r'"cover_image"\s*:\s*"([^"]+)"',

        r'"mainImage"\s*:\s*"([^"]+)"',

        r'"main_image"\s*:\s*"([^"]+)"',

        r'"originalImage"\s*:\s*"([^"]+)"',

        r'"original_image"\s*:\s*"([^"]+)"'
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            html,
            re.IGNORECASE
        )

        for encontrado in encontrados:

            adicionar(
                encontrado
            )

            if len(candidatos) >= 30:
                break

        if candidatos:
            break

    # REMOVE DUPLICADAS
    unicos = []
    vistos = set()

    for url in candidatos:

        chave = url.lower()

        if chave in vistos:
            continue

        vistos.add(chave)
        unicos.append(url)

    # PRIORIDADE SUSERCONTENT
    for url in unicos:

        baixo = url.lower()

        if (
            "susercontent.com/file/"
            in baixo
        ):
            return url

    for url in unicos:

        if "susercontent.com" in url.lower():
            return url

    return (
        unicos[0]
        if unicos
        else None
    )


# ============================================================
# NOME
# ============================================================

def encontrar_nome_html(html):

    if not html:
        return None

    nome = extrair_meta(
        html,
        "og:title"
    )

    if nome:

        if (
            len(nome) >= 5
            and nome.lower()
            not in (
                "shopee",
                "shopee brasil"
            )
        ):
            return nome

    titulo = re.search(
        r'<title[^>]*>(.*?)</title>',
        html,
        re.IGNORECASE | re.DOTALL
    )

    if titulo:

        nome = limpar_texto(
            titulo.group(1)
        )

        if (
            nome
            and len(nome) >= 5
            and "shopee" not in nome.lower()
        ):
            return nome

    return None


# ============================================================
# FORMATA PREÇO
# ============================================================

def formatar_preco(valor):

    numero = converter_preco(
        valor
    )

    if numero is None:
        return None

    return (
        f"R$ {numero:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


# ============================================================
# LEITOR SHOPEE LEVE
# ============================================================

def extrair_dados_shopee(link):

    print("")
    print(
        "🔎 Lendo Shopee..."
    )

    print(
        "🔗",
        link
    )

    nome = None
    preco = None
    imagem = None

    try:

        sessao = nova_sessao()

        resposta = sessao.get(
            link,
            timeout=15,
            allow_redirects=True
        )

        if resposta.status_code != 200:

            print(
                "⚠️ HTTP:",
                resposta.status_code
            )

            return None, None, None

        html = resposta.text

        print(
            "📄 HTML:",
            len(html),
            "caracteres"
        )

        # ====================================================
        # NOME
        # ====================================================

        nome = encontrar_nome_html(
            html
        )

        # ====================================================
        # IMAGEM
        # ====================================================

        imagem = encontrar_imagem_html(
            html
        )

        # ====================================================
        # JSON-LD
        # ====================================================

        try:

            dados = extrair_jsonld(
                html
            )

            (
                nome_json,
                imagem_json,
                preco_json
            ) = procurar_jsonld_produto(
                dados
            )

            if not nome and nome_json:
                nome = nome_json

            if not imagem and imagem_json:
                imagem = imagem_json

            if preco_json:

                numero = converter_preco(
                    preco_json
                )

                if preco_valido(numero):
                    preco = numero

        except Exception as erro:

            print(
                "⚠️ JSON-LD:",
                repr(erro)
            )

        # ====================================================
        # PREÇO
        # ====================================================

        if preco is None:

            precos = precos_do_html(
                html
            )

            if precos:

                # Preferir valores menores
                # e razoáveis
                precos = sorted(
                    set(precos)
                )

                preco = precos[0]

        # ====================================================
        # LIMPEZA
        # ====================================================

        if nome:

            nome = limpar_texto(
                nome
            )

            nome = re.sub(
                r'\s*[-|]\s*Shopee.*$',
                '',
                nome,
                flags=re.IGNORECASE
            ).strip()

        if preco:

            preco = formatar_preco(
                preco
            )

        if imagem:

            imagem = normalizar_url(
                imagem
            )

        print(
            "🛒 Nome:",
            nome or "não encontrado"
        )

        print(
            "💰 Preço:",
            preco or "não encontrado"
        )

        print(
            "🖼️ Imagem:",
            imagem or "não encontrada"
        )

        return (
            nome,
            preco,
            imagem
        )

    except Exception as erro:

        print(
            "❌ Erro leitor Shopee:",
            repr(erro)
        )

        return (
            nome,
            preco,
            imagem
        )


# ============================================================
# PUBLICAR FOTO
# ============================================================

def enviar_foto_produto(
    imagem,
    legenda,
    teclado
):

    # ========================================================
    # 1. FILE_ID TELEGRAM
    # ========================================================

    if (
        imagem
        and not imagem.startswith(
            ("http://", "https://")
        )
    ):

        print(
            "📸 Usando FILE_ID do Telegram."
        )

        return telegram_post(
            "sendPhoto",
            data={
                "chat_id": CHAT_ID,
                "photo": imagem,
                "caption": legenda,
                "reply_markup": json.dumps(
                    teclado,
                    ensure_ascii=False
                )
            },
            timeout=15
        )

    # ========================================================
    # 2. TENTAR URL DIRETA
    # ========================================================

    if imagem:

        print(
            "🌐 Tentando enviar URL da imagem diretamente..."
        )

        resultado = telegram_post(
            "sendPhoto",
            data={
                "chat_id": CHAT_ID,
                "photo": imagem,
                "caption": legenda,
                "reply_markup": json.dumps(
                    teclado,
                    ensure_ascii=False
                )
            },
            timeout=20
        )

        if resultado.get("ok"):

            print(
                "✅ Telegram aceitou a URL da imagem."
            )

            return resultado

        print(
            "⚠️ Telegram não aceitou URL."
        )

    # ========================================================
    # 3. BAIXAR E ENVIAR
    # ========================================================

    if imagem:

        try:

            print(
                "⬇️ Baixando imagem como fallback..."
            )

            sessao = nova_sessao()

            resposta = sessao.get(
                imagem,
                timeout=15,
                allow_redirects=True
            )

            resposta.raise_for_status()

            conteudo = resposta.content

            if not conteudo:

                raise Exception(
                    "Imagem vazia."
                )

            print(
                "📦 Imagem:",
                len(conteudo),
                "bytes"
            )

            arquivos = {
                "photo": (
                    "produto.jpg",
                    conteudo,
                    "image/jpeg"
                )
            }

            resultado = telegram_post(
                "sendPhoto",
                data={
                    "chat_id": CHAT_ID,
                    "caption": legenda,
                    "reply_markup": json.dumps(
                        teclado,
                        ensure_ascii=False
                    )
                },
                files=arquivos,
                timeout=30
            )

            return resultado

        except Exception as erro:

            print(
                "❌ Falha baixando imagem:",
                repr(erro)
            )

    return {
        "ok": False,
        "error": "Imagem indisponível"
    }


# ============================================================
# PUBLICAR PRÓXIMO
# ============================================================

def publicar_proximo():

    fila = carregar_fila()

    if not fila:

        print(
            "📭 Fila vazia."
        )

        return False

    produto = fila[0]

    nome = (
        produto.get("nome")
        or "Produto Shopee"
    )

    link = produto.get(
        "link"
    )

    imagem = produto.get(
        "imagem"
    )

    preco = produto.get(
        "preco"
    )

    oferta = (
        f"💰 {preco}"
        if preco
        else
        "💰 OFERTA ESPECIAL"
    )

    legenda = f"""🔥 ACHADINHO DA SHOPEE 🔥

🛍️ {nome}

{oferta}

⚡ Aproveite enquanto estiver disponível!

👇 Clique abaixo para conferir:"""

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

    print("")
    print(
        "======================================"
    )

    print(
        "📤 PUBLICANDO:"
    )

    print(
        "🛒",
        nome
    )

    print(
        "🖼️",
        imagem or "sem imagem"
    )

    # ========================================================
    # FOTO
    # ========================================================

    resultado = enviar_foto_produto(
        imagem,
        legenda,
        teclado
    )

    # ========================================================
    # FALLBACK TEXTO
    # ========================================================

    if not resultado.get("ok"):

        print(
            "⚠️ Foto falhou."
        )

        print(
            "➡️ Enviando texto..."
        )

        resultado = telegram_post(
            "sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": legenda,
                "reply_markup": json.dumps(
                    teclado,
                    ensure_ascii=False
                )
            },
            timeout=15
        )

    # ========================================================
    # CONFIRMAÇÃO
    # ========================================================

    if resultado.get("ok"):

        fila.pop(0)

        salvar_fila(
            fila
        )

        print(
            "✅ PRODUTO PUBLICADO!"
        )

        print(
            "📦 Restam:",
            len(fila)
        )

        return True

    print(
        "❌ Falha Telegram:",
        resultado
    )

    return False


# ============================================================
# PUBLICADOR AUTOMÁTICO
# ============================================================

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

            agora = time.time()

            if (
                agora - ultimo
                >= INTERVALO
            ):

                if publicar_proximo():

                    redis_cmd(
                        "SET",
                        "ultimo_post",
                        str(agora)
                    )

        except Exception as erro:

            print(
                "❌ Erro publicador:",
                repr(erro)
            )

        time.sleep(30)


# ============================================================
# MOSTRAR FILA
# ============================================================

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
            f"{produto.get('nome', 'Produto')}\n"
            f"💰 "
            f"{produto.get('preco') or 'não encontrado'}\n"
            f"🔗 "
            f"{produto.get('link', '')}\n\n"
        )

    enviar_mensagem(
        chat_id,
        mensagem[:4000]
    )


def limpar_fila(chat_id):

    salvar_fila([])

    enviar_mensagem(
        chat_id,
        "🗑️ Fila limpa com sucesso!"
    )


# ============================================================
# PROCESSAMENTO EM SEGUNDO PLANO
# ============================================================

def processar_link_background(
    chat_id,
    link,
    nome_informado=None,
    imagem_telegram=None
):

    print("")
    print(
        "🧵 PROCESSAMENTO EM SEGUNDO PLANO"
    )

    nome = nome_informado
    imagem = imagem_telegram
    preco = None

    # ========================================================
    # FOTO DO TELEGRAM
    # ========================================================

    if imagem_telegram:

        print(
            "🖼️ Usando foto recebida do Telegram."
        )

    # ========================================================
    # LEITURA SHOPEE
    # ========================================================

    (
        nome_shopee,
        preco,
        imagem_shopee
    ) = extrair_dados_shopee(
        link
    )

    if nome_shopee:

        nome = nome_shopee

    # A foto do Telegram continua tendo
    # prioridade.
    if not imagem:

        imagem = imagem_shopee

    # ========================================================
    # ADICIONAR
    # ========================================================

    adicionado, posicao = adicionar_na_fila(
        nome or "Produto Shopee",
        link,
        imagem,
        preco
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

🛒 {nome or 'Produto Shopee'}

💰 Preço: {preco or 'não encontrado'}

🖼️ Imagem: {'encontrada ✅' if imagem else 'não encontrada ⚠️'}

📦 Posição: {posicao}

⏰ Será publicado automaticamente."""
    )


# ============================================================
# PROCESSAR MENSAGEM
# ============================================================

def processar_mensagem(mensagem):

    chat_id = mensagem["chat"]["id"]

    # ========================================================
    # ADMIN
    # ========================================================

    if ADMIN_ID:

        remetente = (
            mensagem
            .get("from", {})
            .get("id")
        )

        if str(remetente) != str(ADMIN_ID):

            return

    # ========================================================
    # FOTO + LEGENDA
    # ========================================================

    if "photo" in mensagem:

        legenda = (
            mensagem
            .get("caption", "")
            .strip()
        )

        linhas = [
            linha.strip()
            for linha in legenda.split("\n")
            if linha.strip()
        ]

        if len(linhas) < 2:

            enviar_mensagem(
                chat_id,
                """❌ FORMATO INCORRETO

Envie a FOTO com:

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

        # ====================================================
        # FOTO ORIGINAL DO TELEGRAM
        # ====================================================

        file_id = (
            mensagem["photo"][-1]["file_id"]
        )

        # ====================================================
        # RESPONDE IMEDIATAMENTE
        # ====================================================

        enviar_mensagem(
            chat_id,
            "⏳ Link recebido! Estou preparando o produto..."
        )

        # ====================================================
        # PROCESSA SEM TRAVAR O BOT
        # ====================================================

        threading.Thread(
            target=processar_link_background,
            args=(
                chat_id,
                link,
                nome,
                file_id
            ),
            daemon=True
        ).start()

        return

    # ========================================================
    # TEXTO
    # ========================================================

    texto = (
        mensagem
        .get("text", "")
        .strip()
    )

    if texto == "/start":

        enviar_mensagem(
            chat_id,
            """🤖 ACHADOS DA SHOPEE

📸 ADICIONAR PRODUTO

Envie:

FOTO + legenda

Nome do produto
Link da Shopee

OU simplesmente:

🔗 Link da Shopee

O sistema tenta encontrar:

🛒 Nome
💰 Preço
🖼️ Imagem

COMANDOS:

/fila
📦 Ver fila

/limparfila
🗑️ Limpar fila"""
        )

        return

    # ========================================================
    # FILA
    # ========================================================

    if texto == "/fila":

        mostrar_fila(
            chat_id
        )

        return

    # ========================================================
    # LIMPAR
    # ========================================================

    if texto == "/limparfila":

        limpar_fila(
            chat_id
        )

        return

    # ========================================================
    # SOMENTE LINK
    # ========================================================

    if texto.startswith("http"):

        link = texto.split()[0]

        print(
            "🔗 Link recebido:",
            link
        )

        # ====================================================
        # RESPONDE IMEDIATAMENTE
        # ====================================================

        enviar_mensagem(
            chat_id,
            "⏳ Link recebido! Estou preparando o produto..."
        )

        # ====================================================
        # PROCESSAMENTO EM SEGUNDO PLANO
        # ====================================================

        threading.Thread(
            target=processar_link_background,
            args=(
                chat_id,
                link,
                None,
                None
            ),
            daemon=True
        ).start()

        return

    # ========================================================
    # NOME + LINK
    # ========================================================

    linhas = [
        linha.strip()
        for linha in texto.split("\n")
        if linha.strip()
    ]

    if len(linhas) < 2:

        enviar_mensagem(
            chat_id,
            """❌ FORMATO INCORRETO

Você pode enviar:

1️⃣ Somente o link

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

    enviar_mensagem(
        chat_id,
        "⏳ Link recebido! Estou preparando o produto..."
    )

    threading.Thread(
        target=processar_link_background,
        args=(
            chat_id,
            link,
            nome,
            None
        ),
        daemon=True
    ).start()


# ============================================================
# RECEBER TELEGRAM
# ============================================================

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
                "timeout": 25
            }

            if ultimo_update is not None:

                parametros["offset"] = (
                    ultimo_update + 1
                )

            resposta = requests.get(
                url,
                params=parametros,
                timeout=30
            )

            dados = resposta.json()

            if not dados.get("ok"):

                print(
                    "❌ Erro Telegram:",
                    dados
                )

                time.sleep(3)

                continue

            for update in dados.get(
                "result",
                []
            ):

                ultimo_update = (
                    update["update_id"]
                )

                mensagem = update.get(
                    "message"
                )

                if not mensagem:
                    continue

                try:

                    processar_mensagem(
                        mensagem
                    )

                except Exception as erro:

                    print(
                        "❌ Erro processando:",
                        repr(erro)
                    )

                    try:

                        enviar_mensagem(
                            mensagem["chat"]["id"],
                            "⚠️ Erro temporário. Tente novamente."
                        )

                    except Exception:
                        pass

        except Exception as erro:

            print(
                "❌ Erro recebendo Telegram:",
                repr(erro)
            )

            time.sleep(3)


# ============================================================
# SERVIDOR RENDER
# ============================================================

class Saude(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain"
        )

        self.end_headers()

        self.wfile.write(
            b"ACHADOS DA SHOPEE ONLINE"
        )

    def do_HEAD(self):

        self.send_response(200)

        self.end_headers()

    def log_message(
        self,
        formato,
        *args
    ):

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

    print(
        f"🌐 Render ativo na porta {porta}"
    )

    servidor.serve_forever()


# ============================================================
# INICIAR
# ============================================================

if __name__ == "__main__":

    threading.Thread(
        target=servidor_saude,
        daemon=True
    ).start()

    threading.Thread(
        target=publicador_automatico,
        daemon=True
    ).start()

    print("")
    print(
        "======================================"
    )
    print(
        "✅ ACHADOS DA SHOPEE ONLINE"
    )
    print(
        "⚡ MODO LEVE ATIVO"
    )
    print(
        "🖼️ ENVIO DE IMAGEM OTIMIZADO"
    )
    print(
        "🧵 PROCESSAMENTO EM SEGUNDO PLANO"
    )
    print(
        "⏰ 5 PRODUTOS POR HORA"
    )
    print(
        "======================================"
    )
    print("")

    iniciar_bot()
