"""Envio em tempo real para o webhook do Power Automate (Microsoft 365).
Rodar a partir de backend/:  python3 tests/test_tempo_real.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

recebidos = []


class Webhook(BaseHTTPRequestHandler):
    def do_POST(self):
        recebidos.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(202)
        self.end_headers()

    def log_message(self, *a):
        pass


srv = HTTPServer(('127.0.0.1', 0), Webhook)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ['MS_WEBHOOK_URL'] = f'http://127.0.0.1:{srv.server_port}/webhook'
os.environ['BANCO'] = os.path.join(tempfile.mkdtemp(), 'teste.db')
os.environ['PONTOS_PADRAO'] = '0'
os.environ['ACESSO'] = '0'

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


def esperar(cond, seg=10):
    fim = time.time() + seg
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.2)
    return False


def situacoes(cid):
    return [r['viagem']['status'] for r in recebidos if r['viagem']['id'] == cid]


def test_tempo_real():
    with TestClient(app) as c:
        r = c.post('/api/assistente', json={'texto': 'Eu sou Mizael Souza, matrícula 45871, preciso de um pool da oficina para a Gaing'}).json()
        cid = r['corrida']['id']
        assert esperar(lambda: situacoes(cid)), 'a viagem aberta deveria ser enviada na hora'
        primeira = next(x for x in recebidos if x['viagem']['id'] == cid)
        assert primeira['type'] == 'message' and primeira['attachments'][0]['content']['type'] == 'AdaptiveCard'
        assert primeira['viagem']['aberta'] and primeira['viagem']['matricula'] == '45871'
        for nome in ('cheguei', 'embarcou', 'finalizar'):
            c.post(f'/api/corridas/{cid}/{nome}', json={})
            time.sleep(1.5)
        assert esperar(lambda: 'concluida' in situacoes(cid)), situacoes(cid)
        fim = [x for x in recebidos if x['viagem']['id'] == cid][-1]['viagem']
        assert not fim['aberta'] and fim['situacao'] == 'Concluída' and fim['totalMin'] is not None and fim['encerradaEm']
        assert {'no_local', 'em_viagem'} <= set(situacoes(cid)), situacoes(cid)
        assert c.get('/api/saude').json()['microsoft365']['enviados'] >= 3


if __name__ == '__main__':
    test_tempo_real()
    print('ok')
