# Simula um turno no motor de despacho e confere as regras e a coerência dos tempos.
# Porte de app-corridas/testes/testar-motor.js (mesma semente, mesma sequência).
# Uso (a partir de backend/): python3 -m pytest tests/test_despacho.py   ou   python3 tests/test_despacho.py
import math
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import despacho as M  # noqa: E402
from app.locais import LOCAIS, ROTAS_TEMPO  # noqa: E402

MIN = M.MIN
L = {l['id']: l for l in LOCAIS}


def turno():
    """Roda o turno simulado. Devolve (falhas, linhas de resumo)."""
    M.definir_locais(LOCAIS, ROTAS_TEMPO)
    semente = 7

    def rnd():
        nonlocal semente
        semente = (semente * 16807) % 2147483647
        return semente / 2147483647

    est = {'motoristas': {}, 'corridas': {}}

    def aplicar(ops):
        for o in ops:
            c = est[o['col']]
            c[o['id']] = dict(o['set']) if o.get('set') else {**c.get(o['id'], {}), **o['patch']}

    falhas = []

    def check(c, msg):
        if not c:
            falhas.append(msg)
            print('FALHA:', msg)

    t0 = int(datetime(2026, 10, 19, 11, 0, tzinfo=timezone.utc).timestamp() * 1000)
    for id_, base, cap in [('V01', 'gainf', 4), ('V02', 'usina', 4), ('V03', 'oficina', 7)]:
        est['motoristas'][id_] = {'id': id_, 'nome': id_, 'capacidade': cap, 'ativo': True, 'modo': 'simulado',
                                  'x': L[base]['x'], 'y': L[base]['y'], 'livreDesde': t0}

    # 1) Regra: carro livre recebe direto
    t = t0
    res = M.criar_corrida({'origem': 'gainf', 'destino': 'oficina', 'passageiros': 1}, est, t)
    r1 = res['corrida']
    aplicar(res['ops'])
    check(r1['status'] == 'a_caminho' and r1['carro'] == 'V01' and r1['direto'], 'livre deveria receber direto (V01 está na GAINF)')
    # 2) Ocupado perto termina antes → oferecida; recusa → vai para outro
    res = M.criar_corrida({'origem': 'oficina', 'destino': 'usina', 'passageiros': 2}, est, t)
    r1 = res['corrida']
    aplicar(res['ops'])
    check(r1['carro'], 'segunda corrida deveria ter carro')
    oferecida = next((c for c in est['corridas'].values() if c['status'] == 'oferecida'), None)
    if oferecida:
        antes = oferecida['carro']
        aplicar(M.acao('recusar', oferecida['id'], est, t))
        check(est['corridas'][oferecida['id']]['carro'] != antes, 'recusa deveria passar para outro carro')
    # rota medida na planilha: GAINF → Oficina = 5 min
    check(abs(M.viagem_ms(L['gainf'], L['oficina']) - 5 * MIN) < 1, 'GAINF→Oficina deveria usar 5 min da planilha')
    check(abs(M.viagem_ms(L['ps3'], L['ps1']) - 3 * MIN) < 1, 'PS3→PS1 deveria usar o sentido inverso medido (3 min)')
    # 3) Turno aleatório com motoristas simulados
    for i in range(60):
        prox = t + rnd() * 10 * MIN
        while t < prox:
            t += 15000
            aplicar(M.tarefas(est, t, True))
        o = LOCAIS[math.floor(rnd() * len(LOCAIS))]['id']
        while True:
            d = LOCAIS[math.floor(rnd() * len(LOCAIS))]['id']
            if d != o:
                break
        pedido = {'origem': o, 'destino': d, 'passageiros': 1 + math.floor(rnd() * 3), 'urgente': rnd() < .08, 'canal': 'app'}
        aplicar(M.criar_corrida(pedido, est, t)['ops'])
        if i == 30:
            alvo = next((c for c in est['corridas'].values() if c['status'] in ('na_fila', 'a_caminho')), None)
            if alvo:
                aplicar(M.acao('cancelar', alvo['id'], est, t))
        if i == 40:
            aplicar(M.definir_ativo('V02', False, est, t))
        if i == 50:
            aplicar(M.definir_ativo('V02', True, est, t))
        # nunca dois atendimentos ao mesmo tempo no mesmo carro
        for id_ in est['motoristas']:
            check(len([c for c in est['corridas'].values() if c.get('carro') == id_ and c['status'] in M.ATUAL]) <= 1,
                  f'{id_} com duas corridas em andamento')
        check(not any(c.get('carro') == 'V02' and c['status'] in M.FILA and not est['motoristas']['V02']['ativo']
                      for c in est['corridas'].values()), 'V02 fora de operação com fila')
    for _ in range(400):
        t += 15000
        aplicar(M.tarefas(est, t, True))
    ind = M.indicadores(est, t)
    for l in ind['linhas']:
        if l['status'] == 'cancelada':
            continue
        check(l['status'] == 'concluida', f"{l['id']} terminou em {l['status']}")
        seq = [l.get(k) for k in ('criadoEm', 'atribuidoEm', 'saiuParaBuscaEm', 'chegouOrigemEm', 'iniciouViagemEm', 'finalizadoEm')]
        check(all(x is not None for x in seq) and all(seq[k] >= seq[k - 1] for k in range(1, len(seq))), f"{l['id']} tempos fora de ordem")

    def f(x):  # igual ao toFixed(1) do JS (empate arredonda para longe do zero)
        return '-' if x is None else str(Decimal(x).quantize(Decimal('0.1'), ROUND_HALF_UP))

    s = ind['resumo']
    linhas = [
        f"corridas {s['total']}, concluídas {s['concluidas']}, canceladas {s['canceladas']}, diretas {s['diretas']}",
        f"espera média {f(s['esperaMedia'])} min (mediana {f(s['esperaMediana'])}, p90 {f(s['esperaP90'])}), fila {f(s['emFilaMedio'])}, "
        f"deslocamento {f(s['deslocamentoMedio'])}, viagem {f(s['viagemMedia'])}, erro previsão {f(s['erroEtaMedio'])}",
    ]
    for c in ind['carros']:
        linhas.append(f"{c['id']}: {c['corridas']} corridas, ocioso médio {f(c['ociosoMedio'])} min, km vazio {f(c['kmVazio'])}, km c/ pax {f(c['kmComPassageiro'])}")
    return falhas, linhas


def test_rotas_da_planilha():
    M.definir_locais(LOCAIS, ROTAS_TEMPO)
    assert abs(M.viagem_ms(L['gainf'], L['oficina']) - 5 * MIN) < 1  # GAINF → Oficina = 5 min
    assert abs(M.viagem_ms(L['ps3'], L['ps1']) - 3 * MIN) < 1  # PS3 → PS1 usa o sentido inverso medido
    assert M.tem_rota('ps3', 'ps1') and not M.tem_rota('ps3', 'mina4')


def test_turno_simulado():
    falhas, linhas = turno()
    assert not falhas, falhas
    assert linhas[0].startswith('corridas ')


if __name__ == '__main__':
    falhas, linhas = turno()
    for ln in linhas:
        print(ln)
    print(f'{len(falhas)} falha(s)' if falhas else 'Todas as verificações passaram')
    sys.exit(1 if falhas else 0)
