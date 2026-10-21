"""Feed das viagens encerradas para o Power Automate (Microsoft 365).
Rodar a partir de backend/:  python3 tests/test_integracao.py
"""
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ['BANCO'] = os.path.join(tempfile.mkdtemp(), 'teste.db')
os.environ['PONTOS_PADRAO'] = '0'
os.environ['ACESSO'] = '0'
os.environ['INTEGRACAO_CHAVE'] = 'segredo-teste'

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


def test_feed():
    with TestClient(app) as c:
        assert c.get('/api/integracao/corridas.rss').status_code == 404
        assert c.get('/api/integracao/corridas.rss?chave=errada').status_code == 404
        diz = lambda t: c.post('/api/assistente', json={'texto': t}).json()
        a = diz('Eu sou Mizael Souza, matrícula 45871, preciso de um pool da oficina para a Gaing')['corrida']
        b = diz('Eu sou Carlos Mendes, matrícula 7788, preciso de um pool do canteiro para a britagem')['corrida']
        for nome in ('cheguei', 'embarcou', 'finalizar'):
            c.post(f"/api/corridas/{a['id']}/{nome}", json={})
        c.post(f"/api/corridas/{b['id']}/cancelar", json={'motivo': 'teste'})

        lista = c.get('/api/integracao/corridas.json?chave=segredo-teste').json()
        por_id = {v['id']: v for v in lista}
        assert por_id[a['id']]['status'] == 'concluida' and por_id[a['id']]['matricula'] == '45871'
        assert por_id[a['id']]['encerradaEm'].endswith('-03:00') and por_id[a['id']]['totalMin'] is not None
        assert por_id[b['id']]['status'] == 'cancelada'

        r = c.get('/api/integracao/corridas.rss?chave=segredo-teste')
        assert r.status_code == 200 and 'rss' in r.headers['content-type']
        itens = ET.fromstring(r.text).findall('./channel/item')
        assert len(itens) == 2
        dados = [json.loads(i.find('description').text) for i in itens]
        assert {d['id'] for d in dados} == {a['id'], b['id']}
        assert itens[0].find('pubDate').text != itens[1].find('pubDate').text  # datas distintas (o RSS do Power Automate exige)


if __name__ == '__main__':
    test_feed()
    print('ok')
