"""Estado do sistema (motoristas, corridas, checagens) em memória, gravado em SQLite a cada alteração.

O motor de despacho devolve "ops" ({col, id, set} ou {col, id, patch}); aqui elas são aplicadas e gravadas.
"""
import json
import math
import sqlite3
import threading
import time

from . import despacho
from .locais import LOCAIS, ROTAS_TEMPO
from .mapa import geo_para_xy, xy_para_geo  # noqa: F401 (usados por quem importa daqui)
from .pessoas import Pessoas


def agora():
    return int(time.time() * 1000)


COLECOES = ('motoristas', 'corridas', 'checagens')


class Estado:
    def __init__(self, caminho_db):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(caminho_db, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS docs (col TEXT, id TEXT, dados TEXT, PRIMARY KEY (col, id))')
        self.db.execute('''CREATE TABLE IF NOT EXISTS conversas (
            n INTEGER PRIMARY KEY AUTOINCREMENT, em INTEGER, canal TEXT, matricula TEXT, nome TEXT, texto TEXT,
            intencao TEXT, fonte TEXT, resposta TEXT, corrida TEXT, detalhes TEXT)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS eventos (
            n INTEGER PRIMARY KEY AUTOINCREMENT, em INTEGER, tipo TEXT, corrida TEXT, carro TEXT, texto TEXT)''')
        self.db.commit()
        self.pessoas = Pessoas(self.db, self.lock)
        self.dados = {c: {} for c in COLECOES}
        for col, id_, dados in self.db.execute('SELECT col, id, dados FROM docs'):
            if col in self.dados:
                self.dados[col][id_] = json.loads(dados)
        despacho.definir_locais(LOCAIS, ROTAS_TEMPO)
        if not self.dados['motoristas']:
            self.carros_teste()

    # visão usada pelo motor
    def est(self):
        return {'motoristas': self.dados['motoristas'], 'corridas': self.dados['corridas']}

    def aplicar(self, ops):
        with self.lock:
            for op in ops:
                col, id_ = op['col'], op['id']
                if 'set' in op:
                    doc = dict(op['set'])
                elif 'apagar' in op:
                    self.dados[col].pop(id_, None)
                    self.db.execute('DELETE FROM docs WHERE col=? AND id=?', (col, id_))
                    continue
                else:
                    doc = {**self.dados[col].get(id_, {}), **op['patch']}
                self.dados[col][id_] = doc
                self.db.execute('INSERT OR REPLACE INTO docs VALUES (?,?,?)', (col, id_, json.dumps(doc, ensure_ascii=False)))
            self.db.commit()
        return ops

    def evento(self, tipo, texto, corrida=None, carro=None):
        with self.lock:
            self.db.execute('INSERT INTO eventos (em, tipo, corrida, carro, texto) VALUES (?,?,?,?,?)', (agora(), tipo, corrida, carro, texto))
            self.db.commit()

    def eventos(self, limite=60):
        cur = self.db.execute('SELECT em, tipo, corrida, carro, texto FROM eventos ORDER BY n DESC LIMIT ?', (limite,))
        return [dict(zip(('em', 'tipo', 'corrida', 'carro', 'texto'), r)) for r in cur]

    def registrar_conversa(self, **c):
        with self.lock:
            self.db.execute('INSERT INTO conversas (em, canal, matricula, nome, texto, intencao, fonte, resposta, corrida, detalhes) VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (agora(), c.get('canal'), c.get('matricula'), c.get('nome'), c.get('texto'), c.get('intencao'), c.get('fonte'),
                             c.get('resposta'), c.get('corrida'), json.dumps(c.get('detalhes'), ensure_ascii=False)))
            self.db.commit()

    def conversas(self, limite=200):
        cols = ('n', 'em', 'canal', 'matricula', 'nome', 'texto', 'intencao', 'fonte', 'resposta', 'corrida', 'detalhes')
        cur = self.db.execute(f'SELECT {", ".join(cols)} FROM conversas ORDER BY n DESC LIMIT ?', (limite,))
        linhas = [dict(zip(cols, r)) for r in cur]
        for l in linhas:
            try:
                l['detalhes'] = json.loads(l['detalhes'] or 'null')
            except ValueError:
                l['detalhes'] = None
        return linhas

    def conversa(self, n):
        r = self.db.execute('SELECT texto, intencao FROM conversas WHERE n=?', (n,)).fetchone()
        return {'n': n, 'texto': r[0], 'intencao': r[1]} if r else None

    def carros_teste(self):
        t = agora()
        loc = {l['id']: l for l in LOCAIS}
        ops = []
        for id_, nome, cap, base in [('V01', 'Motorista A', 4, 'gainf'), ('V02', 'Motorista B', 4, 'usina'),
                                     ('V03', 'Motorista C', 7, 'oficina'), ('V04', 'Motorista D', 4, 'canteiro')]:
            if id_ not in self.dados['motoristas']:
                ops.append({'col': 'motoristas', 'id': id_, 'set': {'id': id_, 'nome': nome, 'capacidade': cap, 'ativo': True,
                            'modo': 'simulado', 'x': loc[base]['x'], 'y': loc[base]['y'], 'livreDesde': t}})
        self.aplicar(ops)

    # ---------- posição dos carros ----------
    def posicao(self, m, t=None):
        """Posição atual: GPS do celular do motorista (se recente) ou a simulação do motor."""
        t = t or agora()
        gps = m.get('gps')
        if gps and t - gps['em'] < 120_000:
            return geo_para_xy(gps['lat'], gps['lon'])
        if m.get('paneEm'):
            t = m['paneEm']
        return despacho.posicao(m, self.dados['corridas'], t)

    def mover_do_local(self, antes, depois):
        """Um local mudou de posição no mapa: os carros parados nele vão junto."""
        ops = [{'col': 'motoristas', 'id': m['id'], 'patch': {'x': depois['x'], 'y': depois['y']}}
               for m in self.dados['motoristas'].values()
               if abs(m['x'] - antes['x']) < 0.05 and abs(m['y'] - antes['y']) < 0.05]
        self.aplicar(ops)

    def local_mais_proximo(self, p):
        return min(LOCAIS, key=lambda l: math.hypot(l['x'] - p['x'], l['y'] - p['y']))

