from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text, JSON, ForeignKey, Numeric
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Usuario(db.Model):
    __tablename__ = 'usuarios'
    
    id = Column(Integer, primary_key=True)
    discord_id = Column(String(50), unique=True, nullable=False)
    nome = Column(String(100), nullable=False)
    avatar = Column(String(200))
    pontos = Column(Integer, default=0)
    data_cadastro = Column(DateTime, default=datetime.now)
    
    pedidos = relationship('Pedido', back_populates='usuario')
    transacoes_pontos = relationship('TransacaoPontos', back_populates='usuario')

class Categoria(db.Model):
    __tablename__ = 'categorias'
    
    id = Column(Integer, primary_key=True)
    nome = Column(String(50), nullable=False)
    icone = Column(String(50))
    status = Column(Boolean, default=True)
    ordem = Column(Integer, default=0)
    
    servicos = relationship('Servico', back_populates='categoria')

class Servico(db.Model):
    __tablename__ = 'servicos'
    
    id = Column(Integer, primary_key=True)
    nome = Column(String(100), nullable=False)
    categoria_id = Column(Integer, ForeignKey('categorias.id'))
    descricao = Column(Text)
    preco = Column(Numeric(10, 2), nullable=False)
    imagem = Column(String(500))
    status = Column(Boolean, default=True)
    destaque = Column(Boolean, default=False)
    tempo_estimado = Column(String(50))
    ordem = Column(Integer, default=0)
    data_criacao = Column(DateTime, default=datetime.now)
    
    categoria = relationship('Categoria', back_populates='servicos')
    pedidos = relationship('Pedido', back_populates='servico')

class Pedido(db.Model):
    __tablename__ = 'pedidos'
    
    id = Column(Integer, primary_key=True)
    numero = Column(String(20), unique=True, nullable=False)
    usuario_id = Column(Integer, ForeignKey('usuarios.id'))
    servico_id = Column(Integer, ForeignKey('servicos.id'))
    valor = Column(Numeric(10, 2), nullable=False)
    status = Column(String(30), default='aguardando_pagamento')
    dados_cliente = Column(JSON)
    historico = Column(JSON)
    data_criacao = Column(DateTime, default=datetime.now)
    data_atualizacao = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    usuario = relationship('Usuario', back_populates='pedidos')
    servico = relationship('Servico', back_populates='pedidos')
    pagamentos = relationship('Pagamento', back_populates='pedido')

class Pagamento(db.Model):
    __tablename__ = 'pagamentos'
    
    id = Column(Integer, primary_key=True)
    pedido_id = Column(Integer, ForeignKey('pedidos.id'))
    metodo = Column(String(30), default='pix')
    valor = Column(Numeric(10, 2), nullable=False)
    status = Column(String(30), default='pendente')
    dados_pagamento = Column(JSON)
    data_criacao = Column(DateTime, default=datetime.now)
    data_pagamento = Column(DateTime)
    
    pedido = relationship('Pedido', back_populates='pagamentos')

class TransacaoPontos(db.Model):
    __tablename__ = 'transacoes_pontos'
    
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey('usuarios.id'))
    tipo = Column(String(20), nullable=False)  # 'ganho' ou 'gasto'
    quantidade = Column(Integer, nullable=False)
    descricao = Column(String(200))
    data = Column(DateTime, default=datetime.now)
    
    usuario = relationship('Usuario', back_populates='transacoes_pontos')

class Resgate(db.Model):
    __tablename__ = 'resgates'
    
    id = Column(Integer, primary_key=True)
    nome = Column(String(100), nullable=False)
    pontos = Column(Integer, nullable=False)
    tipo = Column(String(30), default='desconto')  # 'desconto' ou 'servico'
    valor = Column(Numeric(10, 2))
    descricao = Column(Text)
    status = Column(Boolean, default=True)
    data_criacao = Column(DateTime, default=datetime.now)

class Cupom(db.Model):
    __tablename__ = 'cupons'
    
    id = Column(Integer, primary_key=True)
    codigo = Column(String(50), unique=True, nullable=False)
    tipo = Column(String(20), default='porcentagem')  # 'porcentagem' ou 'valor'
    valor = Column(Numeric(10, 2), nullable=False)
    validade = Column(DateTime)
    quantidade_maxima = Column(Integer, default=1)
    quantidade_usada = Column(Integer, default=0)
    valor_minimo = Column(Numeric(10, 2), default=0)
    usuarios_permitidos = Column(Text)
    status = Column(Boolean, default=True)
    data_criacao = Column(DateTime, default=datetime.now)

class Log(db.Model):
    __tablename__ = 'logs'
    
    id = Column(Integer, primary_key=True)
    tipo = Column(String(30))
    mensagem = Column(Text, nullable=False)
    usuario_id = Column(Integer, ForeignKey('usuarios.id'))
    ip = Column(String(50))
    data = Column(DateTime, default=datetime.now)

class Configuracao(db.Model):
    __tablename__ = 'configuracoes'
    
    id = Column(Integer, primary_key=True)
    chave = Column(String(50), unique=True, nullable=False)
    valor = Column(Text)
    descricao = Column(String(200))
    data_atualizacao = Column(DateTime, default=datetime.now, onupdate=datetime.now)