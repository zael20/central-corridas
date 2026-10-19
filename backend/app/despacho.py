# Motor de despacho das corridas internas (porte fiel de app-corridas/motor.js).
# Funções PURAS: recebem o estado atual {'motoristas': {}, 'corridas': {}} e devolvem as alterações a gravar (ops).
# Quem grava é quem chama (API/banco). Tempos em ms epoch, campos em camelCase como no JS.
#
# Regras de despacho:
#  - Escolhe o carro que chega MAIS RÁPIDO na origem, contando a corrida atual e a fila de cada motorista.
#    Assim um motorista ocupado que está terminando perto do local pode ganhar de um livre que está longe.
#  - Se o escolhido está LIVRE, a corrida cai direto para ele (sem precisar aceitar) e ele já sai para buscar.
#  - Se o escolhido está OCUPADO, a corrida é OFERECIDA: ele aceita ou recusa. Recusou → vai para o próximo melhor.
#    Sem resposta em CFG['aceiteSeg'] segundos → aceite automático (fica registrado).
#  - Urgente entra na frente da fila do carro.
#  - Viagem compartilhada (CFG['compartilhar']): um carro que já está em rota, com lugar sobrando, pode buscar o
#    passageiro novo no caminho. Ordem das paradas: busca o novo, deixa quem já estava, depois deixa o novo.
#    Só vale se o atraso para quem já está no carro for até CFG['desvioMaxMin'] e se o novo for buscado no máximo
#    CFG['toleranciaCompartilharMin'] depois do que levaria o melhor carro sozinho. Urgente nunca é compartilhada.
#    Enquanto leva mais de uma viagem, o carro guarda o "roteiro" (lista de paradas com horário previsto).
import math
import random
from datetime import datetime, timezone

MIN = 60000
CFG = {'velocidadeKmh': 30, 'fatorEstrada': 1.3, 'embarqueMin': 1.5, 'desembarqueMin': 1, 'aceiteSeg': 60, 'antecedenciaAgendadaMin': 10,
       'compartilhar': False, 'desvioMaxMin': 6, 'toleranciaCompartilharMin': 4}
ATUAL = ['a_caminho', 'no_local', 'em_viagem']
FILA = ['oferecida', 'na_fila']
ABERTAS = ['agendada', 'sem_carro', *FILA, *ATUAL]

LOC = {}
ROTAS = {}
# Roteador opcional (o servidor liga com o mapa real; os testes rodam sem):
#  VIA(a, b) -> km pela estrada entre dois pontos {x, y}, ou None (usa linha reta × fatorEstrada)
#  TRACADO(a, b) -> [{x, y}, ...] desenho da rota, ou None (o carro simulado anda em linha reta)
VIA = None
TRACADO = None


def _n(x):
    # float inteiro vira int, para o JSON sair igual ao do JS (2 e não 2.0)
    return int(x) if isinstance(x, float) and x.is_integer() else x


def _soma(a):
    # soma da esquerda para a direita, como o reduce do JS (o sum() do Python compensa o arredondamento)
    s = 0
    for x in a:
        s += x
    return s


def _lista(obj):
    return list((obj or {}).values())


# rotas = [[origemId, destinoId, minutos], ...] medidas na operação (planilha). Têm prioridade sobre a linha reta.
def definir_locais(lista, rotas=None):
    global LOC, ROTAS
    LOC = {l['id']: l for l in lista}
    if rotas is not None:
        ROTAS = {}
        for o, d, m in rotas:
            ROTAS[o + '>' + d] = m


def _hypot(*vs):
    # Math.hypot do V8 (normaliza pelo maior e soma com Kahan): o math.hypot do Python difere no último dígito
    vs = [abs(v) for v in vs]
    mx = max(vs)
    if mx == 0:
        return 0
    soma = comp = 0.0
    for v in vs:
        n = v / mx
        parcela = n * n - comp
        prel = soma + parcela
        comp = (prel - soma) - parcela
        soma = prel
    return math.sqrt(soma) * mx


def dist_km(a, b):
    if VIA:
        km = VIA(a, b)
        if km is not None:
            return km
    return _n(_hypot(a['x'] - b['x'], a['y'] - b['y']) * CFG['fatorEstrada'])


def _ao_longo(pts, f):
    """Ponto a uma fração f (0..1) do comprimento da linha pts."""
    seg = [math.hypot(b['x'] - a['x'], b['y'] - a['y']) for a, b in zip(pts, pts[1:])]
    alvo = sum(seg) * f
    for (a, b), s in zip(zip(pts, pts[1:]), seg):
        if alvo <= s and s > 0:
            k = alvo / s
            return {'x': a['x'] + (b['x'] - a['x']) * k, 'y': a['y'] + (b['y'] - a['y']) * k}
        alvo -= s
    return {'x': pts[-1]['x'], 'y': pts[-1]['y']}


# Ponto → id do local (ou o local onde o carro está parado, até 50 m)
def _id_de(p):
    pid = p.get('id')
    if pid and LOC.get(pid) is p:
        return pid
    for l in LOC.values():
        if abs(l['x'] - p['x']) < 0.05 and abs(l['y'] - p['y']) < 0.05:
            return l['id']
    return None


def _rota(o, d):
    m = ROTAS.get(o + '>' + d)
    return m if m is not None else ROTAS.get(d + '>' + o)


def viagem_ms(a, b):
    ia, ib = _id_de(a), _id_de(b)
    if ia and ib:
        if ia == ib:
            return 0
        m = _rota(ia, ib)
        if m is not None:
            return _n(m * MIN)
    return _n((dist_km(a, b) / CFG['velocidadeKmh']) * 60 * MIN)


def tem_rota(o, d):
    return _rota(o, d) is not None


def do_carro(carro_id, corridas):
    cs = [c for c in _lista(corridas) if c.get('carro') == carro_id]
    ativas = sorted((c for c in cs if c.get('status') in ATUAL), key=lambda c: (c.get('compartilhada') and c.get('atribuidoEm') or 0, c['criadoEm']))
    return {
        'atual': ativas[0] if ativas else None,
        'ativas': ativas,  # mais de uma = viagem compartilhada
        'fila': sorted((c for c in cs if c.get('status') in FILA), key=lambda c: c['ordem']),
    }


# ---------- roteiro (viagem compartilhada) ----------
def _cronometrar(partida, inicio, paradas):
    """Horário previsto de chegada/saída de cada parada, saindo de `partida` no instante `inicio`."""
    tt, p, out = inicio, partida, []
    for s in paradas:
        L = LOC[s['l']]
        ant = out[-1] if out else None
        if ant and s['t'] == 'deixar' and ant['t'] == 'deixar' and ant['l'] == s['l']:
            out.append({**s, 'chega': ant['chega'], 'sai': ant['sai']})  # mesmo destino: descem juntos
            continue
        if s.get('fixa'):  # o carro já está neste ponto (passageiro embarcando)
            chega = s['chega']
            sai = max(inicio, chega + CFG['embarqueMin'] * MIN)
        else:
            chega = tt + viagem_ms(p, L)
            sai = chega + (CFG['embarqueMin'] if s['t'] == 'buscar' else CFG['desembarqueMin']) * MIN
        out.append({**s, 'chega': _n(chega), 'sai': _n(sai)})
        tt, p = sai, L
    return out


def _roteiro_atual(m, corridas, t):
    """Paradas que faltam para o carro (do roteiro ou da corrida atual) e de onde ele parte agora."""
    pos = posicao(m, corridas, t)
    rot = m.get('roteiro')
    if rot and rot.get('paradas'):
        return {'partida': pos, 'inicio': t, 'paradas': [dict(s) for s in rot['paradas']]}
    r = do_carro(m['id'], corridas)['atual']
    if not r:
        return None
    if r['status'] == 'a_caminho':
        par = [{'c': r['id'], 't': 'buscar', 'l': r['origem']}, {'c': r['id'], 't': 'deixar', 'l': r['destino']}]
    elif r['status'] == 'no_local':
        par = [{'c': r['id'], 't': 'buscar', 'l': r['origem'], 'fixa': True, 'chega': r['chegouOrigemEm']}, {'c': r['id'], 't': 'deixar', 'l': r['destino']}]
    else:
        par = [{'c': r['id'], 't': 'deixar', 'l': r['destino']}]
    return {'partida': pos, 'inicio': t, 'paradas': par}


def _ocupacao_ok(paradas, corridas, nova, cap):
    """Em nenhum trecho o carro leva mais gente do que cabe."""
    def pax(cid):
        return (nova if cid == nova['id'] else corridas[cid]).get('passageiros') or 1
    bordo = sum(pax(cid) for cid in {s['c'] for s in paradas} if (corridas.get(cid) or {}).get('status') == 'em_viagem')
    for s in paradas:
        bordo += pax(s['c']) if s['t'] == 'buscar' else -pax(s['c'])
        if bordo > cap:
            return False
    return True


def carona(r, m, corridas, t):
    """Melhor jeito de encaixar a corrida r no carro m, que já está em rota. None = não cabe / desvio grande."""
    if not CFG.get('compartilhar') or r.get('urgente') or not m.get('ativo') or m.get('modo') == 'pane':
        return None
    if m['id'] in (r.get('recusadoPor') or []):
        return None
    dc = do_carro(m['id'], corridas)
    if dc['fila'] or not dc['ativas'] or any(c.get('urgente') for c in dc['ativas']):
        return None
    base = _roteiro_atual(m, corridas, t)
    if not base or not base['paradas']:
        return None
    antes = _cronometrar(base['partida'], base['inicio'], base['paradas'])
    entrega_antes = {s['c']: s['chega'] for s in antes if s['t'] == 'deixar'}
    ult_deixar = max(i for i, s in enumerate(base['paradas']) if s['t'] == 'deixar')
    cap = m.get('capacidade') or 4
    nb, nd = {'c': r['id'], 't': 'buscar', 'l': r['origem']}, {'c': r['id'], 't': 'deixar', 'l': r['destino']}
    melhor = None
    for i in range(ult_deixar + 1):  # busca o novo antes de deixar quem já estava
        if i == 0 and base['paradas'][0].get('fixa'):
            continue  # o carro está parado embarcando alguém: primeiro termina esse embarque
        par = base['paradas'][:i] + [nb] + base['paradas'][i:] + [nd]
        if not _ocupacao_ok(par, corridas, r, cap):
            continue
        crono = _cronometrar(base['partida'], base['inicio'], par)
        atraso = max([0] + [s['chega'] - entrega_antes[s['c']] for s in crono if s['t'] == 'deixar' and s['c'] in entrega_antes])
        if atraso > CFG['desvioMaxMin'] * MIN:
            continue
        busca = next(s for s in crono if s['c'] == r['id'] and s['t'] == 'buscar')
        entrega = next(s for s in crono if s['c'] == r['id'] and s['t'] == 'deixar')
        cand = {'m': m, 'paradas': crono, 'partida': base['partida'], 'busca': busca['chega'], 'entrega': entrega['chega'],
                'atraso': atraso, 'outras': [c['id'] for c in dc['ativas']],
                'livres': cap - sum((c.get('passageiros') or 1) for c in dc['ativas'])}
        if not melhor or (cand['busca'], cand['atraso']) < (melhor['busca'], melhor['atraso']):
            melhor = cand
    return melhor


def _rerotear(m, t, partida, remover=(), fixar=None, inserir=None):
    """Atualiza o roteiro do carro depois de uma parada feita/cancelada. → ops do motorista."""
    rot = m.get('roteiro')
    if not rot:
        return []
    par = []
    for s in rot.get('paradas') or []:
        if (s['c'], s['t']) in remover:
            continue
        s = dict(s)
        if fixar and (s['c'], s['t']) == fixar:
            s.update({'fixa': True, 'chega': t})
        par.append(s)
    if inserir:
        par.insert(0, inserir)
    if not par:
        return [{'col': 'motoristas', 'id': m['id'], 'patch': {'roteiro': None}}]
    return [{'col': 'motoristas', 'id': m['id'], 'patch': {'roteiro': {
        'partida': {'x': partida['x'], 'y': partida['y']}, 'inicio': t, 'paradas': _cronometrar(partida, t, par)}}}]


def _parada(m, cid, tipo):
    rot = (m or {}).get('roteiro') or {}
    return next((s for s in rot.get('paradas') or [] if s['c'] == cid and s['t'] == tipo), None)


def eta_destino(r, motoristas, t):
    """Previsão de chegada ao destino de uma corrida em viagem (considera as paradas da viagem compartilhada)."""
    s = _parada(motoristas.get(r.get('carro')), r['id'], 'deixar')
    if s:
        return s['chega']
    if r.get('iniciouViagemEm'):
        return r['iniciouViagemEm'] + viagem_ms(LOC[r['origem']], LOC[r['destino']])
    return None


# Quando a corrida atual do carro termina e onde ele fica.
def _fim_da_atual(r, t):
    o, d = LOC[r['origem']], LOC[r['destino']]
    emb, des = CFG['embarqueMin'] * MIN, CFG['desembarqueMin'] * MIN
    if r['status'] == 'a_caminho':
        fim = max(t, r['saiuParaBuscaEm'] + viagem_ms(r.get('posSaida') or o, o)) + emb + viagem_ms(o, d) + des
    elif r['status'] == 'no_local':
        fim = max(t, r['chegouOrigemEm'] + emb) + viagem_ms(o, d) + des
    else:
        fim = max(t, r['iniciouViagemEm'] + viagem_ms(o, d)) + des
    return {'fim': fim, 'pos': d}


# Previsão de chegada de um carro numa origem. `ate_id`: considera só a fila antes dessa corrida.
def estimar(m, corridas, origem_id, t, *, urgente=False, ate_id=None):
    dc = do_carro(m['id'], corridas)
    atual, fila = dc['atual'], dc['fila']
    fim, pos, a_frente = t, {'x': m['x'], 'y': m['y']}, 0
    rot = m.get('roteiro')
    if rot and rot.get('paradas'):  # viagem compartilhada: fica livre na última parada
        ult = rot['paradas'][-1]
        fim, pos, a_frente = max(t, ult['sai']), LOC[ult['l']], len({s['c'] for s in rot['paradas']})
    elif atual:
        f = _fim_da_atual(atual, t)
        fim, pos, a_frente = f['fim'], f['pos'], 1
    if not urgente:
        for r in fila:
            if r['id'] == ate_id:
                break
            o, d = LOC[r['origem']], LOC[r['destino']]
            fim += viagem_ms(pos, o) + CFG['embarqueMin'] * MIN + viagem_ms(o, d) + CFG['desembarqueMin'] * MIN
            pos = d
            a_frente += 1
    return {'chegadaEm': _n(fim + viagem_ms(pos, LOC[origem_id])), 'aFrente': a_frente, 'livre': not atual and not fila,
            'livreEm': fim, 'saiDe': pos}


# ETA atual de uma corrida já atribuída (para mostrar ao passageiro).
def eta_da_corrida(r, motoristas, corridas, t):
    s = _parada(motoristas.get(r.get('carro')), r['id'], 'buscar') if r['status'] == 'a_caminho' else None
    if s:
        return s['chega']
    if r['status'] == 'a_caminho':
        return _n(r['saiuParaBuscaEm'] + viagem_ms(r.get('posSaida') or LOC[r['origem']], LOC[r['origem']]))
    if r['status'] in FILA and motoristas.get(r.get('carro')):
        return estimar(motoristas[r['carro']], corridas, r['origem'], t, ate_id=r['id'])['chegadaEm']
    return None


def posicao(m, corridas, t):
    def lerp(a, b, ini, dur):
        f = 1 if dur <= 0 else min(1, max(0, (t - ini) / dur))
        pts = TRACADO(a, b) if TRACADO else None
        if pts and len(pts) > 1:
            return _ao_longo(pts, f)  # anda pela estrada
        return {'x': _n(a['x'] + (b['x'] - a['x']) * f), 'y': _n(a['y'] + (b['y'] - a['y']) * f)}

    rot = m.get('roteiro')
    if rot and rot.get('paradas'):  # viagem compartilhada: segue o roteiro de paradas
        prev, tp = rot['partida'], rot['inicio']
        for s in rot['paradas']:
            L = LOC[s['l']]
            if not s.get('fixa') and t < s['chega']:
                return lerp(prev, L, tp, s['chega'] - tp)
            if t < s['sai']:
                return {'x': L['x'], 'y': L['y']}
            prev, tp = L, s['sai']
        return {'x': prev['x'], 'y': prev['y']}
    atual = do_carro(m['id'], corridas)['atual']
    if not atual:
        return {'x': m['x'], 'y': m['y']}
    o, d = LOC[atual['origem']], LOC[atual['destino']]

    if atual['status'] == 'a_caminho':
        de = atual.get('posSaida') or o
        return lerp(de, o, atual['saiuParaBuscaEm'], viagem_ms(de, o))
    if atual['status'] == 'no_local':
        return {'x': o['x'], 'y': o['y']}
    return lerp(o, d, atual['iniciouViagemEm'], viagem_ms(o, d))


def escolher(r, motoristas, corridas, t):
    melhor = None
    for m in _lista(motoristas):
        if not m.get('ativo') or (m.get('capacidade') or 4) < r['passageiros'] or m['id'] in (r.get('recusadoPor') or []):
            continue
        e = estimar(m, corridas, r['origem'], t, urgente=r.get('urgente'))
        if not melhor or e['chegadaEm'] < melhor['e']['chegadaEm']:
            melhor = {'m': m, 'e': e}
    return melhor


# Explicação da escolha: a conta de cada carro para buscar na origem de r (para a central ver o "porquê")
def explicar(r, motoristas, corridas, t):
    linhas = []
    for m in _lista(motoristas):
        dc = do_carro(m['id'], corridas)
        base = {'carro': m['id'], 'nome': m.get('nome'), 'capacidade': m.get('capacidade') or 4}
        if not m.get('ativo'):
            linhas.append({**base, 'fora': 'fora de operação'})
            continue
        if (m.get('capacidade') or 4) < r['passageiros']:
            linhas.append({**base, 'fora': f"só tem {m.get('capacidade') or 4} lugares"})
            continue
        if m['id'] in (r.get('recusadoPor') or []):
            linhas.append({**base, 'fora': 'recusou esta corrida'})
            continue
        e = estimar(m, corridas, r['origem'], t, urgente=r.get('urgente'))
        atual = dc['atual']
        linhas.append({**base, 'livre': e['livre'], 'aFrente': e['aFrente'],
                       'atual': atual and {'id': atual['id'], 'status': atual['status'], 'origem': atual['origem'], 'destino': atual['destino']},
                       'fila': len(dc['fila']),
                       'terminaEmMin': round((e['livreEm'] - t) / MIN, 1), 'deslocamentoMin': round((e['chegadaEm'] - e['livreEm']) / MIN, 1),
                       'kmAteOrigem': round(dist_km(e['saiDe'], LOC[r['origem']]), 2), 'chegaEmMin': round((e['chegadaEm'] - t) / MIN, 1)})
    for m in _lista(motoristas):
        cr = carona(r, m, corridas, t)
        if cr:
            linhas.append({'carro': m['id'], 'nome': m.get('nome'), 'capacidade': m.get('capacidade') or 4, 'compartilhada': True,
                           'livre': False, 'aFrente': 0, 'fila': 0, 'outras': cr['outras'], 'lugaresLivres': cr['livres'],
                           'atrasoOutrosMin': round(cr['atraso'] / MIN, 1), 'terminaEmMin': 0,
                           'deslocamentoMin': round((cr['busca'] - t) / MIN, 1), 'kmAteOrigem': round(dist_km(cr['partida'], LOC[r['origem']]), 2),
                           'chegaEmMin': round((cr['busca'] - t) / MIN, 1), 'entregaEmMin': round((cr['entrega'] - t) / MIN, 1),
                           'paradas': [{'c': s['c'], 't': s['t'], 'l': s['l']} for s in cr['paradas']]})
    ok = sorted((l for l in linhas if 'fora' not in l), key=lambda l: l['chegaEmMin'])
    return ok + [l for l in linhas if 'fora' in l]


# Atribui a corrida r (dict já montado) → ops
def _atribuir(r, motoristas, corridas, t):
    ops = []
    best = escolher(r, motoristas, corridas, t)
    decisao = {'em': t, 'urgente': bool(r.get('urgente')), 'candidatos': explicar(r, motoristas, corridas, t)}
    if not best and CFG.get('compartilhar'):
        caronas = [cr for cr in (carona(r, mm, corridas, t) for mm in _lista(motoristas)) if cr]
        if caronas:
            return _atribuir_carona(r, min(caronas, key=lambda x: (x['busca'], x['atraso'])), corridas, t, decisao)
    if not best:
        cap = max([0, *((m.get('capacidade') or 4) for m in _lista(motoristas) if m.get('ativo'))])
        patch = {'status': 'sem_carro', 'carro': None, 'decisao': decisao,
                 'observacao': f"Nenhum carro ativo com {r['passageiros']} lugares: dividir o grupo." if r['passageiros'] > cap else 'Nenhum carro disponível no momento.'}
        return [{'col': 'corridas', 'id': r['id'], 'patch': patch}]
    caronas = [cr for cr in (carona(r, mm, corridas, t) for mm in _lista(motoristas)) if cr]
    cr = min(caronas, key=lambda x: (x['busca'], x['atraso'])) if caronas else None
    if cr and cr['busca'] <= best['e']['chegadaEm'] + CFG['toleranciaCompartilharMin'] * MIN:
        return _atribuir_carona(r, cr, corridas, t, decisao)
    m, e = best['m'], best['e']
    for l in decisao['candidatos']:
        l['escolhido'] = l['carro'] == m['id'] and not l.get('compartilhada')
    base = {'carro': m['id'], 'atribuidoEm': t, 'etaPrevistoEm': e['chegadaEm'], 'aFrenteNaAtribuicao': e['aFrente'], 'observacao': '', 'decisao': decisao}
    if e['livre']:
        base.update({'status': 'a_caminho', 'direto': True, 'aceitoEm': t, 'saiuParaBuscaEm': t, 'posSaida': {'x': m['x'], 'y': m['y']},
                     'kmAteOrigem': dist_km(m, LOC[r['origem']]), 'ociosoAntesMin': _n((t - m['livreDesde']) / MIN) if m.get('livreDesde') else None})
        ops.append({'col': 'motoristas', 'id': m['id'], 'patch': {'livreDesde': None}})
    else:
        fila = do_carro(m['id'], corridas)['fila']
        base['status'] = 'oferecida'
        base['direto'] = False
        base['ordem'] = fila[0]['ordem'] - 1 if r.get('urgente') and fila else t
    ops.insert(0, {'col': 'corridas', 'id': r['id'], 'patch': base})
    return ops


def _atribuir_carona(r, cr, corridas, t, decisao):
    """Coloca r no carro que já está em rota (viagem compartilhada) → ops."""
    m = cr['m']
    for l in decisao['candidatos']:
        l['escolhido'] = l['carro'] == m['id'] and bool(l.get('compartilhada'))
    decisao['compartilhada'] = True
    p = cr['partida']
    patch = {'carro': m['id'], 'atribuidoEm': t, 'etaPrevistoEm': cr['busca'], 'aFrenteNaAtribuicao': len(cr['outras']), 'observacao': '',
             'decisao': decisao, 'status': 'a_caminho', 'direto': True, 'aceitoEm': t, 'saiuParaBuscaEm': t, 'posSaida': {'x': p['x'], 'y': p['y']},
             'kmAteOrigem': dist_km(p, LOC[r['origem']]), 'ociosoAntesMin': 0, 'compartilhada': True, 'compartilhadaCom': list(cr['outras']),
             'entregaPrevistaEm': cr['entrega']}
    ops = [{'col': 'corridas', 'id': r['id'], 'patch': patch},
           {'col': 'motoristas', 'id': m['id'], 'patch': {'roteiro': {'partida': {'x': p['x'], 'y': p['y']}, 'inicio': t, 'paradas': cr['paradas']},
                                                         'livreDesde': None}}]
    for cid in cr['outras']:
        outra = corridas[cid]
        ops.append({'col': 'corridas', 'id': cid, 'patch': {'compartilhada': True, 'compartilhadaCom': [*(outra.get('compartilhadaCom') or []), r['id']]}})
    return ops


_B36 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def _novo_id(t):
    dia = datetime.fromtimestamp(t / 1000, timezone.utc).strftime('%y%m%d')
    return 'C' + dia + '-' + ''.join(random.choice(_B36) for _ in range(4))


def _passageiros(v):
    # Math.max(1, +v || 1)
    try:
        n = float(v)
    except (TypeError, ValueError):
        n = 0
    if not n or math.isnan(n):
        n = 1
    return _n(max(1, n))


# Pedido → {'corrida', 'ops'} (a primeira op é a criação da corrida)
def criar_corrida(p, est, t):
    if not LOC.get(p.get('origem')) or not LOC.get(p.get('destino')):
        raise ValueError('Escolha a origem e o destino.')
    if p['origem'] == p['destino']:
        raise ValueError('Origem e destino são iguais.')
    r = {
        'id': _novo_id(t), 'canal': p.get('canal') or 'app', 'solicitante': p.get('solicitante') or '', 'matricula': p.get('matricula') or '',
        'setor': p.get('setor') or '', 'criadoPor': p.get('criadoPor') or '',
        'origem': p['origem'], 'destino': p['destino'], 'passageiros': _passageiros(p.get('passageiros')), 'urgente': bool(p.get('urgente')),
        'textoFalado': p.get('textoFalado') or '', 'interpretacao': p.get('interpretacao') or None, 'agendadoPara': p.get('agendadoPara') or None,
        'status': 'agendada', 'carro': None, 'ordem': t, 'recusadoPor': [], 'criadoEm': t, 'kmViagem': dist_km(LOC[p['origem']], LOC[p['destino']]),
    }
    ops = [{'col': 'corridas', 'id': r['id'], 'set': r}]
    if r['agendadoPara'] and r['agendadoPara'] - CFG['antecedenciaAgendadaMin'] * MIN > t:
        return {'corrida': r, 'ops': ops}
    corr = {**est['corridas'], r['id']: r}
    a = _atribuir(r, est['motoristas'], corr, t)
    r.update(a[0]['patch'])  # a criação já sai com o carro escolhido
    return {'corrida': r, 'ops': ops + a[1:]}


# Ao liberar o carro, começa a próxima da fila (ou fica livre).
def _proxima_do_carro(m, corridas, t, pos, ignorar_id=None):
    fila = [r for r in do_carro(m['id'], corridas)['fila'] if r['id'] != ignorar_id]
    if not fila:
        return [{'col': 'motoristas', 'id': m['id'], 'patch': {'x': pos['x'], 'y': pos['y'], 'livreDesde': t}}]
    prox = fila[0]
    extra = {'aceitoEm': t, 'aceiteAutomatico': True} if prox['status'] == 'oferecida' else {}
    return [
        {'col': 'motoristas', 'id': m['id'], 'patch': {'x': pos['x'], 'y': pos['y'], 'livreDesde': None}},
        {'col': 'corridas', 'id': prox['id'], 'patch': {**extra, 'status': 'a_caminho', 'saiuParaBuscaEm': t, 'posSaida': {'x': pos['x'], 'y': pos['y']},
                                                       'kmAteOrigem': dist_km(pos, LOC[prox['origem']]), 'ociosoAntesMin': 0}},
    ]


# Ações do motorista / central → ops
def acao(nome, corrida_id, est, t, extra=None):
    extra = extra or {}
    r = est['corridas'].get(corrida_id)
    if not r:
        raise ValueError('Corrida não encontrada.')
    m = est['motoristas'].get(r['carro']) if r.get('carro') else None

    def p(patch):
        return {'col': 'corridas', 'id': corrida_id, 'patch': patch}

    st = r['status']
    if nome == 'aceitar':
        if st != 'oferecida':
            return []
        return [p({'status': 'na_fila', 'aceitoEm': t, 'aceiteAutomatico': bool(extra.get('automatico'))})]
    if nome == 'recusar':
        if st != 'oferecida':
            return []
        rec = {**r, 'recusadoPor': [*(r.get('recusadoPor') or []), r['carro']], 'carro': None, 'status': 'sem_carro'}
        corr = {**est['corridas'], corrida_id: rec}
        a = _atribuir(rec, est['motoristas'], corr, t)
        a[0]['patch'] = {'recusadoPor': rec['recusadoPor'], **a[0]['patch']}
        return a
    if nome == 'cheguei':
        if st != 'a_caminho':
            return []
        o = LOC[r['origem']]
        return [p({'status': 'no_local', 'chegouOrigemEm': t}), {'col': 'motoristas', 'id': m['id'], 'patch': {'x': o['x'], 'y': o['y']}}] \
            + _rerotear(m, t, o, fixar=(corrida_id, 'buscar'))
    if nome == 'embarcou':
        if st != 'no_local':
            return []
        return [p({'status': 'em_viagem', 'iniciouViagemEm': t})] + _rerotear(m, t, LOC[r['origem']], remover={(corrida_id, 'buscar')})
    if nome == 'desfazer_embarque':
        # o motorista iniciou a viagem sem o passageiro: volta a esperar no local de embarque
        if st != 'em_viagem':
            return []
        o = LOC[r['origem']]
        return [p({'status': 'no_local', 'iniciouViagemEm': None, 'embarqueDesfeitoEm': t}),
                {'col': 'motoristas', 'id': m['id'], 'patch': {'x': o['x'], 'y': o['y']}}] \
            + _rerotear(m, t, o, inserir={'c': corrida_id, 't': 'buscar', 'l': r['origem'], 'fixa': True, 'chega': t})
    if nome == 'finalizar':
        if st != 'em_viagem':
            return []
        corr = {**est['corridas'], corrida_id: {**r, 'status': 'concluida'}}
        d = LOC[r['destino']]
        if do_carro(m['id'], corr)['ativas']:  # viagem compartilhada: ainda tem gente para buscar/deixar
            return [p({'status': 'concluida', 'finalizadoEm': t}), {'col': 'motoristas', 'id': m['id'], 'patch': {'x': d['x'], 'y': d['y']}}] \
                + _rerotear(m, t, d, remover={(corrida_id, 'deixar')})
        fim = [{'col': 'motoristas', 'id': m['id'], 'patch': {'roteiro': None}}] if m.get('roteiro') else []
        return [p({'status': 'concluida', 'finalizadoEm': t})] + fim + _proxima_do_carro(m, corr, t, d)
    if nome == 'cancelar':
        if st not in ('agendada', 'sem_carro', 'oferecida', 'na_fila', 'a_caminho', 'no_local'):
            return []
        ops = [p({'status': 'cancelada', 'canceladoEm': t, 'observacao': extra.get('motivo') or ''})]
        if st in ('a_caminho', 'no_local') and m:
            pos = posicao(m, est['corridas'], t)
            corr = {**est['corridas'], corrida_id: {**r, 'status': 'cancelada'}}
            if do_carro(m['id'], corr)['ativas']:  # viagem compartilhada: os outros seguem
                return ops + _rerotear(m, t, pos, remover={(corrida_id, 'buscar'), (corrida_id, 'deixar')})
            fim = [{'col': 'motoristas', 'id': m['id'], 'patch': {'roteiro': None}}] if m.get('roteiro') else []
            return ops + fim + _proxima_do_carro(m, corr, t, pos, corrida_id)
        return ops
    raise ValueError('Ação desconhecida: ' + str(nome))


# Tirar/voltar carro de operação. A fila dele é redistribuída.
def definir_ativo(m_id, ativo, est, t):
    m = est['motoristas'][m_id]
    livre_desde = (None if do_carro(m_id, est['corridas'])['atual'] else t) if ativo else None
    ops = [{'col': 'motoristas', 'id': m_id, 'patch': {'ativo': ativo, 'livreDesde': livre_desde}}]
    if not ativo:
        motoristas = {**est['motoristas'], m_id: {**m, 'ativo': False}}
        corr = dict(est['corridas'])
        for r in do_carro(m_id, est['corridas'])['fila']:
            livre = {**r, 'carro': None, 'status': 'sem_carro'}
            corr[r['id']] = livre
            a = _atribuir(livre, motoristas, corr, t)
            ops.extend(a)
            corr[r['id']] = {**livre, **a[0]['patch']}
    return ops


# Tarefas periódicas (rodam só na Central): corridas sem carro, agendadas vencendo, aceite por tempo
# e, se ligado, os motoristas simulados avançando sozinhos.
def tarefas(est, t, simular=False):
    ops = []
    corr = dict(est['corridas'])
    mot = dict(est['motoristas'])

    def aplicar(lst):
        for o in lst:
            if o['col'] == 'corridas':
                corr[o['id']] = {**corr.get(o['id'], {}), **o['patch']}
            elif o['col'] == 'motoristas' and 'patch' in o:
                mot[o['id']] = {**mot.get(o['id'], {}), **o['patch']}
        ops.extend(lst)

    for r in _lista(est['corridas']):
        pend = r['status'] == 'sem_carro' or (r['status'] == 'agendada' and r.get('agendadoPara') is not None
                                              and t >= r['agendadoPara'] - CFG['antecedenciaAgendadaMin'] * MIN)
        if pend:
            a = _atribuir(corr[r['id']], mot, corr, t)
            if a[0]['patch']['status'] != 'sem_carro' or r['status'] != 'sem_carro':
                aplicar(a)
        if r['status'] == 'oferecida':
            m = est['motoristas'].get(r.get('carro'))
            vencida = r.get('atribuidoEm') is not None and t - r['atribuidoEm'] > CFG['aceiteSeg'] * 1000
            if (simular and m and m.get('modo') == 'simulado') or vencida:
                aplicar(acao('aceitar', r['id'], {'motoristas': mot, 'corridas': corr}, t, {'automatico': True}))
    if simular:
        for mid in list(mot):
            m = mot[mid]
            if m.get('modo') != 'simulado':
                continue
            rot = m.get('roteiro')
            if rot and rot.get('paradas'):  # viagem compartilhada: faz a próxima parada na hora prevista
                s, e2 = rot['paradas'][0], {'motoristas': mot, 'corridas': corr}
                rr = corr.get(s['c'])
                if not rr or rr['status'] not in ATUAL:
                    aplicar(_rerotear(m, t, posicao(m, corr, t), remover={(s['c'], 'buscar'), (s['c'], 'deixar')}))
                elif s['t'] == 'buscar' and rr['status'] == 'a_caminho' and t >= s['chega']:
                    aplicar(acao('cheguei', rr['id'], e2, t))
                elif s['t'] == 'buscar' and rr['status'] == 'no_local' and t >= s['sai']:
                    aplicar(acao('embarcou', rr['id'], e2, t))
                elif s['t'] == 'deixar' and rr['status'] == 'em_viagem' and t >= s['sai']:
                    aplicar(acao('finalizar', rr['id'], e2, t))
                continue
            r = do_carro(m['id'], corr)['atual']
            if not r:
                continue
            o, d = LOC[r['origem']], LOC[r['destino']]
            e2 = {'motoristas': mot, 'corridas': corr}
            if r['status'] == 'a_caminho' and t >= r['saiuParaBuscaEm'] + viagem_ms(r.get('posSaida') or o, o):
                aplicar(acao('cheguei', r['id'], e2, t))
            elif r['status'] == 'no_local' and t >= r['chegouOrigemEm'] + CFG['embarqueMin'] * MIN:
                aplicar(acao('embarcou', r['id'], e2, t))
            elif r['status'] == 'em_viagem' and t >= r['iniciouViagemEm'] + viagem_ms(o, d) + CFG['desembarqueMin'] * MIN:
                aplicar(acao('finalizar', r['id'], e2, t))
    return ops


# Indicadores para gestão (minutos)
def indicadores(est, t):
    def mm(a, b):
        return _n((b - a) / MIN) if a is not None and b is not None else None

    linhas = []
    for r in sorted(_lista(est['corridas']), key=lambda c: c['criadoEm']):
        g = r.get
        linhas.append({
            **r,
            'espera': mm(g('criadoEm'), g('chegouOrigemEm')),
            'aceite': 0 if g('direto') else mm(g('atribuidoEm'), g('aceitoEm')),
            'emFila': mm(g('aceitoEm') or g('atribuidoEm'), g('saiuParaBuscaEm')),
            'deslocamento': mm(g('saiuParaBuscaEm'), g('chegouOrigemEm')),
            'embarque': mm(g('chegouOrigemEm'), g('iniciouViagemEm')),
            'viagem': mm(g('iniciouViagemEm'), g('finalizadoEm')),
            'total': mm(g('criadoEm'), g('finalizadoEm')),
            'erroEta': mm(g('etaPrevistoEm'), g('chegouOrigemEm')),
        })

    def media(a):
        v = [x for x in a if x is not None]
        return _n(_soma(v) / len(v)) if v else None

    def pct(a, p):
        v = sorted(x for x in a if x is not None)
        return v[min(len(v) - 1, math.floor(p * len(v)))] if v else None

    conc = [l for l in linhas if l['status'] == 'concluida']
    carros = []
    for mo in _lista(est['motoristas']):
        cs = [l for l in conc if l.get('carro') == mo['id']]
        carros.append({
            'id': mo['id'], 'nome': mo.get('nome'), 'ativo': mo.get('ativo'), 'modo': mo.get('modo'), 'corridas': len(cs),
            'recusas': len([l for l in linhas if mo['id'] in (l.get('recusadoPor') or [])]),
            'ociosoMedio': media([l.get('ociosoAntesMin') for l in cs]),
            'ociosoAgora': _n((t - mo['livreDesde']) / MIN) if mo.get('livreDesde') else 0,
            'kmVazio': _n(_soma((l.get('kmAteOrigem') or 0) for l in cs)), 'kmComPassageiro': _n(_soma((l.get('kmViagem') or 0) for l in cs)),
        })
    espera = [l['espera'] for l in conc]
    return {'linhas': linhas, 'carros': carros, 'resumo': {
        'total': len(linhas), 'concluidas': len(conc), 'canceladas': len([l for l in linhas if l['status'] == 'cancelada']),
        'abertas': len([l for l in linhas if l['status'] in ABERTAS]),
        'esperaMedia': media(espera), 'esperaMediana': pct(espera, 0.5), 'esperaP90': pct(espera, 0.9),
        'emFilaMedio': media([l['emFila'] for l in conc]), 'deslocamentoMedio': media([l['deslocamento'] for l in conc]),
        'viagemMedia': media([l['viagem'] for l in conc]),
        'erroEtaMedio': media([None if l['erroEta'] is None else abs(l['erroEta']) for l in conc]),
        'diretas': len([l for l in conc if l.get('direto')]),
        'ocorrencias': len([l for l in linhas if l.get('ocorrencia')]),
        'compartilhadas': len([l for l in linhas if l.get('compartilhada')]),
        'porCanal': {c: len([l for l in linhas if l.get('canal') == c]) for c in ('app', 'radio', 'voz')},
    }}
