"""Interpretador de pedidos de corrida falados no rádio (porte fiel de app-corridas/parser.js).

Entrada: o texto já transcrito do áudio. Saída: dict com origem, destino, passageiros, urgência,
solicitante etc. + confiança + o que falta. As chaves seguem o JS (camelCase).
"""
import math
import re
import unicodedata

from .locais import LOCAIS

NUMEROS = {
    'um': 1, 'uma': 1, 'dois': 2, 'duas': 2, 'tres': 3, 'quatro': 4, 'cinco': 5, 'seis': 6, 'sete': 7, 'oito': 8,
    'nove': 9, 'dez': 10, 'onze': 11, 'doze': 12, 'treze': 13, 'catorze': 14, 'quatorze': 14, 'quinze': 15,
}
PESSOAS = {'pessoa', 'pessoas', 'passageiro', 'passageiros', 'colaborador', 'colaboradores', 'funcionario',
           'funcionarios', 'tecnico', 'tecnicos', 'operador', 'operadores', 'pax', 'visitante', 'visitantes', 'gente',
           'cara', 'caras'}
ORIGEM_FORTE = {'de', 'do', 'da', 'dos', 'das', 'saindo', 'sair', 'buscar', 'busca', 'pegar', 'pega', 'origem',
                'estou', 'estamos', 'to', 'tamo', 'qth', 'partindo', 'embarque', 'aqui', 'parado', 'parados',
                'aguardando', 'esperando'}
DESTINO_FORTE = {'para', 'pra', 'pro', 'pras', 'pros', 'ate', 'destino', 'levar', 'leva', 'levando', 'deixar',
                 'deixa', 'ir', 'vai', 'indo', 'vou', 'vamos', 'sentido', 'chegar', 'rumo'}
ORIGEM_FRACA = {'no', 'na', 'em', 'nos', 'nas'}
ENVIAR = {'manda', 'mande', 'mandar', 'envia', 'envie', 'enviar', 'encaminha', 'encaminhar', 'despacha'}
VEICULO = {'carro', 'viatura', 'veiculo', 'conducao', 'transporte', 'van', 'caminhonete', 'hilux', 'uber',
           'motorista', 'pool', 'pul', 'poll', 'pulso', 'pulo', 'povo', 'pum', 'puu', 'pull', 'corrida'}
URGENCIA = ['urgente', 'urgencia', 'emergencia', 'emergente', 'acidente', 'prioridade', 'mais rapido possivel',
            'o quanto antes']
SETORES = ['manutencao', 'seguranca', 'operacao', 'producao', 'rh', 'ti', 'eletrica', 'mecanica', 'meio ambiente',
           'planejamento', 'almoxarifado', 'geologia', 'topografia', 'beneficiamento', 'mina', 'saude', 'logistica',
           'portaria',
           # gerências da planilha (algumas também são locais, ex.: GAINF)
           'gainf', 'gemin', 'gemmi', 'geman', 'gapro', 'gaint', 'gaimp', 'gammi', 'gapmi', 'gamea', 'gaeem',
           'automacao', 'confiabilidade']
NAO_NOME = {'o', 'a', 'da', 'do', 'de', 'central', 'base', 'operador', 'motorista', 'pessoal', 'equipe', 'turma', 'eu'}

_A = re.ASCII
RE_HORA_DOIS_PONTOS = re.compile(r'(\d+)\s*:\s*(\d+)', _A)
# "matrícula 123", "minha matrícula é 123", "matrícula número 1 2 3"
RE_MATRICULA = re.compile(r'matricula(?: (?:e|eh|numero|n|no|de))* ((?:\d+ ?)+)', _A)
RE_NOME_MATRICULA = re.compile(
    r'(?:^| )(?:eu sou|sou|aqui e|o meu nome e|meu nome e|o meu nome|meu nome|quem fala e|fala|me chamo)(?: o| a)? '
    r'((?:[a-z]+ ){0,4}?[a-z]+)(?: (?:e )?(?:a )?minha)? matricula')
# palavras que aparecem logo antes de "matrícula" e não são nome (ruído da transcrição, verbos comuns)
NAO_NOME_ANTES = {'pool', 'carro', 'viagem', 'corrida', 'minha', 'meu', 'sua', 'com', 'e', 'a', 'o', 'da', 'do', 'de', 'na', 'no',
                  'opa', 'ola', 'oi', 'google', 'alo', 'bom', 'dia', 'tarde', 'noite', 'central', 'base', 'cambio', 'ok', 'sim', 'nao',
                  'chegou', 'saiu', 'cancela', 'cancelar', 'informacao', 'favor', 'aqui', 'certo', 'entao', 'tudo', 'bem'}
RE_NOME = re.compile(
    r'(?:^| )(?:aqui e|aqui quem fala e|fala|sou|meu nome e|quem fala e)(?: o| a)? ([a-z]+)'
    r'(?: (?:da|do|de) ((?:meio ambiente)|[a-z]+))?')
RE_DIGITO = re.compile(r'^\d$', _A)
RE_DIGITOS = re.compile(r'^\d+$', _A)


def normalizar(texto):
    """Sem acento, minúsculas, só [a-z0-9] e espaços simples ("14:30" vira "14h30")."""
    t = str(texto) if texto else ''
    t = unicodedata.normalize('NFD', t.lower())
    t = re.sub('[̀-ͯ]', '', t)
    t = RE_HORA_DOIS_PONTOS.sub(r'\1h\2', t)
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def _levenshtein(a, b):
    if abs(len(a) - len(b)) > 1:
        return 2
    d = [[i] + [0] * len(b) for i in range(len(a) + 1)]
    for j in range(1, len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (0 if a[i - 1] == b[j - 1] else 1))
    return d[len(a)][len(b)]


def _parecida(tok, alvo):
    # tolera 1 letra errada em palavras de 6+ letras (erro comum da transcrição)
    if tok == alvo:
        return 0
    if len(alvo) >= 6 and len(tok) >= 5 and _levenshtein(tok, alvo) <= 1:
        return 1
    return -1


def _capitalizar(s):
    return s[:1].upper() + s[1:]


def _num(w):
    if w is None:
        return None
    return int(w) if RE_DIGITOS.match(w) else NUMEROS.get(w)


def _arredondar(x):
    # Math.round do JS (meio arredonda para cima)
    return math.floor(x + 0.5)


def interpretar_pedido(texto, locais=None):
    if locais is None:
        locais = LOCAIS or []
    norm = normalizar(texto)
    tok = norm.split(' ') if norm else []
    n_tok = len(tok)
    usado = [False] * n_tok
    obs = []
    r = {'textoOriginal': texto, 'textoNormalizado': norm, 'origem': None, 'destino': None, 'passageiros': 1,
         'passageirosAssumido': True, 'urgente': False, 'solicitante': None, 'matricula': None, 'setor': None,
         'horario': None, 'confianca': 0, 'faltando': [], 'observacoes': obs}

    def t(i):
        return tok[i] if 0 <= i < n_tok else None

    def marcar(trecho):
        pal = trecho.strip().split(' ')
        for i in range(n_tok - len(pal) + 1):
            if all(tok[i + k] == w for k, w in enumerate(pal)):
                for k in range(len(pal)):
                    usado[i + k] = True
                return

    # 0) "eu sou Fulano de Tal, matrícula 12345, ..."
    mm0 = RE_MATRICULA.search(norm)
    if mm0:
        r['matricula'] = mm0.group(1).replace(' ', '')
        marcar(mm0.group(0))
    mm0 = RE_NOME_MATRICULA.search(norm)
    if mm0 and mm0.group(1).split(' ')[0] not in NAO_NOME:
        r['solicitante'] = ' '.join(_capitalizar(p) for p in mm0.group(1).split(' '))
        marcar(mm0.group(0))
    elif r['matricula'] and texto:
        # "Bruna Misael matrícula 123", "Carlos, matrícula 7788": a palavra com maiúscula logo antes de "matrícula"
        m_ant = re.search(r'([A-Za-zÀ-ÿ]+)[\s,.;:-]*matr[ií]cula', str(texto), re.I)
        if m_ant and m_ant.group(1)[:1].isupper():
            w = normalizar(m_ant.group(1))
            apelidos = {ap for l in (locais or []) for ap in l['apelidos']}
            if len(w) >= 3 and w not in NAO_NOME and w not in NAO_NOME_ANTES and w not in VEICULO and w not in apelidos:
                r['solicitante'] = _capitalizar(w)
                obs.append('Nome pego pela palavra antes de "matrícula". Confirme.')

    # 1) Solicitante e setor: "aqui é o João da manutenção", "fala Carlos da elétrica"
    m = RE_NOME.search(norm)
    if not r['solicitante'] and m and m.group(1) not in NAO_NOME and not NUMEROS.get(m.group(1)):
        r['solicitante'] = _capitalizar(m.group(1))
        consumido = m.group(0).strip().split(' ')
        if m.group(2) and m.group(2) in SETORES:
            r['setor'] = _capitalizar(m.group(2))
        elif m.group(2):
            consumido = consumido[:-2]  # "da X" não é setor: devolve para a análise de locais
        ini = tok.index(consumido[0]) if consumido[0] in tok else -1
        i = ini
        while 0 <= i < ini + len(consumido):
            usado[i] = True
            i += 1

    # 2) Locais citados (maior apelido primeiro)
    cand = []
    for loc in locais:
        for ap in loc['apelidos']:
            at = ap.split(' ')
            for i in range(n_tok - len(at) + 1):
                erro, ok = 0, True
                for k in range(len(at)):
                    p = _parecida(tok[i + k], at[k])
                    if p < 0:
                        ok = False
                        break
                    erro += p
                # "portaria dois visitantes": o número é quantidade de pessoas, não parte do local
                ult = at[-1]
                if ok and (NUMEROS.get(ult) or RE_DIGITO.match(ult)) and (
                        t(i + len(at)) in PESSOAS or t(i + len(at) + 1) in PESSOAS):
                    ok = False
                if ok:
                    cand.append({'loc': loc, 'ini': i, 'fim': i + len(at), 'erro': erro, 'tam': len(at)})
    cand.sort(key=lambda c: (-c['tam'], c['erro'], c['ini']))
    achados = []
    for c in cand:
        if any(usado[i] for i in range(c['ini'], c['fim'])):
            continue
        for i in range(c['ini'], c['fim']):
            usado[i] = True
        achados.append(c)
    achados.sort(key=lambda c: c['ini'])

    # 3) Papel de cada local olhando as palavras antes dele
    fim_anterior = 0
    for a in achados:
        a['papel'] = None
        janela = tok[max(fim_anterior, a['ini'] - 4):a['ini']][::-1]
        fraca = False
        for k, w in enumerate(janela):
            if w in DESTINO_FORTE:
                # "manda um carro pra portaria" = o carro vai buscar na portaria
                a['papel'] = 'destino'
                if any(x in VEICULO for x in janela[k + 1:k + 4]):
                    a['carroPara'] = True
                break
            if w in ORIGEM_FORTE:
                a['papel'] = 'origem'
                break
            if w in ORIGEM_FRACA:
                fraca = True
        if not a['papel'] and fraca:
            a['papel'] = 'origem_fraca'
        fim_anterior = a['fim']
    # remove repetições seguidas do mesmo local ("na oficina, oficina")
    lista = [a for i, a in enumerate(achados) if i == 0 or a['loc']['id'] != achados[i - 1]['loc']['id']]

    # "carro para X": X é onde buscar se houver verbo de envio ou outro local já for destino
    tem_envio = any(w in ENVIAR for w in tok)
    for a in lista:
        if a.get('carroPara') and not any(b['papel'] == 'origem' for b in lista):
            outro_destino = any(b is not a and b['papel'] == 'destino' for b in lista)
            if tem_envio or outro_destino:
                a['papel'] = 'origem_carro'
    origem = next((a for a in lista if a['papel'] == 'origem'), None)
    destino = next((a for a in lista if a['papel'] == 'destino'), None)
    if not origem:
        origem = next((a for a in lista if a['papel'] in ('origem_carro', 'origem_fraca') and a is not destino), None)
    por_ordem = origem_por_setor = False
    id_o = origem['loc']['id'] if origem else None
    id_d = destino['loc']['id'] if destino else None
    resto = [a for a in lista if a is not origem and a is not destino and a['loc']['id'] != id_o and a['loc']['id'] != id_d]
    if not destino and resto:
        destino = resto[-1]
        if not destino['papel']:
            por_ordem = True
    if not origem and resto and resto[0] is not destino:
        origem = resto[0]
        por_ordem = True
    if origem and destino and origem['loc']['id'] == destino['loc']['id']:
        destino = None
    if len(lista) == 1 and not lista[0]['papel']:
        origem, destino = lista[0], None
        obs.append('Só um local citado e sem indicação de "de"/"para": considerado como origem.')

    # "aqui é o João da GAINF" sem outra origem: a gerência também é um local -> vira origem
    if not origem and r['setor']:
        setor_norm = normalizar(r['setor'])
        loc = next((l for l in locais if setor_norm in l['apelidos'] and (not destino or destino['loc']['id'] != l['id'])), None)
        if loc:
            origem = {'loc': loc, 'erro': 0}
            obs.append(f"Origem assumida pelo setor do solicitante ({loc['nome']}). Confirme.")
            origem_por_setor = True
    r['origem'] = origem['loc']['id'] if origem else None
    r['destino'] = destino['loc']['id'] if destino else None
    com_erro = [a for a in (origem, destino) if a and a['erro'] > 0]
    if com_erro:
        obs.append('Local reconhecido por semelhança (possível erro de transcrição): '
                   + ', '.join(a['loc']['nome'] for a in com_erro) + '.')
    if por_ordem:
        obs.append('Origem/destino definidos pela ordem em que foram falados. Confirme com o solicitante.')
    if len(lista) > 2:
        obs.append('Mais de dois locais citados: ' + ', '.join(a['loc']['nome'] for a in lista) + '.')

    # 4) Passageiros
    for i in range(n_tok):
        n = _num(tok[i])
        if usado[i]:
            continue  # número que faz parte de um local ("PS 1", "Mina 5")
        if n and (t(i + 1) in PESSOAS or t(i + 2) in PESSOAS):
            r['passageiros'], r['passageirosAssumido'] = n, False
            break
    if (mm := re.search(r'eu e mais (\w+)', norm, _A)) and _num(mm.group(1)):
        r['passageiros'], r['passageirosAssumido'] = _num(mm.group(1)) + 1, False
    elif (mm := re.search(r'(?:somos|sao|seremos) (\w+)', norm, _A)) and _num(mm.group(1)):
        r['passageiros'], r['passageirosAssumido'] = _num(mm.group(1)), False
    elif r['passageirosAssumido'] and re.search(r'(?:^| )eu e (?:o|a|meu|minha) ', norm):
        r['passageiros'], r['passageirosAssumido'] = 2, False
    elif r['passageirosAssumido'] and re.search(r'(sozinho|sozinha|so eu|apenas eu|somente eu)', norm):
        r['passageirosAssumido'] = False

    # 4b) Matrícula falada sem a palavra "matrícula": "Misael 12345, ainda estou esperando"
    if not r['matricula']:
        for i in range(n_tok):
            w = tok[i]
            if usado[i] or not RE_DIGITOS.match(w) or len(w) < 3 or t(i + 1) in PESSOAS or t(i + 1) in ('minutos', 'min', 'h', 'horas'):
                continue
            if t(i - 1) in ('as', 'pras', 'daqui', 'em') or any(l['id'] == w or w in l['apelidos'] for l in locais):
                continue
            r['matricula'] = w
            obs.append('Matrícula entendida pelo número falado (sem a palavra "matrícula"). Confirme.')
            m_ant = re.search(r'([A-Za-zÀ-ÿ]+)[\s,.;:-]*' + w, str(texto))
            if not r['solicitante'] and m_ant and m_ant.group(1)[:1].isupper():
                nw = normalizar(m_ant.group(1))
                if len(nw) >= 3 and nw not in NAO_NOME and nw not in NAO_NOME_ANTES and nw not in VEICULO:
                    r['solicitante'] = _capitalizar(nw)
            break

    # 5) Urgência
    r['urgente'] = any(u in norm for u in URGENCIA)

    # 6) Horário: "as 14h", "as 14h30", "as 14 horas", "daqui a 10 minutos", "em 15 min"
    if mm := re.search(r'(?:as|para as|pras) (\d{1,2})(?:h| horas?)(\d{2})?', norm, _A):
        r['horario'] = {'tipo': 'agendado', 'hh': int(mm.group(1)), 'mm': int(mm.group(2) or 0)}
    elif (mm := re.search(r'(?:daqui a|daqui|em) (\w+) (?:minutos|min)', norm, _A)) and _num(mm.group(1)):
        r['horario'] = {'tipo': 'em_minutos', 'minutos': _num(mm.group(1))}

    # 7) Confiança e pendências
    c = 1
    if not r['origem']:
        r['faltando'].append('origem')
        c -= 0.4
    if not r['destino']:
        r['faltando'].append('destino')
        c -= 0.3
    if por_ordem:
        c -= 0.15
    if origem_por_setor:
        c -= 0.15
    if com_erro:
        c -= 0.1 * len(com_erro)
    if r['passageirosAssumido']:
        c -= 0.05
    r['confianca'] = max(0, _arredondar(c * 100) / 100)
    return r
