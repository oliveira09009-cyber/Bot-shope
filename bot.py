import requests
import json
import os
import time
import threading
import re
import html
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote
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
REDIS_TOKEN = os.environ["UPSTASH_REDIS_REST_TOKEN"]

ADMIN_ID = os.environ.get("ADMIN_ID")

# 720 segundos = 12 minutos = 5 produtos por hora
INTERVALO = 720

CRAWLER_UAS = [
    "WhatsApp/2.23.20.0 A",
    "TelegramBot (like TwitterBot)",
    "Twitterbot/1.0",
]


def headers_crawler(ua):
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
    }


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; SM-G991B) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
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
            "Authorization": f"Bearer {REDIS_TOKEN}",
            "Content-Type": "application/json"
        },
        json=list(args),
        timeout=15
    )

    resposta.raise_for_status()

    return resposta.json().get("result")


def carregar_fila():
    dado = redis_cmd("GET", "fila")

    if not dado:
        return []

    try:
        return json.loads(dado)

    except Exception as erro:
        print("❌ Erro ao ler fila:", erro)
        return []


def salvar_fila(fila):
    redis_cmd(
        "SET",
        "fila",
        json.dumps(fila, ensure_ascii=False)
    )


def adicionar_na_fila(
    nome,
    link,
    imagem=None,
    preco=None,
    link_resolvido=None
):
    fila = carregar_fila()

    for produto in fila:
        if produto.get("link") == link:
            return False, len(fila)

    fila.append({
        "nome": nome,
        "link": link,
        "imagem": imagem,
        "preco": preco,
        "link_resolvido": link_resolvido
    })

    salvar_fila(fila)

    return True, len(fila)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_post(metodo, data=None, files=None):

    url = f"https://api.telegram.org/bot{TOKEN}/{metodo}"

    try:

        resposta = requests.post(
            url,
            data=data,
            files=files,
            timeout=60
        )

        print(
            f"📡 Telegram {metodo} HTTP:",
            resposta.status_code
        )

        try:
            resultado = resposta.json()

        except Exception:
            resultado = {
                "ok": False,
                "description": resposta.text
            }

        if not resultado.get("ok"):
            print(
                f"❌ Telegram {metodo} FALHOU:"
            )
            print(resultado)

        return resultado

    except Exception as erro:

        print(
            f"❌ Erro Telegram {metodo}:",
            repr(erro)
        )

        return {
            "ok": False,
            "description": str(erro)
        }


def enviar_mensagem(chat_id, texto):

    return telegram_post(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": texto
        }
    )


# ============================================================
# VALIDAR IMAGEM
# ============================================================

def imagem_valida_bytes(conteudo):

    if not conteudo:
        return False

    if len(conteudo) < 1000:
        return False

    if conteudo[:3] == b"\xff\xd8\xff":
        return True

    if conteudo[:8] == b"\x89PNG\r\n\x1a\n":
        return True

    if conteudo[:6] in (
        b"GIF87a",
        b"GIF89a"
    ):
        return True

    if (
        len(conteudo) >= 12
        and conteudo[:4] == b"RIFF"
        and conteudo[8:12] == b"WEBP"
    ):
        return True

    if conteudo[:2] == b"BM":
        return True

    return False


# ============================================================
# BAIXAR FOTO DO TELEGRAM
# ============================================================

def baixar_foto_telegram(file_id):

    if not file_id:

        print("❌ FILE_ID vazio.")

        return None

    try:

        print(
            "🔐 FILE_ID recebido:",
            file_id
        )

        resposta = telegram_post(
            "getFile",
            {
                "file_id": file_id
            }
        )

        if not resposta.get("ok"):

            print(
                "❌ Telegram não localizou o arquivo."
            )

            return None

        file_path = (
            resposta
            .get("result", {})
            .get("file_path")
        )

        if not file_path:

            print(
                "❌ Telegram não retornou file_path."
            )

            return None

        url_arquivo = (
            f"https://api.telegram.org/file/"
            f"bot{TOKEN}/{file_path}"
        )

        print(
            "📥 Baixando foto do Telegram..."
        )

        download = requests.get(
            url_arquivo,
            timeout=60
        )

        print(
            "📥 Download Telegram:",
            download.status_code,
            download.headers.get(
                "Content-Type",
                ""
            ),
            len(download.content)
        )

        if (
            download.status_code != 200
            or not download.content
        ):
            return None

        if not imagem_valida_bytes(
            download.content
        ):
            print(
                "❌ Arquivo não parece imagem."
            )

            return None

        if len(download.content) > 10 * 1024 * 1024:

            print(
                "❌ Foto maior que 10 MB."
            )

            return None

        print(
            "✅ FOTO TELEGRAM BAIXADA:",
            len(download.content),
            "bytes"
        )

        return download.content

    except Exception as erro:

        print(
            "❌ Erro baixando foto Telegram:",
            repr(erro)
        )

        return None


# ============================================================
# BAIXAR IMAGEM DA SHOPEE
# ============================================================

def baixar_imagem(url, referer=None):

    if not url:
        return None

    try:

        headers = dict(HEADERS)

        headers["Accept"] = (
            "image/avif,image/webp,image/apng,"
            "image/jpeg,image/png,*/*;q=0.8"
        )

        if referer:
            headers["Referer"] = referer

        resposta = requests.get(
            url,
            headers=headers,
            timeout=30,
            allow_redirects=True
        )

        print(
            "🖼️ Download imagem Shopee:",
            resposta.status_code,
            resposta.headers.get(
                "Content-Type",
                ""
            ),
            len(resposta.content)
        )

        if resposta.status_code != 200:
            return None

        if not imagem_valida_bytes(
            resposta.content
        ):

            print(
                "⚠️ Bytes não parecem imagem."
            )

            return None

        if len(resposta.content) > 10 * 1024 * 1024:

            print(
                "⚠️ Imagem maior que 10 MB."
            )

            return None

        print(
            "✅ Imagem Shopee baixada:",
            len(resposta.content),
            "bytes"
        )

        return resposta.content

    except Exception as erro:

        print(
            "⚠️ Erro baixando imagem Shopee:",
            repr(erro)
        )

        return None


# ============================================================
# NORMALIZAR LINK
# ============================================================

def normalizar_link(link):

    if not link:
        return None

    try:

        link = html.unescape(
            str(link)
        ).strip()

        link = re.sub(
            r"[\u200b-\u200f\u202a-\u202e\ufeff]",
            "",
            link
        )

        if link.startswith(
            "www.shopee.com.br"
        ):
            link = "https://" + link

        elif link.startswith(
            "s.shopee.com.br"
        ):
            link = "https://" + link

        elif link.startswith(
            "shopee.com.br"
        ):
            link = "https://" + link

        if not re.match(
            r"^https?://",
            link,
            re.IGNORECASE
        ):
            return None

        link = link.rstrip(
            ".,;:!?)]}>\"'"
        )

        partes = urlsplit(link)

        if not partes.netloc:
            return None

        dominio = partes.netloc.lower()

        dominio_sem_www = (
            dominio[4:]
            if dominio.startswith("www.")
            else dominio
        )

        dominios_validos = [
            "shopee.com.br",
            "s.shopee.com.br",
            "shopee.com",
            "s.shopee.com"
        ]

        if dominio_sem_www not in dominios_validos:

            print(
                "⚠️ Domínio não reconhecido:",
                dominio
            )

            return None

        # IMPORTANTE:
        # Mantém query string.
        # Não vamos mais apagar os parâmetros do link.

        return urlunsplit((
            "https",
            dominio,
            partes.path,
            partes.query,
            ""
        ))

    except Exception as erro:

        print(
            "❌ Erro normalizando link:",
            erro
        )

        return None


def encontrar_link(texto):

    if not texto:
        return None

    encontrados = re.findall(
        r'https?://[^\s<>"\']+',
        texto,
        re.IGNORECASE
    )

    if not encontrados:
        return None

    for encontrado in encontrados:

        link = normalizar_link(
            encontrado
        )

        if link:

            print(
                "🔗 Link normalizado:",
                link
            )

            return link

    return None


# ============================================================
# RESOLVER LINK
# ============================================================

def resolver_link(link):

    link_original = normalizar_link(
        link
    )

    if not link_original:
        return None

    try:

        print(
            "🔗 Link original:",
            link_original
        )

        resposta = requests.get(
            link_original,
            headers=HEADERS,
            allow_redirects=True,
            timeout=30
        )

        print(
            "🔗 URL final:",
            resposta.url
        )

        final = normalizar_link(
            resposta.url
        )

        if final:

            print(
                "🔗 Link resolvido:",
                final
            )

            return final

        return link_original

    except Exception as erro:

        print(
            "⚠️ Erro resolvendo link:",
            erro
        )

        return link_original


# ============================================================
# BAIXAR PÁGINA SHOPEE
# ============================================================

def _html_tem_produto(texto):
    if not texto:
        return False
    return bool(
        re.search(r"susercontent\.com/file/[A-Za-z0-9_-]{16,}", texto)
    )


def baixar_pagina(link):

    tentativas = [headers_crawler(ua) for ua in CRAWLER_UAS] + [HEADERS]

    reserva = (None, link)

    for numero, headers in enumerate(tentativas, start=1):

        try:

            print(
                f"🌐 Tentativa página Shopee {numero}/{len(tentativas)}",
                "UA:", headers.get("User-Agent", "")[:30]
            )

            resposta = requests.get(
                link,
                headers=headers,
                allow_redirects=True,
                timeout=35
            )

            print("🌐 Status Shopee:", resposta.status_code)
            print("📄 HTML recebido:", len(resposta.text), "caracteres")
            print("🌐 URL final:", resposta.url)

            if resposta.status_code == 200:

                if _html_tem_produto(resposta.text):
                    print("✅ HTML com foto de produto")
                    return resposta.text, resposta.url

                if reserva[0] is None:
                    reserva = (resposta.text, resposta.url)

            else:
                print("⚠️ Shopee retornou:", resposta.status_code)

        except Exception as erro:
            print("⚠️ Erro tentativa:", repr(erro))

    if reserva[0]:
        print("⚠️ Nenhum HTML trouxe foto; usando o primeiro 200.")
        return reserva

    print("❌ Não foi possível baixar a página Shopee.")

    return None, link

# ============================================================
# LIMPAR TEXTO
# ============================================================

def limpar_texto(texto):

    if not texto:
        return ""

    texto = html.unescape(
        str(texto)
    )

    texto = (
        texto
        .replace("\\/", "/")
        .replace("\\u002F", "/")
        .replace("\\u002f", "/")
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
    ).strip()

    return texto


# ============================================================
# PREÇO
# ============================================================

def converter_preco(valor):

    if valor is None:
        return None

    valor = (
        str(valor)
        .strip()
        .replace("R$", "")
        .replace(" ", "")
    )

    if "," in valor:

        valor = (
            valor
            .replace(".", "")
            .replace(",", ".")
        )

    try:

        numero = float(valor)

    except Exception:

        return None

    if numero <= 0:
        return None

    if numero >= 100000:
        numero /= 100

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


def extrair_precos(texto):

    if not texto:
        return []

    texto = html.unescape(texto)

    precos = []

    padroes = [
        r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})",
        r"R\$\s*([0-9]+(?:\.[0-9]{2})?)"
    ]

    # Ignora parcelamento ("12x R$ 13,87") e frete: nao sao preco do produto.
    ruido = re.compile(
        r"(?:\d+\s*x\s*)?"
        r"(?:R\$\s*[0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}"
        r"|R\$\s*[0-9]+(?:\.[0-9]{2})?)"
        r"\s*(?:x\b|/mes|por mes|no mes)?"
        r"\s*(?:de\s*)?(?:frete|parcela|juros)?",
        re.IGNORECASE
    )

    for padrao in padroes:

        for encontrado in re.finditer(
            padrao,
            texto,
            re.IGNORECASE
        ):

            valor = encontrado.group(1)

            antes = texto[max(0, encontrado.start() - 12):encontrado.start()]

            depois = texto[encontrado.end():encontrado.end() + 25]

            contexto = (antes + " " + depois).lower()

            if re.search(r"\d+\s*x\s*$", antes.lower()):
                continue

            if re.search(r"^\s*x\b", depois.lower()):
                continue

            if "frete" in contexto and "grátis" not in contexto:
                continue

            if "parcela" in contexto or "juros" in contexto:
                continue

            numero = converter_preco(
                valor
            )

            if numero is not None:
                precos.append(numero)

    campos = [
        "price",
        "salePrice",
        "currentPrice",
        "priceMin",
        "priceMax",
        "discountPrice",
        "finalPrice",
        "price_min",
        "price_max"
    ]

    for campo in campos:

        padroes_campo = [
            rf'"{campo}"\s*:\s*"([^"]+)"',
            rf'"{campo}"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
        ]

        for padrao in padroes_campo:

            for valor in re.findall(
                padrao,
                texto,
                re.IGNORECASE
            ):

                numero = converter_preco(
                    valor
                )

                if numero is not None:
                    precos.append(numero)

    return sorted(
        set(round(p, 2) for p in precos)
    )


def extrair_faixa_preco(texto):

    if not texto:
        return None

    texto = html.unescape(texto)

    padroes = [
        r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})\s*[-–—]\s*R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})",
        r"R\$\s*([0-9]+(?:[.,][0-9]{2})?)\s*[-–—]\s*R\$\s*([0-9]+(?:[.,][0-9]{2})?)"
    ]

    for padrao in padroes:

        for primeiro, segundo in re.findall(
            padrao,
            texto,
            re.IGNORECASE
        ):

            p1 = converter_preco(
                primeiro
            )

            p2 = converter_preco(
                segundo
            )

            if p1 and p2:

                return {
                    "menor": min(p1, p2),
                    "maior": max(p1, p2)
                }

    return None


# ============================================================
# DESESCAPAR URL
# ============================================================

def limpar_url_imagem(url, base_url=None):

    if not url:
        return None

    try:

        url = html.unescape(
            str(url)
        )

        url = (
            url
            .replace("\\/", "/")
            .replace("\\u002F", "/")
            .replace("\\u002f", "/")
            .replace("\\u003A", ":")
            .replace("\\u003a", ":")
        )

        url = (
            url
            .replace("&amp;", "&")
            .strip()
            .strip("\"'")
        )

        if url.startswith("//"):
            url = "https:" + url

        elif url.startswith("/") and base_url:
            url = urljoin(
                base_url,
                url
            )

        if not url.startswith(
            "http://"
        ) and not url.startswith(
            "https://"
        ):
            return None

        return url

    except Exception:
        return None


# ============================================================
# EXTRAIR NOME
# ============================================================

def extrair_nome(html_text, url):

    if not html_text:
        return None

    candidatos = []

    # --------------------------------------------------------
    # META TAGS
    # --------------------------------------------------------

    meta_tags = re.findall(
        r"<meta\b[^>]*>",
        html_text,
        re.IGNORECASE
    )

    for tag in meta_tags:

        propriedade = re.search(
            r'(?:property|name)\s*=\s*["\']([^"\']+)["\']',
            tag,
            re.IGNORECASE
        )

        conteudo = re.search(
            r'content\s*=\s*["\']([^"\']*)["\']',
            tag,
            re.IGNORECASE
        )

        if not propriedade or not conteudo:
            continue

        chave = propriedade.group(1).lower()

        if chave in (
            "og:title",
            "twitter:title"
        ):

            nome = limpar_texto(
                conteudo.group(1)
            )

            if len(nome) >= 5:
                candidatos.append(nome)

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    encontrado = re.search(
        r"<title[^>]*>(.*?)</title>",
        html_text,
        re.IGNORECASE | re.DOTALL
    )

    if encontrado:

        nome = limpar_texto(
            encontrado.group(1)
        )

        if len(nome) >= 5:
            candidatos.append(nome)

    # --------------------------------------------------------
    # JSON-LD
    # --------------------------------------------------------

    blocos = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_text,
        re.IGNORECASE | re.DOTALL
    )

    for bloco in blocos:

        try:

            bloco_limpo = html.unescape(
                bloco
            ).strip()

            dados = json.loads(
                bloco_limpo
            )

            objetos = (
                dados
                if isinstance(dados, list)
                else [dados]
            )

            for objeto in objetos:

                if not isinstance(
                    objeto,
                    dict
                ):
                    continue

                nome = objeto.get(
                    "name"
                )

                if isinstance(
                    nome,
                    str
                ):

                    nome = limpar_texto(
                        nome
                    )

                    if len(nome) >= 5:
                        candidatos.append(nome)

        except Exception:
            pass

    # --------------------------------------------------------
    # PADRÕES INTERNOS DA SHOPEE
    # --------------------------------------------------------

    padroes_internos = [
        r'"name"\s*:\s*"([^"]{5,300})"',
        r'"productName"\s*:\s*"([^"]{5,300})"',
        r'"product_name"\s*:\s*"([^"]{5,300})"',
        r'"itemName"\s*:\s*"([^"]{5,300})"',
        r'"item_name"\s*:\s*"([^"]{5,300})"'
    ]

    for padrao in padroes_internos:

        encontrados = re.findall(
            padrao,
            html_text,
            re.IGNORECASE
        )

        for nome in encontrados:

            nome = limpar_texto(
                nome
            )

            if len(nome) >= 5:
                candidatos.append(nome)

    # --------------------------------------------------------
    # FILTRAR NOMES RUINS
    # --------------------------------------------------------

    invalidos = [
        "shopee brasil",
        "ofertas incríveis",
        "ofertas incriveis",
        "melhores preços do mercado",
        "melhores precos do mercado",
        "compre na shopee",
        "shopee"
    ]

    filtrados = []

    for nome in candidatos:

        nome = re.sub(
            r"\s*[|\-–—]\s*shopee(\s+brasil)?\s*$",
            "",
            nome,
            flags=re.IGNORECASE
        ).strip()

        nome_lower = nome.lower()

        if any(
            x in nome_lower
            for x in invalidos
        ):
            continue

        if len(nome) < 5:
            continue

        filtrados.append(nome)

    if not filtrados:

        print(
            "⚠️ Nome do produto não encontrado."
        )

        return None

    # Prioriza nomes com aparência de produto
    melhor = max(
        filtrados,
        key=lambda x: (
            len(x.split()) >= 2,
            len(x)
        )
    )

    melhor = melhor[:250]

    print(
        "🛍️ NOME ENCONTRADO:",
        melhor
    )

    return melhor


# ============================================================
# EXTRAIR IMAGENS
# ============================================================

def extrair_imagens(
    html_text,
    url_base
):

    if not html_text:
        return []

    imagens = []

    # --------------------------------------------------------
    # TODAS AS META TAGS
    # --------------------------------------------------------

    meta_tags = re.findall(
        r"<meta\b[^>]*>",
        html_text,
        re.IGNORECASE
    )

    for tag in meta_tags:

        propriedade = re.search(
            r'(?:property|name)\s*=\s*["\']([^"\']+)["\']',
            tag,
            re.IGNORECASE
        )

        conteudo = re.search(
            r'content\s*=\s*["\']([^"\']*)["\']',
            tag,
            re.IGNORECASE
        )

        if not propriedade or not conteudo:
            continue

        chave = propriedade.group(1).lower()

        if chave in (
            "og:image",
            "og:image:url",
            "og:image:secure_url",
            "twitter:image",
            "twitter:image:src"
        ):

            imagem = limpar_url_imagem(
                conteudo.group(1),
                url_base
            )

            if imagem:
                imagens.append(imagem)

    # --------------------------------------------------------
    # JSON-LD
    # --------------------------------------------------------

    blocos = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_text,
        re.IGNORECASE | re.DOTALL
    )

    for bloco in blocos:

        try:

            dados = json.loads(
                html.unescape(bloco)
            )

            objetos = (
                dados
                if isinstance(dados, list)
                else [dados]
            )

            for objeto in objetos:

                if not isinstance(
                    objeto,
                    dict
                ):
                    continue

                imagem = objeto.get(
                    "image"
                )

                if isinstance(
                    imagem,
                    str
                ):

                    imagem = limpar_url_imagem(
                        imagem,
                        url_base
                    )

                    if imagem:
                        imagens.append(imagem)

                elif isinstance(
                    imagem,
                    list
                ):

                    for item in imagem:

                        if not isinstance(
                            item,
                            str
                        ):
                            continue

                        item = limpar_url_imagem(
                            item,
                            url_base
                        )

                        if item:
                            imagens.append(item)

        except Exception:
            pass

    # --------------------------------------------------------
    # URLS SUSERCONTENT
    # --------------------------------------------------------

    padroes_susercontent = [
        r'https?://[^"\'<>\s]+susercontent\.com[^"\'<>\s]*',
        r'https?:\\?/\\?/[^"\'<>\s]+susercontent\.com[^"\'<>\s]*',
        r'//[^"\'<>\s]+susercontent\.com[^"\'<>\s]*'
    ]

    for padrao in padroes_susercontent:

        encontrados = re.findall(
            padrao,
            html_text,
            re.IGNORECASE
        )

        for imagem in encontrados:

            imagem = limpar_url_imagem(
                imagem,
                url_base
            )

            if imagem:
                imagens.append(imagem)

    # --------------------------------------------------------
    # OUTROS CDN DA SHOPEE
    # --------------------------------------------------------

    padroes_cdn = [
        r'https?://[^"\'<>\s]+shopee[^"\'<>\s]+\.(?:jpg|jpeg|png|webp)',
        r'https?://[^"\'<>\s]+cdn[^"\'<>\s]+\.(?:jpg|jpeg|png|webp)'
    ]

    for padrao in padroes_cdn:

        encontrados = re.findall(
            padrao,
            html_text,
            re.IGNORECASE
        )

        for imagem in encontrados:

            imagem = limpar_url_imagem(
                imagem,
                url_base
            )

            if imagem:
                imagens.append(imagem)

    # --------------------------------------------------------
    # FILTRO
    # --------------------------------------------------------

    ignorar = [
        "logo",
        "avatar",
        "favicon",
        "placeholder",
        "icon",
        "sprite",
        "qr-code",
        "qrcode"
    ]

    finais = []

    for imagem in imagens:

        imagem = imagem.strip()

        if not imagem.startswith(
            "http"
        ):
            continue

        if len(imagem) < 30:
            continue

        if any(
            palavra in imagem.lower()
            for palavra in ignorar
        ):
            continue

        if imagem not in finais:

            finais.append(imagem)

    finais = [
        i for i in finais
        if re.search(
            r"susercontent\.com/file/[A-Za-z0-9_-]{16,}",
            i,
            re.IGNORECASE
        )
    ]

    print(
        "🖼️ CANDIDATAS DE IMAGEM:",
        len(finais)
    )

    for numero, imagem in enumerate(
        finais[:10],
        start=1
    ):

        print(
            f"   {numero}.",
            imagem[:180]
        )

    return finais


# ============================================================
# API INTERNA DA SHOPEE (sem API oficial)
# ============================================================

API_HEADERS = {
    **HEADERS,
    "Accept": "application/json",
    "Referer": "https://shopee.com.br/",
    "X-API-SOURCE": "pc",
    "X-Requested-With": "XMLHttpRequest",
}

# opcional: cookie copiado do navegador logado
if os.environ.get("SHOPEE_COOKIE"):
    API_HEADERS["Cookie"] = os.environ["SHOPEE_COOKIE"]


def extrair_ids(*urls):
    for u in urls:
        u = unquote(unquote(u or ""))
        for padrao in (
            r"i\.(\d+)\.(\d+)",
            r"/product/(\d+)/(\d+)",
            r"/(\d{5,})/(\d{5,})(?:[/?]|$)",
        ):
            m = re.search(padrao, u)
            if m:
                return m.group(1), m.group(2)
    return None


def resolver_com_historico(link):
    melhor = None
    for ua in CRAWLER_UAS:
        try:
            r = requests.get(
                link,
                headers=headers_crawler(ua),
                allow_redirects=True,
                timeout=30
            )
        except Exception as erro:
            print("⚠️ Erro resolvendo:", repr(erro))
            continue
        urls = [h.headers.get("Location", "") for h in r.history]
        urls += [h.url for h in r.history] + [r.url]
        print("🔗 Redirecionamentos:", [u[:120] for u in urls if u])
        if melhor is None:
            melhor = (r.url, urls)
        if extrair_ids(*urls):
            return r.url, urls
    if melhor:
        return melhor
    return link, [link]


def ids_do_html(html_text):
    if not html_text:
        return None

    texto = (
        html_text
        .replace("\\/", "/")
        .replace("\\u002F", "/")
        .replace("\\u002f", "/")
    )

    padroes = (
        r'"shopid"\s*:\s*"?(\d+)"?[^}]{0,40}?"itemid"\s*:\s*"?(\d+)',
        r'"itemid"\s*:\s*"?(\d+)"?[^}]{0,40}?"shopid"\s*:\s*"?(\d+)',
    )

    m = re.search(padroes[0], texto)
    if m:
        return m.group(1), m.group(2)

    m = re.search(padroes[1], texto)
    if m:
        return m.group(2), m.group(1)

    return extrair_ids(texto)


def _montar_dados_api(d):
    base = (d.get("price") or 0)
    menor = (d.get("price_min") or base) / 100000
    maior = (d.get("price_max") or base) / 100000
    imagens = [
        f"https://down-br.img.susercontent.com/file/{i}"
        for i in (d.get("images") or [])
    ]
    if not imagens and d.get("image"):
        imagens = [
            "https://down-br.img.susercontent.com/file/" + d["image"]
        ]
    return {
        "nome": d.get("name") or d.get("title"),
        "menor": menor,
        "maior": maior,
        "imagens": imagens,
    }


def consultar_api_shopee(shopid, itemid):

    rotas = [
        (
            "https://shopee.com.br/api/v4/item/get"
            f"?itemid={itemid}&shopid={shopid}"
        ),
        (
            "https://shopee.com.br/api/v4/pdp/get_pc"
            f"?item_id={itemid}&shop_id={shopid}"
            "&tz_offset_minutes=-180&detail_level=0"
        ),
    ]

    for url in rotas:
        try:
            r = requests.get(url, headers=API_HEADERS, timeout=30)
            print("🌐 API Shopee:", url.split("?")[0][-22:], r.status_code)

            if r.status_code != 200:
                print("🌐 corpo:", r.text[:200])
                continue

            j = r.json() or {}
            d = j.get("data")

            if isinstance(d, dict) and isinstance(d.get("item"), dict):
                d = d["item"]

            if not d:
                print("⚠️ sem data:", r.text[:200])
                continue

            dados = _montar_dados_api(d)

            if dados["nome"]:
                return dados

        except Exception as erro:
            print("⚠️ Erro rota API:", repr(erro))

    return None

# ============================================================
# CONSULTAR SHOPEE
# ============================================================

def consultar_shopee(link):

    resultado = {
        "nome": None,
        "preco": None,
        "imagem": None,
        "imagens": [],
        "link": link,
        "link_resolvido": link
    }

    link_original = normalizar_link(
        link
    )

    if not link_original:

        print(
            "❌ Link Shopee inválido."
        )

        return resultado

    resultado["link"] = link_original

    # --------------------------------------------------------
    # TENTA A API INTERNA PRIMEIRO
    # --------------------------------------------------------

    ids = extrair_ids(link_original)

    try:
        url_final, historico = resolver_com_historico(link_original)
        resultado["link_resolvido"] = url_final
        ids = extrair_ids(url_final, *historico, link_original) or ids
    except Exception as erro:
        print("⚠️ Erro resolvendo:", repr(erro))

    print("🆔 IDs (shopid, itemid):", ids)

    if ids:
        try:
            dados_api = consultar_api_shopee(*ids)
        except Exception as erro:
            print("⚠️ Erro API Shopee:", repr(erro))
            dados_api = None

        if dados_api and dados_api["nome"]:
            resultado["nome"] = dados_api["nome"]
            resultado["imagens"] = dados_api["imagens"]
            resultado["preco"] = {
                "tipo": (
                    "faixa"
                    if dados_api["maior"] > dados_api["menor"]
                    else "unico"
                ),
                "menor": dados_api["menor"],
                "maior": dados_api["maior"],
            }
            for img in dados_api["imagens"][:8]:
                if baixar_imagem(img, referer="https://shopee.com.br/"):
                    resultado["imagem"] = img
                    resultado["imagem_validada"] = True
                    break
            print("✅ Dados obtidos pela API:", resultado["nome"])
            return resultado
    # Se a API falhar, segue o método antigo (HTML) abaixo.


    link_final = resolver_link(
        link_original
    )

    if link_final:
        resultado["link_resolvido"] = (
            link_final
        )

    html_text, url_real = baixar_pagina(
        link_final or link_original
    )

    if not html_text:
        return resultado

    # --------------------------------------------------------
    # ID ACHADO DENTRO DO HTML
    # --------------------------------------------------------

    if not ids:
        ids = ids_do_html(html_text)
        print("🆔 IDs vindos do HTML:", ids)

        if ids:
            try:
                dados_api = consultar_api_shopee(*ids)
            except Exception as erro:
                print("⚠️ Erro API Shopee:", repr(erro))
                dados_api = None

            if dados_api and dados_api["nome"]:
                resultado["nome"] = dados_api["nome"]
                resultado["imagens"] = dados_api["imagens"]
                resultado["preco"] = {
                    "tipo": (
                        "faixa"
                        if dados_api["maior"] > dados_api["menor"]
                        else "unico"
                    ),
                    "menor": dados_api["menor"],
                    "maior": dados_api["maior"],
                }
                for img in dados_api["imagens"][:8]:
                    if baixar_imagem(img, referer="https://shopee.com.br/"):
                        resultado["imagem"] = img
                        resultado["imagem_validada"] = True
                        break
                print("✅ Dados obtidos pela API:", resultado["nome"])
                return resultado

    # --------------------------------------------------------
    # NOME
    # --------------------------------------------------------

    nome_produto = extrair_nome(
        html_text,
        url_real
    )

    if nome_produto:
        nome_produto = re.sub(
            r"[.\u2026]{2,}\s*$", "", nome_produto
        ).strip()
        nome_produto = re.sub(
            r"\s+", " ", nome_produto
        ).strip()
        nome_produto = nome_produto.rstrip(" -–—,;:")

    resultado["nome"] = nome_produto or None

    # --------------------------------------------------------
    # IMAGENS
    # --------------------------------------------------------

    imagens = extrair_imagens(
        html_text,
        url_real
    )

    resultado["imagens"] = imagens

    # --------------------------------------------------------
    # TESTAR AS IMAGENS IMEDIATAMENTE
    # --------------------------------------------------------

    if imagens:

        print(
            "🔎 Validando imagens encontradas..."
        )

        for numero, url_imagem in enumerate(
            imagens[:8],
            start=1
        ):

            print(
                f"🖼️ Testando imagem {numero}/"
                f"{min(8, len(imagens))}"
            )

            conteudo = baixar_imagem(
                url_imagem,
                referer=url_real
            )

            if conteudo:

                resultado["imagem"] = (
                    url_imagem
                )

                # Guardamos também a URL
                # que comprovadamente funciona.

                resultado["imagem_validada"] = True

                print(
                    "✅ IMAGEM VALIDADA:",
                    url_imagem[:180]
                )

                break

        if not resultado.get(
            "imagem"
        ):

            print(
                "❌ Nenhuma candidata pôde ser baixada."
            )

    else:

        print(
            "❌ Nenhuma imagem encontrada."
        )

    # --------------------------------------------------------
    # PREÇO
    # --------------------------------------------------------

    faixa = extrair_faixa_preco(
        html_text
    )

    if faixa:

        resultado["preco"] = {
            "tipo": "faixa",
            "menor": faixa["menor"],
            "maior": faixa["maior"]
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

            menor = min(precos)

            resultado["preco"] = {
                "tipo": "unico",
                "menor": menor,
                "maior": menor
            }

            print(
                "💰 Preço encontrado:",
                formatar_preco(menor)
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
        "🖼️ Imagem validada:",
        resultado.get("imagem")
    )

    print(
        "🔗 Link resolvido:",
        resultado["link_resolvido"]
    )

    print(
        "--------------------------------"
    )

    return resultado


# ============================================================
# PREÇO DA MENSAGEM
# ============================================================

def texto_preco(preco):

    if not preco:
        return "💰 OFERTA ESPECIAL"

    if preco.get("tipo") == "faixa":

        return (
            f"💰 A PARTIR DE "
            f"{formatar_preco(preco['menor'])}"
        )

    return (
        f"💰 {formatar_preco(preco['menor'])}"
    )


def extrair_preco_mensagem(texto):

    if not texto:
        return None

    texto = html.unescape(texto)

    faixa = extrair_faixa_preco(
        texto
    )

    if faixa:

        return {
            "tipo": "faixa",
            "menor": faixa["menor"],
            "maior": faixa["maior"]
        }

    numero = (
        r"([0-9]{1,3}"
        r"(?:\.[0-9]{3})+"
        r"(?:,[0-9]{1,2})?"
        r"|[0-9]+"
        r"(?:[.,][0-9]{1,2})?)"
    )

    for padrao in (
        rf"por\s*R\$\s*{numero}",
        rf"R\$\s*{numero}"
    ):

        encontrado = re.search(
            padrao,
            texto,
            re.IGNORECASE
        )

        if not encontrado:
            continue

        valor = encontrado.group(1)

        if re.fullmatch(
            r"\d{1,3}(?:\.\d{3})+",
            valor
        ):
            valor = valor.replace(
                ".",
                ""
            )

        preco = converter_preco(
            valor
        )

        if preco:

            return {
                "tipo": "unico",
                "menor": preco,
                "maior": preco
            }

    return None


# ============================================================
# FOTO DO TELEGRAM
# ============================================================

def enviar_foto_bytes(
    chat_id,
    conteudo,
    legenda,
    teclado
):

    if not conteudo:

        return {
            "ok": False,
            "description": "Imagem vazia"
        }

    if not imagem_valida_bytes(
        conteudo
    ):

        return {
            "ok": False,
            "description": (
                "Bytes não são uma imagem válida"
            )
        }

    payload = {
        "chat_id": chat_id,
        "caption": legenda,
        "reply_markup": json.dumps(
            teclado,
            ensure_ascii=False
        )
    }

    arquivos = {
        "photo": (
            "produto.jpg",
            conteudo,
            "image/jpeg"
        )
    }

    print(
        "🖼️ Enviando arquivo ao Telegram..."
    )

    resposta = telegram_post(
        "sendPhoto",
        payload,
        arquivos
    )

    print(
        "📨 Resposta sendPhoto:",
        resposta
    )

    return resposta


def enviar_foto_file_id(
    chat_id,
    file_id,
    legenda,
    teclado
):

    print(
        "================================"
    )

    print(
        "📸 FOTO DO USUÁRIO"
    )

    print(
        "🔐 FILE_ID:",
        file_id
    )

    resposta = telegram_post(
        "sendPhoto",
        {
            "chat_id": chat_id,
            "photo": file_id,
            "caption": legenda,
            "reply_markup": json.dumps(
                teclado,
                ensure_ascii=False
            )
        }
    )

    if resposta.get("ok"):

        print(
            "✅ FILE_ID ACEITO"
        )

        return resposta

    print(
        "❌ FILE_ID recusado."
    )

    conteudo = baixar_foto_telegram(
        file_id
    )

    if not conteudo:

        return resposta

    resposta_bytes = enviar_foto_bytes(
        chat_id,
        conteudo,
        legenda,
        teclado
    )

    return resposta_bytes


# ============================================================
# PUBLICAR
# ============================================================

def publicar_proximo():

    fila = carregar_fila()

    if not fila:

        print(
            "📭 Fila vazia."
        )

        return False

    produto = fila[0]

    nome_original = (
        produto.get("nome")
        or "Achadinho da Shopee"
    )

    link = normalizar_link(
        produto.get("link")
    )

    imagem = produto.get(
        "imagem"
    )

    preco = produto.get(
        "preco"
    )

    if not link:

        print(
            "❌ Produto possui link inválido."
        )

        return False

    print(
        "================================"
    )

    print(
        "🚀 PUBLICANDO PRODUTO"
    )

    print(
        "🔗 Link:",
        link
    )

    print(
        "🛍️ Nome salvo:",
        nome_original
    )

    print(
        "🖼️ Imagem salva:",
        imagem
    )

    # --------------------------------------------------------
    # CONSULTAR SHOPEE
    # --------------------------------------------------------

    dados = consultar_shopee(
        produto.get(
            "link_resolvido"
        ) or link
    )

    # --------------------------------------------------------
    # NOME
    # --------------------------------------------------------

    nome = (
        dados.get("nome")
        or nome_original
        or "Achadinho da Shopee"
    )

    # --------------------------------------------------------
    # PREÇO
    # --------------------------------------------------------

    if not preco:

        preco = dados.get(
            "preco"
        )

    # --------------------------------------------------------
    # LINK
    # --------------------------------------------------------

    link_botao = (
        dados.get("link_resolvido")
        or produto.get("link_resolvido")
        or link
    )

    link_botao = normalizar_link(
        link_botao
    )

    if not link_botao:

        link_botao = link

    # --------------------------------------------------------
    # IMAGEM
    # --------------------------------------------------------

    imagens_shopee = (
        dados.get("imagens")
        or []
    )

    imagem_validada = (
        dados.get("imagem")
    )

    legenda = f"""🔥 ACHADINHO DA SHOPEE 🔥

🛍️ {nome}

{texto_preco(preco)}
⚡ Aproveite enquanto estiver disponível!

👇 Clique abaixo para conferir:

🛒 COMPRAR AGORA"""

    teclado = {
        "inline_keyboard": [[
            {
                "text": "🛒 COMPRAR AGORA",
                "url": link_botao
            }
        ]]
    }

    # ========================================================
    # 1. FOTO ENVIADA PELO USUÁRIO
    # ========================================================

    if imagem:

        print(
            "📸 Usando foto enviada pelo usuário."
        )

        resposta = enviar_foto_file_id(
            CHAT_ID,
            imagem,
            legenda,
            teclado
        )

        if resposta.get("ok"):

            fila.pop(0)

            salvar_fila(
                fila
            )

            print(
                "✅ PRODUTO PUBLICADO"
            )

            return True

        print(
            "❌ Falha ao publicar foto do usuário."
        )

        return False

    # ========================================================
    # 2. IMAGEM SHOPEE VALIDADA
    # ========================================================

    if imagem_validada:

        print(
            "================================"
        )

        print(
            "🌐 USANDO IMAGEM SHOPEE VALIDADA"
        )

        conteudo = baixar_imagem(
            imagem_validada,
            referer=dados.get(
                "link_resolvido"
            )
        )

        if conteudo:

            resposta = enviar_foto_bytes(
                CHAT_ID,
                conteudo,
                legenda,
                teclado
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
                    "🖼️ IMAGEM SHOPEE"
                )

                print(
                    "🛍️",
                    nome
                )

                print(
                    "💰",
                    texto_preco(preco)
                )

                return True

    # ========================================================
    # 3. TENTAR OUTRAS IMAGENS
    # ========================================================

    if imagens_shopee:

        print(
            "🔎 Tentando demais imagens..."
        )

        for numero, url_imagem in enumerate(
            imagens_shopee[:8],
            start=1
        ):

            if url_imagem == imagem_validada:
                continue

            print(
                f"🖼️ Tentativa {numero}:",
                url_imagem[:180]
            )

            conteudo = baixar_imagem(
                url_imagem,
                referer=dados.get(
                    "link_resolvido"
                )
            )

            if not conteudo:
                continue

            resposta = enviar_foto_bytes(
                CHAT_ID,
                conteudo,
                legenda,
                teclado
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

                return True

    # ========================================================
    # 4. SEM IMAGEM
    # ========================================================

    print(
        "⚠️ Nenhuma imagem conseguiu ser baixada."
    )

    print(
        "📝 Publicando produto como texto."
    )

    resposta = telegram_post(
        "sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": legenda,
            "reply_markup": json.dumps(
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
            "✅ PRODUTO PUBLICADO COMO TEXTO"
        )

        return True

    print(
        "❌ Telegram recusou publicação."
    )

    print(
        resposta
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
                time.time() - ultimo
                >= INTERVALO
            ):

                if publicar_proximo():

                    redis_cmd(
                        "SET",
                        "ultimo_post",
                        str(time.time())
                    )

        except Exception as erro:

            print(
                "❌ Erro no publicador:",
                repr(erro)
            )

        time.sleep(60)


# ============================================================
# FILA
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
            f"🔗 {produto.get('link', '')}\n\n"
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
# NOME DA MENSAGEM
# ============================================================

def encontrar_nome_mensagem(texto):

    if not texto:

        return "Achadinho da Shopee"

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

        if (
            "r$" in linha_lower
        ):
            continue

        if len(linha) >= 5:

            return linha[:250]

    return "Achadinho da Shopee"


# ============================================================
# PROCESSAR MENSAGEM
# ============================================================

def diagnosticar_shopee(chat_id, texto):

    link = encontrar_link(texto)

    if not link:
        enviar_mensagem(
            chat_id,
            "Use: /testar https://s.shopee.com.br/xxxx"
        )
        return

    linhas = ["DIAGNOSTICO SHOPEE", "Link: " + link[:150]]

    try:
        url_final, historico = resolver_com_historico(link)
        linhas.append("URL final: " + url_final[:200])
        ids = extrair_ids(url_final, *historico, link)
    except Exception as erro:
        linhas.append("Erro resolvendo: " + repr(erro)[:150])
        url_final, ids = link, extrair_ids(link)

    linhas.append("IDs da URL: " + str(ids))

    agentes = [(ua[:18], headers_crawler(ua)) for ua in CRAWLER_UAS]
    agentes.append(("Celular", HEADERS))

    primeiro_html = None

    for nome_ua, hdr in agentes:
        try:
            r = requests.get(
                url_final, headers=hdr,
                allow_redirects=True, timeout=25
            )
            corpo = r.text or ""
            if primeiro_html is None and r.status_code == 200:
                primeiro_html = corpo
            tem_foto = bool(re.search(
                r"susercontent\\.com/file/[A-Za-z0-9_-]{16,}", corpo
            ))
            titulo = re.search(
                r"<title[^>]*>(.*?)</title>", corpo,
                re.IGNORECASE | re.DOTALL
            )
            linhas.append(
                f"[{nome_ua}] HTTP {r.status_code} | {len(corpo)} chars"
                f" | foto produto: {tem_foto}"
                f" | titulo: "
                + (limpar_texto(titulo.group(1))[:60] if titulo else "-")
            )
        except Exception as erro:
            linhas.append(f"[{nome_ua}] erro: " + repr(erro)[:100])

    if primeiro_html and not ids:
        ids = ids_do_html(primeiro_html)
        linhas.append("IDs achados no HTML: " + str(ids))

    if ids:
        shopid, itemid = ids
        for rota in (
            f"https://shopee.com.br/api/v4/item/get?itemid={itemid}&shopid={shopid}",
            f"https://shopee.com.br/api/v4/pdp/get_pc?item_id={itemid}&shop_id={shopid}&tz_offset_minutes=-180&detail_level=0",
        ):
            try:
                r = requests.get(rota, headers=API_HEADERS, timeout=25)
                linhas.append(
                    "[API " + rota.split("?")[0][-12:] + "] HTTP "
                    + str(r.status_code) + " | " + r.text[:130].replace(chr(10), " ")
                )
            except Exception as erro:
                linhas.append("[API] erro: " + repr(erro)[:100])

    linhas.append("Cookie configurado: " + str(bool(os.environ.get("SHOPEE_COOKIE"))))

    enviar_mensagem(chat_id, chr(10).join(linhas)[:3900])

def processar_mensagem(mensagem):

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
            mensagem
            .get("from", {})
            .get("id", "")
        )

        if usuario_id != str(
            ADMIN_ID
        ):
            return

    # ========================================================
    # FOTO + LEGENDA
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
                "❌ Não encontrei um link válido da Shopee.\n\n"
                "Envie a foto com o link da Shopee na legenda."
            )

            return

        nome = encontrar_nome_mensagem(
            legenda
        )

        preco_msg = extrair_preco_mensagem(
            legenda
        )

        # FOTO DO USUÁRIO
        adicionado, posicao = adicionar_na_fila(
            nome,
            link,
            file_id,
            preco_msg
        )

        if not adicionado:

            enviar_mensagem(
                chat_id,
                "⚠️ Esse produto já está na fila."
            )

            return

        linha_preco = (
            texto_preco(preco_msg)
            + " (da legenda)"
            if preco_msg
            else
            "💰 Preço será consultado na Shopee."
        )

        enviar_mensagem(
            chat_id,
            f"""✅ PRODUTO ADICIONADO!

🛍️ {nome}

📦 Posição na fila: {posicao}

🔗 Link encontrado.
{linha_preco}
🖼️ Sua foto será usada na publicação.
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

⭐ MELHOR OPÇÃO:

Envie a FOTO do produto com o link na legenda.

Assim o bot usa a própria foto que você enviou.

Se enviar somente o link, o bot tentará buscar automaticamente:

🛍️ Nome do produto
💰 Preço
🖼️ Imagem do produto

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
    # DIAGNOSTICO
    # --------------------------------------------------------

    if texto.startswith("/testar"):

        diagnosticar_shopee(
            chat_id,
            texto
        )

        return

    # --------------------------------------------------------
    # LIMPAR FILA
    # --------------------------------------------------------

    if texto == "/limparfila":

        limpar_fila(
            chat_id
        )

        return

    # ========================================================
    # LINK
    # ========================================================

    link = encontrar_link(
        texto
    )

    if not link:

        enviar_mensagem(
            chat_id,
            "❌ Não encontrei um link válido da Shopee.\n\n"
            "Cole a mensagem completa do produto ou envie somente o link da Shopee."
        )

        return

    # --------------------------------------------------------
    # PRIMEIRO: NOME DA MENSAGEM
    # --------------------------------------------------------

    nome_mensagem = (
        encontrar_nome_mensagem(
            texto
        )
    )

    preco_msg = (
        extrair_preco_mensagem(
            texto
        )
    )

    # ========================================================
    # AGORA CONSULTA A SHOPEE
    # ========================================================

    print(
        "================================"
    )

    print(
        "🔎 CONSULTANDO SHOPEE NA ENTRADA..."
    )

    print(
        "🔗",
        link
    )

    dados = consultar_shopee(
        link
    )

    # ========================================================
    # NOME
    # ========================================================

    nome = (
        dados.get("nome")
        or nome_mensagem
        or "Achadinho da Shopee"
    )

    # ========================================================
    # PREÇO
    # ========================================================

    preco = (
        preco_msg
        or dados.get("preco")
    )

    # ========================================================
    # IMAGEM
    # ========================================================

    imagem = dados.get(
        "imagem"
    )

    link_resolvido = dados.get(
        "link_resolvido"
    )

    # ========================================================
    # ADICIONAR
    # ========================================================

    adicionado, posicao = adicionar_na_fila(
        nome,
        link,
        imagem,
        preco,
        link_resolvido
    )

    if not adicionado:

        enviar_mensagem(
            chat_id,
            "⚠️ Esse produto já está na fila."
        )

        return

    # ========================================================
    # MENSAGEM DE CONFIRMAÇÃO
    # ========================================================

    if preco_msg:

        linha_preco = (
            texto_preco(preco_msg)
            + " (da mensagem)"
        )

    elif dados.get("preco"):

        linha_preco = (
            texto_preco(
                dados.get("preco")
            )
            + " (Shopee)"
        )

    else:

        linha_preco = (
            "💰 Preço não encontrado."
        )

    if imagem:

        linha_imagem = (
            "🖼️ Imagem encontrada e validada."
        )

    else:

        linha_imagem = (
            "🖼️ Imagem não encontrada."
        )

    enviar_mensagem(
        chat_id,
        f"""✅ PRODUTO ADICIONADO!

🛍️ {nome}

📦 Posição na fila: {posicao}

🔗 Link encontrado.
{linha_preco}
{linha_imagem}
⏰ Será publicado automaticamente."""
    )

    print(
        "================================"
    )

    print(
        "✅ PRODUTO SALVO NA FILA"
    )

    print(
        "🛍️ NOME:",
        nome
    )

    print(
        "💰 PREÇO:",
        texto_preco(preco)
    )

    print(
        "🖼️ IMAGEM:",
        imagem
    )

    print(
        "================================"
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

            for update in dados[
                "result"
            ]:

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
                            repr(erro)
                        )

                        try:

                            enviar_mensagem(
                                update["message"][
                                    "chat"
                                ]["id"],
                                "⚠️ Erro temporário. Tente enviar novamente."
                            )

                        except Exception:
                            pass

        except Exception as erro:

            print(
                "❌ Erro:",
                repr(erro)
            )

            time.sleep(5)


# ============================================================
# SERVIDOR DE SAÚDE
# ============================================================

class Saude(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(200)

        self.end_headers()

        self.wfile.write(
            b"ok"
        )

    def do_HEAD(self):

        self.send_response(200)

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
        "🛍️ LEITOR DE NOMES ATIVO"
    )

    print(
        "📸 FILE_ID TELEGRAM ATIVO"
    )

    print(
        "🔗 LEITOR DE LINKS ATIVO"
    )

    print(
        "======================================"
    )

    iniciar_bot()
