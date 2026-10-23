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
        headers={"Authorization": f"Bearer {REDIS_TOKEN}"},
        json=list(args),
        timeout=15
    )
    resp.raise_for_status()
    return resp.json().get("result")


def carregar_fila():
    dado = redis_cmd("GET", "fila")
    return json.loads(dado) if dado else []


def salvar_fila(fila):
    redis_cmd("SET", "fila", json.dumps(fila, ensure_ascii=False))


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
            data={"chat_id": chat_id, "text": texto},
            timeout=30
        )
        return resposta.json()

    except Exception as erro:
        print("❌ Erro Telegram:", erro)
        return {"ok": False}


# ==========================================
# CAPTURA DE DADOS DA SHOPEE
# ==========================================

def limpar_html(texto):
    texto = unescape(texto)
    texto = re.sub(r"<[^>]+>", "", texto)
    return texto.strip()


def extrair_meta(html, propriedade):
    padroes = [
        rf'<meta[^>]+property=["\']{re.escape(propriedade)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(propriedade)}["\']',
        rf'<meta[^>]+name=["\']{re.escape(propriedade)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(propriedade)}["\']'
    ]

    for padrao in padroes:
        resultado = re.search(padrao, html, re.IGNORECASE)

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
            dados = json.loads(unescape(bloco.strip()))

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

        if not eh_produto:
            continue

        nome = item.get("name")
        imagem = item.get("image")
        oferta = item.get("offers")

        if isinstance(oferta, list) and oferta:
            oferta = oferta[0]

        if isinstance(oferta, dict):
            preco = oferta.get("price") or oferta.get("lowPrice")

        break

    return nome, imagem, preco


# ==========================================
# CONVERTER PREÇO
# ==========================================

def converter_preco(valor):
    if valor is None:
        return None

    try:
        texto = str(valor).strip()
        texto = texto.replace("R$", "").replace("BRL", "").replace(" ", "")

        # Formato brasileiro: 1.234,56
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
            numero = float(texto)
        else:
            numero = float(texto)

        if numero <= 0 or numero >= 1000000:
            return None

        # Shopee pode devolver preço em centavos
        if numero >= 1000:
            numero = numero / 100

        if numero <= 0 or numero >= 100000:
            return None

        return numero

    except Exception:
        return None


# ==========================================
# ENCONTRAR PREÇO
# ==========================================

def encontrar_preco(html, texto, pagina):
    candidatos = []

    # 1. TEXTO VISÍVEL
    if texto:
        padroes = [
            r'R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})',
            r'R\$\s*([0-9]+,[0-9]{2})',
            r'R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*\.[0-9]{2})',
            r'BRL\s*([0-9]+(?:[.,][0-9]{2})?)'
        ]

        for padrao in padroes:
            encontrados = re.findall(padrao, texto, re.IGNORECASE)

            for valor in encontrados:
                numero = converter_preco(valor)

                if numero:
                    candidatos.append(numero)

    if candidatos:
        candidatos.sort()
        preco = candidatos[0]
        print("💰 PREÇO ENCONTRADO NO TEXTO:", preco)
        return preco

    # 2. DOM
    try:
        elementos = pagina.locator("text=/R\\$\\s*[0-9]/")
        quantidade = elementos.count()

        print("🔍 Elementos com R$:", quantidade)

        candidatos_dom = []

        for i in range(min(quantidade, 50)):
            try:
                valor_texto = elementos.nth(i).inner_text(timeout=3000).strip()

                encontrados = re.findall(
                    r'R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2})',
                    valor_texto
                )

                for valor in encontrados:
                    numero = converter_preco(valor)

                    if numero:
                        candidatos_dom.append(numero)

            except Exception:
                continue

        if candidatos_dom:
            candidatos_dom.sort()
            preco = candidatos_dom[0]
            print("💰 PREÇO ENCONTRADO NO DOM:", preco)
            return preco

    except Exception as erro:
        print("⚠️ Erro lendo DOM:", erro)

    # 3. HTML / JSON
    padroes_html = [
        r'"price"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',
        r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"salePrice"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',
        r'"currentPrice"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',
        r'"minPrice"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',
        r'"priceMin"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',
        r'"price_min"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',
        r'"price_max"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',
        r'"price_before_discount"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"',
        r'"priceBeforeDiscount"\s*:\s*"([0-9]+(?:[.,][0-9]+)?)"'
    ]

    candidatos_html = []

    for padrao in padroes_html:
        encontrados = re.findall(padrao, html, re.IGNORECASE)

        for valor in encontrados:
            numero = converter_preco(valor)

            if numero:
                candidatos_html.append(numero)

    if candidatos_html:
        validos = sorted(x for x in candidatos_html if x < 100000)

        if validos:
            preco = validos[0]
            print("💰 PREÇO ENCONTRADO NO HTML:", preco)
            return preco

    return None


# ==========================================
# NOVO LEITOR SHOPEE
# ==========================================

def extrair_dados_shopee(link):
    print("")
    print("======================================")
    print("🔎 ANALISANDO LINK SHOPEE")
    print(link)
    print("======================================")

    nome = None
    preco = None
    imagem = None
    navegador = None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,"
            "image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
        "Referer": "https://shopee.com.br/"
    }

    try:
        # ==========================================
        # RESOLVER LINK
        # ==========================================

        print("🔗 Resolvendo link...")

        try:
            sessao = requests.Session()

            resposta_link = sessao.get(
                link,
                headers=headers,
                timeout=25,
                allow_redirects=True
            )

            url_final = resposta_link.url
            html_http = resposta_link.text

            print("🔗 URL final:", url_final)
            print("📄 HTML HTTP:", len(html_http), "caracteres")

        except Exception as erro:
            print("⚠️ Falha ao resolver link:", repr(erro))
            url_final = link
            html_http = ""

        # ==========================================
        # PRIMEIRA TENTATIVA NO HTML HTTP
        # ==========================================

        if html_http:

            nome_meta = extrair_meta(html_http, "og:title")

            if nome_meta:
                nome_meta = re.sub(r"\s+", " ", unescape(str(nome_meta))).strip()

                if (
                    len(nome_meta) > 3
                    and nome_meta.lower() not in ("shopee", "shopee brasil")
                ):
                    nome = nome_meta
                    print("🛒 Nome encontrado no HTML:", nome)

            imagem_meta = extrair_meta(html_http, "og:image")

            if imagem_meta:
                imagem = str(imagem_meta).strip()

                if imagem.startswith("//"):
                    imagem = "https:" + imagem

                if imagem.startswith("http"):
                    print("🖼️ Imagem encontrada no HTML:", imagem)

            # Meta de preço
            for propriedade in (
                "product:price:amount",
                "og:price:amount",
                "price"
            ):
                if preco:
                    break

                valor = extrair_meta(html_http, propriedade)

                if valor:
                    convertido = converter_preco(valor)

                    if convertido:
                        preco = convertido
                        print("💰 Preço encontrado na meta:", preco)
                        break

            # JSON-LD
            try:
                jsonld = extrair_jsonld(html_http)

                nome_json, imagem_json, preco_json = procurar_produto_jsonld(jsonld)

                if not nome and nome_json:
                    nome = str(nome_json).strip()
                    print("🛒 Nome encontrado no JSON-LD:", nome)

                if not imagem and imagem_json:
                    if isinstance(imagem_json, list):
                        for img in imagem_json:
                            if img:
                                imagem = str(img).strip()
                                break
                    else:
                        imagem = str(imagem_json).strip()

                    if imagem.startswith("//"):
                        imagem = "https:" + imagem

                    print("🖼️ Imagem encontrada no JSON-LD:", imagem)

                if not preco and preco_json:
                    convertido = converter_preco(preco_json)

                    if convertido:
                        preco = convertido
                        print("💰 Preço encontrado no JSON-LD:", preco)

            except Exception as erro:
                print("⚠️ Erro no JSON-LD:", repr(erro))

        # ==========================================
        # PLAYWRIGHT
        # ==========================================

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            print("🌐 Abrindo produto no navegador...")

            navegador = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled"
                ]
            )

            pagina = navegador.new_page(
                viewport={"width": 1366, "height": 900},
                user_agent=headers["User-Agent"],
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                extra_http_headers={
                    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8"
                }
            )

            pagina.set_default_timeout(15000)

            try:
                pagina.add_init_script("""
                    Object.defineProperty(
                        navigator,
                        'webdriver',
                        { get: () => undefined }
                    );
                """)
            except Exception:
                pass

            print("📍 Navegando para:", url_final)

            try:
                pagina.goto(
                    url_final,
                    wait_until="domcontentloaded",
                    timeout=45000
                )
            except Exception as erro:
                print("⚠️ Erro no carregamento:", repr(erro))

            print("⏳ Aguardando dados da Shopee...")
            pagina.wait_for_timeout(7000)

            try:
                pagina.evaluate("""
                    window.scrollTo(
                        0,
                        Math.min(document.body.scrollHeight, 1200)
                    );
                """)
                pagina.wait_for_timeout(3000)
            except Exception:
                pass

            print("📍 Página atual:", pagina.url)

            try:
                html = pagina.content()
            except Exception:
                html = ""

            print("📄 HTML renderizado:", len(html), "caracteres")

            try:
                texto = pagina.locator("body").inner_text(timeout=10000)
            except Exception:
                texto = ""

            print("📝 Texto visível:", len(texto), "caracteres")

            # ==========================================
            # NOME
            # ==========================================

            if not nome:
                nome_og = extrair_meta(html, "og:title")

                if nome_og:
                    nome_og = re.sub(
                        r"\s+",
                        " ",
                        unescape(str(nome_og))
                    ).strip()

                    if (
                        len(nome_og) > 3
                        and "shopee brasil" not in nome_og.lower()
                        and nome_og.lower() != "shopee"
                    ):
                        nome = nome_og
                        print("🛒 Nome encontrado:", nome)

            if not nome:
                try:
                    h1s = pagina.locator("h1")
                    quantidade = h1s.count()

                    print("🔤 H1 encontrados:", quantidade)

                    for i in range(min(quantidade, 10)):
                        try:
                            texto_h1 = h1s.nth(i).inner_text(timeout=3000).strip()
                            texto_h1 = re.sub(r"\s+", " ", texto_h1).strip()

                            if (
                                len(texto_h1) >= 5
                                and "shopee" not in texto_h1.lower()
                            ):
                                nome = texto_h1
                                print("🛒 Nome encontrado no H1:", nome)
                                break

                        except Exception:
                            continue

                except Exception:
                    pass

            # ==========================================
            # IMAGEM
            # ==========================================

            if not imagem:
                imagem_og = extrair_meta(html, "og:image")

                if imagem_og:
                    imagem = str(imagem_og).strip()

                    if imagem.startswith("//"):
                        imagem = "https:" + imagem

                    if imagem.startswith("http"):
                        print("🖼️ Imagem encontrada:", imagem)

            # Procurar diretamente URLs da CDN no HTML renderizado
            if not imagem and html:
                padroes_imagem = [
                    r'https?://[^"\']*susercontent\.com/file/[^"\'\s]+',
                    r'https?:\\?/\\?/[^"\'\s]*susercontent\.com/file/[^"\'\s]+',
                    r'https?://[^"\']*shopee[^"\'\s]*/file/[^"\'\s]+'
                ]

                encontradas = []

                for padrao in padroes_imagem:
                    for img in re.findall(padrao, html, re.IGNORECASE):
                        img = unescape(
                            img.replace("\\/", "/").replace("\\u002F", "/")
                        )

                        if img.startswith("http") and img not in encontradas:
                            encontradas.append(img)

                if encontradas:
                    imagem = encontradas[0]
                    print("🖼️ Imagem encontrada na CDN:", imagem)

            # ==========================================
            # IMAGENS DO DOM
            # ==========================================

            if not imagem:
                try:
                    imagens = pagina.locator("img")
                    quantidade = imagens.count()

                    print("🖼️ IMG no DOM:", quantidade)

                    candidatas = []

                    for i in range(min(quantidade, 100)):
                        try:
                            elemento = imagens.nth(i)
                            src = None

                            for atributo in (
                                "src",
                                "data-src",
                                "data-lazy-src",
                                "data-original",
                                "data-url"
                            ):
                                valor = elemento.get_attribute(atributo)

                                if valor:
                                    src = valor
                                    break

                            if not src:
                                srcset = elemento.get_attribute("srcset")

                                if srcset:
                                    src = (
                                        srcset.split(",")[0]
                                        .strip()
                                        .split(" ")[0]
                                    )

                            if not src:
                                continue

                            src = str(src).strip().replace("\\/", "/")

                            if src.startswith("//"):
                                src = "https:" + src

                            if not src.startswith("http"):
                                continue

                            src_lower = src.lower()

                            if any(
                                palavra in src_lower
                                for palavra in (
                                    "logo",
                                    "avatar",
                                    "icon",
                                    "favicon"
                                )
                            ):
                                continue

                            candidatas.append(src)

                        except Exception:
                            continue

                    for src in candidatas:
                        if (
                            "susercontent.com/file/" in src.lower()
                            or "/file/" in src.lower()
                        ):
                            imagem = src
                            break

                    if not imagem and candidatas:
                        imagem = candidatas[0]

                    if imagem:
                        print("🖼️ IMAGEM ENCONTRADA NO DOM:", imagem)

                except Exception as erro:
                    print("⚠️ Erro lendo imagens:", repr(erro))

            # ==========================================
            # JSON-LD RENDERIZADO
            # ==========================================

            try:
                jsonld = extrair_jsonld(html)

                nome_json, imagem_json, preco_json = procurar_produto_jsonld(jsonld)

                if not nome and nome_json:
                    nome = str(nome_json).strip()
                    print("🛒 Nome JSON-LD:", nome)

                if not imagem and imagem_json:
                    if isinstance(imagem_json, list):
                        for img in imagem_json:
                            if img:
                                imagem = str(img).strip()
                                break
                    else:
                        imagem = str(imagem_json).strip()

                    if imagem.startswith("//"):
                        imagem = "https:" + imagem

                    print("🖼️ Imagem JSON-LD:", imagem)

                if not preco and preco_json:
                    convertido = converter_preco(preco_json)

                    if convertido:
                        preco = convertido
                        print("💰 Preço JSON-LD:", preco)

            except Exception as erro:
                print("⚠️ Erro JSON-LD renderizado:", repr(erro))

            # ==========================================
            # PREÇO
            # ==========================================

            if not preco:
                preco = encontrar_preco(html, texto, pagina)

            # ==========================================
            # ATRIBUTOS DO DOM
            # ==========================================

            if not preco:
                try:
                    # Procurar somente elementos razoavelmente próximos
                    # de textos com R$, evitando varrer milhares de nós.
                    elementos = pagina.locator(
                        "span,div,p,strong,b"
                    )

                    quantidade = elementos.count()
                    candidatos = []

                    for i in range(min(quantidade, 1500)):
                        try:
                            valor_texto = elementos.nth(i).inner_text(timeout=300).strip()

                            if "R$" not in valor_texto:
                                continue

                            encontrados = re.findall(
                                r'R\$\s*'
                                r'([0-9]{1,3}(?:\.[0-9]{3})*,\d{2}|[0-9]+,\d{2})',
                                valor_texto
                            )

                            for valor in encontrados:
                                convertido = converter_preco(valor)

                                if convertido:
                                    candidatos.append(convertido)

                        except Exception:
                            continue

                    if candidatos:
                        candidatos.sort()
                        preco = candidatos[0]
                        print("💰 PREÇO ENCONTRADO NO DOM:", preco)

                except Exception as erro:
                    print("⚠️ Erro procurando preço no DOM:", repr(erro))

            try:
                navegador.close()
            except Exception:
                pass

            navegador = None

    except Exception as erro:
        print("❌ ERRO NO LEITOR SHOPEE:", repr(erro))

        try:
            if navegador:
                navegador.close()
        except Exception:
            pass

    # ==========================================
    # FORMATAÇÃO
    # ==========================================

    if preco:
        try:
            valor = float(preco)

            preco = (
                f"R$ {valor:,.2f}"
                .replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
            )

        except Exception as erro:
            print("⚠️ Erro formatando preço:", repr(erro))

    if imagem:
        imagem = str(imagem).strip().replace("\\/", "/")

        if imagem.startswith("//"):
            imagem = "https:" + imagem

        if not imagem.startswith("http"):
            imagem = None

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

    oferta = f"💰 {preco}" if preco else "💰 OFERTA ESPECIAL"

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

    if imagem:
        url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"

        try:
            print("🖼️ Baixando imagem para publicar...")

            resposta_imagem = requests.get(
                imagem,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Linux; Android 10; K) "
                        "AppleWebKit/537.36 "
                        "(KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Mobile Safari/537.36"
                    ),
                    "Referer": "https://shopee.com.br/"
                },
                timeout=30
            )

            resposta_imagem.raise_for_status()

            arquivo_imagem = resposta_imagem.content

            print(
                "🖼️ Imagem baixada:",
                len(arquivo_imagem),
                "bytes"
            )

            dados = {
                "chat_id": CHAT_ID,
                "caption": legenda,
                "reply_markup": json.dumps(teclado)
            }

            arquivos = {
                "photo": (
                    "produto.jpg",
                    arquivo_imagem,
                    "image/jpeg"
                )
            }

            resposta = requests.post(
                url,
                data=dados,
                files=arquivos,
                timeout=60
            )

            resultado = resposta.json()

        except Exception as erro:
            print("⚠️ Não conseguiu enviar a foto:", repr(erro))

            try:
                print("🔄 Tentando enviar imagem pela URL...")

                dados = {
                    "chat_id": CHAT_ID,
                    "photo": imagem,
                    "caption": legenda,
                    "reply_markup": json.dumps(teclado)
                }

                resposta = requests.post(
                    url,
                    data=dados,
                    timeout=60
                )

                resultado = resposta.json()

            except Exception as erro2:
                print("❌ Falha total ao enviar foto:", repr(erro2))
                resultado = {"ok": False}

    else:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

        dados = {
            "chat_id": CHAT_ID,
            "text": legenda,
            "reply_markup": json.dumps(teclado)
        }

        try:
            resposta = requests.post(
                url,
                data=dados,
                timeout=30
            )

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
# ==========================================

def publicador_automatico():
    print("🚀 Publicador automático iniciado.")

    while True:
        try:
            ultimo = float(
                redis_cmd("GET", "ultimo_post") or 0
            )

            if time.time() - ultimo >= INTERVALO:
                if publicar_proximo():
                    redis_cmd(
                        "SET",
                        "ultimo_post",
                        str(time.time())
                    )

        except Exception as erro:
            print("❌ Erro no publicador:", erro)

        time.sleep(60)


# ==========================================
# MOSTRAR FILA
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


# ==========================================
# LIMPAR FILA
# ==========================================

def limpar_fila(chat_id):
    salvar_fila([])
    enviar_mensagem(chat_id, "🗑️ Fila limpa com sucesso!")


# ==========================================
# PROCESSAR MENSAGEM
# ==========================================

def processar_mensagem(mensagem):
    chat_id = mensagem["chat"]["id"]

    if ADMIN_ID and str(
        mensagem.get("from", {}).get("id")
    ) != str(ADMIN_ID):
        return

    # FOTO + LEGENDA
    if "photo" in mensagem:
        file_id = mensagem["photo"][-1]["file_id"]

        legenda = mensagem.get("caption", "").strip()

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

        nome_shopee, preco, imagem_shopee = extrair_dados_shopee(link)

        if nome_shopee:
            nome = nome_shopee

        # Foto enviada pelo usuário continua tendo prioridade
        imagem = file_id

        adicionado, posicao = adicionar_na_fila(
            nome,
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

🛒 {nome}

💰 Preço: {preco or 'não encontrado'}

📦 Posição na fila: {posicao}

🖼️ Foto salva.
⏰ Publicação automática ativa."""
        )

        return

    # TEXTO
    texto = mensagem.get("text", "").strip()

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

    if texto == "/fila":
        mostrar_fila(chat_id)
        return

    if texto == "/limparfila":
        limpar_fila(chat_id)
        return

    # SOMENTE LINK
    if texto.startswith("http"):
        link = texto.split()[0]

        print("🔎 Link recebido:", link)

        nome, preco, imagem = extrair_dados_shopee(link)

        if not nome:
            enviar_mensagem(
                chat_id,
                """⚠️ Não consegui identificar o nome do produto automaticamente.

Envie uma FOTO com a legenda:

Nome do produto
Link da Shopee"""
            )
            return

        adicionado, posicao = adicionar_na_fila(
            nome,
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

🛒 {nome}

💰 Preço: {preco or 'não encontrado'}

🖼️ Imagem: {'encontrada ✅' if imagem else 'não encontrada ⚠️'}

📦 Posição: {posicao}

⏰ Será publicado automaticamente."""
        )

        return

    # NOME + LINK
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
        enviar_mensagem(chat_id, "❌ Link inválido.")
        return

    nome_shopee, preco, imagem = extrair_dados_shopee(link)

    if nome_shopee:
        nome = nome_shopee

    adicionado, posicao = adicionar_na_fila(
        nome,
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
    print("📥 Sistema de recebimento iniciado.")

    ultimo_update = None

    while True:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"

            parametros = {"timeout": 30}

            if ultimo_update is not None:
                parametros["offset"] = ultimo_update + 1

            resposta = requests.get(
                url,
                params=parametros,
                timeout=35
            )

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
# MINI SERVIDOR DO RENDER
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

    print("======================================")
    print("✅ SISTEMA COMPLETO ATIVO")
    print("⏰ 5 PRODUTOS POR HORA")
    print("🔎 CAPTURA AUTOMÁTICA SHOPEE ATIVA")
    print("======================================")

    iniciar_bot()
