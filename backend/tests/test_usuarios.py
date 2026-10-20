"""Acesso por pessoa: matrícula + código, perfis adm / motorista / passageiro; 8015 é sempre adm.
Rodar a partir de backend/:  python tests/test_usuarios.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ['BANCO'] = os.path.join(tempfile.mkdtemp(), 'teste.db')
os.environ['PONTOS_PADRAO'] = '0'
os.environ['ROTAS_OFFLINE'] = '1'
os.environ.pop('ACESSO', None)
os.environ.pop('ADM_MATRICULA', None)
os.environ.pop('ADM_CODIGO', None)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import acesso, app  # noqa: E402


def test_usuarios():
    with TestClient(app) as c:
        cod_op = acesso.ver()['operacao']
        # 8015 entra como adm com o código da operação
        assert c.post('/api/entrar', json={'matricula': '8015', 'codigo': 'errado'}).status_code == 401
        adm = c.post('/api/entrar', json={'matricula': '8015', 'codigo': cod_op}).json()
        assert adm['papel'] == 'operacao' and adm['perfil'] == 'adm', adm
        ha = {'X-Acesso': adm['token']}
        assert c.get('/api/usuarios', headers=ha).json()[0]['matricula'] == '8015'
        # cadastra motorista e passageiro
        assert c.post('/api/usuarios/7001', headers=ha, json={'nome': 'Carlos Lima', 'perfil': 'motorista'}).status_code == 400  # sem código
        assert c.post('/api/usuarios/7001', headers=ha, json={'nome': 'Carlos Lima', 'perfil': 'motorista', 'codigo': '4321'}).status_code == 200
        assert c.post('/api/usuarios/7002', headers=ha, json={'nome': 'Ana Paula', 'perfil': 'passageiro', 'codigo': '9999'}).status_code == 200
        assert c.post('/api/usuarios/8015', headers=ha, json={'perfil': 'motorista'}).status_code == 400  # adm principal continua adm
        mot = c.post('/api/entrar', json={'matricula': '7001', 'codigo': '4321'}).json()
        assert mot['papel'] == 'motorista' and mot['nome'] == 'Carlos Lima'
        hm = {'X-Acesso': mot['token']}
        carro = c.get('/api/estado', headers=hm).json()['motoristas'][0]['id']
        assert c.post(f'/api/motoristas/{carro}/gps', headers=hm, json={'lat': -3.25, 'lon': -47.73}).status_code == 200
        assert c.post(f'/api/motoristas/{carro}/condutor', headers=hm, json={'matricula': '7001'}).json()['condutor']['nome'] == 'Carlos Lima'
        assert c.get('/api/usuarios', headers=hm).status_code == 403   # motorista não cadastra acessos
        assert c.post('/api/locais/gainf/limpar', headers=hm).status_code == 403
        with c.websocket_connect(f"/ws?t={mot['token']}") as w:
            assert len(w.receive_json()['estado']['motoristas']) >= 1
        pas = c.post('/api/entrar', json={'matricula': '7002', 'codigo': '9999'}).json()
        assert pas['papel'] == 'passageiro'
        assert c.get('/api/estado', headers={'X-Acesso': pas['token']}).status_code == 403
        # trocar o código derruba quem entrou com o antigo; remover tira o acesso
        assert c.post('/api/usuarios/7001', headers=ha, json={'perfil': 'motorista', 'codigo': '5555'}).status_code == 200
        assert c.get('/api/estado', headers=hm).status_code == 401
        assert c.post('/api/usuarios/7002/remover', headers=ha).status_code == 200
        assert c.get('/api/locais', headers={'X-Acesso': pas['token']}).status_code == 401
        assert c.post('/api/usuarios/8015/remover', headers=ha).status_code == 400
        # o código geral da operação continua valendo
        assert c.post('/api/entrar', json={'codigo': cod_op}).json()['papel'] == 'operacao'


if __name__ == '__main__':
    test_usuarios()
    print('ok')
