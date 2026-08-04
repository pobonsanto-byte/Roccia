import os
import json
import base64
import re
import requests
import time
import secrets
import hashlib
import hmac
from io import BytesIO
from threading import Thread
from datetime import datetime, timezone, timedelta
from functools import wraps
import asyncio
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import discord
from discord import app_commands
from discord.ext import commands
from discord import ui, Interaction, ButtonStyle
from PIL import Image, ImageDraw, ImageFont

# ========================
# CONFIGURAÇÃO DO AMBIENTE
# ========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USER = os.getenv("GITHUB_USER", "pobonsanto-byte")
GITHUB_REPO = os.getenv("GITHUB_REPO", "imune-bot-data")
DATA_FILE = os.getenv("DATA_FILE", "data.json")
BRANCH = os.getenv("GITHUB_BRANCH", "main")
PORT = int(os.getenv("PORT", 8080))
GUILD_ID = os.getenv("GUILD_ID")

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://seu-site.onrender.com/callback")
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))

if not BOT_TOKEN or not GITHUB_TOKEN:
    raise SystemExit("Defina BOT_TOKEN e GITHUB_TOKEN nas variáveis de ambiente.")

GITHUB_API_CONTENT = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{DATA_FILE}"

# ========================
# Sistema de ações
# ========================
acoes_fila_bot = []
processador_acoes_task = None
processador_acoes_rodando = False

# ========================
# FLASK APP
# ========================
app = Flask(__name__)
app.secret_key = SECRET_KEY

# ========================
# BOT SETUP
# ========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

# ========================
# ESTRUTURA DE DADOS
# ========================
dados = {
    "xp": {},
    "nivel": {},
    "advertencias": {},
    "reacoes_cargos": {},
    "config": {
        "canal_boas_vindas": None,
        "mensagem_boas_vindas": "Olá {member}, seja bem-vindo(a)!",
        "fundo_boas_vindas": "",
        "taxa_xp": 3,
        "canal_levelup": None,
        "canal_logs": None,
        "canal_perfil": None,
        "canal_rank": None
    },
    "logs": [],
    "fila": {
        "nome": "Fila de Serviços",
        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
        "entradas": [],
        "historico": []
    },
    "cargos_nivel": {},
    "canais_links_bloqueados": [],
    "botoes_cargos": {},
    "links_fila": {
        "discord_convite": "",
        "botoes_precos": []
    },
    "anti_spam": {
        "ativado": True,
        "limite_mensagens": 5,
        "intervalo_segundos": 5,
        "tempo_mute_minutos": 2,
        "remover_xp": True,
        "xp_penalidade": 50,
        "deletar_mensagens": True,
        "cargos_ignorados": ["Administrador", "Moderador", "Staff", "Dono"],
        "comandos_ignorados": [
            "$w", "$wa", "$wg", "$h", "$ha", "$hg",
            "$W", "$WA", "$WG", "$H", "$HA", "$HG",
            "$tu", "$TU", "$dk", "$mmi", "$vote", "$rolls", "$k", "$mu",
            "$daily", "$Daily", "$rep", "$Rep", "$rep+", "$Rep+",
            "$bitesthedust", "$kb", "$Kb", "$l", "$L", "$ldk", "$Ldk",
        ]
    },
    # NOVAS ESTRUTURAS DA LOJA
    "usuarios": {},
    "servicos": [],
    "categorias": ["League of Legends", "Valorant", "CS2", "Fortnite", "Coaching", "Boost", "Outros"],
    "pedidos": [],
    "pagamentos": [],
    "cupons": [],
    "recompensas": [
        {"id": 1, "nome": "Cupom 5%", "descricao": "Ganhe 5% de desconto na próxima compra", "custo_pontos": 100, "tipo": "desconto", "valor": 5},
        {"id": 2, "nome": "Cupom 10%", "descricao": "Ganhe 10% de desconto na próxima compra", "custo_pontos": 250, "tipo": "desconto", "valor": 10},
        {"id": 3, "nome": "Cupom 20%", "descricao": "Ganhe 20% de desconto na próxima compra", "custo_pontos": 500, "tipo": "desconto", "valor": 20},
        {"id": 4, "nome": "Serviço Grátis", "descricao": "Ganhe um serviço grátis!", "custo_pontos": 1000, "tipo": "servico_gratis", "valor": 0}
    ],
    "config_loja": {
        "nome_loja": "Minha Loja",
        "logo": "",
        "banner": "",
        "pontos_por_real": 10,
        "gateway_pix": "mercadopago",
        "mercadopago_token": "",
        "mercadopago_webhook": "",
        "cores": {"primaria": "#5865F2", "secundaria": "#4752C4", "fundo": "#121212"},
        "redes_sociais": {"discord": "", "twitter": "", "instagram": "", "youtube": ""}
    },
    "financeiro": {
        "total_vendido": 0,
        "total_pedidos": 0,
        "clientes_ativos": 0
    }
}

mensagens_recentes = {}

# ========================
# FUNÇÕES UTILITÁRIAS
# ========================
def agora_br():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3)))

def _gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def carregar_dados_github():
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        if r.status_code == 200:
            js = r.json()
            conteudo_b64 = js.get("content", "")
            if conteudo_b64:
                raw = base64.b64decode(conteudo_b64)
                carregado = json.loads(raw.decode("utf-8"))
                dados.update(carregado)
                
                # Inicializar estruturas da loja se não existirem
                estruturas_loja = ["usuarios", "servicos", "categorias", "pedidos", "pagamentos", 
                                  "cupons", "recompensas", "config_loja", "financeiro"]
                for estrutura in estruturas_loja:
                    if estrutura not in dados:
                        if estrutura == "categorias":
                            dados["categorias"] = ["League of Legends", "Valorant", "CS2", "Fortnite", "Coaching", "Boost", "Outros"]
                        elif estrutura == "recompensas":
                            dados["recompensas"] = [
                                {"id": 1, "nome": "Cupom 5%", "descricao": "Ganhe 5% de desconto na próxima compra", "custo_pontos": 100, "tipo": "desconto", "valor": 5},
                                {"id": 2, "nome": "Cupom 10%", "descricao": "Ganhe 10% de desconto na próxima compra", "custo_pontos": 250, "tipo": "desconto", "valor": 10},
                                {"id": 3, "nome": "Cupom 20%", "descricao": "Ganhe 20% de desconto na próxima compra", "custo_pontos": 500, "tipo": "desconto", "valor": 20},
                                {"id": 4, "nome": "Serviço Grátis", "descricao": "Ganhe um serviço grátis!", "custo_pontos": 1000, "tipo": "servico_gratis", "valor": 0}
                            ]
                        elif estrutura == "config_loja":
                            dados["config_loja"] = {
                                "nome_loja": "Minha Loja",
                                "logo": "",
                                "banner": "",
                                "pontos_por_real": 10,
                                "gateway_pix": "mercadopago",
                                "mercadopago_token": "",
                                "mercadopago_webhook": "",
                                "cores": {"primaria": "#5865F2", "secundaria": "#4752C4", "fundo": "#121212"},
                                "redes_sociais": {"discord": "", "twitter": "", "instagram": "", "youtube": ""}
                            }
                        elif estrutura == "financeiro":
                            dados["financeiro"] = {"total_vendido": 0, "total_pedidos": 0, "clientes_ativos": 0}
                        else:
                            dados[estrutura] = {} if estrutura == "usuarios" else []
                
                print("✅ Dados carregados do GitHub.")
                return True
        else:
            print(f"⚠️ GitHub GET retornou {r.status_code} — iniciando com dados limpos.")
    except Exception as e:
        print(f"❌ Erro ao carregar dados do GitHub: {e}")
    return False

def salvar_dados_github(mensagem="Atualização do bot"):
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        sha = None
        if r.status_code == 200:
            sha = r.json().get("sha")

        conteudo = json.dumps(dados, ensure_ascii=False, indent=2).encode("utf-8")
        payload = {
            "message": f"{mensagem} @ {agora_br().isoformat()}",
            "content": base64.b64encode(conteudo).decode("utf-8"),
            "branch": BRANCH
        }
        if sha:
            payload["sha"] = sha

        put = requests.put(GITHUB_API_CONTENT, headers=_gh_headers(), json=payload, timeout=30)
        if put.status_code in (200, 201):
            print("✅ Dados salvos no GitHub.")
            return True
        else:
            print(f"❌ Erro ao salvar no GitHub: {put.status_code}, {put.text[:400]}")
    except Exception as e:
        print(f"❌ Exception saving to GitHub: {e}")
    return False

def adicionar_log(entrada):
    ts = agora_br().isoformat()
    dados.setdefault("logs", []).append({"ts": ts, "entrada": entrada})
    try:
        salvar_dados_github(f"log: {entrada}")
    except Exception:
        pass

def xp_por_mensagem():
    return 15

def xp_para_nivel(xp):
    nivel = int((xp / 100) ** 0.6) + 1
    return max(nivel, 1)

def escape_html(texto):
    if not texto:
        return ""
    return (texto
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )

def gerar_codigo_cupom():
    import random
    import string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def calcular_pontos(valor):
    """Calcula pontos baseado no valor pago"""
    return int(valor * dados.get("config_loja", {}).get("pontos_por_real", 10))

# ========================
# FUNÇÕES DA LOJA
# ========================

def criar_usuario(discord_id, nome, avatar):
    """Cria um novo usuário no sistema"""
    if str(discord_id) in dados["usuarios"]:
        return dados["usuarios"][str(discord_id)]
    
    usuario = {
        "id": str(discord_id),
        "nome": nome,
        "avatar": avatar,
        "data_cadastro": agora_br().isoformat(),
        "pontos": 0,
        "total_gasto": 0,
        "historico": [],
        "pedidos": [],
        "cupons": [],
        "descontos": []
    }
    dados["usuarios"][str(discord_id)] = usuario
    salvar_dados_github(f"Novo usuário: {nome}")
    return usuario

def obter_usuario(discord_id):
    """Obtém dados de um usuário"""
    uid = str(discord_id)
    if uid not in dados["usuarios"]:
        return None
    return dados["usuarios"][uid]

def adicionar_pontos(discord_id, pontos, motivo=""):
    """Adiciona pontos a um usuário"""
    uid = str(discord_id)
    if uid not in dados["usuarios"]:
        return False
    
    dados["usuarios"][uid]["pontos"] += pontos
    dados["usuarios"][uid]["historico"].append({
        "tipo": "pontos_adicionados",
        "pontos": pontos,
        "motivo": motivo,
        "data": agora_br().isoformat()
    })
    salvar_dados_github(f"Pontos adicionados: +{pontos} para {dados['usuarios'][uid]['nome']}")
    return True

def remover_pontos(discord_id, pontos, motivo=""):
    """Remove pontos de um usuário"""
    uid = str(discord_id)
    if uid not in dados["usuarios"]:
        return False
    
    if dados["usuarios"][uid]["pontos"] < pontos:
        return False
    
    dados["usuarios"][uid]["pontos"] -= pontos
    dados["usuarios"][uid]["historico"].append({
        "tipo": "pontos_removidos",
        "pontos": pontos,
        "motivo": motivo,
        "data": agora_br().isoformat()
    })
    salvar_dados_github(f"Pontos removidos: -{pontos} para {dados['usuarios'][uid]['nome']}")
    return True

def criar_servico(nome, categoria, preco, descricao="", imagem="", tempo_estimado="", destaque=False, ordem=0):
    """Cria um novo serviço"""
    servico = {
        "id": len(dados["servicos"]) + 1,
        "nome": nome,
        "categoria": categoria,
        "preco": float(preco),
        "descricao": descricao,
        "imagem": imagem,
        "tempo_estimado": tempo_estimado,
        "status": "ativo",
        "destaque": destaque,
        "ordem": ordem,
        "data_criacao": agora_br().isoformat()
    }
    dados["servicos"].append(servico)
    salvar_dados_github(f"Serviço criado: {nome}")
    return servico

def atualizar_servico(servico_id, **kwargs):
    """Atualiza um serviço existente"""
    for i, servico in enumerate(dados["servicos"]):
        if servico["id"] == servico_id:
            for chave, valor in kwargs.items():
                if chave == "preco":
                    servico[chave] = float(valor)
                elif chave == "destaque":
                    servico[chave] = bool(valor)
                else:
                    servico[chave] = valor
            salvar_dados_github(f"Serviço atualizado: {servico['nome']}")
            return True
    return False

def remover_servico(servico_id):
    """Remove um serviço"""
    for i, servico in enumerate(dados["servicos"]):
        if servico["id"] == servico_id:
            dados["servicos"].pop(i)
            salvar_dados_github(f"Serviço removido: {servico['nome']}")
            return True
    return False

def criar_pedido(cliente_id, servico_id, discord_username):
    """Cria um novo pedido"""
    servico = None
    for s in dados["servicos"]:
        if s["id"] == servico_id:
            servico = s
            break
    
    if not servico:
        return None, "Serviço não encontrado"
    
    numero_pedido = f"#{len(dados['pedidos']) + 1:06d}"
    
    pedido = {
        "numero": numero_pedido,
        "cliente_id": str(cliente_id),
        "cliente_nome": discord_username,
        "servico_id": servico_id,
        "servico_nome": servico["nome"],
        "valor": servico["preco"],
        "status": "Aguardando pagamento",
        "data_criacao": agora_br().isoformat(),
        "data_pagamento": None,
        "data_conclusao": None,
        "pix_codigo": None,
        "pix_qr": None,
        "historico": [{"status": "Aguardando pagamento", "data": agora_br().isoformat()}],
        "cupom_aplicado": None,
        "desconto_aplicado": 0,
        "valor_final": servico["preco"]
    }
    
    dados["pedidos"].append(pedido)
    dados["financeiro"]["total_pedidos"] += 1
    
    # Adicionar ao histórico do usuário
    uid = str(cliente_id)
    if uid in dados["usuarios"]:
        dados["usuarios"][uid]["pedidos"].append(numero_pedido)
    
    salvar_dados_github(f"Pedido criado: {numero_pedido} - {servico['nome']}")
    return pedido, None

def atualizar_status_pedido(pedido_numero, novo_status):
    """Atualiza o status de um pedido"""
    for pedido in dados["pedidos"]:
        if pedido["numero"] == pedido_numero:
            pedido["status"] = novo_status
            pedido["historico"].append({"status": novo_status, "data": agora_br().isoformat()})
            
            if novo_status == "Pago":
                pedido["data_pagamento"] = agora_br().isoformat()
                # Adicionar pontos
                pontos = calcular_pontos(pedido["valor_final"])
                uid = pedido["cliente_id"]
                if uid in dados["usuarios"]:
                    dados["usuarios"][uid]["pontos"] += pontos
                    dados["usuarios"][uid]["total_gasto"] += pedido["valor_final"]
                    dados["usuarios"][uid]["historico"].append({
                        "tipo": "compra",
                        "pedido": pedido_numero,
                        "valor": pedido["valor_final"],
                        "pontos_ganhos": pontos,
                        "data": agora_br().isoformat()
                    })
                    dados["financeiro"]["total_vendido"] += pedido["valor_final"]
                    
                    # Adicionar à fila automaticamente
                    adicionar_fila(
                        nome_usuario=pedido["cliente_nome"],
                        servico=pedido["servico_nome"],
                        jogo="",
                        usuario_id=pedido["cliente_id"]
                    )
                    
                    # Notificar no Discord
                    enviar_notificacao_discord(
                        f"✅ **PEDIDO PAGO!**\n"
                        f"Cliente: {pedido['cliente_nome']}\n"
                        f"Serviço: {pedido['servico_nome']}\n"
                        f"Valor: R$ {pedido['valor_final']:.2f}\n"
                        f"Pontos ganhos: {pontos}\n"
                        f"Pedido: {pedido_numero}"
                    )
            
            elif novo_status == "Concluído":
                pedido["data_conclusao"] = agora_br().isoformat()
                enviar_notificacao_discord(
                    f"✅ **PEDIDO CONCLUÍDO!**\n"
                    f"Cliente: {pedido['cliente_nome']}\n"
                    f"Serviço: {pedido['servico_nome']}\n"
                    f"Pedido: {pedido_numero}"
                )
            
            salvar_dados_github(f"Status do pedido {pedido_numero} atualizado: {novo_status}")
            return True
    return False

def criar_cupom(tipo, valor, quantidade=1, validade_dias=30, usuario_id=None):
    """Cria um novo cupom"""
    codigo = gerar_codigo_cupom()
    cupom = {
        "codigo": codigo,
        "tipo": tipo,  # "desconto" ou "servico_gratis"
        "valor": valor,
        "quantidade_maxima": quantidade,
        "quantidade_usada": 0,
        "validade": (agora_br() + timedelta(days=validade_dias)).isoformat(),
        "status": "ativo",
        "usuario_id": str(usuario_id) if usuario_id else None,
        "data_criacao": agora_br().isoformat()
    }
    dados["cupons"].append(cupom)
    
    if usuario_id:
        uid = str(usuario_id)
        if uid in dados["usuarios"]:
            dados["usuarios"][uid]["cupons"].append(codigo)
            enviar_notificacao_discord(
                f"🎫 **CUPOM CRIADO!**\n"
                f"Usuário: {dados['usuarios'][uid]['nome']}\n"
                f"Código: `{codigo}`\n"
                f"Tipo: {tipo}\n"
                f"Valor: {valor}%\n"
                f"Validade: {cupom['validade']}"
            )
    
    salvar_dados_github(f"Cupom criado: {codigo}")
    return cupom

def aplicar_cupom(codigo, pedido_numero):
    """Aplica um cupom a um pedido"""
    cupom = None
    for c in dados["cupons"]:
        if c["codigo"] == codigo and c["status"] == "ativo":
            cupom = c
            break
    
    if not cupom:
        return False, "Cupom inválido ou expirado"
    
    # Verificar validade
    if datetime.fromisoformat(cupom["validade"]) < agora_br():
        cupom["status"] = "expirado"
        salvar_dados_github(f"Cupom expirado: {codigo}")
        return False, "Cupom expirado"
    
    # Verificar quantidade
    if cupom["quantidade_usada"] >= cupom["quantidade_maxima"]:
        return False, "Cupom esgotado"
    
    # Aplicar ao pedido
    for pedido in dados["pedidos"]:
        if pedido["numero"] == pedido_numero:
            if cupom["tipo"] == "desconto":
                desconto = pedido["valor"] * (cupom["valor"] / 100)
                pedido["desconto_aplicado"] = desconto
                pedido["valor_final"] = pedido["valor"] - desconto
            elif cupom["tipo"] == "servico_gratis":
                pedido["desconto_aplicado"] = pedido["valor"]
                pedido["valor_final"] = 0
            
            pedido["cupom_aplicado"] = codigo
            cupom["quantidade_usada"] += 1
            
            if cupom["quantidade_usada"] >= cupom["quantidade_maxima"]:
                cupom["status"] = "esgotado"
            
            salvar_dados_github(f"Cupom aplicado: {codigo} ao pedido {pedido_numero}")
            return True, f"Cupom aplicado! Desconto de R$ {pedido['desconto_aplicado']:.2f}"
    
    return False, "Pedido não encontrado"

def resgatar_recompensa(usuario_id, recompensa_id):
    """Resgata uma recompensa usando pontos"""
    recompensa = None
    for r in dados["recompensas"]:
        if r["id"] == recompensa_id:
            recompensa = r
            break
    
    if not recompensa:
        return False, "Recompensa não encontrada"
    
    uid = str(usuario_id)
    if uid not in dados["usuarios"]:
        return False, "Usuário não encontrado"
    
    if dados["usuarios"][uid]["pontos"] < recompensa["custo_pontos"]:
        return False, "Pontos insuficientes"
    
    # Remover pontos
    dados["usuarios"][uid]["pontos"] -= recompensa["custo_pontos"]
    
    # Criar cupom
    if recompensa["tipo"] == "desconto":
        cupom = criar_cupom(
            tipo="desconto",
            valor=recompensa["valor"],
            quantidade=1,
            validade_dias=30,
            usuario_id=usuario_id
        )
    elif recompensa["tipo"] == "servico_gratis":
        cupom = criar_cupom(
            tipo="servico_gratis",
            valor=0,
            quantidade=1,
            validade_dias=30,
            usuario_id=usuario_id
        )
    
    # Registrar histórico
    dados["usuarios"][uid]["historico"].append({
        "tipo": "resgate_recompensa",
        "recompensa": recompensa["nome"],
        "custo_pontos": recompensa["custo_pontos"],
        "cupom": cupom["codigo"],
        "data": agora_br().isoformat()
    })
    
    salvar_dados_github(f"Recompensa resgatada: {recompensa['nome']} por {dados['usuarios'][uid]['nome']}")
    return True, f"Recompensa resgatada! Cupom: {cupom['codigo']}"

def enviar_notificacao_discord(mensagem):
    """Envia uma notificação para o Discord"""
    if not bot.is_ready():
        return
    
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID else None
    if not guild:
        return
    
    # Tentar enviar para o canal de logs
    config = dados.get("config", {})
    canal_logs_id = config.get("canal_logs")
    if canal_logs_id:
        canal = guild.get_channel(int(canal_logs_id))
        if canal:
            asyncio.create_task(canal.send(mensagem))

def gerar_pix_mercadopago(valor, descricao, pedido_numero):
    """Gera um QR Code PIX usando Mercado Pago"""
    config = dados.get("config_loja", {})
    token = config.get("mercadopago_token")
    
    if not token:
        return None, "Token do Mercado Pago não configurado"
    
    try:
        url = "https://api.mercadopago.com/v1/payments"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # Gerar ID externo único
        external_id = f"pedido_{pedido_numero}_{int(time.time())}"
        
        payload = {
            "transaction_amount": float(valor),
            "description": descricao,
            "payment_method_id": "pix",
            "payer": {
                "email": "cliente@exemplo.com"
            },
            "external_reference": external_id,
            "notification_url": config.get("mercadopago_webhook", "")
        }
        
        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code == 201:
            data = response.json()
            return {
                "qr_code": data["point_of_interaction"]["transaction_data"]["qr_code"],
                "qr_code_base64": data["point_of_interaction"]["transaction_data"]["qr_code_base64"],
                "id": data["id"]
            }, None
        else:
            return None, f"Erro ao gerar PIX: {response.text}"
    except Exception as e:
        return None, str(e)

# ========================
# WEBHOOK DO MERCADO PAGO
# ========================

@app.route("/webhook/mercadopago", methods=["POST"])
def webhook_mercadopago():
    """Webhook para receber confirmações de pagamento do Mercado Pago"""
    try:
        data = request.json
        
        # Verificar autenticidade (opcional)
        # Implementar verificação de assinatura aqui
        
        if data.get("type") == "payment":
            payment_id = data["data"]["id"]
            
            # Buscar o pagamento
            config = dados.get("config_loja", {})
            token = config.get("mercadopago_token")
            
            url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
            headers = {"Authorization": f"Bearer {token}"}
            
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                payment = response.json()
                
                if payment["status"] == "approved":
                    external_ref = payment.get("external_reference", "")
                    # Extrair número do pedido do external_reference
                    if "pedido_" in external_ref:
                        pedido_numero = external_ref.split("_")[1]
                        # Atualizar status do pedido
                        atualizar_status_pedido(f"#{pedido_numero}", "Pago")
                        
                        # Registrar pagamento
                        dados["pagamentos"].append({
                            "id": payment_id,
                            "pedido": f"#{pedido_numero}",
                            "valor": payment["transaction_amount"],
                            "status": "aprovado",
                            "data": agora_br().isoformat(),
                            "dados": payment
                        })
                        salvar_dados_github(f"Pagamento aprovado: {payment_id}")
        
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print(f"❌ Erro no webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

# ========================
# ROTAS PÚBLICAS DA LOJA
# ========================

@app.route("/")
def home():
    """Página inicial da loja"""
    status_bot = "✅ Bot Online" if bot.is_ready() else "❌ Bot Offline"
    classe_bot = "online" if bot.is_ready() else "offline"
    usuario = session.get('usuario')
    servicos_destaque = [s for s in dados.get("servicos", []) if s.get("destaque", False)][:6]
    config_loja = dados.get("config_loja", {})
    categorias = dados.get("categorias", [])
    
    # Contar serviços por categoria
    servicos_por_categoria = {}
    for cat in categorias:
        servicos_por_categoria[cat] = len([s for s in dados.get("servicos", []) if s.get("categoria") == cat])
    
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{{ config_loja.nome_loja }}</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0a0a0a; color: #e0e0e0; }
            .navbar { background: #1a1a1a; padding: 1rem 2rem; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; }
            .navbar .logo { font-size: 1.5rem; font-weight: bold; color: #5865F2; text-decoration: none; }
            .navbar .nav-links a { color: #ccc; text-decoration: none; margin-left: 2rem; transition: 0.3s; }
            .navbar .nav-links a:hover { color: #5865F2; }
            .btn { display: inline-block; padding: 0.75rem 1.5rem; border-radius: 8px; text-decoration: none; font-weight: 600; transition: 0.3s; border: none; cursor: pointer; }
            .btn-primary { background: #5865F2; color: white; }
            .btn-primary:hover { background: #4752C4; transform: translateY(-2px); }
            .btn-success { background: #10b981; color: white; }
            .btn-success:hover { background: #059669; transform: translateY(-2px); }
            .btn-outline { background: transparent; color: white; border: 2px solid #5865F2; }
            .btn-outline:hover { background: #5865F2; color: white; transform: translateY(-2px); }
            .btn-danger { background: #ef4444; color: white; }
            .btn-danger:hover { background: #dc2626; transform: translateY(-2px); }
            .container { max-width: 1200px; margin: 0 auto; padding: 0 1rem; }
            .hero { padding: 4rem 0; text-align: center; background: linear-gradient(135deg, #1a1a2e, #16213e); border-radius: 20px; margin: 2rem 0; }
            .hero h1 { font-size: 3rem; margin-bottom: 1rem; background: linear-gradient(135deg, #5865F2, #8b5cf6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
            .hero p { font-size: 1.2rem; color: #aaa; max-width: 600px; margin: 0 auto 2rem; }
            .hero .btn-group { display: flex; gap: 1rem; justify-content: center; flex-wrap: wrap; }
            .servicos-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 2rem; margin: 2rem 0; }
            .servico-card { background: #1a1a1a; border-radius: 12px; overflow: hidden; border: 1px solid #333; transition: 0.3s; }
            .servico-card:hover { transform: translateY(-5px); border-color: #5865F2; }
            .servico-card .imagem { width: 100%; height: 200px; background: #2a2a2a; display: flex; align-items: center; justify-content: center; font-size: 3rem; }
            .servico-card .info { padding: 1.5rem; }
            .servico-card .info h3 { margin-bottom: 0.5rem; color: #fff; }
            .servico-card .info .preco { font-size: 1.5rem; color: #10b981; font-weight: bold; }
            .servico-card .info .categoria { display: inline-block; background: #333; padding: 0.25rem 0.75rem; border-radius: 20px; font-size: 0.8rem; margin: 0.5rem 0; }
            .servico-card .info .descricao { color: #aaa; margin: 0.5rem 0; }
            .servico-card .info .btn { width: 100%; text-align: center; margin-top: 0.5rem; }
            .beneficios { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 2rem; margin: 3rem 0; text-align: center; }
            .beneficio { padding: 2rem; background: #1a1a1a; border-radius: 12px; border: 1px solid #333; }
            .beneficio .icon { font-size: 3rem; margin-bottom: 1rem; }
            .beneficio h4 { color: #fff; margin-bottom: 0.5rem; }
            .beneficio p { color: #888; }
            .footer { background: #1a1a1a; padding: 3rem 0; margin-top: 3rem; border-top: 1px solid #333; text-align: center; }
            .footer .social { display: flex; gap: 1rem; justify-content: center; margin-bottom: 1rem; }
            .footer .social a { color: #888; font-size: 1.5rem; text-decoration: none; transition: 0.3s; }
            .footer .social a:hover { color: #5865F2; }
            .footer p { color: #666; }
            .badge-destaque { position: absolute; top: 10px; right: 10px; background: #f59e0b; color: #000; padding: 0.25rem 0.75rem; border-radius: 20px; font-size: 0.8rem; font-weight: bold; }
            .categorias { display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 1rem 0; }
            .categoria-tag { background: #2a2a2a; padding: 0.5rem 1rem; border-radius: 20px; color: #ccc; font-size: 0.9rem; border: 1px solid #333; }
            .categoria-tag .count { color: #5865F2; font-weight: bold; }
            .user-info { display: flex; align-items: center; gap: 1rem; }
            .user-info .avatar { width: 35px; height: 35px; border-radius: 50%; border: 2px solid #5865F2; }
            .user-info .nome { color: #fff; }
            .dropdown { position: relative; display: inline-block; }
            .dropdown-content { display: none; position: absolute; right: 0; background: #1a1a1a; min-width: 200px; box-shadow: 0 8px 16px rgba(0,0,0,0.5); border-radius: 8px; border: 1px solid #333; z-index: 1; }
            .dropdown:hover .dropdown-content { display: block; }
            .dropdown-content a { color: #ccc; padding: 12px 16px; text-decoration: none; display: block; }
            .dropdown-content a:hover { background: #2a2a2a; color: #5865F2; }
            @media (max-width: 768px) {
                .hero h1 { font-size: 2rem; }
                .navbar .nav-links a { margin-left: 1rem; font-size: 0.9rem; }
                .servicos-grid { grid-template-columns: 1fr; }
            }
        </style>
    </head>
    <body>
        <nav class="navbar">
            <a href="/" class="logo">{{ config_loja.nome_loja }}</a>
            <div class="nav-links">
                <a href="/">Início</a>
                <a href="/servicos">Serviços</a>
                <a href="/fila">Fila</a>
                {% if 'usuario' in session %}
                <a href="/cliente">Minha Conta</a>
                {% endif %}
                {% if 'usuario' in session and usuario.get('eh_admin') %}
                <a href="/dashboard" style="color: #f59e0b;">Painel Admin</a>
                {% endif %}
                <div class="dropdown" style="display:inline-block;">
                    {% if 'usuario' in session %}
                    <div class="user-info">
                        <img src="https://cdn.discordapp.com/avatars/{{ usuario.id }}/{{ usuario.get('avatar', '') }}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                        <span class="nome">{{ usuario.nome_usuario }}</span>
                        <span style="color:#5865F2;">▼</span>
                    </div>
                    <div class="dropdown-content">
                        <a href="/cliente">👤 Minha Conta</a>
                        <a href="/pedidos">📦 Meus Pedidos</a>
                        <a href="/logout">🚪 Sair</a>
                    </div>
                    {% else %}
                    <a href="/login" class="btn btn-primary" style="padding:0.5rem 1rem;">Entrar</a>
                    {% endif %}
                </div>
            </div>
        </nav>

        <div class="container">
            <div class="hero">
                <h1>Bem-vindo à {{ config_loja.nome_loja }}</h1>
                <p>Os melhores serviços para você, com pagamento seguro via PIX e sistema de pontos exclusivo!</p>
                <div class="btn-group">
                    <a href="/servicos" class="btn btn-primary">🛒 Ver Serviços</a>
                    <a href="/fila" class="btn btn-outline">📋 Ver Fila</a>
                    {% if 'usuario' not in session %}
                    <a href="/login" class="btn btn-success">🔐 Entrar com Discord</a>
                    {% endif %}
                </div>
            </div>

            <div style="margin: 2rem 0;">
                <h2 style="color:#fff; margin-bottom: 1rem;">📂 Categorias</h2>
                <div class="categorias">
                    {% for cat in categorias %}
                    <span class="categoria-tag">{{ cat }} <span class="count">({{ servicos_por_categoria.get(cat, 0) }})</span></span>
                    {% endfor %}
                </div>
            </div>

            <h2 style="color:#fff; margin: 2rem 0 1rem;">⭐ Serviços em Destaque</h2>
            <div class="servicos-grid">
                {% for servico in servicos_destaque %}
                <div class="servico-card" style="position:relative;">
                    <div class="imagem">🎮</div>
                    <div class="info">
                        <h3>{{ servico.nome }}</h3>
                        <span class="categoria">{{ servico.categoria }}</span>
                        <p class="descricao">{{ servico.descricao[:100] }}{% if servico.descricao|length > 100 %}...{% endif %}</p>
                        <div class="preco">R$ {{ "%.2f"|format(servico.preco) }}</div>
                        <a href="/servico/{{ servico.id }}" class="btn btn-primary">Comprar</a>
                    </div>
                    {% if servico.destaque %}
                    <span class="badge-destaque">⭐ Destaque</span>
                    {% endif %}
                </div>
                {% else %}
                <p style="color:#666; grid-column: 1/-1; text-align:center;">Nenhum serviço em destaque no momento.</p>
                {% endfor %}
            </div>

            <div class="beneficios">
                <div class="beneficio">
                    <div class="icon">💳</div>
                    <h4>Pagamento PIX</h4>
                    <p>Pagamento seguro e rápido via PIX</p>
                </div>
                <div class="beneficio">
                    <div class="icon">⭐</div>
                    <h4>Sistema de Pontos</h4>
                    <p>Ganhe pontos a cada compra e troque por descontos</p>
                </div>
                <div class="beneficio">
                    <div class="icon">🛡️</div>
                    <h4>100% Confiável</h4>
                    <p>Mais de {{ dados.financeiro.total_pedidos }} pedidos concluídos</p>
                </div>
                <div class="beneficio">
                    <div class="icon">🤖</div>
                    <h4>Integração Discord</h4>
                    <p>Acompanhe tudo pelo nosso servidor</p>
                </div>
            </div>
        </div>

        <div class="footer">
            <div class="container">
                <div class="social">
                    {% if config_loja.redes_sociais.discord %}
                    <a href="{{ config_loja.redes_sociais.discord }}" target="_blank">💬 Discord</a>
                    {% endif %}
                    {% if config_loja.redes_sociais.twitter %}
                    <a href="{{ config_loja.redes_sociais.twitter }}" target="_blank">🐦 Twitter</a>
                    {% endif %}
                    {% if config_loja.redes_sociais.instagram %}
                    <a href="{{ config_loja.redes_sociais.instagram }}" target="_blank">📸 Instagram</a>
                    {% endif %}
                    {% if config_loja.redes_sociais.youtube %}
                    <a href="{{ config_loja.redes_sociais.youtube }}" target="_blank">▶️ YouTube</a>
                    {% endif %}
                </div>
                <p>© 2024 {{ config_loja.nome_loja }} - Todos os direitos reservados</p>
                <p style="font-size:0.8rem; color:#444;">{{ dados.financeiro.total_pedidos }} pedidos • R$ {{ "%.2f"|format(dados.financeiro.total_vendido) }} em vendas</p>
            </div>
        </div>
    </body>
    </html>
    """, usuario=session.get('usuario'), config_loja=config_loja, servicos_destaque=servicos_destaque, categorias=categorias, servicos_por_categoria=servicos_por_categoria, dados=dados)

@app.route("/servicos")
def lista_servicos():
    """Página com todos os serviços"""
    usuario = session.get('usuario')
    config_loja = dados.get("config_loja", {})
    servicos = dados.get("servicos", [])
    categorias = dados.get("categorias", [])
    
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Serviços - {{ config_loja.nome_loja }}</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0a0a0a; color: #e0e0e0; }
            .navbar { background: #1a1a1a; padding: 1rem 2rem; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; }
            .navbar .logo { font-size: 1.5rem; font-weight: bold; color: #5865F2; text-decoration: none; }
            .navbar .nav-links a { color: #ccc; text-decoration: none; margin-left: 2rem; transition: 0.3s; }
            .navbar .nav-links a:hover { color: #5865F2; }
            .btn { display: inline-block; padding: 0.75rem 1.5rem; border-radius: 8px; text-decoration: none; font-weight: 600; transition: 0.3s; border: none; cursor: pointer; }
            .btn-primary { background: #5865F2; color: white; }
            .btn-primary:hover { background: #4752C4; transform: translateY(-2px); }
            .btn-success { background: #10b981; color: white; }
            .btn-success:hover { background: #059669; transform: translateY(-2px); }
            .container { max-width: 1200px; margin: 0 auto; padding: 0 1rem; }
            .servicos-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 2rem; margin: 2rem 0; }
            .servico-card { background: #1a1a1a; border-radius: 12px; overflow: hidden; border: 1px solid #333; transition: 0.3s; }
            .servico-card:hover { transform: translateY(-5px); border-color: #5865F2; }
            .servico-card .imagem { width: 100%; height: 200px; background: #2a2a2a; display: flex; align-items: center; justify-content: center; font-size: 3rem; }
            .servico-card .info { padding: 1.5rem; }
            .servico-card .info h3 { margin-bottom: 0.5rem; color: #fff; }
            .servico-card .info .preco { font-size: 1.5rem; color: #10b981; font-weight: bold; }
            .servico-card .info .categoria { display: inline-block; background: #333; padding: 0.25rem 0.75rem; border-radius: 20px; font-size: 0.8rem; margin: 0.5rem 0; }
            .servico-card .info .descricao { color: #aaa; margin: 0.5rem 0; }
            .servico-card .info .btn { width: 100%; text-align: center; margin-top: 0.5rem; }
            .badge-destaque { position: absolute; top: 10px; right: 10px; background: #f59e0b; color: #000; padding: 0.25rem 0.75rem; border-radius: 20px; font-size: 0.8rem; font-weight: bold; }
            .filtros { display: flex; gap: 1rem; flex-wrap: wrap; margin: 2rem 0; background: #1a1a1a; padding: 1rem; border-radius: 12px; border: 1px solid #333; }
            .filtros select, .filtros input { padding: 0.5rem 1rem; background: #2a2a2a; border: 1px solid #333; border-radius: 8px; color: #e0e0e0; }
            .filtros select:focus, .filtros input:focus { outline: none; border-color: #5865F2; }
            .user-info { display: flex; align-items: center; gap: 1rem; }
            .user-info .avatar { width: 35px; height: 35px; border-radius: 50%; border: 2px solid #5865F2; }
            .dropdown { position: relative; display: inline-block; }
            .dropdown-content { display: none; position: absolute; right: 0; background: #1a1a1a; min-width: 200px; box-shadow: 0 8px 16px rgba(0,0,0,0.5); border-radius: 8px; border: 1px solid #333; z-index: 1; }
            .dropdown:hover .dropdown-content { display: block; }
            .dropdown-content a { color: #ccc; padding: 12px 16px; text-decoration: none; display: block; }
            .dropdown-content a:hover { background: #2a2a2a; color: #5865F2; }
            .busca { flex: 1; min-width: 200px; }
            .badge-status { display: inline-block; padding: 0.25rem 0.5rem; border-radius: 20px; font-size: 0.7rem; }
            .badge-ativo { background: #10b981; color: white; }
            .badge-inativo { background: #ef4444; color: white; }
            @media (max-width: 768px) {
                .servicos-grid { grid-template-columns: 1fr; }
                .navbar .nav-links a { margin-left: 1rem; font-size: 0.9rem; }
                .filtros { flex-direction: column; }
            }
        </style>
    </head>
    <body>
        <nav class="navbar">
            <a href="/" class="logo">{{ config_loja.nome_loja }}</a>
            <div class="nav-links">
                <a href="/">Início</a>
                <a href="/servicos">Serviços</a>
                <a href="/fila">Fila</a>
                {% if 'usuario' in session %}
                <a href="/cliente">Minha Conta</a>
                {% endif %}
                {% if 'usuario' in session and usuario.get('eh_admin') %}
                <a href="/dashboard" style="color: #f59e0b;">Painel Admin</a>
                {% endif %}
                <div class="dropdown" style="display:inline-block;">
                    {% if 'usuario' in session %}
                    <div class="user-info">
                        <img src="https://cdn.discordapp.com/avatars/{{ usuario.id }}/{{ usuario.get('avatar', '') }}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                        <span class="nome">{{ usuario.nome_usuario }}</span>
                        <span style="color:#5865F2;">▼</span>
                    </div>
                    <div class="dropdown-content">
                        <a href="/cliente">👤 Minha Conta</a>
                        <a href="/pedidos">📦 Meus Pedidos</a>
                        <a href="/logout">🚪 Sair</a>
                    </div>
                    {% else %}
                    <a href="/login" class="btn btn-primary" style="padding:0.5rem 1rem;">Entrar</a>
                    {% endif %}
                </div>
            </div>
        </nav>

        <div class="container">
            <h2 style="color:#fff; margin: 2rem 0 0.5rem;">🛒 Nossos Serviços</h2>
            
            <div class="filtros">
                <input type="text" id="busca" class="busca" placeholder="🔍 Buscar serviço..." oninput="filtrar()">
                <select id="filtro-categoria" onchange="filtrar()">
                    <option value="">Todas categorias</option>
                    {% for cat in categorias %}
                    <option value="{{ cat }}">{{ cat }}</option>
                    {% endfor %}
                </select>
                <select id="filtro-status" onchange="filtrar()">
                    <option value="">Todos</option>
                    <option value="ativo">Ativos</option>
                    <option value="inativo">Inativos</option>
                </select>
                <select id="filtro-ordem" onchange="filtrar()">
                    <option value="ordem">Ordem</option>
                    <option value="preco_asc">Preço (menor)</option>
                    <option value="preco_desc">Preço (maior)</option>
                    <option value="nome">Nome</option>
                </select>
            </div>

            <div class="servicos-grid" id="servicos-grid">
                {% for servico in servicos %}
                <div class="servico-card" style="position:relative;" data-nome="{{ servico.nome|lower }}" data-categoria="{{ servico.categoria }}" data-status="{{ servico.status }}" data-preco="{{ servico.preco }}">
                    <div class="imagem">{{ servico.imagem or '🎮' }}</div>
                    <div class="info">
                        <h3>{{ servico.nome }}</h3>
                        <span class="categoria">{{ servico.categoria }}</span>
                        <span class="badge-status badge-{{ servico.status }}">{{ servico.status|upper }}</span>
                        <p class="descricao">{{ servico.descricao[:100] }}{% if servico.descricao|length > 100 %}...{% endif %}</p>
                        <div class="preco">R$ {{ "%.2f"|format(servico.preco) }}</div>
                        {% if servico.tempo_estimado %}
                        <div style="color:#888; font-size:0.8rem;">⏱️ {{ servico.tempo_estimado }}</div>
                        {% endif %}
                        <a href="/servico/{{ servico.id }}" class="btn btn-primary">Comprar</a>
                    </div>
                    {% if servico.destaque %}
                    <span class="badge-destaque">⭐ Destaque</span>
                    {% endif %}
                </div>
                {% else %}
                <p style="color:#666; grid-column: 1/-1; text-align:center; padding:3rem 0;">Nenhum serviço disponível no momento.</p>
                {% endfor %}
            </div>
        </div>

        <script>
            function filtrar() {
                const busca = document.getElementById('busca').value.toLowerCase();
                const categoria = document.getElementById('filtro-categoria').value;
                const status = document.getElementById('filtro-status').value;
                const ordem = document.getElementById('filtro-ordem').value;
                
                const cards = document.querySelectorAll('.servico-card');
                let visiveis = [];
                
                cards.forEach(card => {
                    const nome = card.dataset.nome || '';
                    const cat = card.dataset.categoria || '';
                    const stat = card.dataset.status || '';
                    
                    let mostrar = true;
                    if (busca && !nome.includes(busca)) mostrar = false;
                    if (categoria && cat !== categoria) mostrar = false;
                    if (status && stat !== status) mostrar = false;
                    
                    card.style.display = mostrar ? 'block' : 'none';
                    if (mostrar) visiveis.push(card);
                });
                
                // Ordenar
                if (ordem && visiveis.length > 1) {
                    const grid = document.getElementById('servicos-grid');
                    visiveis.sort((a, b) => {
                        if (ordem === 'preco_asc') return parseFloat(a.dataset.preco) - parseFloat(b.dataset.preco);
                        if (ordem === 'preco_desc') return parseFloat(b.dataset.preco) - parseFloat(a.dataset.preco);
                        if (ordem === 'nome') return a.dataset.nome.localeCompare(b.dataset.nome);
                        return 0;
                    });
                    visiveis.forEach(card => grid.appendChild(card));
                }
            }
        </script>
    </body>
    </html>
    """, usuario=usuario, config_loja=config_loja, servicos=servicos, categorias=categorias)

@app.route("/servico/<int:servico_id>")
def detalhe_servico(servico_id):
    """Página de detalhes de um serviço"""
    usuario = session.get('usuario')
    config_loja = dados.get("config_loja", {})
    
    servico = None
    for s in dados.get("servicos", []):
        if s["id"] == servico_id:
            servico = s
            break
    
    if not servico:
        return "Serviço não encontrado", 404
    
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{{ servico.nome }} - {{ config_loja.nome_loja }}</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0a0a0a; color: #e0e0e0; }
            .navbar { background: #1a1a1a; padding: 1rem 2rem; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; }
            .navbar .logo { font-size: 1.5rem; font-weight: bold; color: #5865F2; text-decoration: none; }
            .navbar .nav-links a { color: #ccc; text-decoration: none; margin-left: 2rem; transition: 0.3s; }
            .navbar .nav-links a:hover { color: #5865F2; }
            .btn { display: inline-block; padding: 0.75rem 1.5rem; border-radius: 8px; text-decoration: none; font-weight: 600; transition: 0.3s; border: none; cursor: pointer; }
            .btn-primary { background: #5865F2; color: white; }
            .btn-primary:hover { background: #4752C4; transform: translateY(-2px); }
            .btn-success { background: #10b981; color: white; }
            .btn-success:hover { background: #059669; transform: translateY(-2px); }
            .btn-outline { background: transparent; color: white; border: 2px solid #5865F2; }
            .btn-outline:hover { background: #5865F2; color: white; }
            .container { max-width: 800px; margin: 2rem auto; padding: 0 1rem; }
            .card { background: #1a1a1a; border-radius: 12px; padding: 2rem; border: 1px solid #333; }
            .card .imagem { width: 100%; height: 300px; background: #2a2a2a; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 5rem; margin-bottom: 1.5rem; }
            .card h1 { color: #fff; margin-bottom: 0.5rem; }
            .card .categoria { display: inline-block; background: #333; padding: 0.25rem 1rem; border-radius: 20px; font-size: 0.9rem; }
            .card .preco { font-size: 2.5rem; color: #10b981; font-weight: bold; margin: 1rem 0; }
            .card .descricao { color: #aaa; line-height: 1.8; margin: 1rem 0; }
            .card .tempo { color: #888; margin: 0.5rem 0; }
            .card .status { display: inline-block; padding: 0.25rem 1rem; border-radius: 20px; font-size: 0.8rem; margin: 0.5rem 0; }
            .status-ativo { background: #10b981; color: white; }
            .status-inativo { background: #ef4444; color: white; }
            .cupom-area { background: #2a2a2a; padding: 1rem; border-radius: 8px; margin: 1rem 0; display: flex; gap: 1rem; flex-wrap: wrap; }
            .cupom-area input { flex: 1; padding: 0.75rem; background: #1a1a1a; border: 1px solid #333; border-radius: 8px; color: #e0e0e0; min-width: 150px; }
            .cupom-area input:focus { outline: none; border-color: #5865F2; }
            .user-info { display: flex; align-items: center; gap: 1rem; }
            .user-info .avatar { width: 35px; height: 35px; border-radius: 50%; border: 2px solid #5865F2; }
            .dropdown { position: relative; display: inline-block; }
            .dropdown-content { display: none; position: absolute; right: 0; background: #1a1a1a; min-width: 200px; box-shadow: 0 8px 16px rgba(0,0,0,0.5); border-radius: 8px; border: 1px solid #333; z-index: 1; }
            .dropdown:hover .dropdown-content { display: block; }
            .dropdown-content a { color: #ccc; padding: 12px 16px; text-decoration: none; display: block; }
            .dropdown-content a:hover { background: #2a2a2a; color: #5865F2; }
            .alert { padding: 1rem; border-radius: 8px; margin: 1rem 0; display: none; }
            .alert-success { background: #1a472a; color: #4ade80; border: 1px solid #2ecc71; }
            .alert-error { background: #7f1d1d; color: #f87171; border: 1px solid #ef4444; }
            .pix-area { text-align: center; margin: 2rem 0; }
            .pix-area img { max-width: 300px; border-radius: 8px; }
            .pix-area .codigo { background: #0a0a0a; padding: 1rem; border-radius: 8px; font-family: monospace; word-break: break-all; margin: 1rem 0; border: 1px solid #333; }
            @media (max-width: 768px) {
                .navbar .nav-links a { margin-left: 1rem; font-size: 0.9rem; }
                .cupom-area { flex-direction: column; }
            }
        </style>
    </head>
    <body>
        <nav class="navbar">
            <a href="/" class="logo">{{ config_loja.nome_loja }}</a>
            <div class="nav-links">
                <a href="/">Início</a>
                <a href="/servicos">Serviços</a>
                <a href="/fila">Fila</a>
                {% if 'usuario' in session %}
                <a href="/cliente">Minha Conta</a>
                {% endif %}
                {% if 'usuario' in session and usuario.get('eh_admin') %}
                <a href="/dashboard" style="color: #f59e0b;">Painel Admin</a>
                {% endif %}
                <div class="dropdown" style="display:inline-block;">
                    {% if 'usuario' in session %}
                    <div class="user-info">
                        <img src="https://cdn.discordapp.com/avatars/{{ usuario.id }}/{{ usuario.get('avatar', '') }}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                        <span class="nome">{{ usuario.nome_usuario }}</span>
                        <span style="color:#5865F2;">▼</span>
                    </div>
                    <div class="dropdown-content">
                        <a href="/cliente">👤 Minha Conta</a>
                        <a href="/pedidos">📦 Meus Pedidos</a>
                        <a href="/logout">🚪 Sair</a>
                    </div>
                    {% else %}
                    <a href="/login" class="btn btn-primary" style="padding:0.5rem 1rem;">Entrar</a>
                    {% endif %}
                </div>
            </div>
        </nav>

        <div class="container">
            <div class="card">
                <div class="imagem">{{ servico.imagem or '🎮' }}</div>
                <span class="categoria">{{ servico.categoria }}</span>
                <span class="status status-{{ servico.status }}">{{ servico.status|upper }}</span>
                <h1>{{ servico.nome }}</h1>
                <div class="preco">R$ {{ "%.2f"|format(servico.preco) }}</div>
                {% if servico.tempo_estimado %}
                <div class="tempo">⏱️ Tempo estimado: {{ servico.tempo_estimado }}</div>
                {% endif %}
                <div class="descricao">{{ servico.descricao }}</div>

                <div class="cupom-area">
                    <input type="text" id="cupom-input" placeholder="🎫 Digite seu cupom">
                    <button onclick="aplicarCupom({{ servico.id }})" class="btn btn-outline">Aplicar Cupom</button>
                </div>

                <div id="alert" class="alert"></div>

                <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                    <button onclick="comprar({{ servico.id }})" class="btn btn-success" style="flex:1; text-align:center;">
                        💳 Comprar com PIX
                    </button>
                    <a href="/servicos" class="btn btn-outline">← Voltar</a>
                </div>

                <div id="pix-area" class="pix-area" style="display:none;">
                    <h3 style="color:#fff;">📱 Pagamento via PIX</h3>
                    <img id="pix-qr" src="" alt="QR Code PIX">
                    <div class="codigo" id="pix-codigo"></div>
                    <button onclick="copiarPIX()" class="btn btn-primary">📋 Copiar Código</button>
                    <p style="color:#888; margin-top: 1rem;">⏳ Aguarde o pagamento ser confirmado automaticamente</p>
                </div>
            </div>
        </div>

        <script>
            let pedidoAtual = null;

            async function comprar(servicoId) {
                {% if 'usuario' not in session %}
                if (confirm('Você precisa estar logado para comprar. Deseja fazer login?')) {
                    window.location.href = '/login';
                }
                return;
                {% endif %}

                const alert = document.getElementById('alert');
                alert.style.display = 'none';

                try {
                    const response = await fetch('/api/pedido/criar', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({servico_id: servicoId})
                    });
                    const data = await response.json();

                    if (data.sucesso) {
                        pedidoAtual = data.pedido;
                        showAlert('✅ Pedido criado! Gerando PIX...', 'success');
                        
                        // Gerar PIX
                        const pixResponse = await fetch('/api/pedido/pix', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({pedido_numero: data.pedido.numero})
                        });
                        const pixData = await pixResponse.json();
                        
                        if (pixData.sucesso) {
                            document.getElementById('pix-area').style.display = 'block';
                            document.getElementById('pix-qr').src = pixData.qr_code_base64 || 'data:image/png;base64,' + pixData.qr_code;
                            document.getElementById('pix-codigo').textContent = pixData.qr_code || pixData.codigo;
                            showAlert('💳 PIX gerado! Faça o pagamento para confirmar.', 'success');
                        } else {
                            showAlert('❌ Erro ao gerar PIX: ' + pixData.mensagem, 'error');
                        }
                    } else {
                        showAlert('❌ ' + data.mensagem, 'error');
                    }
                } catch (e) {
                    showAlert('❌ Erro: ' + e.message, 'error');
                }
            }

            async function aplicarCupom(servicoId) {
                const codigo = document.getElementById('cupom-input').value.trim();
                if (!codigo) {
                    showAlert('Digite um código de cupom', 'error');
                    return;
                }

                try {
                    const response = await fetch('/api/pedido/cupom', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            servico_id: servicoId,
                            cupom: codigo
                        })
                    });
                    const data = await response.json();
                    
                    if (data.sucesso) {
                        showAlert('✅ ' + data.mensagem, 'success');
                        // Atualizar preço
                        document.querySelector('.preco').textContent = 'R$ ' + data.valor_final.toFixed(2);
                    } else {
                        showAlert('❌ ' + data.mensagem, 'error');
                    }
                } catch (e) {
                    showAlert('❌ Erro: ' + e.message, 'error');
                }
            }

            function copiarPIX() {
                const codigo = document.getElementById('pix-codigo').textContent;
                if (navigator.clipboard) {
                    navigator.clipboard.writeText(codigo);
                    showAlert('📋 Código copiado!', 'success');
                } else {
                    alert('Copie o código manualmente: ' + codigo);
                }
            }

            function showAlert(msg, tipo) {
                const alert = document.getElementById('alert');
                alert.textContent = msg;
                alert.className = 'alert alert-' + tipo;
                alert.style.display = 'block';
                setTimeout(() => alert.style.display = 'none', 10000);
            }

            // Verificar status do pedido periodicamente
            setInterval(async () => {
                if (!pedidoAtual) return;
                try {
                    const response = await fetch(`/api/pedido/status/${pedidoAtual.numero}`);
                    const data = await response.json();
                    if (data.status === 'Pago') {
                        showAlert('✅ Pagamento confirmado! Seu pedido foi aprovado.', 'success');
                        document.getElementById('pix-area').style.display = 'none';
                        pedidoAtual = null;
                    }
                } catch (e) {}
            }, 5000);
        </script>
    </body>
    </html>
    """, usuario=usuario, config_loja=config_loja, servico=servico)

@app.route("/cliente")
def area_cliente():
    """Área do cliente"""
    if 'usuario' not in session:
        return redirect(url_for('login'))
    
    usuario = session.get('usuario')
    uid = str(usuario['id'])
    dados_usuario = dados.get("usuarios", {}).get(uid, {})
    config_loja = dados.get("config_loja", {})
    
    # Buscar pedidos do usuário
    pedidos_usuario = [p for p in dados.get("pedidos", []) if p.get("cliente_id") == uid]
    
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Minha Conta - {{ config_loja.nome_loja }}</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0a0a0a; color: #e0e0e0; }
            .navbar { background: #1a1a1a; padding: 1rem 2rem; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; }
            .navbar .logo { font-size: 1.5rem; font-weight: bold; color: #5865F2; text-decoration: none; }
            .navbar .nav-links a { color: #ccc; text-decoration: none; margin-left: 2rem; transition: 0.3s; }
            .navbar .nav-links a:hover { color: #5865F2; }
            .btn { display: inline-block; padding: 0.5rem 1.5rem; border-radius: 8px; text-decoration: none; font-weight: 600; transition: 0.3s; border: none; cursor: pointer; }
            .btn-primary { background: #5865F2; color: white; }
            .btn-primary:hover { background: #4752C4; transform: translateY(-2px); }
            .btn-success { background: #10b981; color: white; }
            .btn-success:hover { background: #059669; transform: translateY(-2px); }
            .btn-outline { background: transparent; color: white; border: 2px solid #5865F2; }
            .btn-outline:hover { background: #5865F2; color: white; }
            .btn-danger { background: #ef4444; color: white; }
            .btn-danger:hover { background: #dc2626; transform: translateY(-2px); }
            .container { max-width: 1200px; margin: 2rem auto; padding: 0 1rem; }
            .perfil-header { display: flex; align-items: center; gap: 2rem; background: #1a1a1a; padding: 2rem; border-radius: 12px; border: 1px solid #333; margin-bottom: 2rem; }
            .perfil-header .avatar { width: 100px; height: 100px; border-radius: 50%; border: 3px solid #5865F2; }
            .perfil-header .info h1 { color: #fff; }
            .perfil-header .info .stats { display: flex; gap: 2rem; margin-top: 0.5rem; flex-wrap: wrap; }
            .perfil-header .info .stats span { color: #aaa; }
            .perfil-header .info .stats strong { color: #fff; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 2rem; }
            .card { background: #1a1a1a; border-radius: 12px; padding: 1.5rem; border: 1px solid #333; }
            .card h3 { color: #5865F2; margin-bottom: 1rem; }
            .card .item { padding: 0.5rem 0; border-bottom: 1px solid #333; display: flex; justify-content: space-between; }
            .card .item:last-child { border-bottom: none; }
            .card .item .label { color: #888; }
            .card .item .value { color: #fff; }
            .status-badge { display: inline-block; padding: 0.25rem 0.75rem; border-radius: 20px; font-size: 0.8rem; }
            .status-Aguardando { background: #f59e0b; color: #000; }
            .status-Pago { background: #10b981; color: #fff; }
            .status-Concluído { background: #5865F2; color: #fff; }
            .status-Cancelado { background: #ef4444; color: #fff; }
            .user-info { display: flex; align-items: center; gap: 1rem; }
            .user-info .avatar-small { width: 35px; height: 35px; border-radius: 50%; border: 2px solid #5865F2; }
            .dropdown { position: relative; display: inline-block; }
            .dropdown-content { display: none; position: absolute; right: 0; background: #1a1a1a; min-width: 200px; box-shadow: 0 8px 16px rgba(0,0,0,0.5); border-radius: 8px; border: 1px solid #333; z-index: 1; }
            .dropdown:hover .dropdown-content { display: block; }
            .dropdown-content a { color: #ccc; padding: 12px 16px; text-decoration: none; display: block; }
            .dropdown-content a:hover { background: #2a2a2a; color: #5865F2; }
            .recompensas-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 1rem; margin-top: 1rem; }
            .recompensa-item { background: #2a2a2a; padding: 1rem; border-radius: 8px; text-align: center; border: 1px solid #333; transition: 0.3s; }
            .recompensa-item:hover { border-color: #5865F2; transform: translateY(-2px); }
            .recompensa-item .custo { color: #f59e0b; font-weight: bold; }
            .recompensa-item .btn { width: 100%; margin-top: 0.5rem; }
            @media (max-width: 768px) {
                .perfil-header { flex-direction: column; text-align: center; }
                .perfil-header .info .stats { justify-content: center; }
                .navbar .nav-links a { margin-left: 1rem; font-size: 0.9rem; }
            }
        </style>
    </head>
    <body>
        <nav class="navbar">
            <a href="/" class="logo">{{ config_loja.nome_loja }}</a>
            <div class="nav-links">
                <a href="/">Início</a>
                <a href="/servicos">Serviços</a>
                <a href="/fila">Fila</a>
                <a href="/cliente">Minha Conta</a>
                {% if usuario.get('eh_admin') %}
                <a href="/dashboard" style="color: #f59e0b;">Painel Admin</a>
                {% endif %}
                <div class="dropdown" style="display:inline-block;">
                    <div class="user-info">
                        <img src="https://cdn.discordapp.com/avatars/{{ usuario.id }}/{{ usuario.get('avatar', '') }}.png" class="avatar-small" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                        <span class="nome">{{ usuario.nome_usuario }}</span>
                        <span style="color:#5865F2;">▼</span>
                    </div>
                    <div class="dropdown-content">
                        <a href="/cliente">👤 Minha Conta</a>
                        <a href="/pedidos">📦 Meus Pedidos</a>
                        <a href="/logout">🚪 Sair</a>
                    </div>
                </div>
            </div>
        </nav>

        <div class="container">
            <div class="perfil-header">
                <img src="https://cdn.discordapp.com/avatars/{{ usuario.id }}/{{ usuario.get('avatar', '') }}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                <div class="info">
                    <h1>{{ dados_usuario.nome or usuario.nome_usuario }}</h1>
                    <div class="stats">
                        <span>⭐ <strong>{{ dados_usuario.pontos or 0 }}</strong> pontos</span>
                        <span>💰 <strong>R$ {{ "%.2f"|format(dados_usuario.total_gasto or 0) }}</strong> gastos</span>
                        <span>📦 <strong>{{ pedidos_usuario|length }}</strong> pedidos</span>
                        <span>🎫 <strong>{{ dados_usuario.cupons|length or 0 }}</strong> cupons</span>
                    </div>
                </div>
            </div>

            <div class="grid">
                <div class="card">
                    <h3>🎫 Meus Cupons</h3>
                    {% if dados_usuario.cupons %}
                        {% for codigo in dados_usuario.cupons %}
                            {% set cupom = None %}
                            {% for c in dados.cupons %}
                                {% if c.codigo == codigo %}
                                    {% set cupom = c %}
                                {% endif %}
                            {% endfor %}
                            {% if cupom %}
                            <div class="item">
                                <span>{{ cupom.codigo }}</span>
                                <span style="color:#10b981;">{{ cupom.tipo }} {{ cupom.valor }}%</span>
                            </div>
                            {% endif %}
                        {% endfor %}
                    {% else %}
                    <p style="color:#666;">Nenhum cupom disponível</p>
                    {% endif %}
                </div>

                <div class="card">
                    <h3>⭐ Recompensas</h3>
                    <div class="recompensas-grid">
                        {% for rec in dados.recompensas %}
                        <div class="recompensa-item">
                            <div style="font-size:1.5rem;">🎁</div>
                            <div><strong>{{ rec.nome }}</strong></div>
                            <div class="custo">{{ rec.custo_pontos }} pontos</div>
                            <button onclick="resgatar({{ rec.id }})" class="btn btn-primary btn-sm" style="padding:0.25rem 0.75rem; font-size:0.8rem;">
                                Resgatar
                            </button>
                        </div>
                        {% endfor %}
                    </div>
                </div>

                <div class="card" style="grid-column: 1/-1;">
                    <h3>📦 Últimos Pedidos</h3>
                    {% if pedidos_usuario %}
                        <div style="overflow-x:auto;">
                            <table style="width:100%; border-collapse: collapse;">
                                <thead>
                                    <tr style="border-bottom: 1px solid #333;">
                                        <th style="padding:0.5rem; text-align:left;">Pedido</th>
                                        <th style="padding:0.5rem; text-align:left;">Serviço</th>
                                        <th style="padding:0.5rem; text-align:left;">Valor</th>
                                        <th style="padding:0.5rem; text-align:left;">Status</th>
                                        <th style="padding:0.5rem; text-align:left;">Data</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for p in pedidos_usuario[:10] %}
                                    <tr style="border-bottom: 1px solid #2a2a2a;">
                                        <td style="padding:0.5rem;">{{ p.numero }}</td>
                                        <td style="padding:0.5rem;">{{ p.servico_nome }}</td>
                                        <td style="padding:0.5rem;">R$ {{ "%.2f"|format(p.valor_final or p.valor) }}</td>
                                        <td style="padding:0.5rem;"><span class="status-badge status-{{ p.status }}">{{ p.status }}</span></td>
                                        <td style="padding:0.5rem;">{{ p.data_criacao[:10] }}</td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    {% else %}
                        <p style="color:#666;">Nenhum pedido realizado</p>
                    {% endif %}
                </div>
            </div>
        </div>

        <script>
            async function resgatar(recompensaId) {
                if (!confirm('Deseja resgatar esta recompensa?')) return;
                
                try {
                    const response = await fetch('/api/recompensa/resgatar', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({recompensa_id: recompensaId})
                    });
                    const data = await response.json();
                    alert(data.mensagem);
                    if (data.sucesso) window.location.reload();
                } catch (e) {
                    alert('Erro: ' + e.message);
                }
            }
        </script>
    </body>
    </html>
    """, usuario=usuario, dados_usuario=dados_usuario, config_loja=config_loja, pedidos_usuario=pedidos_usuario, dados=dados)

@app.route("/pedidos")
def meus_pedidos():
    """Página com todos os pedidos do usuário"""
    if 'usuario' not in session:
        return redirect(url_for('login'))
    
    usuario = session.get('usuario')
    uid = str(usuario['id'])
    pedidos_usuario = [p for p in dados.get("pedidos", []) if p.get("cliente_id") == uid]
    pedidos_usuario.sort(key=lambda x: x.get("data_criacao", ""), reverse=True)
    
    config_loja = dados.get("config_loja", {})
    
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Meus Pedidos - {{ config_loja.nome_loja }}</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0a0a0a; color: #e0e0e0; }
            .navbar { background: #1a1a1a; padding: 1rem 2rem; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; }
            .navbar .logo { font-size: 1.5rem; font-weight: bold; color: #5865F2; text-decoration: none; }
            .navbar .nav-links a { color: #ccc; text-decoration: none; margin-left: 2rem; transition: 0.3s; }
            .navbar .nav-links a:hover { color: #5865F2; }
            .btn { display: inline-block; padding: 0.5rem 1.5rem; border-radius: 8px; text-decoration: none; font-weight: 600; transition: 0.3s; border: none; cursor: pointer; }
            .btn-primary { background: #5865F2; color: white; }
            .btn-primary:hover { background: #4752C4; transform: translateY(-2px); }
            .btn-outline { background: transparent; color: white; border: 2px solid #5865F2; }
            .btn-outline:hover { background: #5865F2; color: white; }
            .container { max-width: 1200px; margin: 2rem auto; padding: 0 1rem; }
            .card { background: #1a1a1a; border-radius: 12px; padding: 1.5rem; border: 1px solid #333; }
            .card h2 { color: #fff; margin-bottom: 1rem; }
            .status-badge { display: inline-block; padding: 0.25rem 0.75rem; border-radius: 20px; font-size: 0.8rem; }
            .status-Aguardando { background: #f59e0b; color: #000; }
            .status-Pago { background: #10b981; color: #fff; }
            .status-Concluído { background: #5865F2; color: #fff; }
            .status-Cancelado { background: #ef4444; color: #fff; }
            .table-responsive { overflow-x: auto; }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 0.75rem; text-align: left; border-bottom: 1px solid #2a2a2a; }
            th { background: #2a2a2a; color: #aaa; font-weight: 600; }
            .user-info { display: flex; align-items: center; gap: 1rem; }
            .user-info .avatar { width: 35px; height: 35px; border-radius: 50%; border: 2px solid #5865F2; }
            .dropdown { position: relative; display: inline-block; }
            .dropdown-content { display: none; position: absolute; right: 0; background: #1a1a1a; min-width: 200px; box-shadow: 0 8px 16px rgba(0,0,0,0.5); border-radius: 8px; border: 1px solid #333; z-index: 1; }
            .dropdown:hover .dropdown-content { display: block; }
            .dropdown-content a { color: #ccc; padding: 12px 16px; text-decoration: none; display: block; }
            .dropdown-content a:hover { background: #2a2a2a; color: #5865F2; }
            .filtros { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1rem; }
            .filtros select, .filtros input { padding: 0.5rem 1rem; background: #2a2a2a; border: 1px solid #333; border-radius: 8px; color: #e0e0e0; }
            .filtros select:focus, .filtros input:focus { outline: none; border-color: #5865F2; }
            @media (max-width: 768px) {
                .navbar .nav-links a { margin-left: 1rem; font-size: 0.9rem; }
                th, td { padding: 0.5rem; font-size: 0.9rem; }
            }
        </style>
    </head>
    <body>
        <nav class="navbar">
            <a href="/" class="logo">{{ config_loja.nome_loja }}</a>
            <div class="nav-links">
                <a href="/">Início</a>
                <a href="/servicos">Serviços</a>
                <a href="/fila">Fila</a>
                <a href="/cliente">Minha Conta</a>
                {% if usuario.get('eh_admin') %}
                <a href="/dashboard" style="color: #f59e0b;">Painel Admin</a>
                {% endif %}
                <div class="dropdown" style="display:inline-block;">
                    <div class="user-info">
                        <img src="https://cdn.discordapp.com/avatars/{{ usuario.id }}/{{ usuario.get('avatar', '') }}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                        <span class="nome">{{ usuario.nome_usuario }}</span>
                        <span style="color:#5865F2;">▼</span>
                    </div>
                    <div class="dropdown-content">
                        <a href="/cliente">👤 Minha Conta</a>
                        <a href="/pedidos">📦 Meus Pedidos</a>
                        <a href="/logout">🚪 Sair</a>
                    </div>
                </div>
            </div>
        </nav>

        <div class="container">
            <div class="card">
                <h2>📦 Meus Pedidos</h2>
                
                <div class="filtros">
                    <select id="filtro-status" onchange="filtrar()">
                        <option value="">Todos os status</option>
                        <option value="Aguardando pagamento">Aguardando</option>
                        <option value="Pago">Pago</option>
                        <option value="Concluído">Concluído</option>
                        <option value="Cancelado">Cancelado</option>
                    </select>
                    <input type="text" id="filtro-busca" placeholder="🔍 Buscar pedido..." oninput="filtrar()">
                </div>

                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>Pedido</th>
                                <th>Serviço</th>
                                <th>Valor</th>
                                <th>Status</th>
                                <th>Data</th>
                                <th>Ações</th>
                            </tr>
                        </thead>
                        <tbody id="tabela-pedidos">
                            {% for p in pedidos_usuario %}
                            <tr data-status="{{ p.status }}" data-numero="{{ p.numero }}" data-servico="{{ p.servico_nome|lower }}">
                                <td><strong>{{ p.numero }}</strong></td>
                                <td>{{ p.servico_nome }}</td>
                                <td>R$ {{ "%.2f"|format(p.valor_final or p.valor) }}</td>
                                <td><span class="status-badge status-{{ p.status }}">{{ p.status }}</span></td>
                                <td>{{ p.data_criacao[:10] }} {{ p.data_criacao[11:16] }}</td>
                                <td>
                                    <a href="/pedido/{{ p.numero }}" class="btn btn-primary" style="padding:0.25rem 0.75rem; font-size:0.8rem;">Detalhes</a>
                                </td>
                            </tr>
                            {% else %}
                            <tr>
                                <td colspan="6" style="text-align:center; color:#666; padding:2rem 0;">
                                    Nenhum pedido encontrado.
                                    <br><a href="/servicos" class="btn btn-primary" style="margin-top:1rem;">🛒 Fazer uma compra</a>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <script>
            function filtrar() {
                const status = document.getElementById('filtro-status').value;
                const busca = document.getElementById('filtro-busca').value.toLowerCase();
                const rows = document.querySelectorAll('#tabela-pedidos tr');
                
                rows.forEach(row => {
                    let mostrar = true;
                    if (status && row.dataset.status !== status) mostrar = false;
                    if (busca && !row.dataset.numero.includes(busca) && !row.dataset.servico.includes(busca)) mostrar = false;
                    row.style.display = mostrar ? '' : 'none';
                });
            }
        </script>
    </body>
    </html>
    """, usuario=usuario, pedidos_usuario=pedidos_usuario, config_loja=config_loja)

# ========================
# APIS DA LOJA
# ========================

@app.route("/api/pedido/criar", methods=["POST"])
def api_criar_pedido():
    """Cria um novo pedido"""
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Faça login primeiro"})
    
    data = request.json
    servico_id = data.get("servico_id")
    
    # Buscar serviço
    servico = None
    for s in dados.get("servicos", []):
        if s["id"] == servico_id:
            servico = s
            break
    
    if not servico:
        return jsonify({"sucesso": False, "mensagem": "Serviço não encontrado"})
    
    if servico.get("status") != "ativo":
        return jsonify({"sucesso": False, "mensagem": "Serviço indisponível"})
    
    usuario = session['usuario']
    uid = str(usuario['id'])
    
    # Criar usuário se não existir
    if uid not in dados.get("usuarios", {}):
        criar_usuario(uid, usuario['nome_usuario'], usuario.get('avatar'))
    
    pedido, erro = criar_pedido(uid, servico_id, usuario['nome_usuario'])
    
    if erro:
        return jsonify({"sucesso": False, "mensagem": erro})
    
    return jsonify({"sucesso": True, "pedido": pedido})

@app.route("/api/pedido/pix", methods=["POST"])
def api_gerar_pix():
    """Gera um PIX para o pedido"""
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Faça login primeiro"})
    
    data = request.json
    pedido_numero = data.get("pedido_numero")
    
    # Buscar pedido
    pedido = None
    for p in dados.get("pedidos", []):
        if p["numero"] == pedido_numero:
            pedido = p
            break
    
    if not pedido:
        return jsonify({"sucesso": False, "mensagem": "Pedido não encontrado"})
    
    if pedido["status"] != "Aguardando pagamento":
        return jsonify({"sucesso": False, "mensagem": f"Pedido já está {pedido['status']}"})
    
    # Gerar PIX
    config = dados.get("config_loja", {})
    gateway = config.get("gateway_pix", "mercadopago")
    
    if gateway == "mercadopago":
        pix_data, erro = gerar_pix_mercadopago(
            valor=pedido["valor_final"],
            descricao=f"Pedido {pedido_numero} - {pedido['servico_nome']}",
            pedido_numero=pedido_numero.replace("#", "")
        )
        
        if erro:
            return jsonify({"sucesso": False, "mensagem": erro})
        
        # Salvar dados do PIX no pedido
        pedido["pix_codigo"] = pix_data.get("qr_code")
        pedido["pix_qr"] = pix_data.get("qr_code_base64")
        salvar_dados_github(f"PIX gerado para {pedido_numero}")
        
        return jsonify({
            "sucesso": True,
            "qr_code": pix_data.get("qr_code"),
            "qr_code_base64": pix_data.get("qr_code_base64"),
            "codigo": pix_data.get("qr_code"),
            "id": pix_data.get("id")
        })
    
    return jsonify({"sucesso": False, "mensagem": f"Gateway {gateway} não implementado"})

@app.route("/api/pedido/cupom", methods=["POST"])
def api_aplicar_cupom():
    """Aplica um cupom a um serviço (cria pedido com cupom)"""
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Faça login primeiro"})
    
    data = request.json
    servico_id = data.get("servico_id")
    codigo_cupom = data.get("cupom", "").strip().upper()
    
    if not codigo_cupom:
        return jsonify({"sucesso": False, "mensagem": "Digite um código de cupom"})
    
    # Buscar serviço
    servico = None
    for s in dados.get("servicos", []):
        if s["id"] == servico_id:
            servico = s
            break
    
    if not servico:
        return jsonify({"sucesso": False, "mensagem": "Serviço não encontrado"})
    
    # Buscar cupom
    cupom = None
    for c in dados.get("cupons", []):
        if c["codigo"] == codigo_cupom and c["status"] == "ativo":
            cupom = c
            break
    
    if not cupom:
        return jsonify({"sucesso": False, "mensagem": "Cupom inválido ou expirado"})
    
    # Verificar validade
    if datetime.fromisoformat(cupom["validade"]) < agora_br():
        cupom["status"] = "expirado"
        salvar_dados_github(f"Cupom expirado: {codigo_cupom}")
        return jsonify({"sucesso": False, "mensagem": "Cupom expirado"})
    
    # Verificar quantidade
    if cupom["quantidade_usada"] >= cupom["quantidade_maxima"]:
        return jsonify({"sucesso": False, "mensagem": "Cupom esgotado"})
    
    # Verificar se o cupom é do usuário
    usuario = session['usuario']
    uid = str(usuario['id'])
    if cupom.get("usuario_id") and cupom["usuario_id"] != uid:
        return jsonify({"sucesso": False, "mensagem": "Este cupom não pertence a você"})
    
    # Calcular desconto
    valor_final = servico["preco"]
    desconto = 0
    
    if cupom["tipo"] == "desconto":
        desconto = servico["preco"] * (cupom["valor"] / 100)
        valor_final = servico["preco"] - desconto
    elif cupom["tipo"] == "servico_gratis":
        desconto = servico["preco"]
        valor_final = 0
    
    return jsonify({
        "sucesso": True,
        "mensagem": f"Cupom aplicado! Desconto de R$ {desconto:.2f}",
        "valor_original": servico["preco"],
        "desconto": desconto,
        "valor_final": valor_final,
        "cupom": codigo_cupom
    })

@app.route("/api/pedido/status/<pedido_numero>")
def api_status_pedido(pedido_numero):
    """Verifica o status de um pedido"""
    for p in dados.get("pedidos", []):
        if p["numero"] == pedido_numero:
            return jsonify({
                "status": p["status"],
                "numero": p["numero"],
                "valor": p["valor_final"]
            })
    return jsonify({"status": "Não encontrado"})

@app.route("/api/recompensa/resgatar", methods=["POST"])
def api_resgatar_recompensa():
    """Resgata uma recompensa"""
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Faça login primeiro"})
    
    data = request.json
    recompensa_id = data.get("recompensa_id")
    
    usuario = session['usuario']
    uid = str(usuario['id'])
    
    sucesso, mensagem = resgatar_recompensa(uid, recompensa_id)
    return jsonify({"sucesso": sucesso, "mensagem": mensagem})

# ========================
# PAINEL ADMIN - NOVAS ROTAS
# ========================

@app.route("/dashboard")
def dashboard_admin():
    """Dashboard administrativo completo"""
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return redirect(url_for('login'))
    
    usuario = session['usuario']
    config_loja = dados.get("config_loja", {})
    
    # Estatísticas
    total_clientes = len(dados.get("usuarios", {}))
    total_pedidos = len(dados.get("pedidos", []))
    total_servicos = len(dados.get("servicos", []))
    total_vendido = dados.get("financeiro", {}).get("total_vendido", 0)
    
    # Pedidos recentes
    pedidos_recentes = sorted(dados.get("pedidos", []), key=lambda x: x.get("data_criacao", ""), reverse=True)[:10]
    
    # Estatísticas por status
    pedidos_status = {}
    for p in dados.get("pedidos", []):
        status = p.get("status", "Desconhecido")
        pedidos_status[status] = pedidos_status.get(status, 0) + 1
    
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Dashboard Admin - {{ config_loja.nome_loja }}</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0a0a0a; color: #e0e0e0; }
            .navbar { background: #1a1a1a; padding: 1rem 2rem; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; }
            .navbar .logo { font-size: 1.5rem; font-weight: bold; color: #5865F2; text-decoration: none; }
            .navbar .nav-links a { color: #ccc; text-decoration: none; margin-left: 2rem; transition: 0.3s; }
            .navbar .nav-links a:hover { color: #5865F2; }
            .btn { display: inline-block; padding: 0.5rem 1.5rem; border-radius: 8px; text-decoration: none; font-weight: 600; transition: 0.3s; border: none; cursor: pointer; }
            .btn-primary { background: #5865F2; color: white; }
            .btn-primary:hover { background: #4752C4; transform: translateY(-2px); }
            .btn-success { background: #10b981; color: white; }
            .btn-success:hover { background: #059669; transform: translateY(-2px); }
            .btn-danger { background: #ef4444; color: white; }
            .btn-danger:hover { background: #dc2626; transform: translateY(-2px); }
            .btn-warning { background: #f59e0b; color: #000; }
            .btn-warning:hover { background: #d97706; transform: translateY(-2px); }
            .btn-outline { background: transparent; color: white; border: 2px solid #5865F2; }
            .btn-outline:hover { background: #5865F2; color: white; }
            .btn-sm { padding: 0.25rem 0.75rem; font-size: 0.8rem; }
            .container { max-width: 1400px; margin: 2rem auto; padding: 0 1rem; }
            .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
            .stat-card { background: #1a1a1a; padding: 1.5rem; border-radius: 12px; border: 1px solid #333; text-align: center; }
            .stat-card h3 { font-size: 2rem; color: #5865F2; }
            .stat-card p { color: #888; }
            .grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; }
            .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }
            .card { background: #1a1a1a; border-radius: 12px; padding: 1.5rem; border: 1px solid #333; }
            .card h3 { color: #5865F2; margin-bottom: 1rem; }
            .tab-nav { display: flex; gap: 0.5rem; margin-bottom: 2rem; flex-wrap: wrap; border-bottom: 2px solid #333; padding-bottom: 0.5rem; }
            .tab-btn { padding: 0.5rem 1.5rem; background: transparent; border: none; color: #888; cursor: pointer; border-radius: 8px; transition: 0.3s; }
            .tab-btn:hover { background: #2a2a2a; color: #fff; }
            .tab-btn.active { background: #5865F2; color: #fff; }
            .tab { display: none; }
            .tab.active { display: block; animation: fadeIn 0.3s; }
            @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 0.75rem; text-align: left; border-bottom: 1px solid #2a2a2a; }
            th { background: #2a2a2a; color: #aaa; font-weight: 600; }
            .status-badge { display: inline-block; padding: 0.25rem 0.75rem; border-radius: 20px; font-size: 0.8rem; }
            .status-Aguardando { background: #f59e0b; color: #000; }
            .status-Pago { background: #10b981; color: #fff; }
            .status-Concluído { background: #5865F2; color: #fff; }
            .status-Cancelado { background: #ef4444; color: #fff; }
            .form-group { margin-bottom: 1rem; }
            .form-group label { display: block; margin-bottom: 0.25rem; color: #aaa; }
            .form-control { width: 100%; padding: 0.75rem; background: #2a2a2a; border: 1px solid #333; border-radius: 8px; color: #e0e0e0; }
            .form-control:focus { outline: none; border-color: #5865F2; }
            .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
            .alert { padding: 1rem; border-radius: 8px; margin: 1rem 0; display: none; }
            .alert-success { background: #1a472a; color: #4ade80; border: 1px solid #2ecc71; }
            .alert-error { background: #7f1d1d; color: #f87171; border: 1px solid #ef4444; }
            .user-info { display: flex; align-items: center; gap: 1rem; }
            .user-info .avatar { width: 35px; height: 35px; border-radius: 50%; border: 2px solid #5865F2; }
            .dropdown { position: relative; display: inline-block; }
            .dropdown-content { display: none; position: absolute; right: 0; background: #1a1a1a; min-width: 200px; box-shadow: 0 8px 16px rgba(0,0,0,0.5); border-radius: 8px; border: 1px solid #333; z-index: 1; }
            .dropdown:hover .dropdown-content { display: block; }
            .dropdown-content a { color: #ccc; padding: 12px 16px; text-decoration: none; display: block; }
            .dropdown-content a:hover { background: #2a2a2a; color: #5865F2; }
            .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); z-index: 1000; align-items: center; justify-content: center; }
            .modal-content { background: #1a1a1a; padding: 2rem; border-radius: 12px; max-width: 600px; width: 90%; max-height: 90vh; overflow-y: auto; border: 1px solid #333; }
            .modal-close { float: right; background: none; border: none; color: #fff; font-size: 1.5rem; cursor: pointer; }
            @media (max-width: 768px) {
                .grid-2, .grid-3 { grid-template-columns: 1fr; }
                .form-row { grid-template-columns: 1fr; }
                .navbar .nav-links a { margin-left: 1rem; font-size: 0.9rem; }
                .stats-grid { grid-template-columns: repeat(2, 1fr); }
            }
        </style>
    </head>
    <body>
        <nav class="navbar">
            <a href="/" class="logo">{{ config_loja.nome_loja }}</a>
            <div class="nav-links">
                <a href="/">Início</a>
                <a href="/servicos">Serviços</a>
                <a href="/fila">Fila</a>
                <a href="/cliente">Minha Conta</a>
                <a href="/dashboard" style="color: #f59e0b;">Painel Admin</a>
                <div class="dropdown" style="display:inline-block;">
                    <div class="user-info">
                        <img src="https://cdn.discordapp.com/avatars/{{ usuario.id }}/{{ usuario.get('avatar', '') }}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                        <span class="nome">{{ usuario.nome_usuario }}</span>
                        <span style="color:#5865F2;">▼</span>
                    </div>
                    <div class="dropdown-content">
                        <a href="/cliente">👤 Minha Conta</a>
                        <a href="/pedidos">📦 Meus Pedidos</a>
                        <a href="/logout">🚪 Sair</a>
                    </div>
                </div>
            </div>
        </nav>

        <div class="container">
            <h2 style="color:#fff; margin-bottom: 1rem;">📊 Dashboard Administrativo</h2>

            <div class="stats-grid">
                <div class="stat-card"><h3>{{ total_clientes }}</h3><p>👥 Clientes</p></div>
                <div class="stat-card"><h3>{{ total_pedidos }}</h3><p>📦 Pedidos</p></div>
                <div class="stat-card"><h3>{{ total_servicos }}</h3><p>🛒 Serviços</p></div>
                <div class="stat-card"><h3>R$ {{ "%.2f"|format(total_vendido) }}</h3><p>💰 Faturamento</p></div>
            </div>

            <div class="tab-nav">
                <button class="tab-btn active" onclick="showTab('dashboard')">📊 Dashboard</button>
                <button class="tab-btn" onclick="showTab('servicos')">🛒 Serviços</button>
                <button class="tab-btn" onclick="showTab('pedidos')">📦 Pedidos</button>
                <button class="tab-btn" onclick="showTab('clientes')">👥 Clientes</button>
                <button class="tab-btn" onclick="showTab('config')">⚙️ Configurações</button>
            </div>

            <!-- Tab Dashboard -->
            <div id="dashboard" class="tab active">
                <div class="grid-2">
                    <div class="card">
                        <h3>📊 Pedidos por Status</h3>
                        {% for status, count in pedidos_status.items() %}
                        <div style="display:flex; justify-content:space-between; padding:0.5rem 0; border-bottom:1px solid #2a2a2a;">
                            <span>{{ status }}</span>
                            <span class="status-badge status-{{ status }}">{{ count }}</span>
                        </div>
                        {% else %}
                        <p style="color:#666;">Nenhum pedido</p>
                        {% endfor %}
                    </div>
                    <div class="card">
                        <h3>📋 Últimos Pedidos</h3>
                        {% for p in pedidos_recentes %}
                        <div style="display:flex; justify-content:space-between; padding:0.5rem 0; border-bottom:1px solid #2a2a2a;">
                            <span>{{ p.numero }} - {{ p.servico_nome }}</span>
                            <span class="status-badge status-{{ p.status }}">{{ p.status }}</span>
                        </div>
                        {% else %}
                        <p style="color:#666;">Nenhum pedido</p>
                        {% endfor %}
                    </div>
                </div>
            </div>

            <!-- Tab Serviços -->
            <div id="servicos" class="tab">
                <div class="card">
                    <h3>🛒 Gerenciar Serviços</h3>
                    <button onclick="abrirModal('servico')" class="btn btn-success" style="margin-bottom:1rem;">➕ Novo Serviço</button>
                    <div class="table-responsive">
                        <table>
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>Nome</th>
                                    <th>Categoria</th>
                                    <th>Preço</th>
                                    <th>Status</th>
                                    <th>Destaque</th>
                                    <th>Ações</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for s in dados.servicos %}
                                <tr>
                                    <td>{{ s.id }}</td>
                                    <td>{{ s.nome }}</td>
                                    <td>{{ s.categoria }}</td>
                                    <td>R$ {{ "%.2f"|format(s.preco) }}</td>
                                    <td><span class="status-badge status-{{ s.status }}">{{ s.status }}</span></td>
                                    <td>{% if s.destaque %}⭐{% endif %}</td>
                                    <td>
                                        <button onclick="editarServico({{ s.id }})" class="btn btn-primary btn-sm">✏️</button>
                                        <button onclick="removerServico({{ s.id }})" class="btn btn-danger btn-sm">🗑️</button>
                                    </td>
                                </tr>
                                {% else %}
                                <tr><td colspan="7" style="text-align:center; color:#666;">Nenhum serviço cadastrado</td></tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Tab Pedidos -->
            <div id="pedidos" class="tab">
                <div class="card">
                    <h3>📦 Gerenciar Pedidos</h3>
                    <div class="table-responsive">
                        <table>
                            <thead>
                                <tr>
                                    <th>Pedido</th>
                                    <th>Cliente</th>
                                    <th>Serviço</th>
                                    <th>Valor</th>
                                    <th>Status</th>
                                    <th>Data</th>
                                    <th>Ações</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for p in dados.pedidos|reverse %}
                                <tr>
                                    <td>{{ p.numero }}</td>
                                    <td>{{ p.cliente_nome }}</td>
                                    <td>{{ p.servico_nome }}</td>
                                    <td>R$ {{ "%.2f"|format(p.valor_final or p.valor) }}</td>
                                    <td><span class="status-badge status-{{ p.status }}">{{ p.status }}</span></td>
                                    <td>{{ p.data_criacao[:10] }}</td>
                                    <td>
                                        <select onchange="atualizarStatus('{{ p.numero }}', this.value)" class="form-control" style="width:auto; display:inline-block; padding:0.25rem;">
                                            <option value="Aguardando pagamento" {% if p.status == 'Aguardando pagamento' %}selected{% endif %}>Aguardando</option>
                                            <option value="Pago" {% if p.status == 'Pago' %}selected{% endif %}>Pago</option>
                                            <option value="Em andamento" {% if p.status == 'Em andamento' %}selected{% endif %}>Em andamento</option>
                                            <option value="Concluído" {% if p.status == 'Concluído' %}selected{% endif %}>Concluído</option>
                                            <option value="Cancelado" {% if p.status == 'Cancelado' %}selected{% endif %}>Cancelado</option>
                                        </select>
                                    </td>
                                </tr>
                                {% else %}
                                <tr><td colspan="7" style="text-align:center; color:#666;">Nenhum pedido</td></tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Tab Clientes -->
            <div id="clientes" class="tab">
                <div class="card">
                    <h3>👥 Gerenciar Clientes</h3>
                    <div class="table-responsive">
                        <table>
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>Nome</th>
                                    <th>Pontos</th>
                                    <th>Total Gasto</th>
                                    <th>Pedidos</th>
                                    <th>Ações</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for uid, user in dados.usuarios.items() %}
                                <tr>
                                    <td>{{ uid[:8] }}...</td>
                                    <td>{{ user.nome }}</td>
                                    <td>{{ user.pontos }}</td>
                                    <td>R$ {{ "%.2f"|format(user.total_gasto or 0) }}</td>
                                    <td>{{ user.pedidos|length }}</td>
                                    <td>
                                        <button onclick="editarCliente('{{ uid }}')" class="btn btn-primary btn-sm">✏️</button>
                                        <button onclick="adicionarPontos('{{ uid }}')" class="btn btn-success btn-sm">+⭐</button>
                                    </td>
                                </tr>
                                {% else %}
                                <tr><td colspan="6" style="text-align:center; color:#666;">Nenhum cliente</td></tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Tab Configurações -->
            <div id="config" class="tab">
                <div class="card">
                    <h3>⚙️ Configurações da Loja</h3>
                    <form id="config-form" onsubmit="salvarConfig(event)">
                        <div class="form-row">
                            <div class="form-group">
                                <label>Nome da Loja</label>
                                <input type="text" id="config-nome" class="form-control" value="{{ config_loja.nome_loja }}">
                            </div>
                            <div class="form-group">
                                <label>Pontos por R$</label>
                                <input type="number" id="config-pontos" class="form-control" value="{{ config_loja.pontos_por_real }}" min="1">
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>Logo (URL)</label>
                                <input type="url" id="config-logo" class="form-control" value="{{ config_loja.logo or '' }}" placeholder="URL da logo">
                            </div>
                            <div class="form-group">
                                <label>Banner (URL)</label>
                                <input type="url" id="config-banner" class="form-control" value="{{ config_loja.banner or '' }}" placeholder="URL do banner">
                            </div>
                        </div>
                        <div class="form-group">
                            <label>Gateway PIX</label>
                            <select id="config-gateway" class="form-control">
                                <option value="mercadopago" {% if config_loja.gateway_pix == 'mercadopago' %}selected{% endif %}>Mercado Pago</option>
                                <option value="efi" {% if config_loja.gateway_pix == 'efi' %}selected{% endif %}>Efí</option>
                                <option value="asaas" {% if config_loja.gateway_pix == 'asaas' %}selected{% endif %}>Asaas</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Mercado Pago Token</label>
                            <input type="text" id="config-token" class="form-control" value="{{ config_loja.mercadopago_token or '' }}" placeholder="Token de acesso">
                        </div>
                        <div class="form-group">
                            <label>Webhook URL</label>
                            <input type="url" id="config-webhook" class="form-control" value="{{ config_loja.mercadopago_webhook or '' }}" placeholder="https://seu-site.com/webhook/mercadopago">
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>Discord</label>
                                <input type="url" id="config-discord" class="form-control" value="{{ config_loja.redes_sociais.discord or '' }}" placeholder="https://discord.gg/...">
                            </div>
                            <div class="form-group">
                                <label>Instagram</label>
                                <input type="url" id="config-instagram" class="form-control" value="{{ config_loja.redes_sociais.instagram or '' }}">
                            </div>
                        </div>
                        <button type="submit" class="btn btn-primary">💾 Salvar Configurações</button>
                        <div id="config-alert" class="alert"></div>
                    </form>
                </div>
            </div>
        </div>

        <!-- Modal para Serviço -->
        <div id="modal-servico" class="modal">
            <div class="modal-content">
                <button class="modal-close" onclick="fecharModal('servico')">×</button>
                <h3 id="modal-servico-titulo" style="color:#fff; margin-bottom:1rem;">Novo Serviço</h3>
                <form id="form-servico" onsubmit="salvarServico(event)">
                    <input type="hidden" id="servico-id" value="">
                    <div class="form-group">
                        <label>Nome</label>
                        <input type="text" id="servico-nome" class="form-control" required>
                    </div>
                    <div class="form-group">
                        <label>Categoria</label>
                        <select id="servico-categoria" class="form-control" required>
                            {% for cat in dados.categorias %}
                            <option value="{{ cat }}">{{ cat }}</option>
                            {% endfor %}
                            <option value="Outros">Outros</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Preço (R$)</label>
                        <input type="number" id="servico-preco" class="form-control" step="0.01" required>
                    </div>
                    <div class="form-group">
                        <label>Descrição</label>
                        <textarea id="servico-descricao" class="form-control" rows="3"></textarea>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>Tempo Estimado</label>
                            <input type="text" id="servico-tempo" class="form-control" placeholder="Ex: 2-4 horas">
                        </div>
                        <div class="form-group">
                            <label>Imagem (emoji ou URL)</label>
                            <input type="text" id="servico-imagem" class="form-control" placeholder="🎮 ou https://...">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>
                                <input type="checkbox" id="servico-destaque"> Destaque
                            </label>
                        </div>
                        <div class="form-group">
                            <label>Status</label>
                            <select id="servico-status" class="form-control">
                                <option value="ativo">Ativo</option>
                                <option value="inativo">Inativo</option>
                            </select>
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary">💾 Salvar</button>
                    <div id="servico-alert" class="alert"></div>
                </form>
            </div>
        </div>

        <script>
            let configData = {{ dados.config_loja|tojson }};

            function showTab(tabId) {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.getElementById(tabId).classList.add('active');
                event.target.classList.add('active');
            }

            function abrirModal(tipo, dados = null) {
                if (tipo === 'servico') {
                    const modal = document.getElementById('modal-servico');
                    modal.style.display = 'flex';
                    
                    if (dados) {
                        document.getElementById('modal-servico-titulo').textContent = 'Editar Serviço';
                        document.getElementById('servico-id').value = dados.id;
                        document.getElementById('servico-nome').value = dados.nome;
                        document.getElementById('servico-categoria').value = dados.categoria;
                        document.getElementById('servico-preco').value = dados.preco;
                        document.getElementById('servico-descricao').value = dados.descricao || '';
                        document.getElementById('servico-tempo').value = dados.tempo_estimado || '';
                        document.getElementById('servico-imagem').value = dados.imagem || '';
                        document.getElementById('servico-destaque').checked = dados.destaque || false;
                        document.getElementById('servico-status').value = dados.status || 'ativo';
                    } else {
                        document.getElementById('modal-servico-titulo').textContent = 'Novo Serviço';
                        document.getElementById('servico-id').value = '';
                        document.getElementById('form-servico').reset();
                        document.getElementById('servico-status').value = 'ativo';
                    }
                }
            }

            function fecharModal(tipo) {
                if (tipo === 'servico') {
                    document.getElementById('modal-servico').style.display = 'none';
                }
            }

            async function salvarServico(event) {
                event.preventDefault();
                const id = document.getElementById('servico-id').value;
                const dados = {
                    nome: document.getElementById('servico-nome').value,
                    categoria: document.getElementById('servico-categoria').value,
                    preco: parseFloat(document.getElementById('servico-preco').value),
                    descricao: document.getElementById('servico-descricao').value,
                    tempo_estimado: document.getElementById('servico-tempo').value,
                    imagem: document.getElementById('servico-imagem').value,
                    destaque: document.getElementById('servico-destaque').checked,
                    status: document.getElementById('servico-status').value
                };

                try {
                    const url = id ? '/api/servico/editar' : '/api/servico/criar';
                    if (id) dados.id = parseInt(id);
                    
                    const response = await fetch(url, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(dados)
                    });
                    const result = await response.json();
                    
                    mostrarAlert('servico-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {
                        setTimeout(() => window.location.reload(), 1500);
                    }
                } catch (e) {
                    mostrarAlert('servico-alert', 'Erro: ' + e.message, false);
                }
            }

            function editarServico(id) {
                const servico = {{ dados.servicos|tojson }}.find(s => s.id === id);
                if (servico) abrirModal('servico', servico);
            }

            async function removerServico(id) {
                if (!confirm('Remover este serviço?')) return;
                try {
                    const response = await fetch('/api/servico/remover', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({id})
                    });
                    const result = await response.json();
                    alert(result.mensagem);
                    if (result.sucesso) window.location.reload();
                } catch (e) {
                    alert('Erro: ' + e.message);
                }
            }

            async function atualizarStatus(pedido, status) {
                try {
                    const response = await fetch('/api/pedido/status/atualizar', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({pedido, status})
                    });
                    const result = await response.json();
                    alert(result.mensagem);
                    if (result.sucesso) window.location.reload();
                } catch (e) {
                    alert('Erro: ' + e.message);
                }
            }

            function editarCliente(uid) {
                const pontos = prompt('Quantos pontos adicionar? (Digite negativo para remover)');
                if (pontos === null) return;
                const valor = parseInt(pontos);
                if (isNaN(valor)) return;
                
                fetch('/api/cliente/pontos', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({usuario_id: uid, pontos: valor})
                }).then(r => r.json()).then(data => {
                    alert(data.mensagem);
                    if (data.sucesso) window.location.reload();
                });
            }

            function adicionarPontos(uid) {
                editarCliente(uid);
            }

            async function salvarConfig(event) {
                event.preventDefault();
                const dados = {
                    nome_loja: document.getElementById('config-nome').value,
                    pontos_por_real: parseInt(document.getElementById('config-pontos').value),
                    logo: document.getElementById('config-logo').value,
                    banner: document.getElementById('config-banner').value,
                    gateway_pix: document.getElementById('config-gateway').value,
                    mercadopago_token: document.getElementById('config-token').value,
                    mercadopago_webhook: document.getElementById('config-webhook').value,
                    redes_sociais: {
                        discord: document.getElementById('config-discord').value,
                        instagram: document.getElementById('config-instagram').value
                    }
                };

                try {
                    const response = await fetch('/api/config/loja', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(dados)
                    });
                    const result = await response.json();
                    mostrarAlert('config-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {
                        setTimeout(() => window.location.reload(), 1500);
                    }
                } catch (e) {
                    mostrarAlert('config-alert', 'Erro: ' + e.message, false);
                }
            }

            function mostrarAlert(id, msg, sucesso) {
                const el = document.getElementById(id);
                el.textContent = msg;
                el.className = 'alert ' + (sucesso ? 'alert-success' : 'alert-error');
                el.style.display = 'block';
                setTimeout(() => el.style.display = 'none', 5000);
            }

            // Fechar modal ao clicar fora
            document.querySelectorAll('.modal').forEach(modal => {
                modal.addEventListener('click', function(e) {
                    if (e.target === this) this.style.display = 'none';
                });
            });
        </script>
    </body>
    </html>
    """, usuario=usuario, config_loja=config_loja, total_clientes=total_clientes, total_pedidos=total_pedidos, total_servicos=total_servicos, total_vendido=total_vendido, pedidos_recentes=pedidos_recentes, pedidos_status=pedidos_status, dados=dados)

# ========================
# APIS ADMIN DA LOJA
# ========================

@app.route("/api/servico/criar", methods=["POST"])
def api_criar_servico_admin():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"})
    
    data = request.json
    servico = criar_servico(
        nome=data.get('nome'),
        categoria=data.get('categoria'),
        preco=data.get('preco'),
        descricao=data.get('descricao', ''),
        imagem=data.get('imagem', ''),
        tempo_estimado=data.get('tempo_estimado', ''),
        destaque=data.get('destaque', False),
        ordem=len(dados.get('servicos', []))
    )
    return jsonify({"sucesso": True, "mensagem": f"Serviço '{servico['nome']}' criado!"})

@app.route("/api/servico/editar", methods=["POST"])
def api_editar_servico_admin():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"})
    
    data = request.json
    servico_id = data.get('id')
    if not servico_id:
        return jsonify({"sucesso": False, "mensagem": "ID do serviço não informado"})
    
    sucesso = atualizar_servico(servico_id, **data)
    if sucesso:
        return jsonify({"sucesso": True, "mensagem": "Serviço atualizado!"})
    return jsonify({"sucesso": False, "mensagem": "Serviço não encontrado"})

@app.route("/api/servico/remover", methods=["POST"])
def api_remover_servico_admin():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"})
    
    data = request.json
    servico_id = data.get('id')
    if not servico_id:
        return jsonify({"sucesso": False, "mensagem": "ID do serviço não informado"})
    
    sucesso = remover_servico(servico_id)
    if sucesso:
        return jsonify({"sucesso": True, "mensagem": "Serviço removido!"})
    return jsonify({"sucesso": False, "mensagem": "Serviço não encontrado"})

@app.route("/api/pedido/status/atualizar", methods=["POST"])
def api_atualizar_status_pedido():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"})
    
    data = request.json
    pedido = data.get('pedido')
    status = data.get('status')
    
    if not pedido or not status:
        return jsonify({"sucesso": False, "mensagem": "Dados incompletos"})
    
    sucesso = atualizar_status_pedido(pedido, status)
    if sucesso:
        return jsonify({"sucesso": True, "mensagem": f"Status do pedido {pedido} atualizado para {status}"})
    return jsonify({"sucesso": False, "mensagem": "Pedido não encontrado"})

@app.route("/api/cliente/pontos", methods=["POST"])
def api_editar_pontos_cliente():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"})
    
    data = request.json
    usuario_id = data.get('usuario_id')
    pontos = data.get('pontos', 0)
    
    if not usuario_id:
        return jsonify({"sucesso": False, "mensagem": "ID do usuário não informado"})
    
    if pontos > 0:
        sucesso = adicionar_pontos(usuario_id, pontos, "Ajuste manual pelo admin")
    elif pontos < 0:
        sucesso = remover_pontos(usuario_id, abs(pontos), "Ajuste manual pelo admin")
    else:
        return jsonify({"sucesso": True, "mensagem": "Nenhuma alteração"})
    
    if sucesso:
        return jsonify({"sucesso": True, "mensagem": f"Pontos {'adicionados' if pontos > 0 else 'removidos'}!"})
    return jsonify({"sucesso": False, "mensagem": "Usuário não encontrado"})

@app.route("/api/config/loja", methods=["POST"])
def api_config_loja():
    if 'usuario' not in session or not session['usuario'].get('eh_admin'):
        return jsonify({"sucesso": False, "mensagem": "Acesso negado"})
    
    data = request.json
    config = dados.get("config_loja", {})
    
    for chave, valor in data.items():
        if chave in config:
            config[chave] = valor
        elif chave == "redes_sociais" and isinstance(valor, dict):
            for sub_chave, sub_valor in valor.items():
                config["redes_sociais"][sub_chave] = sub_valor
    
    dados["config_loja"] = config
    salvar_dados_github("Configurações da loja atualizadas")
    return jsonify({"sucesso": True, "mensagem": "Configurações salvas!"})

# ========================
# FUNÇÕES ANTI-SPAM E IGNORADOS
# ========================

def verificar_comando_ignorado(conteudo: str) -> bool:
    """Verifica se a mensagem é um comando ignorado (não conta como spam e NÃO ganha XP)"""
    conteudo_lower = conteudo.lower().strip()
    comandos_ignorados = dados.get("anti_spam", {}).get("comandos_ignorados", [])
    
    for comando in comandos_ignorados:
        if conteudo_lower.startswith(comando.lower()):
            return True
        if conteudo_lower == comando.lower():
            return True
    
    return False

def verificar_cargo_ignorado(member: discord.Member) -> bool:
    """Verifica se o membro tem cargo que ignora o anti-spam"""
    cargos_ignorados = dados.get("anti_spam", {}).get("cargos_ignorados", [])
    cargos_membro = [role.name for role in member.roles]
    for cargo_ignorado in cargos_ignorados:
        if cargo_ignorado in cargos_membro:
            return True
    return False

def limpar_mensagens_antigas(user_id: int):
    """Remove mensagens mais antigas que o intervalo configurado"""
    if user_id not in mensagens_recentes:
        return
    
    intervalo = dados.get("anti_spam", {}).get("intervalo_segundos", 5)
    agora = time.time()
    mensagens_recentes[user_id] = [
        ts for ts in mensagens_recentes[user_id] 
        if agora - ts < intervalo
    ]
    
    if not mensagens_recentes[user_id]:
        del mensagens_recentes[user_id]

def registrar_mensagem(user_id: int) -> int:
    """Registra uma mensagem e retorna quantas mensagens o usuário enviou no intervalo"""
    agora = time.time()
    
    if user_id not in mensagens_recentes:
        mensagens_recentes[user_id] = []
    
    mensagens_recentes[user_id].append(agora)
    limpar_mensagens_antigas(user_id)
    
    return len(mensagens_recentes.get(user_id, []))

async def aplicar_mute(member: discord.Member, duracao_minutos: int = 2):
    """Aplica mute temporário no membro"""
    guild = member.guild
    
    mute_role = discord.utils.get(guild.roles, name="Muted")
    
    if not mute_role:
        try:
            mute_role = await guild.create_role(name="Muted", permissions=discord.Permissions.none())
            for channel in guild.channels:
                try:
                    await channel.set_permissions(mute_role, send_messages=False, add_reactions=False, speak=False)
                except:
                    pass
            print(f"✅ Cargo 'Muted' criado no servidor {guild.name}")
        except Exception as e:
            print(f"❌ Erro ao criar cargo de mute: {e}")
            return False
    
    try:
        await member.add_roles(mute_role, reason=f"Anti-spam: {duracao_minutos} minutos de mute")
        
        async def remover_mute():
            await asyncio.sleep(duracao_minutos * 60)
            try:
                await member.remove_roles(mute_role, reason="Fim do mute por spam")
            except:
                pass
        
        asyncio.create_task(remover_mute())
        return True
    except Exception as e:
        print(f"❌ Erro ao aplicar mute: {e}")
        return False

async def deletar_mensagens_spam(member: discord.Member, channel: discord.TextChannel, quantidade: int):
    """Deleta as mensagens de spam do usuário"""
    if not dados.get("anti_spam", {}).get("deletar_mensagens", True):
        return
    
    try:
        async for msg in channel.history(limit=quantidade + 5):
            if msg.author == member:
                try:
                    await msg.delete()
                    await asyncio.sleep(0.5)
                except:
                    pass
    except:
        pass

async def remover_xp_por_spam(member: discord.Member):
    """Remove XP do usuário por spam"""
    if not dados.get("anti_spam", {}).get("remover_xp", True):
        return False
    
    uid = str(member.id)
    penalidade = dados.get("anti_spam", {}).get("xp_penalidade", 50)
    xp_atual = dados.get("xp", {}).get(uid, 0)
    
    novo_xp = max(0, xp_atual - penalidade)
    dados["xp"][uid] = novo_xp
    
    novo_nivel = xp_para_nivel(novo_xp)
    dados["nivel"][uid] = novo_nivel
    
    salvar_dados_github(f"Anti-spam: {penalidade} XP removido de {member.name}")
    
    return True

# ========================
# SISTEMA DE FILA
# ========================

def obter_dados_fila():
    dados.setdefault("fila", {
        "nome": "Fila de Serviços",
        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
        "entradas": [],
        "historico": []
    })
    return dados["fila"]

def salvar_fila():
    return salvar_dados_github("Atualização da fila")

def adicionar_fila(nome_usuario: str, servico: str, jogo: str = "", usuario_id: str = None):
    fila = obter_dados_fila()
    
    if not fila["configuracoes"]["aberta"]:
        return False, "Fila está fechada no momento"
    
    if len(fila["entradas"]) >= fila["configuracoes"]["tamanho_maximo"]:
        return False, "Fila está cheia"
    
    for entrada in fila["entradas"]:
        if entrada["nome_usuario"].lower() == nome_usuario.lower():
            return False, f"{nome_usuario} já está na fila"
    
    entrada = {
        "id": str(int(datetime.now().timestamp() * 1000)),
        "nome_usuario": nome_usuario,
        "servico": servico,
        "jogo": jogo,
        "usuario_id": usuario_id or nome_usuario,
        "timestamp": agora_br().isoformat(),
        "status": "aguardando",
        "posicao": len(fila["entradas"]) + 1
    }
    
    fila["entradas"].append(entrada)
    atualizar_posicoes(fila["entradas"])
    salvar_fila()
    adicionar_log(f"fila_adicionar: {nome_usuario} - {servico} - {jogo}")
    return True, entrada

def remover_fila(entrada_id: str):
    fila = obter_dados_fila()
    
    for i, entrada in enumerate(fila["entradas"]):
        if entrada["id"] == entrada_id:
            removido = fila["entradas"].pop(i)
            removido["removido_em"] = agora_br().isoformat()
            fila["historico"].append(removido)
            if len(fila["historico"]) > 100:
                fila["historico"] = fila["historico"][-100:]
            atualizar_posicoes(fila["entradas"])
            salvar_fila()
            adicionar_log(f"fila_remover: {removido['nome_usuario']}")
            return True, removido
    return False, None

def atualizar_posicoes(entradas):
    for i, entrada in enumerate(entradas):
        entrada["posicao"] = i + 1
        entrada["status"] = "aguardando"

def mover_cima(entrada_id: str):
    fila = obter_dados_fila()
    entradas = fila["entradas"]
    for i, entrada in enumerate(entradas):
        if entrada["id"] == entrada_id and i > 0:
            entradas[i], entradas[i-1] = entradas[i-1], entradas[i]
            atualizar_posicoes(entradas)
            salvar_fila()
            return True, entrada
    return False, None

def mover_baixo(entrada_id: str):
    fila = obter_dados_fila()
    entradas = fila["entradas"]
    for i, entrada in enumerate(entradas):
        if entrada["id"] == entrada_id and i < len(entradas) - 1:
            entradas[i], entradas[i+1] = entradas[i+1], entradas[i]
            atualizar_posicoes(entradas)
            salvar_fila()
            return True, entrada
    return False, None

def concluir_servico(entrada_id: str):
    fila = obter_dados_fila()
    for i, entrada in enumerate(fila["entradas"]):
        if entrada["id"] == entrada_id:
            removido = fila["entradas"].pop(i)
            removido["status"] = "concluido"
            removido["concluido_em"] = agora_br().isoformat()
            fila["historico"].append(removido)
            atualizar_posicoes(fila["entradas"])
            salvar_fila()
            adicionar_log(f"fila_concluir: {removido['nome_usuario']}")
            return True, removido
    return False, None

def limpar_fila():
    fila = obter_dados_fila()
    for entrada in fila["entradas"]:
        entrada["status"] = "limpo"
        entrada["limpo_em"] = agora_br().isoformat()
        fila["historico"].append(entrada)
    fila["entradas"] = []
    salvar_fila()
    adicionar_log("fila_limpa")
    return True

def alternar_fila(aberto: bool = None):
    fila = obter_dados_fila()
    if aberto is None:
        fila["configuracoes"]["aberta"] = not fila["configuracoes"]["aberta"]
    else:
        fila["configuracoes"]["aberta"] = aberto
    salvar_fila()
    return fila["configuracoes"]["aberta"]

def definir_tamanho_maximo(tamanho: int):
    fila = obter_dados_fila()
    fila["configuracoes"]["tamanho_maximo"] = max(1, min(tamanho, 100))
    salvar_fila()
    return fila["configuracoes"]["tamanho_maximo"]

def definir_nome_fila(nome: str):
    fila = obter_dados_fila()
    fila["nome"] = nome[:50]
    salvar_fila()
    return fila["nome"]

# ========================
# FUNÇÕES PARA LINKS DA FILA
# ========================
def obter_links_fila():
    dados.setdefault("links_fila", {"discord_convite": "", "botoes_precos": []})
    return dados["links_fila"]

def salvar_links_fila(discord_convite: str):
    dados["links_fila"]["discord_convite"] = discord_convite or ""
    return salvar_dados_github("Links da fila atualizados")

def adicionar_botao_preco(nome: str, url: str):
    if not nome or not url:
        return False
    dados["links_fila"].setdefault("botoes_precos", [])
    dados["links_fila"]["botoes_precos"].append({"nome": nome[:30], "url": url[:500]})
    return salvar_dados_github(f"Botão de preço adicionado: {nome}")

def remover_botao_preco(index: int):
    botoes = dados["links_fila"].get("botoes_precos", [])
    if 0 <= index < len(botoes):
        removido = botoes.pop(index)
        salvar_dados_github(f"Botão de preço removido: {removido['nome']}")
        return True
    return False

def atualizar_botao_preco(index: int, nome: str, url: str):
    botoes = dados["links_fila"].get("botoes_precos", [])
    if 0 <= index < len(botoes):
        botoes[index] = {"nome": nome[:30], "url": url[:500]}
        salvar_dados_github(f"Botão de preço atualizado: {nome}")
        return True
    return False

# ========================
# SISTEMA DE AÇÕES DO SITE
# ========================
def executar_acao_bot(tipo_acao, **kwargs):
    acoes_fila_bot.append({
        "tipo": tipo_acao,
        "dados": kwargs,
        "timestamp": agora_br().isoformat()
    })
    print(f"🤖 [AÇÃO BOT] Adicionada ação: {tipo_acao}")
    return True

async def executar_acao_bot_interno(acao):
    tipo_acao = acao["tipo"]
    dados_acao = acao["dados"]
    
    print(f"\n{'='*50}")
    print(f"🤖 EXECUTANDO AÇÃO: {tipo_acao}")
    print(f"{'='*50}")
    
    if not bot.is_ready():
        print("❌ Bot não está pronto!")
        return False
    
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID else None
    if not guild:
        print(f"❌ Servidor {GUILD_ID} não encontrado!")
        return False
    
    try:
        if tipo_acao == "criar_embed":
            canal_id = int(dados_acao["canal_id"])
            canal = guild.get_channel(canal_id)
            if not canal:
                return False
            
            cor = discord.Color.blue()
            if dados_acao.get('cor'):
                try:
                    cor_hex = dados_acao['cor'].replace('#', '')
                    cor = discord.Color(int(cor_hex, 16))
                except:
                    pass
            
            embed = discord.Embed(
                title=dados_acao["titulo"],
                description=dados_acao["corpo"],
                color=cor
            )
            
            if dados_acao.get('url_imagem'):
                embed.set_image(url=dados_acao['url_imagem'])
            
            texto_mencao = ""
            if dados_acao.get('mencao') == 'everyone':
                texto_mencao = "@everyone"
            elif dados_acao.get('mencao') == 'here':
                texto_mencao = "@here"
            
            await canal.send(content=texto_mencao, embed=embed)
            print(f"✅ Embed enviada para #{canal.name}")
            return True
        
        elif tipo_acao == "criar_reacao_cargo":
            canal_id = int(dados_acao["canal_id"])
            canal = guild.get_channel(canal_id)
            if not canal:
                return False
            
            mensagem = await canal.send(dados_acao["conteudo"])
            mensagem_id = str(mensagem.id)
            
            pares_str = dados_acao.get("emoji_cargo", "")
            pares = []
            par_atual = ""
            contador_chaves = 0
            
            for char in pares_str:
                if char == '<':
                    contador_chaves += 1
                elif char == '>':
                    contador_chaves -= 1
                if char == ',' and contador_chaves == 0:
                    if par_atual.strip():
                        pares.append(par_atual.strip())
                        par_atual = ""
                else:
                    par_atual += char
            if par_atual.strip():
                pares.append(par_atual.strip())
            
            EMOJI_RE = re.compile(r"<a?:([a-zA-Z0-9_]+):([0-9]+)>")
            EMOJI_NOME_RE = re.compile(r":([a-zA-Z0-9_]+):")
            
            def processar_emoji_str(emoji_str, guild):
                if not emoji_str:
                    return None
                emoji_str = emoji_str.strip()
                m = EMOJI_RE.match(emoji_str)
                if m:
                    nome, id_str = m.groups()
                    try:
                        eid = int(id_str)
                        animado = emoji_str.startswith('<a:')
                        if guild:
                            e = discord.utils.get(guild.emojis, id=eid)
                            if e:
                                return e
                        return discord.PartialEmoji(name=nome, id=eid, animated=animado)
                    except:
                        pass
                m2 = EMOJI_NOME_RE.match(emoji_str)
                if m2:
                    nome_emoji = m2.group(1)
                    if guild:
                        emoji = discord.utils.get(guild.emojis, name=nome_emoji)
                        if emoji:
                            return emoji
                    emojis_padrao = {
                        "thumbsup": "👍", "thumbsdown": "👎", "check": "✅", "x": "❌",
                        "warning": "⚠️", "exclamation": "❗", "question": "❓", "star": "⭐",
                        "heart": "❤️", "fire": "🔥", "rocket": "🚀", "tada": "🎉"
                    }
                    if nome_emoji.lower() in emojis_padrao:
                        return emojis_padrao[nome_emoji.lower()]
                    return emoji_str
                return emoji_str
            
            dados_reacoes = {}
            for par in pares:
                par = par.strip()
                if not par:
                    continue
                if ":" in par:
                    try:
                        emoji_str, nome_cargo = par.split(":", 1)
                        cargo = discord.utils.get(guild.roles, name=nome_cargo.strip())
                        if not cargo:
                            continue
                        emoji_processado = processar_emoji_str(emoji_str.strip(), guild)
                        if not emoji_processado:
                            continue
                        if isinstance(emoji_processado, (discord.Emoji, discord.PartialEmoji)):
                            await mensagem.add_reaction(emoji_processado)
                            chave = str(emoji_processado.id)
                        else:
                            await mensagem.add_reaction(emoji_processado)
                            chave = str(emoji_processado)
                        dados_reacoes[chave] = str(cargo.id)
                    except:
                        continue
            
            if dados_reacoes:
                dados.setdefault("reacoes_cargos", {})[mensagem_id] = dados_reacoes
                salvar_dados_github("Reação cargo via site")
                return True
            else:
                try:
                    await mensagem.delete()
                except:
                    pass
                return False
        
        elif tipo_acao == "criar_botoes_cargo":
            canal_id = int(dados_acao["canal_id"])
            canal = guild.get_channel(canal_id)
            if not canal:
                return False
            
            pares = dados_acao.get("cargos", "").split(",")
            dicionario_botoes = {}
            for par in pares:
                if ":" in par:
                    try:
                        nome_botao, nome_cargo = par.split(":", 1)
                        cargo = discord.utils.get(guild.roles, name=nome_cargo.strip())
                        if cargo:
                            dicionario_botoes[nome_botao.strip()] = cargo.id
                    except:
                        pass
            
            if dicionario_botoes:
                class PersistentRoleButton(ui.Button):
                    def __init__(self, label: str, cargo_id: int, mensagem_id: int):
                        super().__init__(label=label, style=ButtonStyle.primary)
                        self.cargo_id = cargo_id
                        self.mensagem_id = mensagem_id
                    async def callback(self, interaction: Interaction):
                        guild = interaction.guild
                        membro = interaction.user
                        cargo = guild.get_role(self.cargo_id)
                        if not cargo:
                            await interaction.response.send_message("Cargo não encontrado.", ephemeral=True)
                            return
                        if cargo in membro.roles:
                            await membro.remove_roles(cargo, reason="Botão de cargo")
                            await interaction.response.send_message(f"Você **removeu** o cargo {cargo.mention}.", ephemeral=True)
                        else:
                            await membro.add_roles(cargo, reason="Botão de cargo")
                            await interaction.response.send_message(f"Você **recebeu** o cargo {cargo.mention}.", ephemeral=True)
                        adicionar_log(f"botao_cargo: usuario={membro.id} cargo={cargo.id}")
                
                class PersistentRoleButtonView(ui.View):
                    def __init__(self, mensagem_id: int, dicionario_botoes: dict):
                        super().__init__(timeout=None)
                        self.mensagem_id = mensagem_id
                        for label, cargo_id in dicionario_botoes.items():
                            self.add_item(PersistentRoleButton(label=label, cargo_id=cargo_id, mensagem_id=mensagem_id))
                
                view = PersistentRoleButtonView(0, dicionario_botoes)
                enviado = await canal.send(dados_acao["conteudo"], view=view)
                view.mensagem_id = enviado.id
                for item in view.children:
                    if isinstance(item, PersistentRoleButton):
                        item.mensagem_id = enviado.id
                dados.setdefault("botoes_cargos", {})[str(enviado.id)] = dicionario_botoes
                salvar_dados_github("Botões de cargo via site")
                return True
            return False
        
        elif tipo_acao == "advertir_membro":
            membro_id = int(dados_acao["membro_id"])
            membro = guild.get_member(membro_id)
            if not membro:
                return False
            
            entrada = {
                "por": "admin_site",
                "motivo": dados_acao["motivo"],
                "ts": agora_br().strftime("%d/%m/%Y %H:%M"),
                "admin": dados_acao.get('admin', 'Admin')
            }
            dados.setdefault("advertencias", {}).setdefault(str(membro.id), []).append(entrada)
            salvar_dados_github(f"Advertência via site: {membro.display_name}")
            return True
        
        elif tipo_acao == "configurar_boas_vindas":
            config = dados.setdefault("config", {})
            if 'canal_id' in dados_acao:
                config["canal_boas_vindas"] = dados_acao['canal_id']
            if 'mensagem' in dados_acao:
                config["mensagem_boas_vindas"] = dados_acao['mensagem']
            if 'imagem_url' in dados_acao:
                config["fundo_boas_vindas"] = dados_acao['imagem_url']
            salvar_dados_github("Config boas-vindas atualizada")
            return True
        
        elif tipo_acao == "configurar_xp":
            config = dados.setdefault("config", {})
            if 'taxa' in dados_acao:
                config["taxa_xp"] = dados_acao['taxa']
            if 'canal_id' in dados_acao:
                config["canal_levelup"] = dados_acao['canal_id']
            salvar_dados_github("Config XP atualizada")
            return True
        
        elif tipo_acao == "configurar_comandos":
            config = dados.setdefault("config", {})
            if 'canal_perfil' in dados_acao:
                canal_perfil_atual = config.get("canal_perfil")
                novo_canal_perfil = dados_acao['canal_perfil']
                if novo_canal_perfil and canal_perfil_atual == novo_canal_perfil:
                    config["canal_perfil"] = None
                else:
                    config["canal_perfil"] = novo_canal_perfil if novo_canal_perfil else None
            
            if 'canal_rank' in dados_acao:
                canal_rank_atual = config.get("canal_rank")
                novo_canal_rank = dados_acao['canal_rank']
                if novo_canal_rank and canal_rank_atual == novo_canal_rank:
                    config["canal_rank"] = None
                else:
                    config["canal_rank"] = novo_canal_rank if novo_canal_rank else None
            
            salvar_dados_github("Config canais de comandos atualizada")
            return True
        
        elif tipo_acao == "adicionar_cargo_nivel":
            dados.setdefault("cargos_nivel", {})[str(dados_acao['nivel'])] = dados_acao['cargo_id']
            salvar_dados_github(f"Cargo para nível {dados_acao['nivel']} adicionado")
            return True
        
        elif tipo_acao == "remover_cargo_nivel":
            nivel = str(dados_acao['nivel'])
            if nivel in dados.get("cargos_nivel", {}):
                del dados["cargos_nivel"][nivel]
                salvar_dados_github(f"Cargo do nível {nivel} removido")
            return True
        
        elif tipo_acao == "alternar_bloqueio_links":
            canal_id = int(dados_acao["canal_id"])
            canais = dados.setdefault("canais_links_bloqueados", [])
            if canal_id in canais:
                canais.remove(canal_id)
            else:
                canais.append(canal_id)
            salvar_dados_github(f"Bloqueio de links alternado no canal {canal_id}")
            return True
        
        elif tipo_acao == "configurar_anti_spam":
            anti_spam = dados.setdefault("anti_spam", {})
            if 'ativado' in dados_acao:
                anti_spam["ativado"] = dados_acao['ativado']
            if 'limite_mensagens' in dados_acao:
                anti_spam["limite_mensagens"] = dados_acao['limite_mensagens']
            if 'intervalo_segundos' in dados_acao:
                anti_spam["intervalo_segundos"] = dados_acao['intervalo_segundos']
            if 'tempo_mute_minutos' in dados_acao:
                anti_spam["tempo_mute_minutos"] = dados_acao['tempo_mute_minutos']
            if 'remover_xp' in dados_acao:
                anti_spam["remover_xp"] = dados_acao['remover_xp']
            if 'xp_penalidade' in dados_acao:
                anti_spam["xp_penalidade"] = dados_acao['xp_penalidade']
            if 'deletar_mensagens' in dados_acao:
                anti_spam["deletar_mensagens"] = dados_acao['deletar_mensagens']
            if 'cargos_ignorados' in dados_acao:
                anti_spam["cargos_ignorados"] = [c.strip() for c in dados_acao['cargos_ignorados'].split(",") if c.strip()]
            if 'comandos_ignorados' in dados_acao:
                anti_spam["comandos_ignorados"] = [c.strip() for c in dados_acao['comandos_ignorados'].split(",") if c.strip()]
            salvar_dados_github("Config anti-spam atualizada")
            return True
        
        else:
            print(f"❌ Tipo de ação desconhecido: {tipo_acao}")
            return False
    
    except Exception as e:
        print(f"❌ Erro: {e}")
        return False

async def processar_acoes_bot_continuo():
    global processador_acoes_rodando
    
    print("\n" + "="*60)
    print("🚀 PROCESSADOR DE AÇÕES INICIADO")
    print("="*60)
    
    processador_acoes_rodando = True
    
    if not bot.is_ready():
        await bot.wait_until_ready()
        await asyncio.sleep(2)
    
    while processador_acoes_rodando and not bot.is_closed():
        try:
            if acoes_fila_bot:
                acao = acoes_fila_bot.pop(0)
                await executar_acao_bot_interno(acao)
            await asyncio.sleep(1)
        except Exception as e:
            print(f"⚠️ Erro no processador: {e}")
            await asyncio.sleep(5)
    
    print("⏹️ PROCESSADOR DE AÇÕES ENCERRADO")

def iniciar_processador_acoes():
    global processador_acoes_task, processador_acoes_rodando
    if processador_acoes_rodando:
        return False
    try:
        processador_acoes_task = bot.loop.create_task(processar_acoes_bot_continuo())
        print("✅ Processador de ações iniciado!")
        return True
    except Exception as e:
        print(f"❌ Erro ao iniciar processador: {e}")
        return False

# ========================
# ROTAS DE LOGIN (MANTIDAS)
# ========================

@app.route("/login")
def login():
    if not CLIENT_ID or not CLIENT_SECRET:
        return "Erro: CLIENT_ID ou CLIENT_SECRET não configurados.", 500
    
    url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
    return redirect(url)

@app.route("/callback")
def callback():
    if not CLIENT_ID or not CLIENT_SECRET:
        return "Erro de configuração.", 500
    
    code = request.args.get('code')
    if not code:
        return "Erro: código não recebido", 400
    
    try:
        dados_req = {
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': REDIRECT_URI,
            'scope': 'identify guilds'
        }
        
        r = requests.post('https://discord.com/api/oauth2/token', data=dados_req)
        if r.status_code != 200:
            return f"Erro ao obter token: {r.text[:100]}", 400
        
        access_token = r.json()['access_token']
        
        user_r = requests.get('https://discord.com/api/users/@me', headers={'Authorization': f'Bearer {access_token}'})
        if user_r.status_code != 200:
            return "Erro ao obter informações", 400
        
        user_data = user_r.json()
        
        guilds_r = requests.get('https://discord.com/api/users/@me/guilds', headers={'Authorization': f'Bearer {access_token}'})
        guilds = guilds_r.json() if guilds_r.status_code == 200 else []
        
        is_admin = False
        for guild in guilds:
            if str(guild['id']) == GUILD_ID and (guild['permissions'] & 0x8):
                is_admin = True
                break
        
        # Criar usuário no sistema se não existir
        uid = str(user_data['id'])
        if uid not in dados.get("usuarios", {}):
            criar_usuario(uid, user_data['username'], user_data.get('avatar'))
        else:
            # Atualizar dados do usuário
            dados["usuarios"][uid]["nome"] = user_data['username']
            dados["usuarios"][uid]["avatar"] = user_data.get('avatar')
            salvar_dados_github(f"Usuário atualizado: {user_data['username']}")
        
        session['usuario'] = {
            'id': user_data['id'],
            'nome_usuario': user_data['username'],
            'avatar': user_data.get('avatar'),
            'eh_admin': is_admin
        }
        
        return redirect(url_for('home'))
        
    except Exception as e:
        return f"Erro interno: {str(e)}", 500

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('home'))

# ========================
# ROTAS DA FILA (MANTIDAS)
# ========================

@app.route("/fila")
def fila_publica():
    fila = obter_dados_fila()
    links = obter_links_fila()
    botoes_precos = links.get("botoes_precos", [])
    
    botoes_html = ""
    for botao in botoes_precos:
        botoes_html += f'<a href="{escape_html(botao["url"])}" target="_blank" class="btn-link btn-link-precos">💰 {escape_html(botao["nome"])}</a>'
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="30">
        <title>{escape_html(fila["nome"])}</title>
        <style>
            * {{ margin:0; padding:0; box-sizing:border-box; }}
            body {{ font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); min-height:100vh; padding:20px; color:#fff; }}
            .container {{ max-width:800px; margin:0 auto; }}
            .header {{ text-align:center; margin-bottom:30px; padding:20px; background:rgba(0,0,0,0.5); border-radius:20px; }}
            h1 {{ background: linear-gradient(135deg, #ff6b6b, #ffd93d); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
            .status {{ display:inline-block; padding:5px 15px; border-radius:20px; }}
            .status-aberta {{ background:#00b894; }}
            .status-fechada {{ background:#d63031; }}
            .links-container {{ display: flex; justify-content: center; gap: 20px; margin: 20px 0; flex-wrap: wrap; }}
            .btn-link {{ display: inline-flex; align-items: center; gap: 10px; padding: 12px 24px; border-radius: 30px; text-decoration: none; font-weight: bold; transition: all 0.3s; }}
            .btn-link-discord {{ background: #5865F2; color: white; }}
            .btn-link-discord:hover {{ background: #4752C4; transform: translateY(-2px); }}
            .btn-link-precos {{ background: #f59e0b; color: white; }}
            .btn-link-precos:hover {{ background: #d97706; transform: translateY(-2px); }}
            .lista-fila {{ background:rgba(0,0,0,0.4); border-radius:20px; overflow:hidden; }}
            .cabecalho-fila {{ display:grid; grid-template-columns:60px 1fr 1fr 1fr 80px; padding:15px; background:rgba(255,255,255,0.1); font-weight:bold; }}
            .item-fila {{ display:grid; grid-template-columns:60px 1fr 1fr 1fr 80px; padding:12px 15px; border-bottom:1px solid rgba(255,255,255,0.1); }}
            .posicao {{ font-weight:bold; color:#ffd93d; }}
            .servico {{ color:#a8e6cf; }}
            .jogo {{ color:#ffb347; }}
            .vazio {{ text-align:center; padding:40px; }}
            .footer {{ text-align:center; margin-top:20px; font-size:0.8rem; color:#888; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📋 {escape_html(fila["nome"])}</h1>
                <span class="status status-{'aberta' if fila['configuracoes']['aberta'] else 'fechada'}">{'🟢 ABERTA' if fila['configuracoes']['aberta'] else '🔴 FECHADA'}</span>
                <div>📊 {len(fila["entradas"])} / {fila["configuracoes"]["tamanho_maximo"]} pessoas</div>
            </div>
            
            <div class="links-container">
                {'<a href="' + escape_html(links["discord_convite"]) + '" target="_blank" class="btn-link btn-link-discord">💬 Entrar no Discord</a>' if links.get("discord_convite") else ''}
                {botoes_html}
            </div>
            
            <div class="lista-fila">
                <div class="cabecalho-fila"><span>#</span><span>Jogador</span><span>Serviço</span><span>Jogo</span><span></span></div>
                {''.join(f'<div class="item-fila"><span class="posicao">{e["posicao"]}</span><span>{escape_html(e["nome_usuario"])}</span><span class="servico">{escape_html(e["servico"])}</span><span class="jogo">{escape_html(e.get("jogo", ""))}</span><span>⏳</span></div>' for e in fila["entradas"]) or '<div class="vazio">✨ Ninguém na fila</div>'}
            </div>
            <div class="footer">Atualizado a cada 30s • {agora_br().strftime("%d/%m/%Y %H:%M:%S")}</div>
        </div>
    </body>
    </html>
    '''

@app.route("/fila/embed")
def fila_embed():
    fila = obter_dados_fila()
    entradas_html = ""
    for e in fila["entradas"][:10]:
        entradas_html += f'<div style="display:flex;justify-content:space-between;padding:5px 0;"><span style="color:#ffd93d;">#{e["posicao"]}</span><span>{escape_html(e["nome_usuario"])}</span><span style="color:#a8e6cf;">{escape_html(e["servico"])}</span><span style="color:#ffb347;">{escape_html(e.get("jogo", ""))}</span></div>'
    if not entradas_html:
        entradas_html = '<div style="text-align:center;padding:20px;">✨ Fila vazia</div>'
    return f'''
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><meta http-equiv="refresh" content="15"><style>body{{margin:0;padding:10px;background:transparent;color:white;font-size:14px;}}.container{{background:rgba(0,0,0,0.7);border-radius:10px;padding:10px;}}</style></head>
    <body><div class="container"><div style="text-align:center;margin-bottom:10px;"><strong>📋 {escape_html(fila["nome"])}</strong><span style="background:{'#00b894' if fila['configuracoes']['aberta'] else '#d63031'};padding:2px 8px;border-radius:10px;margin-left:5px;">{'ABERTA' if fila['configuracoes']['aberta'] else 'FECHADA'}</span></div>{entradas_html}<div style="text-align:center;margin-top:8px;font-size:10px;color:#888;">Total: {len(fila["entradas"])}</div></div></body>
    </html>
    '''

@app.route("/fila/api")
def fila_api():
    fila = obter_dados_fila()
    return jsonify({
        "sucesso": True,
        "fila": {
            "nome": fila["nome"],
            "aberta": fila["configuracoes"]["aberta"],
            "tamanho_maximo": fila["configuracoes"]["tamanho_maximo"],
            "contagem": len(fila["entradas"]),
            "entradas": [{"posicao": e["posicao"], "nome_usuario": e["nome_usuario"], "servico": e["servico"], "jogo": e.get("jogo", ""), "timestamp": e["timestamp"], "id": e["id"]} for e in fila["entradas"]]
        }
    })

# ========================
# APIs DA FILA (MANTIDAS)
# ========================

@app.route("/api/fila/adicionar", methods=["POST"])
def api_fila_adicionar():
    dados_req = request.json
    nome = dados_req.get("nome_usuario", "").strip()
    servico = dados_req.get("servico", "").strip()
    jogo = dados_req.get("jogo", "").strip()
    if not nome or not servico:
        return jsonify({"sucesso": False, "mensagem": "Nome e serviço são obrigatórios"})
    sucesso, resultado = adicionar_fila(nome, servico, jogo)
    return jsonify({"sucesso": sucesso, "mensagem": f"{nome} adicionado!" if sucesso else resultado})

@app.route("/api/fila/remover", methods=["POST"])
def api_fila_remover():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    sucesso, _ = remover_fila(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/mover-cima", methods=["POST"])
def api_fila_mover_cima():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    sucesso, _ = mover_cima(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/mover-baixo", methods=["POST"])
def api_fila_mover_baixo():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    sucesso, _ = mover_baixo(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/concluir", methods=["POST"])
def api_fila_concluir():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    sucesso, _ = concluir_servico(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})

@app.route("/api/fila/limpar", methods=["POST"])
def api_fila_limpar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    limpar_fila()
    return jsonify({"sucesso": True})

@app.route("/api/fila/configuracoes", methods=["GET", "POST"])
def api_fila_configuracoes():
    if request.method == "GET":
        fila = obter_dados_fila()
        links = obter_links_fila()
        return jsonify({"sucesso": True, "configuracoes": fila["configuracoes"], "nome": fila["nome"], "links": links})
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    if "aberta" in req:
        alternar_fila(req["aberta"])
    if "tamanho_maximo" in req:
        definir_tamanho_maximo(int(req["tamanho_maximo"]))
    if "nome" in req:
        definir_nome_fila(req["nome"])
    if "discord_convite" in req:
        salvar_links_fila(req.get("discord_convite", ""))
    return jsonify({"sucesso": True})

# ========================
# APIs DOS BOTÕES DE PREÇO (MANTIDAS)
# ========================

@app.route("/api/fila/botoes", methods=["GET"])
def api_fila_botoes():
    links = obter_links_fila()
    return jsonify({"sucesso": True, "botoes": links.get("botoes_precos", [])})

@app.route("/api/fila/botoes/adicionar", methods=["POST"])
def api_fila_botoes_adicionar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    nome = req.get("nome", "").strip()
    url = req.get("url", "").strip()
    if not nome or not url:
        return jsonify({"sucesso": False, "mensagem": "Nome e URL são obrigatórios"})
    adicionar_botao_preco(nome, url)
    return jsonify({"sucesso": True, "mensagem": f"Botão '{nome}' adicionado!"})

@app.route("/api/fila/botoes/remover", methods=["POST"])
def api_fila_botoes_remover():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    index = request.json.get("index")
    if index is None:
        return jsonify({"sucesso": False, "mensagem": "Índice não informado"})
    remover_botao_preco(int(index))
    return jsonify({"sucesso": True, "mensagem": "Botão removido!"})

@app.route("/api/fila/botoes/atualizar", methods=["POST"])
def api_fila_botoes_atualizar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    index = req.get("index")
    nome = req.get("nome", "").strip()
    url = req.get("url", "").strip()
    if index is None or not nome or not url:
        return jsonify({"sucesso": False, "mensagem": "Dados incompletos"})
    atualizar_botao_preco(int(index), nome, url)
    return jsonify({"sucesso": True, "mensagem": "Botão atualizado!"})

# ========================
# APIs DE CONFIGURAÇÃO (MANTIDAS)
# ========================

@app.route("/api/servidor/canais")
def api_servidor_canais():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    if not guild:
        return jsonify({"sucesso": False, "canais": []})
    return jsonify({"sucesso": True, "canais": [{"id": str(c.id), "nome": c.name} for c in guild.text_channels]})

@app.route("/api/servidor/cargos")
def api_servidor_cargos():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    if not guild:
        return jsonify({"sucesso": False, "cargos": []})
    return jsonify({"sucesso": True, "cargos": [{"id": str(r.id), "nome": r.name} for r in guild.roles if r.name != "@everyone"]})

@app.route("/api/servidor/membros")
def api_servidor_membros():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    if not guild:
        return jsonify({"sucesso": False, "membros": []})
    membros = [{"id": str(m.id), "nome": m.display_name} for m in guild.members if not m.bot][:100]
    return jsonify({"sucesso": True, "membros": membros})

@app.route("/api/anti_spam", methods=["GET", "POST"])
def api_anti_spam():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        anti_spam = dados.get("anti_spam", {})
        return jsonify({
            "sucesso": True,
            "config": {
                "ativado": anti_spam.get("ativado", True),
                "limite_mensagens": anti_spam.get("limite_mensagens", 5),
                "intervalo_segundos": anti_spam.get("intervalo_segundos", 5),
                "tempo_mute_minutos": anti_spam.get("tempo_mute_minutos", 2),
                "remover_xp": anti_spam.get("remover_xp", True),
                "xp_penalidade": anti_spam.get("xp_penalidade", 50),
                "deletar_mensagens": anti_spam.get("deletar_mensagens", True),
                "cargos_ignorados": ",".join(anti_spam.get("cargos_ignorados", ["Administrador", "Moderador", "Staff", "Dono"])),
                "comandos_ignorados": ",".join(anti_spam.get("comandos_ignorados", [
                    "$w", "$wa", "$wg", "$h", "$ha", "$hg",
                    "$W", "$WA", "$WG", "$H", "$HA", "$HG",
                    "$tu", "$TU", "$dk", "$mmi", "$vote", "$rolls", "$k", "$mu"
                ]))
            }
        })
    
    req = request.json
    executar_acao_bot("configurar_anti_spam", **req)
    return jsonify({"sucesso": True, "mensagem": "Configuração anti-spam salva!"})

@app.route("/api/config/boasvindas", methods=["GET", "POST"])
def api_config_boasvindas():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        config = dados.get("config", {})
        return jsonify({
            "sucesso": True,
            "canal": config.get("canal_boas_vindas", ""),
            "mensagem": config.get("mensagem_boas_vindas", "Olá {member}, seja bem-vindo(a)!"),
            "imagem": config.get("fundo_boas_vindas", "")
        })
    
    req = request.json
    executar_acao_bot("configurar_boas_vindas", **req)
    return jsonify({"sucesso": True, "mensagem": "Configuração salva!"})

@app.route("/api/config/xp", methods=["GET", "POST"])
def api_config_xp():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        config = dados.get("config", {})
        return jsonify({
            "sucesso": True,
            "taxa": config.get("taxa_xp", 3),
            "canal": config.get("canal_levelup", "")
        })
    
    req = request.json
    executar_acao_bot("configurar_xp", **req)
    return jsonify({"sucesso": True, "mensagem": "Configuração salva!"})

@app.route("/api/config/comandos", methods=["GET", "POST"])
def api_config_comandos():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        config = dados.get("config", {})
        return jsonify({
            "sucesso": True,
            "canal_perfil": config.get("canal_perfil", ""),
            "canal_rank": config.get("canal_rank", "")
        })
    
    req = request.json
    executar_acao_bot("configurar_comandos", **req)
    return jsonify({"sucesso": True, "mensagem": "Configuração de comandos salva!"})

@app.route("/api/cargos/nivel", methods=["GET", "POST", "DELETE"])
def api_cargos_nivel():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        return jsonify({"sucesso": True, "cargos": dados.get("cargos_nivel", {})})
    
    elif request.method == "POST":
        req = request.json
        executar_acao_bot("adicionar_cargo_nivel", nivel=req.get('nivel'), cargo_id=req.get('cargo_id'))
        return jsonify({"sucesso": True, "mensagem": "Cargo adicionado!"})
    
    elif request.method == "DELETE":
        nivel = request.args.get('nivel')
        if nivel:
            executar_acao_bot("remover_cargo_nivel", nivel=nivel)
        return jsonify({"sucesso": True, "mensagem": "Cargo removido!"})

@app.route("/api/config/links", methods=["GET", "POST"])
def api_config_links():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    
    if request.method == "GET":
        return jsonify({"sucesso": True, "canais": dados.get("canais_links_bloqueados", [])})
    
    req = request.json
    executar_acao_bot("alternar_bloqueio_links", canal_id=req.get('canal_id'))
    return jsonify({"sucesso": True, "mensagem": "Configuração salva!"})

# ========================
# APIs DE COMANDOS (MANTIDAS)
# ========================

@app.route("/api/comando/embed", methods=["POST"])
def api_comando_embed():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_embed", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Embed criada!" if sucesso else "❌ Falha"})

@app.route("/api/comando/advertir", methods=["POST"])
def api_comando_advertir():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("advertir_membro", membro_id=req.get('membro_id'), motivo=req.get('motivo'), admin=session['usuario']['nome_usuario'])
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Advertência aplicada!" if sucesso else "❌ Falha"})

@app.route("/api/comando/limpar_advertencias", methods=["POST"])
def api_comando_limpar_advertencias():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    membro_id = str(request.json.get('membro_id'))
    if membro_id in dados.get("advertencias", {}):
        dados["advertencias"].pop(membro_id)
        salvar_dados_github(f"Advertências limpas: {membro_id}")
        return jsonify({"sucesso": True, "mensagem": "✅ Advertências removidas!"})
    return jsonify({"sucesso": False, "mensagem": "❌ Membro sem advertências"})

@app.route("/api/reacao_cargo/criar", methods=["POST"])
def api_reacao_cargo_criar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_reacao_cargo", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Reaction role criada!" if sucesso else "❌ Falha"})

@app.route("/api/botoes_cargo/criar", methods=["POST"])
def api_botoes_cargo_criar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_botoes_cargo", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Botões criados!" if sucesso else "❌ Falha"})

@app.route("/api/membro/advertencias")
def api_membro_advertencias():
    membro_id = request.args.get('membro_id')
    if not membro_id:
        return jsonify({"sucesso": False, "advertencias": []})
    warns = dados.get("advertencias", {}).get(str(membro_id), [])
    return jsonify({"sucesso": True, "advertencias": warns})

# ========================
# FUNÇÃO PARA VERIFICAR CANAL PERMITIDO
# ========================

async def verificar_canal_permitido(interaction: discord.Interaction, comando: str) -> bool:
    """Verifica se o comando pode ser usado no canal atual"""
    config = dados.get("config", {})
    canal_permitido = config.get(f"canal_{comando}", None)
    
    if not canal_permitido:
        return True
    
    if str(interaction.channel_id) == str(canal_permitido):
        return True
    
    return False

# ========================
# COMANDOS SLASH DO DISCORD
# ========================

@tree.command(name="perfil", description="Mostra o seu perfil com XP e nível")
@app_commands.describe(membro="Membro para ver o perfil (opcional)")
async def slash_perfil(interaction: discord.Interaction, membro: discord.Member = None):
    if not await verificar_canal_permitido(interaction, "perfil"):
        config = dados.get("config", {})
        canal_permitido = config.get("canal_perfil")
        if canal_permitido:
            canal_menção = f"<#{canal_permitido}>"
        else:
            canal_menção = "nenhum canal configurado"
        await interaction.response.send_message(
            f"❌ O comando `/perfil` só pode ser usado no canal {canal_menção}!",
            ephemeral=True
        )
        return
    
    await interaction.response.defer(thinking=True)
    
    alvo = membro or interaction.user
    uid = str(alvo.id)
    xp = dados.get("xp", {}).get(uid, 0)
    nivel = dados.get("nivel", {}).get(uid, xp_para_nivel(xp))
    
    ranking = sorted(dados.get("xp", {}).items(), key=lambda t: t[1], reverse=True)
    pos = next((i+1 for i, (u, _) in enumerate(ranking) if u == uid), len(ranking))
    
    largura, altura = 900, 200
    img = Image.new("RGBA", (largura, altura), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    
    font_b = ImageFont.truetype(os.path.join(BASE_DIR, "DejaVuSans-Bold.ttf"),32)
    
    font_s = ImageFont.truetype(os.path.join(BASE_DIR, "DejaVuSans.ttf"),22)
    
    try:
        avatar_bytes = await alvo.avatar.read()
        avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
        avatar = avatar.resize((120, 120))
        mask = Image.new("L", (120, 120), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 120, 120), fill=255)
        img.paste(avatar, (20, 40), mask)
    except:
        pass
    
    draw.text((160, 50), alvo.display_name, font=font_b, fill=(0, 255, 255))
    draw.text((largura - 220, 40), f"CLASSIFICAÇÃO #{pos}", font=font_s, fill=(0, 255, 255))
    draw.text((largura - 220, 80), f"NÍVEL {nivel}", font=font_s, fill=(255, 0, 255))
    
    proximo_xp = 100 + nivel * 50
    atual = xp % proximo_xp
    barra_total_w, barra_h = 560, 36
    x0, y0 = 160, 140
    raio = barra_h // 2
    
    draw.rounded_rectangle([x0, y0, x0 + barra_total_w, y0 + barra_h], radius=raio, fill=(50, 50, 50))
    
    preenchimento_w = int(barra_total_w * min(1.0, atual / proximo_xp))
    if preenchimento_w > 0:
        barra_preenchida = Image.new("RGBA", (preenchimento_w, barra_h), (0, 0, 0, 0))
        fill_draw = ImageDraw.Draw(barra_preenchida)
        fill_draw.rounded_rectangle([0, 0, preenchimento_w, barra_h], radius=raio, fill=(0, 200, 255))
        img.paste(barra_preenchida, (x0, y0), barra_preenchida)
    
    texto_xp = f"{atual} / {proximo_xp} XP"
    bbox = draw.textbbox((0, 0), texto_xp, font=font_s)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = x0 + (barra_total_w - text_w) // 2
    text_y = y0 + (barra_h - text_h) // 2
    draw.text((text_x, text_y), texto_xp, font=font_s, fill=(255, 255, 255))
    
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    arquivo = discord.File(buf, filename="perfil.png")
    await interaction.followup.send(file=arquivo)

@tree.command(name="rank", description="Mostra o ranking dos 10 maiores XP")
async def slash_rank(interaction: discord.Interaction):
    if not await verificar_canal_permitido(interaction, "rank"):
        config = dados.get("config", {})
        canal_permitido = config.get("canal_rank")
        if canal_permitido:
            canal_menção = f"<#{canal_permitido}>"
        else:
            canal_menção = "nenhum canal configurado"
        await interaction.response.send_message(
            f"❌ O comando `/rank` só pode ser usado no canal {canal_menção}!",
            ephemeral=True
        )
        return
    
    await interaction.response.defer()
    
    ranking = sorted(dados.get("xp", {}).items(), key=lambda t: t[1], reverse=True)[:10]
    linhas = []
    for i, (uid, xp) in enumerate(ranking, 1):
        user = interaction.guild.get_member(int(uid))
        nome = user.display_name if user else f"Usuário {uid}"
        nivel = dados.get("nivel", {}).get(uid, xp_para_nivel(xp))
        linhas.append(f"{i}. **{nome}** — {xp} XP (Nível {nivel})")
    
    texto = "\n".join(linhas) if linhas else "Sem dados ainda."
    
    embed = discord.Embed(
        title="🏆 Top 10 Ranking de XP",
        description=texto,
        color=discord.Color.gold()
    )
    await interaction.followup.send(embed=embed)

# ========================
# AUTO PING
# ========================
def auto_ping():
    while True:
        try:
            url = os.environ.get("REPLIT_URL") or os.environ.get("SELF_URL")
            if url:
                requests.get(url)
            time.sleep(300)
        except:
            pass

Thread(target=auto_ping, daemon=True).start()

# ========================
# EVENTOS DO BOT
# ========================

@bot.event
async def on_ready():
    print(f"\n{'='*50}")
    print(f"🤖 BOT INICIADO: {bot.user}")
    print(f"{'='*50}")
    
    print("📂 Carregando dados do GitHub...")
    carregar_dados_github()
    
    print("⚙️ Sincronizando comandos slash...")
    try:
        if GUILD_ID:
            await tree.sync(guild=discord.Object(id=int(GUILD_ID)))
            print(f"✅ Comandos sincronizados no servidor")
        else:
            await tree.sync()
            print("✅ Comandos globais sincronizados")
    except Exception as e:
        print(f"❌ Erro ao sincronizar: {e}")
    
    print("🔄 Restaurando botões persistentes...")
    botoes_cargos = dados.get("botoes_cargos", {})
    restaurados = 0
    for msg_id_str, dicionario_botoes in botoes_cargos.items():
        try:
            msg_id = int(msg_id_str)
            for guild in bot.guilds:
                for channel in guild.text_channels:
                    try:
                        mensagem = await channel.fetch_message(msg_id)
                        if mensagem:
                            class PersistentRoleButton(ui.Button):
                                def __init__(self, label: str, cargo_id: int, mensagem_id: int):
                                    super().__init__(label=label, style=ButtonStyle.primary)
                                    self.cargo_id = cargo_id
                                    self.mensagem_id = mensagem_id
                                async def callback(self, interaction: Interaction):
                                    guild = interaction.guild
                                    membro = interaction.user
                                    cargo = guild.get_role(self.cargo_id)
                                    if not cargo:
                                        await interaction.response.send_message("Cargo não encontrado.", ephemeral=True)
                                        return
                                    if cargo in membro.roles:
                                        await membro.remove_roles(cargo, reason="Botão de cargo")
                                        await interaction.response.send_message(f"Você **removeu** o cargo {cargo.mention}.", ephemeral=True)
                                    else:
                                        await membro.add_roles(cargo, reason="Botão de cargo")
                                        await interaction.response.send_message(f"Você **recebeu** o cargo {cargo.mention}.", ephemeral=True)
                                    adicionar_log(f"botao_cargo: usuario={membro.id} cargo={cargo.id}")
                            
                            class PersistentRoleButtonView(ui.View):
                                def __init__(self, mensagem_id: int, dicionario_botoes: dict):
                                    super().__init__(timeout=None)
                                    self.mensagem_id = mensagem_id
                                    for label, cargo_id in dicionario_botoes.items():
                                        self.add_item(PersistentRoleButton(label=label, cargo_id=cargo_id, mensagem_id=mensagem_id))
                            
                            view = PersistentRoleButtonView(msg_id, dicionario_botoes)
                            await mensagem.edit(view=view)
                            restaurados += 1
                            break
                    except:
                        continue
                if restaurados > 0:
                    break
        except:
            pass
    print(f"✅ {restaurados}/{len(botoes_cargos)} botões restaurados")
    
    await asyncio.sleep(2)
    iniciar_processador_acoes()
    
    config = dados.get("config", {})
    links = obter_links_fila()
    print(f"{'='*50}")
    print(f"✨ BOT PRONTO! Comandos: /perfil e /rank")
    print(f"🛡️ Anti-Spam: {'ATIVADO' if dados.get('anti_spam', {}).get('ativado', True) else 'DESATIVADO'}")
    print(f"🚫 Comandos da Mudae: NÃO ganham XP e NÃO contam como spam")
    print(f"📢 Canal do /perfil: {config.get('canal_perfil') or 'TODOS OS CANAIS'}")
    print(f"📢 Canal do /rank: {config.get('canal_rank') or 'TODOS OS CANAIS'}")
    botoes_qtd = len(links.get("botoes_precos", []))
    if links.get('discord_convite') or botoes_qtd > 0:
        print(f"🔗 Links da fila configurados: {botoes_qtd} botão(ões) de preço")
    print(f"💡 Dica: Selecione o mesmo canal duas vezes no painel para remover a restrição!")
    print(f"{'='*50}\n")

@bot.event
async def on_member_join(member: discord.Member):
    ch_id = dados.get("config", {}).get("canal_boas_vindas")
    canal = None
    if ch_id:
        canal = member.guild.get_channel(int(ch_id))
    if not canal:
        canal = discord.utils.get(member.guild.text_channels, name="boas-vindas")
    if not canal:
        return
    
    msg = dados.get("config", {}).get("mensagem_boas_vindas", "Olá {member}, seja bem-vindo(a)!")
    msg = msg.replace("{member}", member.mention)
    
    fundo_url = dados.get("config", {}).get("fundo_boas_vindas", "")
    
    largura, altura = 900, 300
    img = Image.new("RGBA", (largura, altura), (0, 0, 0, 255))
    
    if fundo_url:
        try:
            response = requests.get(fundo_url)
            bg = Image.open(BytesIO(response.content)).convert("RGBA")
            bg = bg.resize((largura, altura))
            img.paste(bg, (0, 0))
        except:
            pass
    
    overlay = Image.new("RGBA", (largura, altura), (50, 50, 50, 150))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    
    try:
        avatar_bytes = await member.avatar.read()
        avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
        avatar = avatar.resize((150, 150))
        mask = Image.new("L", (150, 150), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 150, 150), fill=255)
        img.paste(avatar, (375, 30), mask)
    except:
        pass
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except:
        font = ImageFont.load_default()
        font_s = ImageFont.load_default()
    
    nome = member.display_name
    bbox = draw.textbbox((0, 0), nome, font=font)
    text_x = (largura - (bbox[2] - bbox[0])) // 2
    draw.text((text_x, 200), nome, font=font, fill=(0, 255, 255))
    
    texto_membro = f"Membro #{len(member.guild.members)}"
    bbox2 = draw.textbbox((0, 0), texto_membro, font=font_s)
    text_x2 = (largura - (bbox2[2] - bbox2[0])) // 2
    draw.text((text_x2, 250), texto_membro, font=font_s, fill=(255, 255, 255))
    
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    arquivo = discord.File(buf, filename="welcome.png")
    
    await canal.send(content=msg, file=arquivo)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    msgmap = dados.get("reacoes_cargos", {}).get(str(payload.message_id))
    if not msgmap:
        return
    
    role_id = None
    if payload.emoji.id and str(payload.emoji.id) in msgmap:
        role_id = msgmap[str(payload.emoji.id)]
    elif str(payload.emoji) in msgmap:
        role_id = msgmap[str(payload.emoji)]
    
    if not role_id:
        return
    
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return
    role = guild.get_role(int(role_id))
    if role:
        await member.add_roles(role, reason="Reaction role")

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    msgmap = dados.get("reacoes_cargos", {}).get(str(payload.message_id))
    if not msgmap:
        return
    
    role_id = None
    if payload.emoji.id and str(payload.emoji.id) in msgmap:
        role_id = msgmap[str(payload.emoji.id)]
    elif str(payload.emoji) in msgmap:
        role_id = msgmap[str(payload.emoji)]
    
    if not role_id:
        return
    
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return
    role = guild.get_role(int(role_id))
    if role:
        await member.remove_roles(role, reason="Reaction role")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    
    conteudo = message.content.strip()
    anti_spam_config = dados.get("anti_spam", {})
    
    eh_comando_ignorado = verificar_comando_ignorado(conteudo)
    
    if eh_comando_ignorado:
        await bot.process_commands(message)
        return
    
    if anti_spam_config.get("ativado", True):
        if not verificar_cargo_ignorado(message.author):
            quantidade = registrar_mensagem(message.author.id)
            limite = anti_spam_config.get("limite_mensagens", 5)
            
            if quantidade > limite:
                duracao = anti_spam_config.get("tempo_mute_minutos", 2)
                sucesso = await aplicar_mute(message.author, duracao)
                
                if sucesso:
                    if anti_spam_config.get("deletar_mensagens", True):
                        await deletar_mensagens_spam(message.author, message.channel, quantidade)
                    
                    xp_removido = False
                    if anti_spam_config.get("remover_xp", True):
                        xp_removido = await remover_xp_por_spam(message.author)
                    
                    xp_msg = f" e teve **{anti_spam_config.get('xp_penalidade', 50)} XP removido**" if xp_removido else ""
                    try:
                        await message.author.send(f"⚠️ **Você foi mutado por {duracao} minutos** devido a spam no servidor {message.guild.name}!{xp_msg}\nPor favor, evite enviar muitas mensagens repetidas em um curto período.\n")
                    except:
                        await message.channel.send(f"⚠️ {message.author.mention}, você foi mutado por **{duracao} minutos** por spam!{xp_msg}")
                    
                    adicionar_log(f"anti_spam: {message.author.name} mutado por {duracao} min | {quantidade} msgs em {anti_spam_config.get('intervalo_segundos', 5)}s | XP removido: {xp_removido}")
                
                return
    
    canais_bloqueados = dados.get("canais_links_bloqueados", [])
    if message.channel.id in canais_bloqueados:
        url_pattern = r"https?://[^\s]+"
        if re.search(url_pattern, conteudo):
            cargos_ignorados = {"Administrador", "Moderador"}
            if not any(r.name in cargos_ignorados for r in message.author.roles):
                try:
                    await message.delete()
                    await message.channel.send(f"⚠️ {message.author.mention}, links não são permitidos aqui!")
                except:
                    pass
                return
    
    dados.setdefault("xp", {})
    dados.setdefault("nivel", {})
    
    taxa_xp = dados.get("config", {}).get("taxa_xp", 3)
    ganho_xp = max(1, xp_por_mensagem() // taxa_xp)
    dados["xp"][str(message.author.id)] = dados["xp"].get(str(message.author.id), 0) + ganho_xp
    
    xp_atual = dados["xp"][str(message.author.id)]
    nivel_atual = xp_para_nivel(xp_atual)
    nivel_anterior = dados["nivel"].get(str(message.author.id), 1)
    
    if nivel_atual > nivel_anterior:
        dados["nivel"][str(message.author.id)] = nivel_atual
        
        canal_levelup_id = dados.get("config", {}).get("canal_levelup")
        if canal_levelup_id:
            canal = message.guild.get_channel(int(canal_levelup_id))
            if canal:
                await canal.send(f"🎉 {message.author.mention} subiu para o nível **{nivel_atual}**!")
        
        cargo_id = dados.get("cargos_nivel", {}).get(str(nivel_atual))
        if cargo_id:
            cargo = message.guild.get_role(int(cargo_id))
            if cargo:
                try:
                    await message.author.add_roles(cargo, reason=f"Nível {nivel_atual}")
                except:
                    pass
    
    try:
        salvar_dados_github("XP update")
    except:
        pass
    
    await bot.process_commands(message)

# ========================
# INICIAR BOT E FLASK
# ========================

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

Thread(target=run_flask, daemon=True).start()

if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        print("Erro ao iniciar o bot:", e)