"""Cancelar por voz, "a viagem começou mas eu não entrei", aprendizado da IA e pontos do mapa.
Rodar a partir de backend/:  python tests/test_novidades.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ['BANCO'] = os.path.join(tempfile.mkdtemp(), 'teste.db')
os.environ['PONTOS_PADRAO'] = '0'  # testes com as posições provisórias
os.environ['ACESSO'] = '0'  # sem código de acesso nos testes
os.environ['ROTAS_OFFLINE'] = '1'  # sem internet nos testes: linha reta

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app, central  # noqa: E402


def em_viagem(c, cid):
    for acao in ('cheguei', 'embarcou'):
        c.post(f'/api/corridas/{cid}/{acao}')
    return c.get('/api/estado').json()


def test_nao_embarquei():
    with TestClient(app) as c:
        diz = lambda t: c.post('/api/assistente', json={'texto': t}).json()
        r = diz('Eu sou Mizael Souza, matrícula 45871, preciso de um pool da oficina para a Gaing')
        cid, carro = r['corrida']['id'], r['corrida']['carro']
        c.post(f'/api/motoristas/{carro}/modo', json={'modo': 'real'})  # sem simulação andando sozinha
        r = diz('Mizael, matrícula 45871, a viagem já começou mas eu não entrei no carro')
        assert r['intencao'] == 'nao_embarquei' and 'ainda não começou' in r['fala'], r['fala']
        em_viagem(c, cid)
        r = diz('Mizael, matrícula 45871, eu vi que a viagem já começou no aplicativo, mas eu não entrei no carro. O que aconteceu?')
        assert r['intencao'] == 'nao_embarquei' and 'perguntando agora ao motorista' in r['fala'], r['fala']
        chk = [k for k in c.get('/api/estado').json()['checagens'] if k.get('tipo') == 'embarque' and not k.get('respondidaEm')]
        assert len(chk) == 1 and 'NÃO embarcou' in chk[0]['texto'] and chk[0]['carro'] == carro, chk
        c.post(f"/api/checagens/{chk[0]['id']}", json={'opcao': 'outra_pessoa'})
        est = c.get('/api/estado').json()
        minhas = [x for x in est['corridas'] if x['matricula'] == '45871']
        nova = next(x for x in minhas if x.get('substituiCorrida') == cid)
        assert nova['urgente'] and nova['carro'] != carro, nova
        assert next(x for x in minhas if x['id'] == cid)['ocorrencia']
        # cancelar a nova por voz
        r = diz('Eu sou Mizael, matrícula 45871, quero cancelar a viagem')
        assert r['intencao'] == 'cancelar' and 'cancelada' in r['fala'], r['fala']


def test_engano_volta_para_o_local():
    with TestClient(app) as c:
        diz = lambda t: c.post('/api/assistente', json={'texto': t}).json()
        r = diz('Eu sou Ana Lima, matrícula 3321, preciso de um pool do refeitório para a usina')
        cid, carro = r['corrida']['id'], r['corrida']['carro']
        c.post(f'/api/motoristas/{carro}/modo', json={'modo': 'real'})
        em_viagem(c, cid)
        r = diz('matrícula 3321, o carro foi embora sem mim')
        assert r['intencao'] == 'nao_embarquei', r
        chk = next(k for k in c.get('/api/estado').json()['checagens'] if k.get('tipo') == 'embarque' and not k.get('respondidaEm'))
        c.post(f"/api/checagens/{chk['id']}", json={'opcao': 'engano'})
        r = next(x for x in c.get('/api/estado').json()['corridas'] if x['id'] == cid)
        assert r['status'] == 'no_local', r['status']


def test_aprendizado():
    with TestClient(app) as c:
        diz = lambda t: c.post('/api/assistente', json={'texto': t}).json()
        r = diz('o motora sumiu com a minha vaga, matrícula 999')
        assert r['intencao'] != 'nao_embarquei'
        n = c.get('/api/conversas?limite=1').json()[0]['n']
        assert c.post('/api/assistente/ensinar', json={'conversa': n, 'intencao': 'nao_embarquei'}).json()['ok']
        r = diz('o motora sumiu com a minha vaga, matrícula 1234')  # outra pessoa, mesma frase
        assert r['intencao'] == 'nao_embarquei' and r['fonte'] == 'aprendido', r
        an = c.get('/api/assistente/analise').json()
        assert an['corrigidas'] == 1 and an['porFonte'].get('aprendido') == 1, an


def test_pontos_no_mapa():
    with TestClient(app) as c:
        l = c.post('/api/locais/gainf/posicao', json={'lat': -3.26, 'lon': -47.73}).json()
        assert l['real'] and abs(l['lat'] + 3.26) < 1e-9
        assert c.post('/api/locais/importar', json={'pontos': {'oficina': [-3.261, -47.731], 'xxx': [0, 0]}}).json()['importados'] == 1
        locs = {x['id']: x for x in c.get('/api/locais').json()}
        assert locs['oficina']['real'] and not locs['usina']['real']
        assert c.get('/api/mapa/config').json()['locaisMarcados'] == 2
        c.post('/api/locais/gainf/limpar')
        assert not {x['id']: x for x in c.get('/api/locais').json()}['gainf']['real']


if __name__ == '__main__':
    test_nao_embarquei()
    test_engano_volta_para_o_local()
    test_aprendizado()
    test_pontos_no_mapa()
    print('Novidades: tudo certo')
