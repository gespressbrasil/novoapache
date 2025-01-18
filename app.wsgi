# app.wsgi
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

# [DEBUG] Carrega o whoami.py e imprime detalhes
#import whoami
#print("=== DEBUG from whoami.py ===", whoami.run_test(), flush=True)

from app import app
application = app
