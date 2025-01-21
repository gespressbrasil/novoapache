from app import app  # Certifique-se de importar o seu app
from db import db, Safe, Attempt

# Criar o contexto da aplicação
with app.app_context():
    try:
        # Buscar o cofre ativo (caso exista)
        safe = Safe.query.first()  # ou Safe.query.filter_by(winner=None).first() dependendo do seu caso

        if safe:  # Verifica se o cofre foi encontrado
            # Exclui todas as tentativas antes de resetar o cofre
            Attempt.query.delete()
            db.session.commit()  # Confirma a exclusão das tentativas

            # Resetando o cofre com uma nova combinação, prêmio e doador
            safe.reset(new_combination="11-22-33-53-54-18", new_prize=" 2.000,00 MIL REAIS!", new_donor="@mullerfp")
            db.session.commit()  # Salva as alterações no banco de dados

            # Exibe a nova combinação resetada
            print(f"Cofre resetado com nova combinação: {safe.combination}")
        else:
            print("Cofre não encontrado.")
    
    except Exception as e:
        # Em caso de erro, faz o rollback e exibe o erro
        db.session.rollback()
        print(f"Erro ao resetar o cofre: {e}")
