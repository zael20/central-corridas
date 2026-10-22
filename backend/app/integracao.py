"""Entrega das viagens encerradas para fora da central (Microsoft 365 da Hydro).

O Power Automate (conector RSS, que é padrão na licença do Microsoft 365) lê o feed
/api/integracao/corridas.rss a cada 30 min e grava cada viagem numa Lista do SharePoint.
Cada item do feed é UMA viagem encerrada (concluída ou cancelada) e leva no resumo
um JSON com todos os campos, já com os tempos calculados.

Protegido pela variável INTEGRACAO_CHAVE: sem ela configurada os endereços nem existem.
"""
import json
from datetime import datetime
from email.utils import format_datetime

from .central import FUSO, nome_local

ENCERRADAS = ('concluida', 'cancelada')
TEMPOS = ('espera', 'aceite', 'emFila', 'deslocamento', 'embarque', 'viagem', 'total', 'erroEta')


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, FUSO).isoformat(timespec='seconds') if ms else None


def _num(v):
    return round(v, 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def encerrada_em(l):
    return l.get('finalizadoEm') if l['status'] == 'concluida' else (l.get('canceladoEm') or l.get('criadoEm'))


def viagens(ind, motoristas):
    """Uma linha plana por viagem encerrada, em ordem de encerramento (a mais antiga primeiro)."""
    nomes = {m['id']: m.get('nome') for m in motoristas.values()}
    out = []
    for l in ind['linhas']:
        if l['status'] not in ENCERRADAS:
            continue
        fim = encerrada_em(l)
        out.append({
            'id': l['id'],
            'status': l['status'],
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
            'iniciouViagemEm': _iso(l.get('iniciouViagemEm')),
            'encerradaEm': _iso(fim),
            **{f'{k}Min': _num(l.get(k)) for k in TEMPOS},
            'kmAteOrigem': _num(l.get('kmAteOrigem')),
            'kmViagem': _num(l.get('kmViagem')),
            'ocorrencia': l.get('ocorrencia') or '',
            'observacao': l.get('observacao') or '',
            '_fim': fim or 0,
        })
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
