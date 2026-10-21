"""Entrega das viagens para fora da central (Microsoft 365 da Hydro).

Tempo real: com MS_WEBHOOK_URL configurada, cada mudança de situação de uma viagem (pedida, carro
indo, no local, em viagem, concluída, cancelada) é enviada na hora para o gatilho "Quando uma
solicitação de webhook do Teams é recebida" do Power Automate (conector Teams, padrão na licença).
O fluxo cria ou atualiza o item da viagem na Lista do SharePoint.

Reserva (a cada 30 min): o Power Automate (conector RSS, que é padrão na licença do Microsoft 365) lê o feed
/api/integracao/corridas.rss a cada 30 min e grava cada viagem numa Lista do SharePoint.
Cada item do feed é UMA viagem encerrada (concluída ou cancelada) e leva no resumo
um JSON com todos os campos, já com os tempos calculados.

Protegido pela variável INTEGRACAO_CHAVE: sem ela configurada os endereços nem existem.
"""
import json
import queue
import threading
import time
import urllib.request
from datetime import datetime
from email.utils import format_datetime

from . import despacho
from .central import FUSO, nome_local

ENCERRADAS = ('concluida', 'cancelada')
SITUACAO = {'agendada': 'Agendada', 'sem_carro': 'Sem carro', 'oferecida': 'Oferecida ao motorista', 'na_fila': 'Na fila do carro',
        'a_caminho': 'Carro a caminho', 'no_local': 'Carro no local', 'em_viagem': 'Em viagem',
        'concluida': 'Concluída', 'cancelada': 'Cancelada'}
TEMPOS = ('espera', 'aceite', 'emFila', 'deslocamento', 'embarque', 'viagem', 'total', 'erroEta')


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, FUSO).isoformat(timespec='seconds') if ms else None


def _num(v):
    return round(v, 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def encerrada_em(l):
    return l.get('finalizadoEm') if l['status'] == 'concluida' else (l.get('canceladoEm') or l.get('criadoEm'))


def linha(l, nomes):
    """Viagem como linha plana (uma coluna por campo), com os tempos em minutos."""
    fim = encerrada_em(l) if l['status'] in ENCERRADAS else None
    return {
        'id': l['id'],
        'status': l['status'],
        'situacao': SITUACAO.get(l['status'], l['status']),
        'aberta': l['status'] not in ENCERRADAS,
        'data': _iso(l.get('criadoEm'))[:10] if l.get('criadoEm') else None,
        'canal': l.get('canal'),
        'solicitante': l.get('solicitante'),
        'matricula': l.get('matricula'),
        'pedidoPor': ' '.join(filter(None, [(l.get('pedidoPor') or {}).get('nome'), (l.get('pedidoPor') or {}).get('matricula')])),
        'setor': l.get('setor'),
        'origem': nome_local(l.get('origem')),
        'destino': nome_local(l.get('destino')),
        'passageiros': l.get('passageiros'),
        'urgente': bool(l.get('urgente')),
        'compartilhada': bool(l.get('compartilhada')),
        'direto': bool(l.get('direto')),
        'carro': l.get('carro'),
        'motorista': nomes.get(l.get('carro')),
        'pedidoEm': _iso(l.get('criadoEm')),
        'chegouOrigemEm': _iso(l.get('chegouOrigemEm')),
        'previstaChegadaEm': _iso(l.get('etaPrevistoEm')),
        'iniciouViagemEm': _iso(l.get('iniciouViagemEm')),
        'encerradaEm': _iso(fim),
        **{f'{k}Min': _num(l.get(k)) for k in TEMPOS},
        'kmAteOrigem': _num(l.get('kmAteOrigem')),
        'kmViagem': _num(l.get('kmViagem')),
        'ocorrencia': l.get('ocorrencia') or '',
        'observacao': l.get('observacao') or '',
        '_fim': fim or 0,
    }


def viagens(ind, motoristas):
    """Uma linha por viagem encerrada, em ordem de encerramento (a mais antiga primeiro)."""
    nomes = {m['id']: m.get('nome') for m in motoristas.values()}
    out = [linha(l, nomes) for l in ind['linhas'] if l['status'] in ENCERRADAS]
    out.sort(key=lambda v: (v['_fim'], v['id']))
    return out


def _cdata(s):
    return '<![CDATA[' + s.replace(']]>', ']]]]><![CDATA[>') + ']]>'


def rss(lista, endereco):
    """RSS 2.0. O Power Automate só pega itens com pubDate MAIOR que o último que viu,
    por isso cada viagem usa a hora em que foi encerrada (e as empatadas ganham +1 s)."""
    itens, ultimo = [], 0
    for v in lista:
        ms = max(v['_fim'], ultimo + 1000)
        ultimo = ms
        dados = {k: x for k, x in v.items() if k != '_fim'}
        titulo = f"{v['id']} · {v['origem']} → {v['destino']} · {v['status']}"
        itens.append(
        '<item>'
        f'<title>{_cdata(titulo)}</title>'
        f'<guid isPermaLink="false">{v["id"]}</guid>'
        f'<pubDate>{format_datetime(datetime.fromtimestamp(ms / 1000, FUSO))}</pubDate>'
        f'<description>{_cdata(json.dumps(dados, ensure_ascii=False))}</description>'
        '</item>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        '<title>Central de corridas - viagens encerradas</title>'
        f'<link>{endereco}</link><description>Uma entrada por viagem concluída ou cancelada</description>'
        + ''.join(reversed(itens)) + '</channel></rss>')


class Envio:
    """Manda cada mudança de situação de uma viagem para o webhook do Power Automate, sem travar a central.
    Mudanças da mesma viagem que chegam juntas viram um envio só (sempre o estado mais recente).
    Se o envio falhar (internet, Microsoft fora), tenta de novo a cada 30 s até conseguir."""
    CAMPOS = ('status', 'carro', 'chegouOrigemEm', 'iniciouViagemEm', 'finalizadoEm', 'canceladoEm', 'ocorrencia', 'compartilhada', 'etaPrevistoEm')

    def __init__(self, url, estado, agora):
        self.url, self.estado, self.agora = url, estado, agora
        self.visto = {}
        self.pendentes = queue.Queue()
        self.enviados = self.falhas = 0
        self.ultimo_erro = None
        if url:
            threading.Thread(target=self._laco, daemon=True).start()

    def observar(self, ops):
        """Chamado depois de cada alteração do estado: agenda as viagens que mudaram de situação."""
        if not self.url:
            return
        for op in ops:
            if op['col'] != 'corridas':
                continue
            r = self.estado.dados['corridas'].get(op['id'])
            if not r:
                continue
            assinatura = tuple(str(r.get(k)) for k in self.CAMPOS)
            if self.visto.get(r['id']) != assinatura:
                self.visto[r['id']] = assinatura
                self.pendentes.put(r['id'])

    def carga(self, cid):
        with self.estado.lock:  # roda fora da linha principal: lê uma foto consistente
            r = self.estado.dados['corridas'].get(cid)
            if not r:
                return None
            mot = dict(self.estado.dados['motoristas'])
            ind = despacho.indicadores({'motoristas': mot, 'corridas': {cid: dict(r)}}, self.agora())
        nomes = {m['id']: m.get('nome') for m in mot.values()}
        v = {k: x for k, x in linha(ind['linhas'][0], nomes).items() if k != '_fim'}
        v['atualizadoEm'] = _iso(self.agora())
        texto = f"{v['id']} · {v['situacao']} · {v['origem']} → {v['destino']} · {v.get('solicitante') or ''}"
        # formato aceito pelo gatilho do Teams; os dados da viagem vão em "viagem"
        return {'type': 'message', 'attachments': [{'contentType': 'application/vnd.microsoft.card.adaptive', 'contentUrl': None, 'content': {
            '$schema': 'http://adaptivecards.io/schemas/adaptive-card.json', 'type': 'AdaptiveCard', 'version': '1.2',
            'body': [{'type': 'TextBlock', 'text': texto, 'wrap': True}]}}], 'viagem': v}

    def enviar(self, cid):
        corpo = self.carga(cid)
        if corpo is None:
            return True
        req = urllib.request.Request(self.url, data=json.dumps(corpo, ensure_ascii=False).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                ok = 200 <= resp.status < 300
        except Exception as e:  # noqa: BLE001 (qualquer falha de rede vira nova tentativa)
            self.ultimo_erro, ok = f'{type(e).__name__}: {e}'[:300], False
        if ok:
            self.enviados += 1
        else:
            self.falhas += 1
        return ok

    def _laco(self):
        atrasadas = set()
        while True:
            try:
                cid = self.pendentes.get(timeout=30)
                lote = {cid}
            except queue.Empty:
                lote = set()
            time.sleep(1)  # junta mudanças em sequência da mesma viagem
            while not self.pendentes.empty():
                lote.add(self.pendentes.get_nowait())
            if not lote:  # 30 s sem novidade: hora de tentar de novo as que falharam
                lote, atrasadas = atrasadas, set()
            for c in sorted(lote):
                if not self.enviar(c):
                    atrasadas.add(c)
                else:
                    atrasadas.discard(c)

    def situacao(self):
        return {'ligado': bool(self.url), 'enviados': self.enviados, 'falhas': self.falhas, 'ultimoErro': self.ultimo_erro}
