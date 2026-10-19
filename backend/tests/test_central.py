"""Conversa completa com a central: pedido, informação, carro parado, resposta do motorista e cancelamento.
Rodar a partir de backend/:  python3 tests/test_central.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ['BANCO'] = os.path.join(tempfile.mkdtemp(), 'teste.db')
os.environ['PONTOS_PADRAO'] = '0'  # testes com as posições provisórias
os.environ['ACESSO'] = '0'  # sem código de acesso nos testes

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app, central  # noqa: E402


def test_conversa():
    with TestClient(app) as c:
        diz = lambda t: c.post('/api/assistente', json={'texto': t}).json()
        r = diz('Eu sou Mizael Souza, matrícula 45871, preciso de um pool da oficina para a Gaing')
        assert r['criada'] and 'viagem confirmada' in r['fala'] and 'Para mais informações' in r['fala'], r['fala']
        assert r['corrida']['matricula'] == '45871' and r['corrida']['origem'] == 'oficina' and r['corrida']['destino'] == 'gainf'
        r = diz('Eu sou Ana Lima, matrícula 3321, preciso de um pool')
        assert not r['criada'] and 'não entendi' in r['fala'].lower()
        r = diz('Eu sou Carlos Mendes, matrícula 7788, preciso de um pool do canteiro para a britagem')
        carro = r['corrida']['carro']
        c.post(f'/api/motoristas/{carro}/pane', json={'ligar': True})
        central.tick()
        central.mov[carro]['desde'] -= 5 * 60000  # finge 5 min parado
        r = diz('Carlos matrícula 7788, o carro não chegou, aconteceu alguma coisa?')
        assert r['intencao'] == 'informacao' and 'parado há 5 minutos' in r['fala'], r['fala']
        chk = [k for k in c.get('/api/estado').json()['checagens'] if not k.get('respondidaEm')]
        assert len(chk) == 1 and chk[0]['carro'] == carro and chk[0]['avisar']
        k = c.post(f"/api/checagens/{chk[0]['id']}", json={'opcao': 'problema'}).json()
        assert k['resposta'] == 'problema'
        est = c.get('/api/estado').json()
        r2 = next(x for x in est['corridas'] if x['matricula'] == '7788')
        assert r2['carro'] != carro, 'corrida deveria sair do carro com problema'
        assert not next(m for m in est['motoristas'] if m['id'] == carro)['ativo']
        r = diz('cancela meu pool, matrícula 7788')
        assert r['intencao'] == 'cancelar' and 'cancelada' in r['fala']
        assert diz('bom dia central')['intencao'] == 'desconhecida'
        assert c.get('/api/corridas.csv').status_code == 200
        assert len(c.get('/api/conversas').json()) == 6


if __name__ == '__main__':
    test_conversa()
    print('Conversa com a central: tudo certo')
