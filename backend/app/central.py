"""Regras da central: pedidos falados, pedidos de informação, checagem do motorista e tarefas periódicas.

Une o motor de despacho (despacho.py), o assistente (assistente.py) e o estado gravado (estado.py).
Tudo que precisa chegar aos aplicativos sai por `self.avisar(msg)` (o servidor manda por WebSocket).
"""
import math
import re
import random
import string
from datetime import datetime, timedelta, timezone

from . import despacho
from .assistente import entender, normalizar, preencher
from .pessoas import nomes_parecidos
from .estado import agora, geo_para_xy, xy_para_geo
from .locais import LOCAIS

MIN = despacho.MIN
SECAO_CHECAGEM = {'situacao': 'checagem_motorista', 'embarque': 'checagem_embarque', 'encontro': 'checagem_encontro'}
FUSO = timezone(timedelta(hours=-3))  # Paragominas (sem horário de verão)
LOC = {l['id']: l for l in LOCAIS}
EM_ANDAMENTO = ('a_caminho', 'em_viagem')


def hora(ms):
    return datetime.fromtimestamp(ms / 1000, FUSO).strftime('%H:%M') if ms else ''


def nome_local(i):
    return LOC[i]['nome'] if i in LOC else (i or '')


def novo_id(prefixo):
    return prefixo + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


class Central:
    def __init__(self, estado, cfg, avisar, aprendido=None):
        self.e = estado
        self.cfg = cfg
        self.avisar = avisar
        self.aprendido = aprendido
        self.mov = {}  # carro → {x, y, desde}: último ponto onde andou (para saber há quanto tempo está parado)

    # ---------- utilidades ----------
    @property
    def corridas(self):
        return self.e.dados['corridas']

    @property
    def motoristas(self):
        return self.e.dados['motoristas']

    def lim(self, k, padrao):
        return (self.cfg['monitor'] or {}).get(k, padrao)

    def parado_min(self, carro, t):
        mv = self.mov.get(carro)
        return (t - mv['desde']) / MIN if mv else 0

    def vars_corrida(self, r, t, **extra):
        m = self.motoristas.get(r.get('carro') or '', {})
        v = {
            'nome': (r.get('solicitante') or '').split(' ')[0], 'matricula': r.get('matricula') or '',
            'origem': nome_local(r['origem']), 'destino': nome_local(r['destino']),
            'veiculo': (r.get('carro') or '').lstrip('V').lstrip('0'), 'motorista': re.sub(r'(?i)^motorista\s+', '', m.get('nome', '')),
            'status': r['status'], 'hora_fim': hora(r.get('finalizadoEm')), 'hora_chegou': hora(r.get('chegouOrigemEm')),
        }
        if m:
            pos = self.e.posicao(m, t)
            v['onde'] = self.e.local_mais_proximo(pos)['nome']
            o = LOC[r['origem']]
            v['km'] = f"{despacho.dist_km(pos, o):.1f}".replace('.', ',')
            v['parado_min'] = round(self.parado_min(m['id'], t))
        eta = despacho.eta_da_corrida(r, self.motoristas, self.corridas, t) or r.get('etaPrevistoEm')
        if r['status'] == 'em_viagem':
            eta = despacho.eta_destino(r, self.motoristas, t) or eta
        n = r.get('passageiros') or 1
        v['passageiros'] = n
        v['passageiros_txt'] = f"{n} passageiro{'s' if n > 1 else ''}"
        if r.get('compartilhada') and m:
            # quem mais está no carro e onde o carro para antes de deixar esta pessoa
            outros = [self.corridas[i] for i in (r.get('compartilhadaCom') or []) if i in self.corridas]
            v['com_quem'] = ', '.join(o.get('solicitante') or 'outro passageiro' for o in outros) or 'outro passageiro'
            par = ((m.get('roteiro') or {}).get('paradas')) or []
            minha = next((i for i, s in enumerate(par) if s['c'] == r['id'] and s['t'] == 'deixar'), len(par))
            antes = [nome_local(s['l']) for s in par[:minha] if s['t'] == 'deixar' and s['c'] != r['id']]
            v['parada_antes'] = ' e '.join(dict.fromkeys(antes)) or ''
            dest = next((s for s in par if s['c'] == r['id'] and s['t'] == 'deixar'), None)
            if dest:
                v['hora_destino'] = hora(dest['chega'])
        if r['status'] == 'agendada':
            eta = r.get('agendadoPara')
        if eta:
            v['min'] = max(1, round((eta - t) / MIN))
            v['minutos'] = f"{v['min']} minuto{'s' if v['min'] > 1 else ''}"
            v['hora'] = hora(eta)
            v['atraso_min'] = max(0, round((t - eta) / MIN))
        if r.get('carro') and r['status'] in despacho.FILA:
            v['antes'] = despacho.estimar(m, self.corridas, r['origem'], t, ate_id=r['id'])['aFrente']
        elif r.get('aFrenteNaAtribuicao'):
            v['antes'] = r['aFrenteNaAtribuicao']
        if r['status'] == 'sem_carro':
            v['posicao'] = sum(1 for c in self.corridas.values() if c['status'] == 'sem_carro' and c['criadoEm'] <= r['criadoEm']) or 1
        v.update(extra)
        return v

    def falar(self, texto, r=None, canal=None, alvo=None):
        """Manda uma fala para quem pediu (app do passageiro) e para a central (rádio)."""
        alvo = alvo or {}
        msg = {'tipo': 'fala', 'texto': texto, 'canal': canal or (r or {}).get('canal'), 'corrida': (r or {}).get('id'),
               'matricula': alvo.get('matricula') or (r or {}).get('matricula'), 'clienteId': alvo.get('clienteId') or (r or {}).get('criadoPor')}
        self.avisar(msg)
        self.e.evento('fala', texto, corrida=msg['corrida'])

    def identificar(self, nome=None, matricula=None):
        """Nome/matrícula ouvidos → pessoa do cadastro (corrige erro de transcrição). Ver pessoas.py."""
        return self.e.pessoas.resolver(nome, matricula)

    def corrida_da_pessoa(self, matricula=None, nome=None, cliente=None, idt=None):
        """Viagem da pessoa: aberta tem prioridade; senão a última das últimas 12 h.
        Acha por matrícula (inclusive as parecidas do cadastro), só pelo nome (parecido) ou pelo aparelho."""
        t = agora()
        idt = idt or self.identificar(nome, matricula)
        mats = idt['matriculas']
        nome = idt.get('nome') or nome

        def nota(c):
            if mats and c.get('matricula') in mats:
                return 3
            outra_mat = matricula and c.get('matricula') and c['matricula'] not in mats
            if nome and not outra_mat and nomes_parecidos(c.get('solicitante'), nome):
                return 2
            if not matricula and not nome and cliente and c.get('criadoPor') == cliente:
                return 1
            return 0
        cs = [(nota(c), c) for c in self.corridas.values() if t - c['criadoEm'] < 12 * 3600_000]
        cs = [(n, c) for n, c in cs if n]
        if not cs:
            return None
        cs.sort(key=lambda nc: (nc[1]['status'] in despacho.ABERTAS, nc[0], nc[1]['criadoEm']), reverse=True)
        return cs[0][1]

    # ---------- pedido falado (rádio ou app) ----------
    def mensagem(self, texto, canal='radio', ident=None):
        ident = ident or {}
        t = agora()
        self.cfg.recarregar()
        ent = entender(texto, self.cfg, self.aprendido)
        it, intencao = ent['interp'], ent['intencao']
        ouvida_mat, ouvido_nome = it.get('matricula') or '', it.get('solicitante') or ''
        # quem é: o que foi falado vale mais que os dados salvos no aparelho
        matricula = ouvida_mat or (ident.get('matricula') if not ouvido_nome or not ident.get('nome') or nomes_parecidos(ouvido_nome, ident.get('nome')) else '') or ''
        nome = ouvido_nome or (ident.get('nome') if not ouvida_mat or ouvida_mat == (ident.get('matricula') or '') else '') or ''
        idt = self.identificar(nome, matricula)
        matricula = idt['matricula'] or matricula
        nome = idt['nome'] or nome
        # cada vez que a pessoa fala nome + matrícula é um voto no cadastro (a combinação que mais repete vence)
        if ouvido_nome and ouvida_mat:
            self.e.pessoas.registrar(ouvido_nome, ouvida_mat, 'fala')
        if ident.get('nome') and ident.get('matricula') and (not ouvida_mat or ouvida_mat == ident['matricula']):
            self.e.pessoas.registrar(ident['nome'], ident['matricula'], 'app')
        quem = {'nome': nome.split(' ')[0] if nome else '', 'matricula': matricula}
        cliente = ident.get('clienteId')
        minha = self.corrida_da_pessoa(matricula, nome, cliente, idt) if (matricula or nome or cliente) else None
        intencao, motivo = self._pelo_contexto(intencao, texto, it, minha)
        if intencao == 'pedir':
            res = self._pedir(texto, it, canal, ident, nome, matricula, t, ent['fonte'], minha)
        elif intencao == 'informacao':
            res = self._informar(matricula, nome, cliente, quem, t, idt)
        elif intencao == 'cancelar':
            res = self._cancelar(matricula, nome, cliente, quem, t, idt)
        elif intencao == 'nao_embarquei':
            res = self._nao_embarquei(matricula, nome, cliente, quem, t, idt)
        elif intencao == 'nao_encontrei':
            res = self._nao_encontrei(matricula, nome, cliente, quem, t, idt)
        elif intencao == 'reclamacao':
            res = self._reclamacao(texto, matricula, nome, cliente, quem, t, idt)
        elif ouvido_nome and ouvida_mat:
            res = {'fala': self.cfg.resposta('identificado', **quem)}
        else:
            res = {'fala': self.cfg.resposta('nao_entendi', **quem)}
        if motivo and motivo.startswith('a pessoa já tem'):
            res['fala'] = self.cfg.resposta('ja_tem_viagem', **quem) + ' ' + res['fala']
        r = res.get('corrida')
        detalhes = {k: it.get(k) for k in ('origem', 'destino', 'passageiros', 'urgente', 'confianca', 'faltando')}
        detalhes.update({'ouvido': {'nome': ouvido_nome, 'matricula': ouvida_mat}, 'cadastro': idt.get('corrigiu'), 'contexto': motivo})
        self.e.registrar_conversa(canal=canal, matricula=matricula, nome=nome, texto=texto, intencao=intencao, fonte=ent['fonte'] + ('+contexto' if motivo else ''),
                                  resposta=res['fala'], corrida=r and r['id'], detalhes=detalhes)
        self.e.evento('radio' if canal == 'radio' else 'app', f'“{texto}” → {intencao}', corrida=r and r['id'])
        self.avisar({'tipo': 'estado'})
        return {'intencao': intencao, 'fonte': ent['fonte'], 'fala': res['fala'], 'interpretacao': it, 'corrida': r, 'criada': res.get('criada', False),
                'pessoa': {'nome': nome, 'matricula': matricula, 'confirmado': idt.get('confirmado'), 'corrigiu': idt.get('corrigiu')}}

    # pistas de "o carro foi sem mim" e "ainda estou esperando" (usadas junto com a situação da viagem)
    RE_SAIU = re.compile(r'\b(saiu|sairam|foi embora|ja foi|partiu|arrancou|largou|me deixou|deixou eu|nao esperou|sem esperar|sem mim|sozinho|'
                         r'nao me (levou|pegou|buscou|esperou)|nao entrei|nao embarquei|nao subi|outra pessoa|outro passageiro|no meu lugar)\b')
    RE_ESPERA = re.compile(r'\b((estou|to|tou|tamo|estamos|continuo|ainda) (aqui|esperando|aguardando|no ponto|no local|parado)|esperando|aguardando|'
                           r'ninguem veio|nada do carro|nao veio|nao apareceu)\b')
    RE_NOVO = re.compile(r'\b(outra viagem|outro pool|outra corrida|mais um pool|mais uma viagem|novo pedido|outro carro|tambem preciso)\b')

    def _pelo_contexto(self, intencao, texto, it, r):
        """Ajusta a intenção pela situação da viagem da pessoa. Devolve (intenção, motivo ou None)."""
        n = normalizar(texto)
        if not r:
            return intencao, None
        st = r['status']
        recente = st == 'concluida' and agora() - (r.get('finalizadoEm') or 0) < 30 * MIN
        if (st == 'em_viagem' or recente) and intencao in ('informacao', 'pedir', 'desconhecida', 'nao_encontrei') \
                and (self.RE_SAIU.search(n) or self.RE_ESPERA.search(n)):
            return 'nao_embarquei', 'viagem já iniciada e a pessoa diz que está esperando / que o carro foi sem ela'
        if intencao == 'pedir' and st in despacho.ABERTAS and not self.RE_NOVO.search(n):
            mesma = it.get('origem') == r['origem'] and it.get('destino') == r['destino']
            if mesma or it.get('faltando') or self.RE_SAIU.search(n) or self.RE_ESPERA.search(n):
                return 'informacao', 'a pessoa já tem viagem aberta (pedido repetido ou incompleto)'
        return intencao, None

    @staticmethod
    def local_perto(lat, lon, raio_km=0.4):
        """Local cadastrado mais perto da posição (GPS do celular do passageiro), se estiver a até raio_km."""
        p = geo_para_xy(lat, lon)
        perto = min(LOCAIS, key=lambda l: math.hypot(l['x'] - p['x'], l['y'] - p['y']), default=None)
        return perto['id'] if perto and math.hypot(perto['x'] - p['x'], perto['y'] - p['y']) <= raio_km else None

    def _pedir(self, texto, it, canal, ident, nome, matricula, t, fonte, minha=None):
        # "preciso de um pool para a usina" sem dizer onde está: usa o GPS do celular do passageiro
        if 'origem' in (it.get('faltando') or []) and ident.get('lat') is not None and ident.get('lon') is not None:
            perto = self.local_perto(ident['lat'], ident['lon'])
            if perto and perto != it.get('destino'):
                it = {**it, 'origem': perto, 'faltando': [f for f in it['faltando'] if f != 'origem'], 'origemPeloGps': True}
        if it.get('faltando'):
            falta = ' e '.join('onde você está' if f == 'origem' else 'para onde vai' for f in it['faltando'])
            return {'fala': self.cfg.resposta('falta_dados', falta=falta, nome=nome.split(' ')[0] if nome else '')}
        res = despacho.criar_corrida({
            'origem': it['origem'], 'destino': it['destino'], 'passageiros': it.get('passageiros') or 1,
            'solicitante': nome, 'matricula': matricula, 'setor': it.get('setor') or '', 'urgente': it.get('urgente'),
            'canal': canal, 'criadoPor': ident.get('clienteId') or '', 'textoFalado': texto, 'agendadoPara': self.agendamento(it.get('horario'), t),
            'interpretacao': {'fonte': fonte, 'confianca': it.get('confianca'), 'origem': it.get('origem'), 'destino': it.get('destino'),
                              'origemPeloGps': bool(it.get('origemPeloGps'))},
            'posPassageiro': {'lat': ident['lat'], 'lon': ident['lon'], 'precisao': ident.get('precisao'), 'em': t} if ident.get('lat') is not None else None,
            # pedido para outra pessoa: guarda quem pediu (a matrícula de quem está com o celular)
            'pedidoPor': {'nome': ident.get('nome') or '', 'matricula': ident['matricula']} if ident.get('matricula') and ident['matricula'] != matricula else None,
        }, self.e.est(), t)
        r, ops = res['corrida'], res['ops']
        if not r.get('carro') and r['status'] != 'agendada':
            r['avisarQuandoTiverCarro'] = True
        self.e.aplicar(ops)
        r = self.corridas[r['id']]
        return {'fala': self.fala_confirmacao(r, t), 'corrida': r, 'criada': True}

    def fala_confirmacao(self, r, t):
        v = self.vars_corrida(r, t)
        if r['status'] == 'agendada':
            f = self.cfg.resposta('agendada', **v)
        elif not r.get('carro'):
            f = self.cfg.resposta('na_fila_geral', **v)
        elif r.get('compartilhada'):
            f = self.cfg.resposta('confirmada_compartilhada' if v.get('parada_antes') else 'confirmada_compartilhada_mesmo_destino', **v)
        else:
            antes = '' if r.get('direto') else self.cfg.resposta('confirmada_antes', **v)
            f = self.cfg.resposta('confirmada', antes_txt=antes, **v)
        depois = self.cfg.resposta('depois_de_confirmar', **v)
        if depois and f.endswith('Câmbio.'):
            return f[:-len('Câmbio.')] + depois + ' Câmbio.'
        return f + (' ' + depois if depois else '')

    @staticmethod
    def agendamento(h, t):
        if not h:
            return None
        if h.get('tipo') == 'em_minutos':
            return t + h['minutos'] * MIN
        d = datetime.fromtimestamp(t / 1000, FUSO).replace(hour=h['hh'], minute=h['mm'], second=0, microsecond=0)
        ms = int(d.timestamp() * 1000)
        return ms if ms > t else None

    def _da_pessoa(self, matricula, nome, cliente, quem, t, idt):
        """Acha a viagem da pessoa ou devolve a resposta de "não achei"."""
        if not matricula and not nome and not cliente:
            return None, {'fala': self.cfg.resposta('sem_identificacao')}
        r = self.corrida_da_pessoa(matricula, nome, cliente, idt)
        if not r:
            return None, {'fala': self.cfg.resposta('sem_corrida' if matricula else 'sem_corrida_nome', **quem)}
        return r, None

    def _informar(self, matricula, nome, cliente, quem, t, idt=None):
        r, falha = self._da_pessoa(matricula, nome, cliente, quem, t, idt)
        if falha:
            return falha
        v = self.vars_corrida(r, t)
        if not v['nome']:
            v['nome'] = quem['nome']
        st = r['status']
        chave = {'sem_carro': 'info_sem_carro', 'agendada': 'info_agendada', 'oferecida': 'info_na_fila_motorista',
                 'na_fila': 'info_na_fila_motorista', 'no_local': 'info_no_local', 'em_viagem': 'info_em_viagem',
                 'concluida': 'info_concluida', 'cancelada': 'info_cancelada'}.get(st, 'info_a_caminho')
        if st == 'a_caminho':
            if v.get('parado_min', 0) >= self.lim('info_parado_min', 2):
                chave = 'info_parado'
                self.checar_motorista(r, 'parado', t, avisar=True)
            elif v.get('atraso_min', 0) >= 1:
                chave = 'info_atrasado'
                self.checar_motorista(r, 'atraso', t, avisar=True)
        return {'fala': self.cfg.resposta(chave, **v), 'corrida': r}

    def _cancelar(self, matricula, nome, cliente, quem, t, idt=None):
        r = self.corrida_da_pessoa(matricula, nome, cliente, idt) if (matricula or nome or cliente) else None
        if r and r['status'] == 'em_viagem':
            return {'fala': self.cfg.resposta('cancelar_em_viagem', **self.vars_corrida(r, t)), 'corrida': r}
        if not r or r['status'] not in despacho.ABERTAS:
            return {'fala': self.cfg.resposta('nada_para_cancelar', **quem)}
        self.e.aplicar(despacho.acao('cancelar', r['id'], self.e.est(), t, {'motivo': 'cancelado pelo passageiro'}))
        r = self.corridas[r['id']]
        return {'fala': self.cfg.resposta('cancelada_ok', **self.vars_corrida(r, t)), 'corrida': r}

    def _quem_txt(self, r, nome, matricula):
        n = nome or r.get('solicitante') or 'Passageiro'
        m = matricula or r.get('matricula')
        return f"{n}{' (mat. ' + m + ')' if m else ''}"

    def _situacao_sem_nome(self, matricula, nome, cliente, quem, t, idt, v):
        situacao = self._informar(matricula, nome, cliente, quem, t, idt)['fala']
        situacao = re.sub(r'^\s*' + re.escape(v['nome'] or '') + r',\s*', '', situacao, flags=re.I).replace(' Câmbio.', '').strip()
        return situacao[:1].upper() + situacao[1:]

    def _nao_embarquei(self, matricula, nome, cliente, quem, t, idt=None):
        """"A viagem começou mas eu não entrei no carro": o motorista confirma quem está no carro e a central é avisada."""
        r, falha = self._da_pessoa(matricula, nome, cliente, quem, t, idt)
        if falha:
            return falha
        v = self.vars_corrida(r, t)
        v['nome'] = v['nome'] or quem['nome']
        recente = r['status'] == 'concluida' and t - (r.get('finalizadoEm') or 0) < 60 * MIN
        if r['status'] != 'em_viagem' and not recente:
            # ainda não começou: responde a situação, como num pedido de informação
            situacao = self._situacao_sem_nome(matricula, nome, cliente, quem, t, idt, v)
            return {'fala': self.cfg.resposta('embarque_ainda_nao_comecou', situacao=situacao, **v), 'corrida': r}
        self.checar_motorista(r, 'embarque', t, avisar=True, tipo='embarque')
        self.e.aplicar([{'col': 'corridas', 'id': r['id'], 'patch': {'ocorrencia': 'passageiro diz que não embarcou', 'ocorrenciaEm': t}}])
        alerta = f"{self._quem_txt(r, nome, matricula)} informa que não embarcou no veículo {r['carro']}, mas a viagem já foi iniciada. Pedindo ao motorista para confirmar quem está no carro."
        self.e.evento('alerta', alerta, r['id'], r['carro'])
        self.avisar({'tipo': 'alerta', 'texto': alerta, 'carro': r['carro']})
        return {'fala': self.cfg.resposta('embarque_verificando', **v), 'corrida': self.corridas[r['id']]}

    def _nao_encontrei(self, matricula, nome, cliente, quem, t, idt=None):
        """"Não estou achando o carro" / "o carro foi no lugar errado": pergunta ao motorista onde ele está."""
        r, falha = self._da_pessoa(matricula, nome, cliente, quem, t, idt)
        if falha:
            return falha
        v = self.vars_corrida(r, t)
        v['nome'] = v['nome'] or quem['nome']
        if r['status'] not in ('a_caminho', 'no_local'):
            situacao = self._situacao_sem_nome(matricula, nome, cliente, quem, t, idt, v)
            return {'fala': self.cfg.resposta('encontro_situacao', situacao=situacao, **v), 'corrida': r}
        self.checar_motorista(r, 'encontro', t, avisar=True, tipo='encontro')
        alerta = f"{self._quem_txt(r, nome, matricula)} não está encontrando o veículo {r['carro']} em {v['origem']}. Perguntando ao motorista onde ele está."
        self.e.evento('alerta', alerta, r['id'], r['carro'])
        self.avisar({'tipo': 'alerta', 'texto': alerta, 'carro': r['carro']})
        return {'fala': self.cfg.resposta('encontro_verificando', **v), 'corrida': r}

    def _reclamacao(self, texto, matricula, nome, cliente, quem, t, idt=None):
        """Reclamação do motorista/viagem: registra a ocorrência na viagem e avisa a central."""
        r = self.corrida_da_pessoa(matricula, nome, cliente, idt) if (matricula or nome or cliente) else None
        quem_txt = self._quem_txt(r or {}, nome, matricula)
        if r:
            self.e.aplicar([{'col': 'corridas', 'id': r['id'], 'patch': {'ocorrencia': 'reclamação: ' + texto[:200], 'ocorrenciaEm': t}}])
        alerta = f"Reclamação de {quem_txt}{' sobre o veículo ' + r['carro'] if r and r.get('carro') else ''}: “{texto[:160]}”"
        self.e.evento('ocorrencia', alerta, r and r['id'], r and r.get('carro'))
        self.avisar({'tipo': 'alerta', 'texto': alerta, 'carro': r and r.get('carro')})
        v = self.vars_corrida(r, t) if r else dict(quem)
        v['nome'] = v.get('nome') or quem['nome']
        return {'fala': self.cfg.resposta('reclamacao_registrada', **v), 'corrida': r}

    # ---------- checagem do motorista ----------
    def checar_motorista(self, r, motivo, t, avisar=False, tipo='situacao'):
        aberta = next((c for c in self.e.dados['checagens'].values() if c['corrida'] == r['id'] and (c.get('tipo') or 'situacao') == tipo
                       and not c.get('respondidaEm') and not c.get('encerrada')), None)
        if aberta:
            if avisar and not aberta.get('avisar'):
                self.e.aplicar([{'col': 'checagens', 'id': aberta['id'], 'patch': {'avisar': True}}])
            return aberta
        cc = self.cfg[SECAO_CHECAGEM.get(tipo, 'checagem_motorista')]
        v = self.vars_corrida(r, t)
        texto = preencher(cc.get('texto', 'Você está bem? Aconteceu algo?'), v)
        chk = {'id': novo_id('K'), 'corrida': r['id'], 'carro': r['carro'], 'criadoEm': t, 'motivo': motivo, 'tipo': tipo, 'avisar': avisar,
               'texto': texto, 'opcoes': [{'id': o['id'], 'texto': preencher(o['texto'], v), 'pedeDados': bool(o.get('pede_dados'))} for o in cc.get('opcoes', [])],
               # dados do passageiro registrado, para o motorista conferir com quem está no carro
               'passageiro': {'nome': r.get('solicitante') or '', 'matricula': r.get('matricula') or ''}}
        self.e.aplicar([{'col': 'checagens', 'id': chk['id'], 'set': chk}])
        if tipo == 'embarque':
            self.e.evento('checagem', f"Pedindo ao motorista do {r['carro']} para confirmar quem está no carro (passageiro diz que não entrou)", r['id'], r['carro'])
        elif tipo == 'encontro':
            self.e.evento('checagem', f"Perguntando ao motorista do {r['carro']} onde ele está (passageiro não encontra o carro)", r['id'], r['carro'])
        else:
            self.e.evento('checagem', f"Perguntando ao motorista do {r['carro']} ({motivo}, parado {round(self.parado_min(r['carro'], t))} min)", r['id'], r['carro'])
        self.avisar({'tipo': 'checagem', 'carro': r['carro'], 'checagem': chk})
        return chk

    def responder_checagem(self, chk_id, opcao_id, texto_livre='', dados=None):
        t = agora()
        chk = self.e.dados['checagens'].get(chk_id)
        if not chk or chk.get('respondidaEm'):
            return chk
        if chk.get('tipo') == 'embarque':
            return self._responder_embarque(chk, opcao_id, t, dados or {})
        op = next((o for o in self.cfg[SECAO_CHECAGEM.get(chk.get('tipo'), 'checagem_motorista')].get('opcoes', []) if o['id'] == opcao_id), None)
        resposta = texto_livre or (op and op.get('para_passageiro')) or opcao_id
        self.e.aplicar([{'col': 'checagens', 'id': chk_id, 'patch': {'respondidaEm': t, 'resposta': opcao_id, 'respostaTexto': resposta}}])
        r = self.corridas.get(chk['corrida'])
        self.e.evento('checagem', f"Motorista do {chk['carro']} respondeu: {resposta}", chk['corrida'], chk['carro'])
        self.mov.pop(chk['carro'], None)  # recomeça a contagem de parado
        if r and opcao_id == 'problema' and self.lim('redespachar_se_problema', True) and r['status'] in ('a_caminho', *despacho.FILA):
            self.redespachar(r, t)
        if r and chk.get('avisar'):
            self.falar(self.cfg.resposta('motorista_respondeu', **self.vars_corrida(r, t, resposta_motorista=resposta)), r)
        self.avisar({'tipo': 'estado'})
        return self.e.dados['checagens'][chk_id]

    def _responder_embarque(self, chk, opcao_id, t, dados):
        op = next((o for o in self.cfg['checagem_embarque'].get('opcoes', []) if o['id'] == opcao_id), None)
        p = chk.get('passageiro') or {}
        texto_op = preencher((op or {}).get('texto') or opcao_id, {'nome': p.get('nome'), 'matricula': p.get('matricula')})
        no_carro = {'nome': (dados.get('nome') or '').strip(), 'matricula': re.sub(r'\D', '', dados.get('matricula') or '')}
        quem_foi = ''
        if no_carro['nome'] or no_carro['matricula']:
            quem_foi = f"{no_carro['nome'] or '?'}{', mat. ' + no_carro['matricula'] if no_carro['matricula'] else ''}"
            texto_op += f' (no carro: {quem_foi})'
        self.e.aplicar([{'col': 'checagens', 'id': chk['id'], 'patch': {'respondidaEm': t, 'resposta': opcao_id, 'respostaTexto': texto_op, 'noCarro': no_carro}}])
        self.e.evento('checagem', f"Motorista do {chk['carro']} respondeu sobre o embarque: {texto_op}", chk['corrida'], chk['carro'])
        r = self.corridas.get(chk['corrida'])
        if not r:
            return self.e.dados['checagens'][chk['id']]
        # o que o motorista confirmou vira voto forte no cadastro de pessoas
        if opcao_id == 'comigo' and r.get('solicitante') and r.get('matricula'):
            self.e.pessoas.registrar(r['solicitante'], r['matricula'], 'motorista', r['id'])
        if no_carro['nome'] and no_carro['matricula']:
            self.e.pessoas.registrar(no_carro['nome'], no_carro['matricula'], 'motorista', r['id'])
        if quem_foi:
            self.e.aplicar([{'col': 'corridas', 'id': r['id'], 'patch': {'embarcouDeFato': no_carro}}])
        v = self.vars_corrida(r, t)
        if opcao_id == 'engano' and r['status'] == 'em_viagem':
            self.e.aplicar(despacho.acao('desfazer_embarque', r['id'], self.e.est(), t))
            self.e.aplicar([{'col': 'corridas', 'id': r['id'], 'patch': {'ocorrencia': 'viagem iniciada por engano (passageiro não embarcou)'}}])
            fala = self.cfg.resposta('embarque_engano', **self.vars_corrida(self.corridas[r['id']], t))
        elif opcao_id == 'outra_pessoa':
            self.e.aplicar([{'col': 'corridas', 'id': r['id'], 'patch': {'ocorrencia': 'embarcou outra pessoa no lugar do passageiro' + (': ' + quem_foi if quem_foi else '')}}])
            res = despacho.criar_corrida({
                'origem': r['origem'], 'destino': r['destino'], 'passageiros': r.get('passageiros') or 1, 'solicitante': r.get('solicitante'),
                'matricula': r.get('matricula'), 'setor': r.get('setor'), 'urgente': True, 'canal': r.get('canal'), 'criadoPor': r.get('criadoPor'),
                'textoFalado': f"(novo pedido: na viagem {r['id']} embarcou outra pessoa)"}, self.e.est(), t)
            nova = res['corrida']
            nova['substituiCorrida'] = r['id']
            if not nova.get('carro'):
                nova['avisarQuandoTiverCarro'] = True
            self.e.aplicar(res['ops'])
            nova = self.corridas[nova['id']]
            self.e.aplicar([{'col': 'corridas', 'id': r['id'], 'patch': {'substituidaPor': nova['id']}}])
            self.e.evento('ocorrencia', f"Outra pessoa embarcou no {r['carro']}. Novo pedido {nova['id']} com prioridade para {r.get('solicitante') or 'o passageiro'}.", nova['id'], nova.get('carro'))
            if nova.get('carro'):
                vn = self.vars_corrida(nova, t)
                fala = self.cfg.resposta('embarque_outra_pessoa', novo_veiculo=vn.get('veiculo'), novo_minutos=vn.get('minutos'), **v)
            else:
                fala = self.cfg.resposta('embarque_outra_pessoa_sem_carro', **v)
            r = nova  # a fala vai para quem pediu, ligada ao novo pedido
        else:
            fala = self.cfg.resposta('embarque_comigo', **v)
            alerta = f"Motorista do {chk['carro']} diz que o passageiro {r.get('solicitante') or ''} está no carro. Confirme pelo rádio."
            self.avisar({'tipo': 'alerta', 'texto': alerta, 'carro': chk['carro']})
        self.falar(fala, r)
        self.avisar({'tipo': 'estado'})
        return self.e.dados['checagens'][chk['id']]

    def redespachar(self, r, t):
        """Carro com problema: sai de operação e a corrida volta para a fila geral (o próximo tick escolhe outro carro)."""
        carro = r['carro']
        self.e.aplicar([{'col': 'corridas', 'id': r['id'], 'patch': {'status': 'sem_carro', 'carro': None, 'recusadoPor': (r.get('recusadoPor') or []) + [carro],
                                                                     'avisarQuandoTiverCarro': True, 'observacao': f'{carro} com problema, redespachada'}}])
        self.e.aplicar(despacho.definir_ativo(carro, False, self.e.est(), t))
        self.e.evento('redespacho', f'{carro} saiu de operação (problema). Corrida volta para a fila.', r['id'], carro)

    # ---------- simulação de pane (para testar o alerta) ----------
    def pane(self, carro, ligar):
        t = agora()
        m = self.motoristas[carro]
        if ligar:
            self.e.aplicar([{'col': 'motoristas', 'id': carro, 'patch': {'paneEm': t, 'modoAntesPane': m.get('modo'), 'modo': 'pane'}}])
        elif m.get('paneEm'):
            # empurra os horários da corrida atual pelo tempo parado, para a simulação continuar de onde parou
            d = t - m['paneEm']
            ops = [{'col': 'motoristas', 'id': carro, 'patch': {'paneEm': None, 'modo': m.get('modoAntesPane') or 'simulado'}}]
            rot = m.get('roteiro')
            if rot and rot.get('paradas'):  # viagem compartilhada: empurra o roteiro inteiro
                ops[0]['patch']['roteiro'] = {**rot, 'inicio': rot['inicio'] + d,
                                              'paradas': [{**s, 'chega': s['chega'] + d, 'sai': s['sai'] + d} for s in rot['paradas']]}
            for atual in despacho.do_carro(carro, self.corridas)['ativas']:
                campo = {'a_caminho': 'saiuParaBuscaEm', 'no_local': 'chegouOrigemEm', 'em_viagem': 'iniciouViagemEm'}[atual['status']]
                ops.append({'col': 'corridas', 'id': atual['id'], 'patch': {campo: atual[campo] + d}})
            self.e.aplicar(ops)
        self.avisar({'tipo': 'estado'})

    # ---------- tarefas periódicas ----------
    def tick(self):
        t = agora()
        self.cfg.recarregar()
        ops = despacho.tarefas(self.e.est(), t, True)
        if ops:
            self.e.aplicar(ops)
        mudou = bool(ops)
        # carro parado / atrasado
        for m in self.motoristas.values():
            pos = self.e.posicao(m, t)
            mv = self.mov.get(m['id'])
            if not mv or math.hypot(pos['x'] - mv['x'], pos['y'] - mv['y']) * 1000 > self.lim('parado_metros', 40):
                self.mov[m['id']] = {'x': pos['x'], 'y': pos['y'], 'desde': t}
            atual = despacho.do_carro(m['id'], self.corridas)['atual']
            if not atual or atual['status'] not in EM_ANDAMENTO or not self.lim('perguntar_automatico', True):
                continue
            ref = atual.get('saiuParaBuscaEm') if atual['status'] == 'a_caminho' else atual.get('iniciouViagemEm')
            parado = min(self.parado_min(m['id'], t), (t - (ref or t)) / MIN)
            eta = despacho.eta_da_corrida(atual, self.motoristas, self.corridas, t) if atual['status'] == 'a_caminho' else None
            atraso = (t - eta) / MIN if eta else 0
            ja = any(c['corrida'] == atual['id'] and t - c['criadoEm'] < 2 * self.lim('parado_min', 4) * MIN for c in self.e.dados['checagens'].values())
            if not ja and (parado >= self.lim('parado_min', 4) or atraso >= self.lim('atraso_min', 5)):
                self.checar_motorista(atual, 'parado' if parado >= self.lim('parado_min', 4) else 'atraso', t)
                mudou = True
        # checagem sem resposta
        lim = (self.cfg['checagem_motorista'] or {}).get('sem_resposta_min', 3)
        for c in list(self.e.dados['checagens'].values()):
            if not c.get('respondidaEm') and not c.get('encerrada') and t - c['criadoEm'] > lim * MIN:
                self.e.aplicar([{'col': 'checagens', 'id': c['id'], 'patch': {'encerrada': True, 'semResposta': True}}])
                self.e.evento('alerta', f"Motorista do {c['carro']} não respondeu à checagem. Entrar em contato.", c['corrida'], c['carro'])
                self.avisar({'tipo': 'alerta', 'texto': f"Motorista do veículo {c['carro']} não respondeu. Entre em contato pelo rádio.", 'carro': c['carro']})
                r = self.corridas.get(c['corrida'])
                if r and c.get('avisar'):
                    self.falar(self.cfg.resposta('motorista_sem_resposta', **self.vars_corrida(r, t)), r)
                mudou = True
        # quem estava na fila geral e ganhou carro
        for r in list(self.corridas.values()):
            if r.get('avisarQuandoTiverCarro') and r.get('carro') and r['status'] in (*despacho.FILA, 'a_caminho'):
                self.e.aplicar([{'col': 'corridas', 'id': r['id'], 'patch': {'avisarQuandoTiverCarro': False}}])
                self.falar(self.cfg.resposta('atualizacao_carro', **self.vars_corrida(self.corridas[r['id']], t)), r)
                mudou = True
            elif r.get('avisarQuandoTiverCarro') and r['status'] not in ('sem_carro', 'agendada'):
                self.e.aplicar([{'col': 'corridas', 'id': r['id'], 'patch': {'avisarQuandoTiverCarro': False}}])
        return mudou

    # ---------- visão para os aplicativos ----------
    @staticmethod
    def rota_desenho(r, pos, m=None):
        """Linhas da corrida atual em [[lat, lon], ...]: até o embarque e do embarque ao destino (pela estrada quando houver).
        Viagem compartilhada: uma linha só passando por todas as paradas do roteiro, na ordem."""
        if not r:
            return None

        def linha(a, b):
            pts = (despacho.TRACADO(a, b) if despacho.TRACADO else None) or [a, b]
            return [[round(g['lat'], 6), round(g['lon'], 6)] for g in (xy_para_geo(p['x'], p['y']) for p in pts)]
        par = ((m or {}).get('roteiro') or {}).get('paradas') or []
        if par:
            pontos, prev, estrada = [], pos, True
            for s in par:
                L = LOC[s['l']]
                pontos += linha(prev, L)
                estrada = estrada and bool(despacho.TRACADO and despacho.TRACADO(prev, L))
                prev = L
            return {'busca': None, 'viagem': pontos, 'pelaEstrada': estrada,
                    'paradas': [{'c': s['c'], 't': s['t'], 'l': s['l'], 'chega': s['chega'], **xy_para_geo(LOC[s['l']]['x'], LOC[s['l']]['y'])} for s in par]}
        o, d = LOC[r['origem']], LOC[r['destino']]
        return {'busca': linha(r.get('posSaida') or pos, o) if r['status'] == 'a_caminho' else None,
                'viagem': linha(o, d), 'pelaEstrada': bool(despacho.TRACADO and despacho.TRACADO(o, d))}

    def visao(self):
        t = agora()
        carros = []
        for m in self.motoristas.values():
            pos = self.e.posicao(m, t)
            dc = despacho.do_carro(m['id'], self.corridas)
            carros.append({**m, 'pos': {**pos, **xy_para_geo(pos['x'], pos['y'])}, 'onde': self.e.local_mais_proximo(pos)['nome'],
                           'paradoMin': round(self.parado_min(m['id'], t), 1), 'atual': dc['atual'] and dc['atual']['id'],
                           'fila': [r['id'] for r in dc['fila']], 'ativas': [r['id'] for r in dc['ativas']],
                           'aBordo': sum((r.get('passageiros') or 1) for r in dc['ativas'] if r['status'] == 'em_viagem'),
                           'rota': self.rota_desenho(dc['atual'], pos, m)})
        corridas = []
        for r in sorted(self.corridas.values(), key=lambda c: -c['criadoEm']):
            if t - r['criadoEm'] > 24 * 3600_000 and r['status'] not in despacho.ABERTAS:
                continue
            eta = despacho.eta_da_corrida(r, self.motoristas, self.corridas, t) if r['status'] in ('a_caminho', *despacho.FILA) else None
            dest = despacho.eta_destino(r, self.motoristas, t) if r['status'] == 'em_viagem' else None
            corridas.append({**r, 'etaAgora': eta, 'etaDestino': dest})
        checagens = [c for c in self.e.dados['checagens'].values() if t - c['criadoEm'] < 12 * 3600_000]
        return {'agora': t, 'motoristas': carros, 'corridas': corridas, 'checagens': checagens,
                'eventos': self.e.eventos(), 'configErro': self.cfg.erro}
