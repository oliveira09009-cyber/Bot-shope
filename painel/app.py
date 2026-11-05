import os
import re
from urllib.parse import urlsplit, quote
from io import BytesIO

from flask import (
    Flask, request, redirect, session,
    render_template_string, send_file
)

import bot

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "chave-temporaria")
SENHA = os.environ.get("PANEL_PASSWORD", "")


def validar_link_shopee(link):
    if not link:
        return None

    link = link.strip()
    if not link.startswith(("http://", "https://")):
        link = "https://" + link

    try:
        partes = urlsplit(link)
        dominio = (partes.hostname or "").lower()
        dominios_validos = {
            "shopee.com.br", "www.shopee.com.br", "s.shopee.com.br",
            "shopee.com", "www.shopee.com", "shp.ee", "www.shp.ee"
        }
        if dominio not in dominios_validos or not partes.path or partes.path == "/":
            return None
        return link
    except Exception:
        return None


def extrair_dados_texto(texto):
    texto = (texto or "").strip()
    encontrados = re.findall(r"https?://[^\s<>\"']+", texto, flags=re.IGNORECASE)

    link = None
    for candidato in encontrados:
        link = validar_link_shopee(candidato.rstrip(".,);]}"))
        if link:
            break

    nome = None
    m_nome = re.search(
        r"(?:d[eê] uma olhada em|olha s[oó])\s+(.+?)\s+por\s+R\$",
        texto, flags=re.IGNORECASE | re.DOTALL
    )
    if m_nome:
        nome = re.sub(r"\s+", " ", m_nome.group(1)).strip(" .,!\n\r")

    preco = None
    m_preco = re.search(
        r"\bpor\s+R\$\s*([0-9.]+,[0-9]{2}|[0-9]+\.[0-9]{2})",
        texto, flags=re.IGNORECASE
    )
    if m_preco:
        valor = m_preco.group(1).replace(".", "").replace(",", ".")
        try:
            preco = float(valor)
        except ValueError:
            pass

    return link, nome, preco


def formatar_oferta(dados):
    preco = dados.get("preco")
    desconto = dados.get("desconto")

    if isinstance(desconto, dict):
        de = desconto.get("de")
        por = desconto.get("por")
        if de is not None and por is not None:
            try:
                de, por = float(de), float(por)
            except (TypeError, ValueError):
                de = por = None

            if de and por and de > por:
                texto = (
                    "❌ DE: " + bot.formatar_preco(de) + "\n"
                    "🔥 POR: " + bot.formatar_preco(por)
                )
                percentual = desconto.get("pct")
                if percentual is not None:
                    texto += f"\n🏷️ {int(percentual)}% DE DESCONTO"
                if desconto.get("estimado"):
                    texto += "\nPreço anterior estimado"
                return texto

    if preco:
        return bot.texto_preco(preco)
    return "💰 Preço não encontrado na consulta."


HTML = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Painel Achados Shopee</title>
<style>
* { box-sizing: border-box; }
body {
    font-family: Arial, sans-serif;
    background: #f3f4f6;
    padding: 15px;
    margin: 0;
}
.card {
    background: white;
    padding: 22px;
    border-radius: 15px;
    max-width: 500px;
    margin: 20px auto;
    box-shadow: 0 3px 15px rgba(0,0,0,0.07);
}
h2 { text-align: center; color: #ee4d2d; }
input, textarea, button, .botao {
    width: 100%;
    padding: 14px;
    margin: 9px 0;
    border-radius: 9px;
    font-size: 16px;
}
input, textarea { border: 1px solid #ddd; }
textarea {
    min-height: 105px;
    font-family: Arial, sans-serif;
}
button, .botao {
    display: block;
    border: none;
    color: white;
    font-weight: bold;
    text-align: center;
    text-decoration: none;
    cursor: pointer;
}
.btn-consultar, .btn-fila { background: #ee4d2d; }
.btn-whatsapp { background: #20b85a; }
.btn-instagram {
    background: linear-gradient(45deg, #feda75, #fa7e1e, #d62976, #962fbf, #4f5bd5);
}
img {
    width: 100%;
    border-radius: 10px;
    margin-top: 10px;
}
.preco {
    background: #fff4ef;
    border-radius: 10px;
    padding: 15px;
    font-size: 19px;
    font-weight: bold;
    white-space: pre-line;
    line-height: 1.7;
}
.erro { color: #d00000; }
.sucesso { color: green; }
.link {
    word-break: break-all;
    font-size: 13px;
    color: #666;
}
hr {
    border: 0;
    border-top: 1px solid #eee;
    margin: 20px 0;
}
a { color: #666; }
.info-compartilhamento {
    font-size: 12px;
    color: #777;
    text-align: center;
    line-height: 1.5;
}
</style>
</head>
<body>
<div class="card">
<h2>🛍️ Achados da Shopee</h2>
<p>Consulte seus produtos e prepare suas divulgações.</p>

<form method="post" action="/consultar">
<textarea name="link"
placeholder="Cole o link da Shopee ou a mensagem inteira..."
required></textarea>
<button class="btn-consultar">CONSULTAR PRODUTO</button>
</form>

{% if erro %}<p class="erro">{{ erro }}</p>{% endif %}

{% if produto %}
<hr>
<h3>{{ produto.nome }}</h3>
{% if produto.imagem %}
<img src="{{ produto.imagem }}" alt="Imagem do produto">
{% endif %}
<div class="preco">{{ produto.oferta }}</div>

{% if produto.link %}
<p class="link">🔗 {{ produto.link }}</p>
{% endif %}

<form method="post" action="/adicionar">
<input type="hidden" name="link" value="{{ produto.link }}">
<button class="btn-fila">ADICIONAR À FILA</button>
</form>

<button class="btn-whatsapp" type="button" onclick="compartilharWhatsApp()">
📲 ENVIAR PARA WHATSAPP BUSINESS
</button>

<button class="btn-instagram" type="button" onclick="compartilharInstagram()">
📸 COMPARTILHAR NO INSTAGRAM STORIES
</button>

<p id="status-compartilhamento" class="info-compartilhamento">
Escolha WhatsApp Business ou Instagram no menu de compartilhamento do celular.
No Instagram, confira a arte e adicione o adesivo de link da Shopee antes de publicar.
</p>

<script>
const mensagemProduto = {{ produto.whatsapp_texto|tojson }};
const temImagemProduto = {{ 'true' if produto.imagem else 'false' }};

async function obterArquivoImagem() {
    const resposta = await fetch('/imagem-compartilhar');
    if (!resposta.ok) throw new Error('Não foi possível preparar a imagem.');
    const blob = await resposta.blob();
    const tipo = blob.type || 'image/jpeg';
    const extensao = tipo.includes('png') ? 'png' : (tipo.includes('webp') ? 'webp' : 'jpg');
    return new File([blob], 'achadinho-shopee.' + extensao, {type: tipo});
}

async function compartilharWhatsApp() {
    const status = document.getElementById('status-compartilhamento');
    try {
        if (temImagemProduto && navigator.share && navigator.canShare) {
            status.textContent = 'Preparando a foto do produto...';
            const arquivo = await obterArquivoImagem();
            const dados = {
                files: [arquivo],
                text: mensagemProduto,
                title: 'Achadinho da Shopee'
            };
            if (navigator.canShare(dados)) {
                await navigator.share(dados);
                status.textContent = 'Compartilhamento aberto. Escolha WhatsApp Business e o grupo.';
                return;
            }
        }
    } catch (erro) {
        if (erro.name === 'AbortError') return;
        console.log('Compartilhamento com foto indisponível:', erro);
    }

    window.location.href = {{ produto.whatsapp|tojson }};
}

async function compartilharInstagram() {
    const status = document.getElementById('status-compartilhamento');

    try {
        if (!temImagemProduto) {
            throw new Error('Este produto não tem imagem disponível.');
        }
        if (!navigator.share || !navigator.canShare) {
            throw new Error('Este navegador não oferece compartilhamento de arquivos.');
        }

        status.textContent = 'Preparando a imagem para compartilhar...';
        const arquivo = await obterArquivoImagem();
        const dados = {
            files: [arquivo],
            title: 'Achados da Shopee'
        };

        if (!navigator.canShare(dados)) {
            throw new Error('O celular não permite compartilhar esta imagem por aqui.');
        }

        await navigator.share(dados);
        status.textContent =
            'Menu de compartilhamento aberto. Selecione Instagram/Stories, se aparecer. Depois adicione o adesivo de link da Shopee.';
    } catch (erro) {
        if (erro.name === 'AbortError') return;
        status.textContent = erro.message || 'Não foi possível abrir o compartilhamento do Instagram.';
    }
}
</script>
{% endif %}

{% if mensagem %}
<h3 class="sucesso">{{ mensagem }}</h3>
{% endif %}

<p style="text-align:center"><a href="/sair">Sair</a></p>
</div>
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def inicio():
    if not SENHA:
        return "Configure PANEL_PASSWORD no Render.", 503

    if not session.get("autorizado"):
        if request.method == "POST":
            if request.form.get("senha") == SENHA:
                session["autorizado"] = True
                return redirect("/")
            return "Senha incorreta."

        return """
        <!DOCTYPE html>
        <html lang="pt-br">
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <form method="post" style="padding:30px;max-width:400px;margin:auto">
        <h2>Painel Achados Shopee</h2>
        <input type="password" name="senha" placeholder="Senha de acesso" required>
        <button type="submit">Entrar</button>
        </form>
        </html>
        """

    return render_template_string(HTML)


@app.route("/consultar", methods=["POST"])
def consultar():
    if not session.get("autorizado"):
        return redirect("/")

    texto_original = request.form.get("link", "").strip()
    link_extraido, nome_informado, preco_informado = extrair_dados_texto(texto_original)
    link = link_extraido or validar_link_shopee(texto_original)

    if not link:
        return render_template_string(HTML, erro="Link Shopee inválido. Confira o endereço.")

    try:
        link_normalizado = bot.normalizar_link(link)
        if link_normalizado:
            link = link_normalizado
    except Exception:
        pass

    try:
        dados = bot.consultar_shopee(link)
        if not isinstance(dados, dict):
            dados = {}
    except Exception as erro:
        return render_template_string(
            HTML, erro="Erro ao consultar produto: " + str(erro)
        )

    nome = dados.get("nome") or nome_informado or "Produto Shopee"
    imagem = dados.get("imagem")
    oferta = formatar_oferta(dados)

    if not dados.get("preco") and not dados.get("desconto") and preco_informado is not None:
        oferta = "💰 PREÇO INFORMADO NA MENSAGEM: " + bot.formatar_preco(preco_informado)

    link_produto = dados.get("link_resolvido") or link

    produto = {
        "nome": nome,
        "imagem": imagem,
        "preco": dados.get("preco"),
        "desconto": dados.get("desconto"),
        "oferta": oferta,
        "link": link,
        "link_resolvido": link_produto
    }

    mensagem_whatsapp = (
        "🔥 ACHADINHO DA SHOPEE 🔥\n\n"
        "🛍️ " + str(nome) + "\n\n"
        + oferta + "\n\n"
        "⚡ Aproveite enquanto estiver disponível!\n\n"
        "🛒 Confira aqui:\n"
        + link_produto
    )

    produto["whatsapp_texto"] = mensagem_whatsapp
    produto["whatsapp"] = "https://wa.me/?text=" + quote(mensagem_whatsapp)

    session["produto"] = produto
    return render_template_string(HTML, produto=produto)


@app.route("/imagem-compartilhar", methods=["GET"])
def imagem_compartilhar():
    if not session.get("autorizado"):
        return "Acesso não autorizado.", 401

    produto = session.get("produto") or {}
    url = produto.get("imagem")
    if not url:
        return "Imagem não disponível.", 404

    try:
        partes = urlsplit(url)
        host = (partes.hostname or "").lower()

        if not (host.endswith("susercontent.com") or host.endswith("shopee.com.br")):
            return "Origem da imagem não permitida.", 400

        resposta = bot.requests.get(
            url,
            headers=getattr(bot, "HEADERS", {}),
            timeout=25,
            allow_redirects=True
        )
        resposta.raise_for_status()

        conteudo = resposta.content
        if not bot.imagem_valida_bytes(conteudo):
            return "O arquivo recebido não é uma imagem válida.", 502

        content_type = resposta.headers.get("Content-Type", "image/jpeg").split(";")[0]
        if not content_type.startswith("image/"):
            content_type = "image/jpeg"

        extensao = (
            "png" if "png" in content_type
            else ("webp" if "webp" in content_type else "jpg")
        )

        return send_file(
            BytesIO(conteudo),
            mimetype=content_type,
            as_attachment=True,
            download_name="produto-shopee." + extensao,
            max_age=0
        )

    except Exception as erro:
        print("Erro preparando imagem para compartilhamento:", repr(erro))
        return "Não foi possível baixar a imagem do produto.", 502


@app.route("/adicionar", methods=["POST"])
def adicionar():
    if not session.get("autorizado"):
        return redirect("/")

    produto = session.get("produto")

    if not produto or produto["link"] != request.form.get("link"):
        return render_template_string(HTML, erro="Consulte novamente o produto.")

    try:
        dados = bot.consultar_shopee(produto["link"])
        if not isinstance(dados, dict):
            dados = {}

        nome = dados.get("nome") or produto["nome"]
        imagem = dados.get("imagem") or produto.get("imagem")
        preco = dados.get("preco")

        resolvido = (
            dados.get("link_resolvido")
            or produto.get("link_resolvido")
        )

        ok, posicao = bot.adicionar_na_fila(
            nome,
            produto["link"],
            imagem,
            preco,
            resolvido
        )

        mensagem = (
            f"✅ Produto adicionado! Posição: {posicao}"
            if ok else "⚠️ Produto já está na fila."
        )

        session.pop("produto", None)
        return render_template_string(HTML, mensagem=mensagem)

    except Exception as erro:
        return render_template_string(
            HTML, erro="Erro ao adicionar produto: " + str(erro)
        )


@app.route("/sair")
def sair():
    session.clear()
    return redirect("/")


@app.route("/health")
def health():
    return "OK", 200


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=porta)
