"""Motorista do turno: o app do motorista informa a matrícula; o nome vem do cadastro de pessoas ou é digitado.
Rodar a partir de backend/:  python tests/test_condutor.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ['BANCO'] = os.path.join(tempfile.mkdtemp(), 'teste.db')
os.environ['PONTOS_PADRAO'] = '0'
os.environ['ROTAS_OFFLINE'] = '1'
os.environ['ACESSO'] = '0'

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


def test_condutor():
    with TestClient(app) as c:
        carro = c.get('/api/estado').json()['motoristas'][0]['id']
        r = c.post(f'/api/motoristas/{carro}/condutor', json={'matricula': '70707'})
        assert r.status_code == 400  # matrícula nova: precisa do nome
        r = c.post(f'/api/motoristas/{carro}/condutor', json={'matricula': 'mat. 70707', 'nome': 'Carlos Lima'})
        assert r.status_code == 200 and r.json()['condutor']['nome'] == 'Carlos Lima', r.json()
        # na próxima vez basta a matrícula
        r = c.post(f'/api/motoristas/{carro}/condutor', json={'matricula': '70707'})
        assert r.json()['condutor'] == {**r.json()['condutor'], 'matricula': '70707', 'nome': 'Carlos Lima'}
        r = c.post(f'/api/motoristas/{carro}/condutor', json={'matricula': ''})
        assert not r.json().get('condutor')


if __name__ == '__main__':
    test_condutor()
    print('ok')
