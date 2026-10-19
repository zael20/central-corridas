"""Viagem compartilhada: carro em rota busca outro passageiro no caminho, deixa o primeiro e depois o segundo.
Rodar a partir de backend/:  python tests/test_compartilhada.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import despacho as D  # noqa: E402
from app.locais import LOCAIS  # noqa: E402

MIN = D.MIN
T0 = 1_800_000_000_000


def montar():
    D.definir_locais(LOCAIS, [])  # sem tempos medidos: só distância
    D.CFG.update({'compartilhar': True, 'desvioMaxMin': 6, 'toleranciaCompartilharMin': 4})
    L = D.LOC
    mot = {
        'V01': {'id': 'V01', 'nome': 'A', 'capacidade': 4, 'ativo': True, 'modo': 'simulado', 'x': L['gainf']['x'], 'y': L['gainf']['y']},
        'V02': {'id': 'V02', 'nome': 'B', 'capacidade': 4, 'ativo': True, 'modo': 'simulado', 'x': L['barragem']['x'], 'y': L['barragem']['y'], 'livreDesde': T0},
    }
    a = {'id': 'A', 'origem': 'gainf', 'destino': 'britagem', 'passageiros': 1, 'status': 'em_viagem', 'carro': 'V01', 'criadoEm': T0 - 5 * MIN,
         'iniciouViagemEm': T0, 'chegouOrigemEm': T0 - MIN, 'saiuParaBuscaEm': T0 - 3 * MIN, 'solicitante': 'Ana', 'matricula': '1'}
    return {'motoristas': mot, 'corridas': {'A': a}}


def aplicar(est, ops):
    for o in ops:
        col = est[o['col']]
        col[o['id']] = dict(o['set']) if 'set' in o else {**col.get(o['id'], {}), **o['patch']}


def test_busca_no_caminho():
    est = montar()
    t = T0 + MIN  # o carro já andou 1 min de GAINF para a Britagem
    res = D.criar_corrida({'origem': 'calderaria', 'destino': 'posto', 'passageiros': 1, 'solicitante': 'Bruno', 'matricula': '2'}, est, t)
    aplicar(est, res['ops'])
    b = est['corridas'][res['corrida']['id']]
    assert b['carro'] == 'V01' and b.get('compartilhada') and b['status'] == 'a_caminho', b
    rot = est['motoristas']['V01']['roteiro']['paradas']
    assert [(s['t'], s['l']) for s in rot] == [('buscar', 'calderaria'), ('deixar', 'britagem'), ('deixar', 'posto')], rot
    assert est['corridas']['A']['compartilhadaCom'] == [b['id']]
    dec = b['decisao']
    assert dec.get('compartilhada') and any(c.get('compartilhada') and c['escolhido'] for c in dec['candidatos']), dec
    # simula até tudo terminar
    for k in range(1, 60):
        aplicar(est, D.tarefas(est, t + k * 30_000, simular=True))
    a, b = est['corridas']['A'], est['corridas'][b['id']]
    assert a['status'] == 'concluida' and b['status'] == 'concluida', (a['status'], b['status'])
    assert b['iniciouViagemEm'] < a['finalizadoEm'] < b['finalizadoEm'], 'ordem: busca o novo, deixa o primeiro, depois o novo'
    assert not est['motoristas']['V01'].get('roteiro') and est['motoristas']['V01'].get('livreDesde')
    atraso_a = (a['finalizadoEm'] - (T0 + D.viagem_ms(D.LOC['gainf'], D.LOC['britagem']) + D.CFG['desembarqueMin'] * MIN)) / MIN
    assert atraso_a <= D.CFG['desvioMaxMin'] + 1, atraso_a


def test_sem_lugar_ou_longe_nao_compartilha():
    est = montar()
    est['corridas']['A']['passageiros'] = 4  # carro cheio
    res = D.criar_corrida({'origem': 'calderaria', 'destino': 'posto', 'passageiros': 1}, est, T0 + MIN)
    assert res['corrida']['carro'] != 'V01' or not res['corrida'].get('compartilhada'), res['corrida']
    est = montar()
    res = D.criar_corrida({'origem': 'fiuza', 'destino': 'cth', 'passageiros': 1}, est, T0 + MIN)  # lado oposto: desvio grande
    assert not res['corrida'].get('compartilhada'), res['corrida']
    est = montar()
    res = D.criar_corrida({'origem': 'calderaria', 'destino': 'posto', 'passageiros': 1, 'urgente': True}, est, T0 + MIN)
    assert not res['corrida'].get('compartilhada'), 'urgente vai sozinho'


def test_cancelar_compartilhada():
    est = montar()
    t = T0 + MIN
    res = D.criar_corrida({'origem': 'calderaria', 'destino': 'posto', 'passageiros': 1}, est, t)
    aplicar(est, res['ops'])
    aplicar(est, D.acao('cancelar', res['corrida']['id'], est, t + MIN))
    rot = est['motoristas']['V01']['roteiro']['paradas']
    assert [(s['t'], s['l']) for s in rot] == [('deixar', 'britagem')], rot
    assert est['corridas']['A']['status'] == 'em_viagem'


if __name__ == '__main__':
    test_busca_no_caminho()
    test_sem_lugar_ou_longe_nao_compartilha()
    test_cancelar_compartilhada()
    print('Viagem compartilhada: tudo certo')
