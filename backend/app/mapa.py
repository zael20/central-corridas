"""Mapa real: coordenadas dos locais, chave do satélite e rotas pela estrada.

- Os pontos marcados na central ficam em dados/pontos.json ({id: [lat, lon]}) e valem para todos os aparelhos.
- A chave do MapTiler (satélite) fica em dados/mapa.json. Sem chave, as telas usam o satélite da Esri.
- As rotas vêm do OSRM (OpenStreetMap, gratuito): uma tabela de distâncias pela estrada entre todos os locais
  e o desenho de cada trecho. Fica tudo guardado em dados/rotas.json; sem internet, vale a linha reta × fator.
"""
import json
import math
import os
import queue
import ssl
import threading
import time
from pathlib import Path

import httpx

# Referência para converter lat/lon ↔ km locais (x = leste, y = norte). Mesma do protótipo.
REF_LAT, REF_LON = -3.2505, -47.737
KX = 111.32 * math.cos(math.radians(REF_LAT))
KY = 110.57

OSRM = 'https://router.project-osrm.org'
LONGE_DA_VIA_M = 300  # ponto a mais disso da via mais próxima: o OSRM não conhece a estrada, usa linha reta
FATOR_RETA = 1.3      # trecho fora da via (do ponto até a estrada) e plano B sem rota
# certificados do Windows: na rede da empresa o HTTPS passa por um proxy que o Python sozinho não reconhece
HTTP = httpx.Client(verify=ssl.create_default_context(), timeout=20)


def geo_para_xy(lat, lon):
    return {'x': (lon - REF_LON) * KX, 'y': (lat - REF_LAT) * KY}


def xy_para_geo(x, y):
    return {'lat': REF_LAT + y / KY, 'lon': REF_LON + x / KX}


def _ler(arq, padrao):
    try:
        with open(arq, encoding='utf-8-sig') as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def _gravar(arq, dados):
    tmp = Path(str(arq) + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False)
    tmp.replace(arq)


def _reta_km(a, b):
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


class Mapa:
    def __init__(self, pasta, locais, online=True):
        self.pasta = Path(pasta)
        self.locais = locais
        self.online = online
        self.lock = threading.RLock()
        self.arq_pontos = self.pasta / 'pontos.json'
        self.arq_cfg = self.pasta / 'mapa.json'
        self.arq_rotas = self.pasta / 'rotas.json'
        # pontos marcados na central; na primeira vez (ex.: servidor novo na nuvem) usa os que vieram com o projeto
        self.pontos = _ler(self.arq_pontos, None)
        if self.pontos is None and os.environ.get('PONTOS_PADRAO') != '0':
            self.pontos = _ler(Path(__file__).resolve().parent.parent / 'config' / 'pontos_padrao.json', {})
        if self.pontos is None:
            self.pontos = {}
        self.cfg = _ler(self.arq_cfg, {})
        if not self.cfg.get('maptiler_key') and os.environ.get('MAPTILER_KEY'):
            self.cfg['maptiler_key'] = os.environ['MAPTILER_KEY']  # na nuvem a chave vem da variável de ambiente
        r = _ler(self.arq_rotas, {})
        self.tabela = r.get('tabela') or {}      # {'assinatura': ..., 'km': {'o>d': km}}
        self.tracados = r.get('tracados') or {}  # {'o>d': [[lat, lon], ...]}
        self.ao_mudar = []                        # chamadas quando chega rota nova (para atualizar as telas)
        for l in self.locais:                     # posição provisória original, para quando o ponto for apagado
            l.setdefault('x0', l['x'])
            l.setdefault('y0', l['y'])
        self.aplicar_pontos()
        self.fila = queue.Queue()
        self.pendentes = set()
        self.falhou_em = 0
        if online:
            threading.Thread(target=self._trabalhador, daemon=True).start()
            self.atualizar_tabela()

    # ---------- configuração ----------
    def config_publica(self):
        reais = [l for l in self.locais if l.get('real')]
        base = reais or self.locais
        return {'maptilerKey': self.cfg.get('maptiler_key') or '',
                'centro': [sum(l['lat'] for l in base) / len(base), sum(l['lon'] for l in base) / len(base)],
                'locaisMarcados': len(reais), 'locaisTotal': len(self.locais),
                'rotasPelaEstrada': self.tem_tabela()}

    def definir_chave(self, chave):
        with self.lock:
            self.cfg['maptiler_key'] = (chave or '').strip()
            _gravar(self.arq_cfg, self.cfg)

    # ---------- pontos dos locais ----------
    def aplicar_pontos(self):
        for l in self.locais:
            p = self.pontos.get(l['id'])
            if p:
                l.update({'lat': p[0], 'lon': p[1], **geo_para_xy(p[0], p[1]), 'real': True})
            else:
                l.update({'x': l['x0'], 'y': l['y0'], **xy_para_geo(l['x0'], l['y0']), 'real': False})

    def definir_ponto(self, lid, lat, lon):
        """Grava a posição real de um local. Devolve a posição (x, y) anterior."""
        l = next((x for x in self.locais if x['id'] == lid), None)
        if not l:
            raise ValueError('Local não encontrado.')
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError('Coordenada inválida.')
        antes = {'x': l['x'], 'y': l['y']}
        with self.lock:
            self.pontos[lid] = [round(lat, 6), round(lon, 6)]
            _gravar(self.arq_pontos, self.pontos)
            self.aplicar_pontos()
        self.atualizar_tabela()
        return antes

    def apagar_ponto(self, lid):
        l = next((x for x in self.locais if x['id'] == lid), None)
        if not l:
            raise ValueError('Local não encontrado.')
        antes = {'x': l['x'], 'y': l['y']}
        with self.lock:
            self.pontos.pop(lid, None)
            _gravar(self.arq_pontos, self.pontos)
            self.aplicar_pontos()
        self.atualizar_tabela()
        return antes

    def importar(self, pontos):
        """{id: [lat, lon]} (formato do botão "Copiar coordenadas" do painel antigo). Devolve quantos entraram."""
        ids = {l['id'] for l in self.locais}
        ok = {}
        for k, v in (pontos or {}).items():
            try:
                lat, lon = float(v[0]), float(v[1])
            except (TypeError, ValueError, IndexError):
                continue
            if k in ids and -90 <= lat <= 90 and -180 <= lon <= 180:
                ok[k] = [round(lat, 6), round(lon, 6)]
        with self.lock:
            self.pontos.update(ok)
            _gravar(self.arq_pontos, self.pontos)
            self.aplicar_pontos()
        if ok:
            self.atualizar_tabela()
        return len(ok)

    # ---------- rotas pela estrada ----------
    def _assinatura(self):
        return ';'.join(f"{l['id']}:{l['lat']:.5f},{l['lon']:.5f}" for l in self.locais)

    def tem_tabela(self):
        return bool(self.tabela.get('km')) and self.tabela.get('assinatura') == self._assinatura()

    def atualizar_tabela(self):
        if self.online and not self.tem_tabela():
            self.fila.put(('tabela', None))

    def _salvar_rotas(self):
        with self.lock:
            _gravar(self.arq_rotas, {'tabela': self.tabela, 'tracados': self.tracados})

    def _baixar_tabela(self):
        locs = list(self.locais)
        assin = self._assinatura()
        coords = ';'.join(f"{l['lon']:.6f},{l['lat']:.6f}" for l in locs)
        r = HTTP.get(f'{OSRM}/table/v1/driving/{coords}', params={'annotations': 'distance'}, timeout=20)
        r.raise_for_status()
        j = r.json()
        if j.get('code') != 'Ok':
            raise RuntimeError(j.get('code'))
        snap = [w.get('distance') or 0 for w in j['sources']]
        km = {}
        for i, a in enumerate(locs):
            for k, b in enumerate(locs):
                d = j['distances'][i][k]
                if i == k or d is None:
                    continue
                if snap[i] > LONGE_DA_VIA_M or snap[k] > LONGE_DA_VIA_M:
                    continue  # local longe de qualquer via conhecida: fica na linha reta
                km[a['id'] + '>' + b['id']] = round((d + (snap[i] + snap[k]) * FATOR_RETA) / 1000, 3)
        with self.lock:
            self.tabela = {'assinatura': assin, 'km': km, 'em': int(time.time())}
            # desenhos antigos podem ser de pontos que mudaram: refaz sob demanda
            self.tracados = {k: v for k, v in self.tracados.items() if v.get('assinatura') == self._par_assin(*k.split('>'))}
        self._salvar_rotas()

    def _par_assin(self, a, b):
        la = next((l for l in self.locais if l['id'] == a), None)
        lb = next((l for l in self.locais if l['id'] == b), None)
        if not la or not lb:
            return ''
        return f"{la['lat']:.5f},{la['lon']:.5f}>{lb['lat']:.5f},{lb['lon']:.5f}"

    def _baixar_tracado(self, a, b):
        la = next(l for l in self.locais if l['id'] == a)
        lb = next(l for l in self.locais if l['id'] == b)
        r = HTTP.get(f"{OSRM}/route/v1/driving/{la['lon']:.6f},{la['lat']:.6f};{lb['lon']:.6f},{lb['lat']:.6f}",
                      params={'overview': 'full', 'geometries': 'geojson'}, timeout=15)
        r.raise_for_status()
        j = r.json()
        if j.get('code') != 'Ok' or not j.get('routes'):
            raise RuntimeError(j.get('code'))
        longe = any((w.get('distance') or 0) > LONGE_DA_VIA_M for w in j['waypoints'])
        pts = [[round(c[1], 6), round(c[0], 6)] for c in j['routes'][0]['geometry']['coordinates']]
        pts = [[la['lat'], la['lon']]] + ([] if longe else pts) + [[lb['lat'], lb['lon']]]
        with self.lock:
            self.tracados[a + '>' + b] = {'pts': pts, 'assinatura': self._par_assin(a, b), 'longe': longe}
        self._salvar_rotas()

    def _trabalhador(self):
        while True:
            tipo, par = self.fila.get()
            try:
                if time.time() - self.falhou_em < 30:
                    time.sleep(30 - (time.time() - self.falhou_em))
                if tipo == 'tabela':
                    if not self.tem_tabela():
                        self._baixar_tabela()
                else:
                    self._baixar_tracado(*par)
                    time.sleep(0.4)  # o servidor público do OSRM pede uso moderado
                for f in self.ao_mudar:
                    f()
            except Exception:
                self.falhou_em = time.time()
                if tipo == 'tabela':
                    self.fila.put((tipo, par))  # tenta de novo depois
            finally:
                if par:
                    self.pendentes.discard('>'.join(par))

    # ---------- usado pelo motor de despacho ----------
    def _proximos(self, p, n=3):
        return sorted(self.locais, key=lambda l: _reta_km(l, p))[:n]

    def km_estrada(self, a, b):
        """Distância pela estrada entre dois pontos {x, y}. None = sem rota conhecida (o motor usa a linha reta)."""
        km = self.tabela.get('km') if self.tem_tabela() else None
        if not km:
            return None
        lb = self._proximos(b, 1)[0]
        if _reta_km(lb, b) > 0.05:
            return None  # destino não é um local cadastrado
        melhor = None
        for la in self._proximos(a):
            if _reta_km(la, a) > 0.5:
                continue  # aproximar por um local longe daria uma "rota" que não existe
            ponta = _reta_km(la, a) * FATOR_RETA
            if la['id'] == lb['id']:
                v = ponta
            elif (la['id'] + '>' + lb['id']) in km:
                v = ponta + km[la['id'] + '>' + lb['id']]
            else:
                continue
            if melhor is None or v < melhor:
                melhor = v
        return None if melhor is None else round(melhor, 3)

    def tracado(self, a, b):
        """Desenho da rota entre dois pontos {x, y}, em [{x, y}, ...]. Sem desenho ainda: None (e pede ao OSRM)."""
        la, lb = self._proximos(a, 1)[0], self._proximos(b, 1)[0]
        if la['id'] == lb['id'] or _reta_km(lb, b) > 0.05:
            return None
        chave = la['id'] + '>' + lb['id']
        t = self.tracados.get(chave)
        if t and t.get('assinatura') == self._par_assin(la['id'], lb['id']):
            if t.get('longe') or len(t['pts']) <= 2:
                return None  # o OSRM não conhece a via deste trecho: fica a linha reta
            pts = [geo_para_xy(*p) for p in t['pts']]
            return ([{'x': a['x'], 'y': a['y']}] if _reta_km(la, a) > 0.02 else []) + pts
        if self.online and chave not in self.pendentes:
            self.pendentes.add(chave)
            self.fila.put(('tracado', (la['id'], lb['id'])))
        return None
