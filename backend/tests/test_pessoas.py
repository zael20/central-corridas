"""Cadastro nome ↔ matrícula, frases reais dos testes de 19/10 e confirmação do motorista.
Rodar a partir de backend/:  python tests/test_pessoas.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ['BANCO'] = os.path.join(tempfile.mkdtemp(), 'teste.db')
os.environ['PONTOS_PADRAO'] = '0'  # testes com as posições provisórias
os.environ['ACESSO'] = '0'  # sem código de acesso nos testes
os.environ['ROTAS_OFFLINE'] = '1'

from fastapi.testclient import TestClient  # noqa: E402
from app.interpretador import interpretar_pedido  # noqa: E402
from app.main import app, estado  # noqa: E402
from app.pessoas import fonetico, nomes_parecidos  # noqa: E402


def test_extracao():
    r = interpretar_pedido('minha matrícula é 12345 Gostaria de saber onde veículo se encontra')
    assert r['matricula'] == '12345', r
    r = interpretar_pedido('meu nome Misael matrícula 12345 queria um pulo do CMD para 172')
    assert r['solicitante'] == 'Misael' and r['matricula'] == '12345', r
    r = interpretar_pedido('Bruna Misael matrícula 12345 quero ir na oficina até a britagem')
    assert r['solicitante'] in ('Bruna Misael', 'Misael'), r
    r = interpretar_pedido('como é Orlando matrícula 995 é preciso de uma corrida urgente')
    assert r['solicitante'] == 'Orlando' and r['matricula'] == '995', r
    assert fonetico('Misael') == fonetico('Mizael') and nomes_parecidos('Sávio', 'Sabio') and not nomes_parecidos('Ana', 'Bruno')


def test_cadastro_vota():
    p = estado.pessoas
    p.registrar('Fabio', '822', 'fala')       # 3 vezes, 3 nomes diferentes
    p.registrar('Flávio', '822', 'fala')
    p.registrar('Sávio Mateus', '822', 'fala')
    assert not p.perfis()['822']['confirmado']
    p.registrar('Savio', '822', 'fala')  # a 4ª repete o som de "Sávio": desempata
    perf = p.perfis()['822']
    assert perf['confirmado'] and fonetico(perf['nome'].split()[0]) == fonetico('Savio'), perf
    assert p.resolver(None, '822')['nome'] == perf['nome']
    assert p.resolver('Sabio', None)['matricula'] == '822'
    assert p.resolver('Savio', '832')['matricula'] == '822'  # um dígito errado na transcrição


def test_frases_reais():
    with TestClient(app) as c:
        diz = lambda t, **k: c.post('/api/assistente', json={'texto': t, **k}).json()
        r = diz('meu nome é Misael matrícula 12345 preciso de um pulso saindo da portaria até oficina')
        assert r['criada'], r
        cid, carro = r['corrida']['id'], r['corrida']['carro']
        c.post(f'/api/motoristas/{carro}/modo', json={'modo': 'real'})
        # pedir de novo o mesmo trajeto não cria outra viagem
        r = diz('Misael matrícula 12345 preciso de um pool da portaria para a oficina')
        assert not r['criada'] and r['intencao'] == 'informacao' and 'já tem uma viagem aberta' in r['fala'], r
        # só a matrícula (com "é"), e só o nome
        r = diz('minha matrícula é 12345 Gostaria de saber onde veículo se encontra')
        assert r['intencao'] == 'informacao' and r['corrida'] and r['corrida']['id'] == cid, r
        r = diz('aqui é o Mizael, onde está o meu carro?')
        assert r['corrida'] and r['corrida']['id'] == cid, r
        for acao in ('cheguei', 'embarcou'):
            c.post(f'/api/corridas/{cid}/{acao}')
        for frase in ['Misael matrícula 12345 o veículo saiu iniciou a viagem sozinho saindo da portaria para oficina e não esperou',
                      'Misael matrícula 12345 o motorista deu início tá indo para o local mas não me levou',
                      'matrícula 12345, o carro saiu sem mim, eu ainda estou aqui',
                      'Misael 12345 ainda estou aqui esperando o carro']:
            r = diz(frase)
            assert r['intencao'] == 'nao_embarquei' and not r['criada'], (frase, r)
        chk = next(k for k in c.get('/api/estado').json()['checagens'] if k.get('tipo') == 'embarque' and not k.get('respondidaEm'))
        assert 'Misael' in chk['texto'] and any('12345' in o['texto'] for o in chk['opcoes']), chk
        assert next(o for o in chk['opcoes'] if o['id'] == 'outra_pessoa')['pedeDados']
        c.post(f"/api/checagens/{chk['id']}", json={'opcao': 'outra_pessoa', 'nome': 'Carlos Souza', 'matricula': '7788'})
        est = c.get('/api/estado').json()
        orig = next(x for x in est['corridas'] if x['id'] == cid)
        assert orig['embarcouDeFato']['matricula'] == '7788' and 'Carlos' in orig['ocorrencia'], orig
        assert estado.pessoas.perfis()['7788']['nome'] == 'Carlos Souza'


def test_nao_encontrei_e_reclamacao():
    with TestClient(app) as c:
        diz = lambda t: c.post('/api/assistente', json={'texto': t}).json()
        r = diz('Eu sou Ana Lima, matrícula 3321, preciso de um pool do refeitório para a usina')
        cid, carro = r['corrida']['id'], r['corrida']['carro']
        c.post(f'/api/motoristas/{carro}/modo', json={'modo': 'real'})
        c.post(f'/api/corridas/{cid}/cheguei')
        r = diz('Ana matrícula 3321, o aplicativo diz que chegou mas não estou vendo o carro')
        assert r['intencao'] == 'nao_encontrei' and 'perguntando agora ao motorista' in r['fala'], r
        chk = next(k for k in c.get('/api/estado').json()['checagens'] if k.get('tipo') == 'encontro' and not k.get('respondidaEm'))
        c.post(f"/api/checagens/{chk['id']}", json={'opcao': 'outro_ponto'})
        r = diz('Ana Lima matrícula 3321 quero fazer uma reclamação, o motorista estava correndo demais')
        assert r['intencao'] == 'reclamacao' and 'registrada' in r['fala'], r
        assert 'reclama' in next(x for x in c.get('/api/estado').json()['corridas'] if x['id'] == cid)['ocorrencia']


if __name__ == '__main__':
    test_extracao()
    test_cadastro_vota()
    test_frases_reais()
    test_nao_encontrei_e_reclamacao()
    print('Pessoas e frases reais: tudo certo')
