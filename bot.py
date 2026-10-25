import requests
import json
import os
import time
import threading
import re
import html
from urllib.parse import urljoin
from http.server import BaseHTTPRequestHandler, HTTPServer


# ============================================================
# CONFIGURAÇÕES
# ============================================================

TOKEN = os.environ["TELEGRAM_TOKEN"]

CHAT_ID = os.environ.get(
    "CHAT_ID",
    "-1003905816059"
)

REDIS_URL = os.environ["UPSTASH_REDIS_REST_URL"]

REDIS_TOKEN = os.environ[
    "UPSTASH_REDIS_REST_TOKEN"
]

ADMIN_ID = os.environ.get(
    "ADMIN_ID"
)

# 5 produtos por hora
# 3600 / 5 = 720 segundos
INTERVALO = 720


# ============================================================
# HEADERS
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Linux; Android 13; SM-G991B) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0.0.0 "
        "Mobile Safari/537.36"
    ),

    "Accept-Language": (
        "pt-BR,pt;q=0.9,"
        "en-US;q=0.8,en;q=0.7"
    ),

    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),

    "Connection": "keep-alive",

    "Cache-Control": "no-cache",

    "Pragma": "no-cache"
}


# ============================================================
# REDIS
# ============================================================

def redis_cmd(*args):

    resposta = requests.post(
        REDIS_URL,

        headers={
            "Authorization":
            f"Bearer {REDIS_TOKEN}"
        },

        json=list(args),

        timeout=15
    )

    resposta.raise_for_status()

    return resposta.json().get(
        "result"
    )


def carregar_fila():

    dado = redis_cmd(
        "GET",
        "fila"
    )

    if not dado:
        return []

    try:

        return json.loads(dado)

    except Exception as erro:

        print(
            "❌ Erro ao ler fila:",
            erro
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
    imagem=None
):

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


# ============================================================
# TELEGRAM
# ============================================================

def telegram_post(
    metodo,
    data=None,
    files=None
):

    url = (
        f"https://api.telegram.org/"
        f"bot{TOKEN}/{metodo}"
    )

    try:

        resposta = requests.post(
            url,
            data=data,
            files=files,
            timeout=40
        )

        try:

            return resposta.json()

        except Exception:

            return {
                "ok": False,
                "description": resposta.text
            }

    except Exception as erro:

        print(
            f"❌ Erro Telegram {metodo}:",
            erro
        )

        return {
            "ok": False,
            "description": str(erro)
        }


def enviar_mensagem(
    chat_id,
    texto
):

    return telegram_post(
        "sendMessage",

        {
            "chat_id": chat_id,
            "text": texto
        }
    )


# ============================================================
# RESOLVER LINK
# ============================================================

def resolver_link(link):

    if not link:
        return link

    try:

        print(
            "🔗 Link recebido:",
            link
        )

        resposta = requests.get(
            link,
            headers=HEADERS,
            allow_redirects=True,
            timeout=20
        )

        final = resposta.url

        print(
            "🔗 Link resolvido:",
            final
        )

        return final

    except Exception as erro:

        print(
            "⚠️ Erro ao resolver link:",
            erro
        )

        return link


# ============================================================
# BAIXAR PÁGINA
# ============================================================

def baixar_pagina(link):

    try:

        resposta = requests.get(
            link,
            headers=HEADERS,
            allow_redirects=True,
            timeout=30
        )

        print(
            "🌐 Status Shopee:",
            resposta.status_code
        )

        print(
            "📄 HTML recebido:",
            len(resposta.text),
            "caracteres"
        )

        if resposta.status_code != 200:

            print(
                "⚠️ Shopee retornou:",
                resposta.status_code
            )

            return None, resposta.url

        return (
            resposta.text,
            resposta.url
        )

    except Exception as erro:

        print(
            "❌ Erro ao baixar Shopee:",
            erro
        )

        return None, link


# ============================================================
# LIMPAR TEXTO
# ============================================================

def limpar_texto(texto):

    if not texto:
        return ""

    texto = html.unescape(
        texto
    )

    texto = texto.replace(
        "\\/",
        "/"
    )

    texto = texto.replace(
        "\\u002F",
        "/"
    )

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


# ============================================================
# CONVERTER PREÇO
# ============================================================

def converter_preco(valor):

    if valor is None:
        return None

    valor = str(valor).strip()

    valor = valor.replace(
        "R$",
        ""
    )

    valor = valor.replace(
        " ",
        ""
    )

    # 42,94
    if "," in valor:

        valor = valor.replace(
            ".",
            ""
        )

        valor = valor.replace(
            ",",
            "."
        )

    try:

        numero = float(valor)

    except Exception:

        return None

    if numero <= 0:
        return None

    # Proteção para valores em centavos
    if numero >= 100000:

        numero = numero / 100

    if numero >= 100000:
        return None

    return numero


def formatar_preco(numero):

    if numero is None:
        return None

    valor = (
        f"{numero:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )

    return f"R$ {valor}"


# ============================================================
# EXTRAIR PREÇOS
# ============================================================

def extrair_precos(texto):

    if not texto:

        return []

    texto = html.unescape(
        texto
    )

    precos = []

    # --------------------------------------------------------
    # R$ 42,94
    # R$42,94
    # --------------------------------------------------------

    padroes = [

        r"R\$\s*"
        r"([0-9]{1,3}"
        r"(?:\.[0-9]{3})*"
        r",[0-9]{2})",

        r"R\$\s*"
        r"([0-9]+"
        r"(?:\.[0-9]{2})?)",

    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            texto,
            re.IGNORECASE
        )

        for valor in encontrados:

            numero = converter_preco(
                valor
            )

            if numero is not None:

                precos.append(
                    numero
                )

    # --------------------------------------------------------
    # JSON:
    # "price": 42.94
    # "salePrice": 42.94
    # "currentPrice": 42.94
    # --------------------------------------------------------

    campos = [
        "price",
        "salePrice",
        "currentPrice",
        "priceMin",
        "priceMax",
        "discountPrice",
        "finalPrice"
    ]

    for campo in campos:

        padroes_json = [

            rf'"{campo}"\s*:\s*"([^"]+)"',

            rf'"{campo}"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        ]

        for padrao in padroes_json:

            encontrados = re.findall(
                padrao,
                texto,
                re.IGNORECASE
            )

            for valor in encontrados:

                numero = converter_preco(
                    valor
                )

                if numero is not None:

                    precos.append(
                        numero
                    )

    # --------------------------------------------------------
    # Remove duplicados
    # --------------------------------------------------------

    precos = sorted(
        set(
            round(p, 2)
            for p in precos
        )
    )

    return precos


# ============================================================
# EXTRAIR FAIXA DE PREÇO
# ============================================================

def extrair_faixa_preco(texto):

    if not texto:
        return None

    texto = html.unescape(
        texto
    )

    padroes = [

        r"R\$\s*"
        r"([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})"
        r"\s*[-–—]\s*"
        r"R\$\s*"
        r"([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})",

        r"R\$\s*"
        r"([0-9]+(?:[.,][0-9]{2})?)"
        r"\s*[-–—]\s*"
        r"R\$\s*"
        r"([0-9]+(?:[.,][0-9]{2})?)",

    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            texto,
            re.IGNORECASE
        )

        for primeiro, segundo in encontrados:

            p1 = converter_preco(
                primeiro
            )

            p2 = converter_preco(
                segundo
            )

            if p1 and p2:

                menor = min(
                    p1,
                    p2
                )

                maior = max(
                    p1,
                    p2
                )

                return {
                    "menor": menor,
                    "maior": maior
                }

    return None


# ============================================================
# EXTRAIR NOME
# ============================================================

def extrair_nome(
    html_text,
    url
):

    if not html_text:
        return None

    candidatos = []

    # --------------------------------------------------------
    # OG TITLE
    # --------------------------------------------------------

    padroes = [

        r'<meta[^>]+property=["\']og:title'
        r'["\'][^>]+content=["\']([^"\']+)',

        r'<meta[^>]+content=["\']([^"\']+)'
        r'["\'][^>]+property=["\']og:title["\']',

        r'<meta[^>]+name=["\']twitter:title'
        r'["\'][^>]+content=["\']([^"\']+)',

        r'<meta[^>]+content=["\']([^"\']+)'
        r'["\'][^>]+name=["\']twitter:title["\']',

    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            html_text,
            re.IGNORECASE
        )

        for nome in encontrados:

            nome = limpar_texto(
                nome
            )

            if len(nome) >= 10:

                candidatos.append(
                    nome
                )

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    encontrado = re.search(
        r"<title[^>]*>(.*?)</title>",
        html_text,
        re.IGNORECASE |
        re.DOTALL
    )

    if encontrado:

        nome = limpar_texto(
            encontrado.group(1)
        )

        if len(nome) >= 10:

            candidatos.append(
                nome
            )

    # --------------------------------------------------------
    # JSON-LD
    # --------------------------------------------------------

    blocos = re.findall(
        r'<script[^>]+type=["\']'
        r'application/ld\+json'
        r'["\'][^>]*>'
        r'(.*?)'
        r'</script>',

        html_text,

        re.IGNORECASE |
        re.DOTALL
    )

    for bloco in blocos:

        try:

            dados = json.loads(
                html.unescape(
                    bloco
                )
            )

            objetos = []

            if isinstance(
                dados,
                list
            ):

                objetos = dados

            elif isinstance(
                dados,
                dict
            ):

                objetos = [
                    dados
                ]

            for objeto in objetos:

                if not isinstance(
                    objeto,
                    dict
                ):
                    continue

                nome = objeto.get(
                    "name"
                )

                if nome:

                    nome = limpar_texto(
                        nome
                    )

                    if len(nome) >= 10:

                        candidatos.append(
                            nome
                        )

        except Exception:
            pass

    # --------------------------------------------------------
    # ESCOLHER MELHOR NOME
    # --------------------------------------------------------

    nomes_invalidos = [

        "shopee brasil",

        "ofertas incríveis",

        "ofertas incriveis",

        "melhores preços do mercado",

        "melhores precos do mercado",

        "compre na shopee",

        "shopee",

    ]

    candidatos_filtrados = []

    for nome in candidatos:

        nome_lower = nome.lower()

        if any(
            invalido in nome_lower
            for invalido in nomes_invalidos
        ):
            continue

        # Evita nomes muito genéricos
        if len(nome.split()) < 3:
            continue

        candidatos_filtrados.append(
            nome
        )

    if candidatos_filtrados:

        # Preferir o candidato mais específico
        melhor = max(
            candidatos_filtrados,
            key=len
        )

        print(
            "🛍️ Nome encontrado:",
            melhor
        )

        return melhor[:250]

    print(
        "⚠️ Nome do produto não encontrado."
    )

    return None


# ============================================================
# EXTRAIR IMAGEM
# ============================================================

def extrair_imagem(
    html_text,
    url_base
):

    if not html_text:
        return None

    imagens = []

    # --------------------------------------------------------
    # OG IMAGE
    # --------------------------------------------------------

    padroes = [

        r'<meta[^>]+property=["\']og:image'
        r'["\'][^>]+content=["\']([^"\']+)',

        r'<meta[^>]+content=["\']([^"\']+)'
        r'["\'][^>]+property=["\']og:image["\']',

        r'"og:image"\s*:\s*"([^"]+)"',

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

                imagem = (
                    "https:"
                    + imagem
                )

            elif imagem.startswith("/"):

                imagem = urljoin(
                    url_base,
                    imagem
                )

            if imagem.startswith(
                "http"
            ):

                imagens.append(
                    imagem
                )

    # --------------------------------------------------------
    # SUSERCONTENT
    # --------------------------------------------------------

    encontrados = re.findall(
        r'https?://[^"\']*'
        r'susercontent\.com[^"\']*',

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

        if imagem.startswith(
            "http"
        ):

            imagens.append(
                imagem
            )

    # --------------------------------------------------------
    # FILTROS
    # --------------------------------------------------------

    ignorar = [
        "logo",
        "avatar",
        "favicon",
        "placeholder",
        "icon",
        "sprite"
    ]

    finais = []

    for imagem in imagens:

        baixa = imagem.lower()

        if any(
            palavra in baixa
            for palavra in ignorar
        ):
            continue

        if imagem not in finais:

            finais.append(
                imagem
            )

    if finais:

        print(
            "🖼️ Imagem encontrada."
        )

        return finais[0]

    print(
        "⚠️ Imagem não encontrada."
    )

    return None


# ============================================================
# CONSULTAR PRODUTO SHOPEE
# ============================================================

def consultar_shopee(link):

    resultado = {

        "nome": None,

        "preco": None,

        "imagem": None,

        "link": link
    }

    # --------------------------------------------------------
    # RESOLVE LINK
    # --------------------------------------------------------

    link_final = resolver_link(
        link
    )

    # --------------------------------------------------------
    # BAIXA PÁGINA
    # --------------------------------------------------------

    html_text, url_real = (
        baixar_pagina(
            link_final
        )
    )

    if not html_text:

        return resultado

    # --------------------------------------------------------
    # NOME
    # --------------------------------------------------------

    resultado["nome"] = (
        extrair_nome(
            html_text,
            url_real
        )
    )

    # --------------------------------------------------------
    # IMAGEM
    # --------------------------------------------------------

    resultado["imagem"] = (
        extrair_imagem(
            html_text,
            url_real
        )
    )

    # --------------------------------------------------------
    # PREÇO
    # --------------------------------------------------------

    # Primeiro tenta faixa
    faixa = extrair_faixa_preco(
        html_text
    )

    if faixa:

        resultado["preco"] = {
            "tipo": "faixa",

            "menor":
                faixa["menor"],

            "maior":
                faixa["maior"]
        }

        print(
            "💰 Faixa encontrada:",
            formatar_preco(
                faixa["menor"]
            ),
            "-",
            formatar_preco(
                faixa["maior"]
            )
        )

    else:

        precos = extrair_precos(
            html_text
        )

        if precos:

            menor = min(
                precos
            )

            resultado["preco"] = {
                "tipo": "unico",

                "menor": menor,

                "maior": menor
            }

            print(
                "💰 Preço encontrado:",
                formatar_preco(
                    menor
                )
            )

        else:

            print(
                "💰 Preço: None"
            )

    print(
        "🛍️ Nome:",
        resultado["nome"]
    )

    print(
        "🖼️ Imagem:",
        "SIM"
        if resultado["imagem"]
        else "NÃO"
    )

    print(
        "🔗 URL:",
        url_real
    )

    print(
        "--------------------------------"
    )

    return resultado


# ============================================================
# MONTAR PREÇO DA PUBLICAÇÃO
# ============================================================

def texto_preco(preco):

    if not preco:

        return (
            "💰 OFERTA ESPECIAL"
        )

    if preco.get("tipo") == "faixa":

        menor = formatar_preco(
            preco["menor"]
        )

        return (
            f"💰 A PARTIR DE {menor}"
        )

    menor = formatar_preco(
        preco["menor"]
    )

    return (
        f"💰 {menor}"
    )


# ============================================================
# PUBLICAR PRODUTO
# ============================================================

def publicar_proximo():

    fila = carregar_fila()

    if not fila:

        print(
            "📭 Fila vazia."
        )

        return False

    produto = fila[0]

    nome_original = produto.get(
        "nome"
    ) or "Achadinho da Shopee"

    link = produto.get(
        "link"
    )

    imagem = produto.get(
        "imagem"
    )

    # --------------------------------------------------------
    # CONSULTA SHOPEE
    # --------------------------------------------------------

    dados = consultar_shopee(
        link
    )

    nome = (
        dados.get("nome")
        or nome_original
    )

    preco = dados.get(
        "preco"
    )

    imagem_shopee = dados.get(
        "imagem"
    )

    if imagem_shopee:

        imagem = imagem_shopee

    # --------------------------------------------------------
    # LEGENDA
    # --------------------------------------------------------

    preco_texto = texto_preco(
        preco
    )

    legenda = f"""🔥 ACHADINHO DA SHOPEE 🔥

🛍️ {nome}

{preco_texto}
⚡ Aproveite enquanto estiver disponível!

👇 Clique abaixo para conferir:

🛒 COMPRAR AGORA"""

    teclado = {

        "inline_keyboard": [

            [

                {
                    "text":
                    "🛒 COMPRAR AGORA",

                    "url":
                    link
                }

            ]

        ]
    }

    # ========================================================
    # TENTATIVA 1: IMAGEM
    # ========================================================

    if imagem:

        print(
            "🖼️ Tentando publicar com imagem..."
        )

        resposta = telegram_post(

            "sendPhoto",

            {

                "chat_id":
                CHAT_ID,

                "photo":
                imagem,

                "caption":
                legenda,

                "reply_markup":
                json.dumps(
                    teclado,
                    ensure_ascii=False
                )
            }
        )

        if resposta.get("ok"):

            fila.pop(0)

            salvar_fila(
                fila
            )

            print(
                "================================"
            )

            print(
                "✅ PRODUTO PUBLICADO COM IMAGEM"
            )

            print(
                "🛒",
                nome
            )

            print(
                "💰",
                texto_preco(preco)
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
            "⚠️ Telegram recusou a imagem."
        )

        print(
            resposta
        )

    # ========================================================
    # TENTATIVA 2: TEXTO
    # ========================================================

    print(
        "📝 Tentando publicar como texto..."
    )

    resposta = telegram_post(

        "sendMessage",

        {

            "chat_id":
            CHAT_ID,

            "text":
            legenda,

            "reply_markup":
            json.dumps(
                teclado,
                ensure_ascii=False
            )
        }
    )

    if resposta.get("ok"):

        fila.pop(0)

        salvar_fila(
            fila
        )

        print(
            "================================"
        )

        print(
            "✅ PRODUTO PUBLICADO COMO TEXTO"
        )

        print(
            "🛒",
            nome
        )

        print(
            "💰",
            texto_preco(preco)
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

    print(
        resposta
    )

    print(
        "⚠️ Produto continua na fila."
    )

    return False


# ============================================================
# PUBLICADOR AUTOMÁTICO
# ============================================================

def publicador_automatico():

    print(
        "🚀 Publicador automático iniciado."
    )

    print(
        "⏰ Intervalo:",
        INTERVALO,
        "segundos"
    )

    while True:

        try:

            ultimo = float(
                redis_cmd(
                    "GET",
                    "ultimo_post"
                ) or 0
            )

            if (
                time.time()
                - ultimo
                >= INTERVALO
            ):

                if publicar_proximo():

                    redis_cmd(
                        "SET",
                        "ultimo_post",
                        str(
                            time.time()
                        )
                    )

        except Exception as erro:

            print(
                "❌ Erro no publicador:",
                erro
            )

        time.sleep(60)


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

            f"🔗 "
            f"{produto.get('link', '')}\n\n"
        )

    enviar_mensagem(
        chat_id,
        mensagem[:4000]
    )


# ============================================================
# LIMPAR FILA
# ============================================================

def limpar_fila(chat_id):

    salvar_fila([])

    enviar_mensagem(
        chat_id,
        "🗑️ Fila limpa com sucesso!"
    )


# ============================================================
# ENCONTRAR LINK
# ============================================================

def encontrar_link(texto):

    if not texto:
        return None

    encontrados = re.findall(
        r'https?://[^\s<>"\']+',
        texto
    )

    if not encontrados:

        return None

    link = encontrados[0].strip()

    link = link.rstrip(
        ".,;:!?)]}>\"'"
    )

    return link


# ============================================================
# ENCONTRAR NOME DA MENSAGEM
# ============================================================

def encontrar_nome_mensagem(
    texto
):

    if not texto:

        return (
            "Achadinho da Shopee"
        )

    linhas = [

        linha.strip()

        for linha in texto.splitlines()

        if linha.strip()
    ]

    for linha in linhas:

        if re.search(
            r'https?://',
            linha,
            re.IGNORECASE
        ):
            continue

        linha_lower = linha.lower()

        if (
            "compre na shopee"
            in linha_lower
        ):
            continue

        if (
            "shopee brasil"
            in linha_lower
        ):
            continue

        if (
            "procurando a"
            in linha_lower
        ):
            continue

        if len(linha) >= 10:

            return linha[:250]

    return (
        "Achadinho da Shopee"
    )


# ============================================================
# PROCESSAR MENSAGEM
# ============================================================

def processar_mensagem(
    mensagem
):

    chat_id = mensagem[
        "chat"
    ][
        "id"
    ]

    # --------------------------------------------------------
    # ADMIN
    # --------------------------------------------------------

    if ADMIN_ID:

        usuario_id = str(
            mensagem.get(
                "from",
                {}
            ).get(
                "id",
                ""
            )
        )

        if (
            usuario_id
            != str(ADMIN_ID)
        ):

            return

    # ========================================================
    # FOTO + TEXTO
    # ========================================================

    if "photo" in mensagem:

        file_id = mensagem[
            "photo"
        ][-1][
            "file_id"
        ]

        legenda = mensagem.get(
            "caption",
            ""
        ).strip()

        link = encontrar_link(
            legenda
        )

        if not link:

            enviar_mensagem(
                chat_id,

                """❌ Não encontrei o link da Shopee.

Envie a foto com o link da Shopee
na legenda."""
            )

            return

        nome = (
            encontrar_nome_mensagem(
                legenda
            )
        )

        adicionado, posicao = (
            adicionar_na_fila(
                nome,
                link,
                file_id
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

🛍️ {nome}

📦 Posição na fila: {posicao}

🖼️ Foto salva.
🔗 Link encontrado.
💰 Preço será consultado na Shopee.
⏰ Publicação automática ativa."""
        )

        return

    # ========================================================
    # TEXTO
    # ========================================================

    texto = mensagem.get(
        "text",
        ""
    ).strip()

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    if texto == "/start":

        enviar_mensagem(

            chat_id,

            """🤖 ACHADOS DA SHOPEE

Envie o anúncio ou link da Shopee.

O bot procura automaticamente:

🛍️ Nome
💰 Preço
🖼️ Imagem
🔗 Link

Comandos:

/fila
Ver fila

/limparfila
Limpar fila"""
        )

        return

    # --------------------------------------------------------
    # FILA
    # --------------------------------------------------------

    if texto == "/fila":

        mostrar_fila(
            chat_id
        )

        return

    # --------------------------------------------------------
    # LIMPAR
    # --------------------------------------------------------

    if texto == "/limparfila":

        limpar_fila(
            chat_id
        )

        return

    # --------------------------------------------------------
    # ENCONTRAR LINK
    # --------------------------------------------------------

    link = encontrar_link(
        texto
    )

    if not link:

        enviar_mensagem(

            chat_id,

            """❌ Não encontrei um link da Shopee.

Cole a mensagem completa do produto
ou envie somente o link."""
        )

        return

    # --------------------------------------------------------
    # NOME INICIAL
    # --------------------------------------------------------

    nome = encontrar_nome_mensagem(
        texto
    )

    # --------------------------------------------------------
    # ADICIONAR
    # --------------------------------------------------------

    adicionado, posicao = (
        adicionar_na_fila(
            nome,
            link
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

🛍️ {nome}

📦 Posição na fila: {posicao}

🔗 Link encontrado.
💰 Preço será consultado na Shopee.
🖼️ Imagem será consultada na Shopee.
⏰ Será publicado automaticamente."""
    )


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
                "https://api.telegram.org/"
                f"bot{TOKEN}/getUpdates"
            )

            parametros = {
                "timeout": 30
            }

            if (
                ultimo_update
                is not None
            ):

                parametros[
                    "offset"
                ] = (
                    ultimo_update + 1
                )

            resposta = requests.get(

                url,

                params=parametros,

                timeout=35
            )

            dados = resposta.json()

            if not dados.get(
                "ok"
            ):

                print(
                    "❌ Erro Telegram:",
                    dados
                )

                time.sleep(5)

                continue

            for update in dados[
                "result"
            ]:

                ultimo_update = (
                    update[
                        "update_id"
                    ]
                )

                if (
                    "message"
                    in update
                ):

                    try:

                        processar_mensagem(
                            update[
                                "message"
                            ]
                        )

                    except Exception as erro:

                        print(
                            "❌ Erro ao processar:",
                            erro
                        )

                        enviar_mensagem(

                            update[
                                "message"
                            ][
                                "chat"
                            ][
                                "id"
                            ],

                            "⚠️ Erro temporário. Tente enviar novamente."
                        )

        except Exception as erro:

            print(
                "❌ Erro:",
                erro
            )

            time.sleep(5)


# ============================================================
# SERVIDOR DE SAÚDE
# ============================================================

class Saude(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(
            200
        )

        self.end_headers()

        self.wfile.write(
            b"ok"
        )

    def do_HEAD(self):

        self.send_response(
            200
        )

        self.end_headers()

    def log_message(
        self,
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
        (
            "0.0.0.0",
            porta
        ),
        Saude
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
        "🔗 LEITOR DE LINKS ATIVO"
    )

    print(
        "======================================"
    )

    iniciar_bot()
