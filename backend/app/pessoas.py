"""Cadastro que aprende quem é quem: liga nome ↔ matrícula a partir do que as pessoas falam.

A transcrição de voz erra ("Sabio" no lugar de "Sávio", um dígito trocado na matrícula). Por isso cada vez que
alguém fala nome + matrícula, isso vira um VOTO (tabela pessoas_obs). A combinação que mais se repete vence:
se a pessoa falou 3 vezes e saiu 3 nomes diferentes, a 4ª vez que repetir um deles desempata.
Pesos: fala transcrita 1 · dados digitados no app 2 · motorista confirmou 3 · central corrigiu 10.

Depois a pessoa pode falar só a matrícula, só o nome, ou os dois: `resolver` devolve a matrícula e o nome certos.
"""
import re
import time
import unicodedata

PESO = {'fala': 1, 'app': 2, 'motorista': 3, 'central': 10}


def _sem_acento(s):
    s = unicodedata.normalize('NFD', str(s or '').lower())
    return re.sub(r'[^a-z ]', ' ', re.sub('[̀-ͯ]', '', s)).split()


def fonetico(palavra):
    """Chave de som do português: Misael = Mizael, Sávio = Sabio, Thiago = Tiago, Luiz = Luis."""
    w = ''.join(_sem_acento(palavra))
    for a, b in (('ph', 'f'), ('th', 't'), ('ch', 'x'), ('sh', 'x'), ('lh', 'li'), ('nh', 'ni'), ('qu', 'k'), ('gu', 'g'),
                 ('ss', 's'), ('sc', 's'), ('z', 's'), ('w', 'v'), ('y', 'i'), ('k', 'c'), ('v', 'b')):
        w = w.replace(a, b)
    w = re.sub(r'c(?=[ei])', 's', w).replace('h', '')
    w = re.sub(r'(.)\1+', r'\1', w)
    return w


def _lev(a, b):
    if abs(len(a) - len(b)) > 2:
        return 3
    d = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, cb in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (ca != cb))
    return d[-1]


def primeiro_nome(nome):
    p = _sem_acento(nome)
    return p[0] if p else ''


def nomes_parecidos(a, b):
    """Mesmo primeiro nome, tolerando erro de transcrição."""
    fa, fb = fonetico(primeiro_nome(a)), fonetico(primeiro_nome(b))
    if not fa or not fb:
        return False
    return fa == fb or (min(len(fa), len(fb)) >= 4 and _lev(fa, fb) <= 1)


def so_digitos(m):
    return re.sub(r'\D', '', str(m or ''))


class Pessoas:
    def __init__(self, db, lock):
        self.db, self.lock = db, lock
        self.db.execute('''CREATE TABLE IF NOT EXISTS pessoas_obs (
            n INTEGER PRIMARY KEY AUTOINCREMENT, em INTEGER, nome TEXT, matricula TEXT, fonte TEXT, peso REAL, corrida TEXT)''')
        self.db.commit()

    # ---------- votos ----------
    def registrar(self, nome, matricula, fonte='fala', corrida=None):
        nome, matricula = (nome or '').strip(), so_digitos(matricula)
        if not nome or len(matricula) < 2 or not primeiro_nome(nome):
            return
        with self.lock:
            self.db.execute('INSERT INTO pessoas_obs (em, nome, matricula, fonte, peso, corrida) VALUES (?,?,?,?,?,?)',
                            (int(time.time() * 1000), nome, matricula, fonte, PESO.get(fonte, 1), corrida))
            self.db.commit()

    def _obs(self):
        return [dict(zip(('nome', 'matricula', 'fonte', 'peso', 'em'), r))
                for r in self.db.execute('SELECT nome, matricula, fonte, peso, em FROM pessoas_obs')]

    # ---------- perfis consolidados ----------
    def perfis(self):
        """{matricula: perfil} com o nome mais votado de cada matrícula."""
        por_mat = {}
        for o in self._obs():
            por_mat.setdefault(o['matricula'], []).append(o)
        res = {}
        for mat, obs in por_mat.items():
            grupos = {}  # som do primeiro nome → {peso, nomes}
            for o in obs:
                g = grupos.setdefault(fonetico(primeiro_nome(o['nome'])), {'peso': 0, 'nomes': {}, 'fontes': set()})
                g['peso'] += o['peso']
                g['nomes'][o['nome']] = g['nomes'].get(o['nome'], 0) + o['peso']
                g['fontes'].add(o['fonte'])
            ordem = sorted(grupos.values(), key=lambda g: -g['peso'])
            top = ordem[0]
            segundo = ordem[1]['peso'] if len(ordem) > 1 else 0
            nome = max(top['nomes'].items(), key=lambda kv: (kv[1], len(kv[0])))[0]
            res[mat] = {'matricula': mat, 'nome': nome, 'votos': top['peso'], 'vezes': len(obs),
                        'confirmado': top['peso'] >= 2 and top['peso'] >= 2 * segundo or 'central' in top['fontes'],
                        'variantes': sorted({n for g in ordem for n in g['nomes']}), 'ultimaVez': max(o['em'] for o in obs)}
        return res

    def resolver(self, nome=None, matricula=None):
        """O que foi ouvido → {'matricula', 'nome', 'matriculas' (candidatas), 'confirmado', 'corrigiu'}."""
        matricula = so_digitos(matricula)
        perfis = self.perfis()
        out = {'matricula': matricula or None, 'nome': nome or None, 'matriculas': {matricula} if matricula else set(),
               'confirmado': False, 'corrigiu': None}
        if matricula:
            exata = perfis.get(matricula)
            # um dígito trocado/a mais/a menos na transcrição
            parecidas = [q for m, q in perfis.items() if m != matricula and len(matricula) >= 3 and _lev(m, matricula) <= 1]
            p = None
            if nome:
                mesmo_nome = [q for q in ([exata] if exata else []) + parecidas if nomes_parecidos(q['nome'], nome)]
                if mesmo_nome:  # a matrícula ouvida conta meio voto a mais (foi o que a pessoa falou agora)
                    p = max(mesmo_nome, key=lambda q: q['votos'] + (0.5 if q['matricula'] == matricula else 0))
            if not p:
                p = exata or (parecidas[0] if len(parecidas) == 1 and not nome else None)
            if p and p['matricula'] != matricula:
                out['corrigiu'] = f"matrícula {matricula} → {p['matricula']}"
            if p:
                out['matriculas'].add(p['matricula'])
                out['matricula'] = p['matricula']
                if p['confirmado'] or not nome:
                    out['nome'] = p['nome']
                out['confirmado'] = p['confirmado'] and (not nome or nomes_parecidos(p['nome'], nome))
        elif nome:
            achadas = [q for q in perfis.values() if nomes_parecidos(q['nome'], nome)]
            # nome completo ajuda a desempatar ("Ana Paula" x "Ana Lima")
            if len(achadas) > 1 and len(_sem_acento(nome)) > 1:
                resto = set(_sem_acento(nome)[1:])
                melhores = [q for q in achadas if resto & set(_sem_acento(q['nome'])[1:])]
                achadas = melhores or achadas
            out['matriculas'] = {q['matricula'] for q in achadas}
            if len(achadas) == 1:
                out['matricula'] = achadas[0]['matricula']
                out['confirmado'] = achadas[0]['confirmado']
                if achadas[0]['confirmado']:
                    out['nome'] = achadas[0]['nome']
        return out

    def lista(self):
        return sorted(self.perfis().values(), key=lambda p: -p['ultimaVez'])
