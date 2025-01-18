from app import app  # Certifique-se de importar o seu app
from db import db, Safe

# Criar o contexto da aplicação
with app.app_context():
    try:
        # Buscar o cofre ativo (caso exista)
        safe = Safe.query.first()  # ou Safe.query.filter_by(winner=None).first() dependendo do seu caso

        if safe:  # Verifica se o cofre foi encontrado
            # Resetando o cofre com uma nova combinação, prêmio e doador
            safe.reset(new_combination="12-13-14-15-22-19", new_prize="2 MIL REAIS NO PIX!", new_donor="GESPRESS BRASIL LTDA")
            db.session.commit()  # Salva as alterações no banco de dados

            # Exibe a nova combinação resetada
            print(f"Cofre resetado com nova combinação: {safe.combination}")
        else:
            print("Cofre não encontrado.")
    
    except Exception as e:
        # Em caso de erro, faz o rollback e exibe o erro
        db.session.rollback()
        print(f"Erro ao resetar o cofre: {e}")
