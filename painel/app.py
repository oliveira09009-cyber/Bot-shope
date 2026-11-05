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
        if dominio not in dominios_validos:
            return None
        if not partes.path or partes.path == "/":
            return None
        return link
    except Exception:
        return None


def extrair_dados_texto(texto):
    """Extrai link, nome e preço de mensagens compartilhadas pela Shopee."""
    texto = (texto or "").strip()
    encontrados = re.findall(
        r"https?://[^\s<>\"']+", texto, flags=re.IGNORECASE
    )

    link = None
    for candidato in encontrados:
        link = validar_link_shopee(candidato.rstrip(".,);]}"))
        if link:
            break

    nome = None
    m_nome = re.search(
        r"(?:d[eê] uma olhada em|olha s[oó])\s+(.+?)\s+por\s+R\$",
        texto,
        flags=re.IGNORECASE | re.DOTALL
    )
    if m_nome:
        nome = re.sub(r"\s+", " ", m_nome.group(1)).strip(" .,!\n\r")

    preco = None
    m_preco = re.search(
        r"\bpor\s+R\$\s*([0-9.]+,[0-9]{2}|[0-9]+\.[0-9]{2})",
        texto,
        flags=re.IGNORECASE
    )
    if m_preco:
        valor = m_preco.group(1).replace(".", "").replace(",", ".")
        try:
            preco = float(valor)
        except ValueError:
            preco = None

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
                percentual = desconto.get("pct")
                texto = (
                    "❌ DE: " + bot.formatar_preco(de) + "\n"
                    "🔥 POR: " + bot.formatar_preco(por)
                )
                if percentual is not None:
                    texto += f"\n🏷️ {int(percentual)}% DE DESCONTO"
                if desconto.get("estimado"):
                    texto += "\nPreço anterior estimado"
                return texto

    if preco:
        return bot.texto_preco(preco)
    return "💰 Preço não encontrado na consulta."


HTML = r"""
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
    box-shadow: 0 3px 15px rgba(0,0,0,.07);
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
button:disabled { opacity: .6; cursor: wait; }
.btn-consultar, .btn-fila { background: #ee4d2d; }
.btn-whatsapp { background: #20b85a; }
.btn-instagram { background: #b52bd5; }
.btn-copiar { background: #343a40; }
img.produto-preview {
    width: 100%;
    border-radius: 10px;
    margin-top: 10px;
    object-fit: contain;
    background: #f7f7f7;
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
hr { border: 0; border-top: 1px solid #eee; margin: 20px 0; }
a { color: #666; }
.ajuda {
    font-size: 12px;
    color: #666;
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
placeholder="Cole o link da Shopee ou a mensagem inteira recebida do aplicativo..."
required></textarea>
<button class="btn-consultar">CONSULTAR PRODUTO</button>
</form>

{% if erro %}<p class="erro">{{ erro }}</p>{% endif %}

{% if produto %}
<hr>
<h3>{{ produto.nome }}</h3>
{% if produto.imagem %}
<img class="produto-preview" src="{{ produto.imagem }}" alt="Imagem do produto">
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

<hr>
<h3>📸 Instagram Stories</h3>
<p class="ajuda">O painel monta uma arte vertical 9:16 com a foto e o preço. Depois você escolhe Instagram Stories no menu de compartilhamento.</p>
<button class="btn-instagram" id="btn-story" type="button" onclick="compartilharStory()">
📸 PREPARAR E COMPARTILHAR STORY
</button>
<button class="btn-copiar" type="button" onclick="copiarLinkAfiliado()">
🔗 COPIAR LINK DE AFILIADO
</button>
<p class="ajuda" id="status-compartilhamento" aria-live="polite">
Depois de abrir o Instagram, adicione o adesivo LINK e cole o endereço copiado para deixar a compra clicável.
</p>

<script>
const dadosStory = {
    nome: {{ produto.nome|tojson }},
    oferta: {{ produto.oferta|tojson }},
    link: {{ produto.link_resolvido|tojson }},
    temImagem: {{ 'true' if produto.imagem else 'false' }}
};
const mensagemWhatsApp = {{ produto.whatsapp_texto|tojson }};
const linkWhatsApp = {{ produto.whatsapp|tojson }};

function atualizarStatus(texto) {
    document.getElementById('status-compartilhamento').textContent = texto;
}

async function copiarTexto(texto) {
    if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(texto);
        return;
    }
    const campo = document.createElement('textarea');
    campo.value = texto;
    campo.style.position = 'fixed';
    campo.style.opacity = '0';
    document.body.appendChild(campo);
    campo.select();
    const ok = document.execCommand('copy');
    campo.remove();
    if (!ok) throw new Error('Não foi possível copiar automaticamente.');
}

async function copiarLinkAfiliado() {
    try {
        await copiarTexto(dadosStory.link);
        atualizarStatus('✅ Link copiado! No Instagram, use o adesivo LINK e cole o endereço.');
    } catch (erro) {
        atualizarStatus('Não consegui copiar automaticamente. Copie o link exibido acima.');
    }
}

function carregarImagem(url) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = reject;
        img.src = url;
    });
}

function desenharTextoQuebrado(ctx, texto, x, y, largura, alturaLinha, maxLinhas) {
    const palavras = String(texto || '').split(/\s+/);
    let linha = '';
    let linhas = [];
    for (const palavra of palavras) {
        const teste = linha ? linha + ' ' + palavra : palavra;
        if (ctx.measureText(teste).width > largura && linha) {
            linhas.push(linha);
            linha = palavra;
        } else {
            linha = teste;
        }
    }
    if (linha) linhas.push(linha);
    if (linhas.length > maxLinhas) {
        linhas = linhas.slice(0, maxLinhas);
        let ultima = linhas[maxLinhas - 1];
        while (ctx.measureText(ultima + '…').width > largura && ultima.length > 1) {
            ultima = ultima.slice(0, -1);
        }
        linhas[maxLinhas - 1] = ultima + '…';
    }
    linhas.forEach((l, i) => ctx.fillText(l, x, y + i * alturaLinha));
}

async function criarImagemStory() {
    const canvas = document.createElement('canvas');
    canvas.width = 1080;
    canvas.height = 1920;
    const ctx = canvas.getContext('2d');

    // Fundo premium em tons da Shopee, com áreas seguras para a interface do Instagram.
    const fundo = ctx.createLinearGradient(0, 0, 1080, 1920);
    fundo.addColorStop(0, '#fff2e9');
    fundo.addColorStop(.52, '#fffaf6');
    fundo.addColorStop(1, '#ffe0d2');
    ctx.fillStyle = fundo;
    ctx.fillRect(0, 0, 1080, 1920);

    // Formas decorativas discretas.
    ctx.fillStyle = 'rgba(238,77,45,.08)';
    ctx.beginPath(); ctx.arc(1010, 350, 250, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(45, 1510, 210, 0, Math.PI * 2); ctx.fill();

    function caixa(x, y, w, h, r, cor, sombra=false) {
        ctx.save();
        if (sombra) {
            ctx.shadowColor = 'rgba(60,25,15,.13)';
            ctx.shadowBlur = 28;
            ctx.shadowOffsetY = 12;
        }
        ctx.fillStyle = cor;
        ctx.beginPath();
        ctx.roundRect(x, y, w, h, r);
        ctx.fill();
        ctx.restore();
    }

    function textoCentral(texto, x, y, fonte, cor, largura=900) {
        ctx.save();
        ctx.textAlign = 'center';
        ctx.fillStyle = cor;
        ctx.font = fonte;
        ctx.fillText(String(texto || ''), x, y, largura);
        ctx.restore();
    }

    // Cabeçalho de marca.
    caixa(64, 82, 952, 150, 42, '#ee4d2d', true);
    textoCentral('ACHADOS DA SHOPEE', 540, 177, '900 62px Arial', '#ffffff', 900);
    textoCentral('OFERTAS QUE VALEM A PENA', 540, 282, 'bold 27px Arial', '#9a3a25', 900);

    // Nome do produto, com quebra automática e limite de linhas.
    ctx.save();
    ctx.textAlign = 'center';
    ctx.fillStyle = '#242424';
    ctx.font = 'bold 54px Arial';
    desenharTextoQuebrado(ctx, dadosStory.nome, 540, 385, 900, 66, 2);
    ctx.restore();

    // Cartão da foto.
    caixa(75, 505, 930, 790, 48, '#ffffff', true);
    caixa(105, 535, 870, 730, 32, '#fff8f4');

    if (dadosStory.temImagem) {
        try {
            const resposta = await fetch('/imagem-compartilhar');
            if (!resposta.ok) throw new Error('Imagem indisponível');
            const blob = await resposta.blob();
            const objetoUrl = URL.createObjectURL(blob);
            const img = await carregarImagem(objetoUrl);

            const areaX = 135, areaY = 565, areaW = 810, areaH = 670;
            const escala = Math.min(areaW / img.width, areaH / img.height);
            const w = img.width * escala, h = img.height * escala;
            ctx.drawImage(img, areaX + (areaW - w) / 2, areaY + (areaH - h) / 2, w, h);
            URL.revokeObjectURL(objetoUrl);
        } catch (e) {
            textoCentral('Foto do produto', 540, 925, 'bold 40px Arial', '#888888');
        }
    } else {
        textoCentral('Foto não disponível', 540, 925, 'bold 40px Arial', '#888888');
    }

    // Faixa de oferta.
    caixa(205, 1330, 670, 78, 39, '#ee4d2d');
    textoCentral('🔥 OFERTA ESPECIAL 🔥', 540, 1383, 'bold 38px Arial', '#ffffff', 620);

    // Tenta destacar separadamente preço anterior e preço atual.
    const oferta = String(dadosStory.oferta || 'Consulte o preço na Shopee');
    const valores = oferta.match(/R\$\s*[0-9.]+,[0-9]{2}/g) || [];
    const percentual = oferta.match(/[0-9]{1,3}\s*%\s*DE\s*DESCONTO/i);

    caixa(75, 1440, 930, 275, 42, '#ffffff', true);
    if (valores.length >= 2) {
        const textoAntigo = 'DE ' + valores[0];
        ctx.save();
        ctx.font = 'bold 38px Arial';
        const larguraAntigo = ctx.measureText(textoAntigo).width;
        ctx.restore();
        textoCentral(textoAntigo, 540, 1515, 'bold 38px Arial', '#888888', 850);
        ctx.save();
        ctx.strokeStyle = '#999999'; ctx.lineWidth = 4;
        ctx.beginPath(); ctx.moveTo(540 - larguraAntigo/2, 1502); ctx.lineTo(540 + larguraAntigo/2, 1502); ctx.stroke();
        ctx.restore();
        textoCentral('POR', 540, 1585, 'bold 30px Arial', '#ee4d2d');
        textoCentral(valores[1], 540, 1670, '900 82px Arial', '#ee4d2d', 850);
        if (percentual) textoCentral(percentual[0].toUpperCase(), 540, 1710, 'bold 25px Arial', '#555555', 850);
    } else {
        const linhas = oferta.split('\n').filter(Boolean).slice(0, 3);
        linhas.forEach((linha, i) => textoCentral(linha.replace(/[❌🔥💰🏷️]/g, '').trim(), 540, 1535 + i * 65, 'bold 36px Arial', i === 0 ? '#ee4d2d' : '#333333', 850));
    }

    // Chamada visual. O link clicável será colocado pelo adesivo LINK do Instagram.
    caixa(105, 1750, 870, 95, 45, '#ee4d2d');
    textoCentral('TOQUE NO LINK PARA COMPRAR', 540, 1812, 'bold 36px Arial', '#ffffff', 820);
    textoCentral('Preço e disponibilidade podem mudar na Shopee', 540, 1880, '24px Arial', '#795b50', 900);

    return new Promise(resolve => canvas.toBlob(resolve, 'image/png', 1));
}
async function compartilharStory() {
    const botao = document.getElementById('btn-story');
    botao.disabled = true;
    botao.textContent = 'PREPARANDO SUA ARTE...';
    try {
        atualizarStatus('Montando a arte vertical do produto...');
        const blob = await criarImagemStory();
        if (!blob) throw new Error('Não foi possível gerar a imagem.');

        const arquivo = new File([blob], 'story-achados-shopee.png', {type: 'image/png'});
        const dados = {files: [arquivo], title: 'Achados da Shopee'};
        if (navigator.share && navigator.canShare && navigator.canShare(dados)) {
            await navigator.share(dados);
            atualizarStatus('Compartilhamento aberto. Escolha Instagram Stories. Depois adicione o adesivo LINK e cole seu link de afiliado.');
        } else {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'story-achados-shopee.png';
            a.click();
            setTimeout(() => URL.revokeObjectURL(url), 30000);
            atualizarStatus('A arte foi baixada. Abra o Instagram, crie um Story com essa imagem e adicione o adesivo LINK. Copie o link pelo botão acima.');
        }
    } catch (erro) {
        if (erro.name !== 'AbortError') {
            console.error(erro);
            atualizarStatus('Não consegui abrir o compartilhamento. Tente novamente ou baixe a arte pelo navegador.');
        }
    } finally {
        botao.disabled = false;
        botao.textContent = '📸 PREPARAR E COMPARTILHAR STORY';
    }
}

async function compartilharWhatsApp() {
    try {
        const resposta = await fetch('/imagem-compartilhar');
        if (!resposta.ok) throw new Error('Imagem indisponível');
        const blob = await resposta.blob();
        const tipo = blob.type || 'image/jpeg';
        const extensao = tipo.includes('png') ? 'png' : (tipo.includes('webp') ? 'webp' : 'jpg');
        const arquivo = new File([blob], 'produto-shopee.' + extensao, {type: tipo});
        const dados = {files: [arquivo], text: mensagemWhatsApp, title: 'Achadinho da Shopee'};
        if (navigator.share && navigator.canShare && navigator.canShare(dados)) {
            await navigator.share(dados);
            atualizarStatus('Compartilhamento aberto. Escolha WhatsApp Business e o grupo.');
            return;
        }
    } catch (erro) {
        if (erro.name === 'AbortError') return;
        console.log('Compartilhamento com foto indisponível:', erro);
    }
    window.location.href = linkWhatsApp;
}
</script>
{% endif %}

{% if mensagem %}<h3 class="sucesso">{{ mensagem }}</h3>{% endif %}
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
        <form method="post" style="padding:30px">
        <h2>Painel Achados Shopee</h2>
        <input type="password" name="senha"
        placeholder="Senha de acesso" required>
        <button>Entrar</button>
        </form>
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
        return render_template_string(
            HTML, erro="Link Shopee inválido. Confira o endereço."
        )

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
        host_permitido = (
            host == "susercontent.com"
            or host.endswith(".susercontent.com")
            or host == "shopee.com.br"
            or host.endswith(".shopee.com.br")
        )
        if not host_permitido:
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

        content_type = resposta.headers.get(
            "Content-Type", "image/jpeg"
        ).split(";")[0]
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
        return render_template_string(
            HTML, erro="Consulte novamente o produto."
        )

    try:
        dados = bot.consultar_shopee(produto["link"])
        if not isinstance(dados, dict):
            dados = {}

        nome = dados.get("nome") or produto["nome"]
        imagem = dados.get("imagem") or produto.get("imagem")
        preco = dados.get("preco")
        resolvido = dados.get("link_resolvido") or produto.get("link_resolvido")

        ok, posicao = bot.adicionar_na_fila(
            nome, produto["link"], imagem, preco, resolvido
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
