"""Códigos de acesso: sem código não entra; passageiro só pede/pergunta/cancela e só vê as próprias viagens.
Rodar a partir de backend/:  python tests/test_acesso.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ['BANCO'] = os.path.join(tempfile.mkdtemp(), 'teste.db')
os.environ['PONTOS_PADRAO'] = '0'  # testes com as posições provisórias
os.environ['ROTAS_OFFLINE'] = '1'
os.environ.pop('ACESSO', None)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import acesso, app  # noqa: E402


def test_codigos():
    with TestClient(app) as c:
        assert c.get('/api/locais').status_code == 401
        assert c.post('/api/entrar', json={'codigo': 'errado'}).status_code == 401
        cod = acesso.ver()
        tp = c.post('/api/entrar', json={'codigo': cod['passageiro']}).json()
        to = c.post('/api/entrar', json={'codigo': cod['operacao']}).json()
        assert tp['papel'] == 'passageiro' and to['papel'] == 'operacao'
        hp, ho = {'X-Acesso': tp['token']}, {'X-Acesso': to['token']}
        assert c.get('/api/locais', headers=hp).status_code == 200
        r = c.post('/api/assistente', headers=hp, json={'texto': 'Eu sou Ana, matrícula 3321, preciso de um pool do refeitório para a usina', 'clienteId': 'cli-a'})
        assert r.status_code == 200 and r.json()['criada']
        c.post('/api/assistente', headers=ho, json={'texto': 'Eu sou Beto, matrícula 4455, preciso de um pool da oficina para a GAINF'})
        assert c.get('/api/estado', headers=hp).status_code == 403
        assert c.get('/api/pessoas', headers=hp).status_code == 403
        assert c.get('/api/estado', headers=ho).status_code == 200
        with c.websocket_connect(f"/ws?t={tp['token']}&cliente=cli-a") as w:
            est = w.receive_json()['estado']
            assert [x['matricula'] for x in est['corridas']] == ['3321'], est['corridas']
            assert est['checagens'] == [] and est['eventos'] == []
        tr = c.get('/api/tempo-real?cliente=cli-a', headers=hp).json()  # plano B sem WebSocket
        assert [x['matricula'] for x in tr['estado']['corridas']] == ['3321'] and 'agora' in tr
        assert len(c.get('/api/tempo-real', headers=ho).json()['estado']['corridas']) == 2
        with c.websocket_connect(f"/ws?t={to['token']}") as w:
            assert len(w.receive_json()['estado']['corridas']) == 2


if __name__ == '__main__':
    test_codigos()
    print('Acesso: tudo certo')
