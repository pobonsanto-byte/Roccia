import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.ext.declarative import declarative_base
from models import db

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://usuario:senha@localhost:5432/meu_bot")

engine = None
Session = None

def init_db():
    """Inicializa a conexão com o banco de dados"""
    global engine, Session
    
    try:
        engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20, pool_pre_ping=True)
        Session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
        
        # Criar tabelas
        from models import Usuario, Servico, Categoria, Pedido, Pagamento, TransacaoPontos, Resgate, Cupom, Log, Configuracao
        db.create_all(bind=engine)
        
        print("✅ Banco de dados inicializado com sucesso!")
        return True
    except Exception as e:
        print(f"❌ Erro ao inicializar banco de dados: {e}")
        return False

def get_db():
    """Retorna uma sessão do banco de dados"""
    if Session is None:
        init_db()
    return Session()

def close_db():
    """Fecha a sessão do banco de dados"""
    if Session:
        Session.remove()