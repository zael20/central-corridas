"""Página do passageiro: GPS do celular vira a origem quando a pessoa não diz onde está; pedido para outra pessoa.
Rodar a partir de backend/:  python tests/test_passageiro.py
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
from app.locais import LOCAIS  # noqa: E402
from app.estado import xy_para_geo  # noqa: E402


def test_passageiro():
    with TestClient(app) as c:
        assert c.get('/passageiro').status_code == 200
        usina = next(l for l in LOCAIS if l['id'] == 'usina')
        perto = xy_para_geo(usina['x'] + 0.05, usina['y'])  # 50 m da usina
        # sem dizer a origem: usa o GPS
        r = c.post('/api/assistente', json={'texto': 'Eu sou Ana, matrícula 3321, preciso de um pool para a GAINF', 'canal': 'app',
                                            'matricula': '3321', 'nome': 'Ana', 'lat': perto['lat'], 'lon': perto['lon'], 'precisao': 12}).json()
        assert r['criada'], r
        assert r['corrida']['origem'] == 'usina' and r['corrida']['interpretacao']['origemPeloGps']
        assert r['corrida']['posPassageiro']['precisao'] == 12 and not r['corrida'].get('pedidoPor')
        # longe de tudo: continua perguntando onde a pessoa está
        r = c.post('/api/assistente', json={'texto': 'preciso de um pool para a GAINF', 'canal': 'app', 'matricula': '3321', 'nome': 'Ana',
                                            'lat': -3.0, 'lon': -47.0}).json()
        assert not r.get('criada')
        # pedido para outra pessoa: a corrida fica no nome dela e guarda quem pediu
        r = c.post('/api/assistente', json={'texto': 'Eu sou Beto, matrícula 4455, preciso de um pool da oficina para a usina', 'canal': 'app',
                                            'matricula': '3321', 'nome': 'Ana'}).json()
        assert r['criada'] and r['corrida']['matricula'] == '4455', r
        assert r['corrida']['pedidoPor'] == {'nome': 'Ana', 'matricula': '3321'}


if __name__ == '__main__':
    test_passageiro()
    print('ok')
