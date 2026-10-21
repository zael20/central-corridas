"""Servidor da central de corridas (API + tempo real + aplicativos).

Rodar (na pasta backend):  uvicorn app.main:app --host 0.0.0.0 --port 8000
Aplicativos: http://<ip-do-servidor>:8000/  (Passageiro, Motorista e Central)
"""
import asyncio
import re
import csv
import io
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from collections import Counter

from . import despacho
from .assistente import INTENCOES, Aprendizado, Config, normalizar
from .central import Central, hora, nome_local
from .estado import Estado, agora, geo_para_xy
from .locais import LOCAIS
from .mapa import Mapa
from .voz import Voz
from .acesso import Acesso
from . import integracao

BASE = Path(__file__).resolve().parent.parent
FRONT = Path(os.environ.get('FRONTEND_DIR', BASE.parent / 'frontend'))
DB = os.environ.get('BANCO', str(BASE / 'dados' / 'central.db'))
DADOS = Path(DB).parent
DADOS.mkdir(parents=True, exist_ok=True)

conexoes: dict = {}  # WebSocket → {'papel', 'cliente', 'mat'}
fila_avisos: asyncio.Queue | None = None
laco: asyncio.AbstractEventLoop | None = None


def avisar(msg):
    if fila_avisos is None:
        return
    try:
        if laco and asyncio.get_running_loop() is laco:
            fila_avisos.put_nowait(msg)
            return
    except RuntimeError:
        pass  # chamado de outra thread (ex.: rota nova que chegou do OSRM)
    if laco:
        laco.call_soon_threadsafe(fila_avisos.put_nowait, msg)


# mapa real primeiro: ele ajusta as posições dos locais antes de o estado criar os carros de teste
mapa = Mapa(DADOS, LOCAIS, online=os.environ.get('ROTAS_OFFLINE') != '1')
mapa.ao_mudar.append(lambda: avisar({'tipo': 'estado'}))
despacho.VIA = mapa.km_estrada
despacho.TRACADO = mapa.tracado
# viagem compartilhada (carro em rota busca outro passageiro no caminho). Desligar: COMPARTILHAR=0
despacho.CFG['compartilhar'] = os.environ.get('COMPARTILHAR') != '0'
for k_cfg, k_desp in (('desvio_max_min', 'desvioMaxMin'), ('tolerancia_compartilhar_min', 'toleranciaCompartilharMin')):
    if mapa.cfg.get(k_cfg) is not None:
        despacho.CFG[k_desp] = float(mapa.cfg[k_cfg])
if mapa.cfg.get('compartilhar') is not None and os.environ.get('COMPARTILHAR') != '0':
    despacho.CFG['compartilhar'] = bool(mapa.cfg['compartilhar'])
if mapa.cfg.get('velocidade_kmh'):
    despacho.CFG['velocidadeKmh'] = float(mapa.cfg['velocidade_kmh'])
estado = Estado(DB)
cfg = Config()
aprendido = Aprendizado(DADOS / 'aprendido.json')
voz = Voz()
acesso = Acesso(DADOS / 'acesso.json')
central = Central(estado, cfg, avisar, aprendido)
# tempo real para o Microsoft 365: URL do gatilho de webhook do Power Automate (vazio = desligado)
envio = integracao.Envio((os.environ.get('MS_WEBHOOK_URL') or '').strip(), estado, agora)
estado.observadores.append(envio.observar)


def visao():
    return {**central.visao(), 'rotasPelaEstrada': mapa.tem_tabela(), 'compartilhar': despacho.CFG['compartilhar'],
            'desvioMaxMin': despacho.CFG['desvioMaxMin']}


def para_passageiro(est, info):
    """O passageiro só recebe as próprias viagens (pelo aparelho ou pela matrícula) e a posição dos carros."""
    def minha(r):
        return (info['cliente'] and r.get('criadoPor') == info['cliente']) or (info['mat'] and info['mat'] in (r.get('matricula'), (r.get('pedidoPor') or {}).get('matricula')))
    corridas = [r for r in est['corridas'] if minha(r)]
    carros = {r.get('carro') for r in corridas}
    motoristas = [{k: m.get(k) for k in ('id', 'nome', 'pos', 'onde', 'paradoMin', 'ativo', 'modo', 'capacidade')}
                  | ({'rota': m.get('rota'), 'atual': m.get('atual'), 'ativas': m.get('ativas'), 'fila': [],
                      'motorista': (m.get('condutor') or {}).get('nome')} if m['id'] in carros else {'fila': []})
                  for m in est['motoristas']]
    return {'agora': est['agora'], 'motoristas': motoristas, 'corridas': [{k: v for k, v in r.items() if k != 'decisao'} for r in corridas],
            'checagens': [], 'eventos': [], 'configErro': None, 'rotasPelaEstrada': est.get('rotasPelaEstrada')}


def serve_para(msg, info):
    if info['papel'] in ('operacao', 'motorista'):
        return True
    if msg.get('tipo') == 'fala':
        return bool((info['cliente'] and msg.get('clienteId') == info['cliente']) or (info['mat'] and msg.get('matricula') == info['mat']))
    return msg.get('tipo') in ('locais',)


async def enviar_todos(msg):
    est = visao() if msg.get('tipo') == 'estado' else None
    for ws_, info in list(conexoes.items()):
        try:
            if est is not None:
                await ws_.send_json({'tipo': 'estado', 'estado': est if info['papel'] != 'passageiro' else para_passageiro(est, info)})
            elif serve_para(msg, info):
                await ws_.send_json(msg)
        except Exception:
            conexoes.pop(ws_, None)


async def laco_avisos():
    while True:
        msg = await fila_avisos.get()
        await enviar_todos(msg)


async def laco_tarefas():
    # a cada 2 s: despacho pendente, simulação, carro parado, avisos. Estado completo a cada 5 s mesmo sem mudança.
    n = 0
    while True:
        await asyncio.sleep(2)
        n += 1
        try:
            mudou = central.tick()
            if mudou or n % 3 == 0:
                avisar({'tipo': 'estado'})
        except Exception as e:  # um erro numa volta não pode parar a central
            estado.evento('erro', f'{type(e).__name__}: {e}')


@asynccontextmanager
async def vida(app):
    global fila_avisos, laco
    fila_avisos = asyncio.Queue()
    laco = asyncio.get_running_loop()
    tarefas = [asyncio.create_task(laco_avisos()), asyncio.create_task(laco_tarefas())]
    yield
    for t in tarefas:
        t.cancel()


app = FastAPI(title='Central de Corridas Internas', lifespan=vida, docs_url=None, redoc_url=None, openapi_url=None)


# ---------- acesso (códigos) ----------
LIVRE = {'/api/entrar', '/api/saude'}
PASSAGEIRO_PODE = [('GET', re.compile(r'^/api/(locais|mapa/config|voz(/info)?)$')),
                   ('POST', re.compile(r'^/api/(assistente|corridas|corridas/[^/]+/cancelar)$'))]
MOTORISTA_PODE = [('GET', re.compile(r'^/api/(estado|locais|mapa/config|voz(/info)?)$')),
                  ('POST', re.compile(r'^/api/(assistente|motoristas/[^/]+/(gps|ativo|modo|condutor)|corridas/[^/]+/(cheguei|embarcou|finalizar|aceitar|recusar)|checagens/[^/]+)$'))]


def token_de(request):
    return request.headers.get('x-acesso') or request.query_params.get('t') or request.cookies.get('acesso')


@app.middleware('http')
async def exigir_codigo(request, chamar):
    p = request.url.path
    if p.startswith('/api/') and p not in LIVRE and request.method != 'OPTIONS':
        papel = acesso.papel(token_de(request))
        if not papel:
            return JSONResponse({'detail': 'Entre com a sua matrícula.', 'precisa': 'codigo'}, status_code=401)
        pode = {'passageiro': PASSAGEIRO_PODE, 'motorista': MOTORISTA_PODE}.get(papel)
        if pode is not None and not any(request.method == m and rx.match(p) for m, rx in pode):
            return JSONResponse({'detail': 'Esta parte é só da central: entre com uma matrícula de adm.', 'precisa': 'operacao'}, status_code=403)
    return await chamar(request)


# os aplicativos Android carregam as páginas deste servidor (CORS); o acesso é pelo código, não pela origem
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])


class Entrada(BaseModel):
    codigo: str = ''      # códigos gerais (antigos)
    matricula: str = ''  # acesso por pessoa: só a matrícula cadastrada na aba Acessos


@app.post('/api/entrar')
def entrar(e: Entrada):
    papel, token = acesso.entrar(e.codigo, e.matricula)
    if not papel:
        raise HTTPException(401, 'Matrícula não cadastrada. Peça à central para cadastrar.' if re.sub(r'\D', '', e.matricula) else 'Informe a matrícula.')
    u = acesso.quem(token) or {}
    mat = u.get('matricula') or re.sub(r'\D', '', e.matricula)
    nome = u.get('nome') or (estado.pessoas.perfis().get(mat) or {}).get('nome', '')  # passageiro: nome que a central já aprendeu
    return {'papel': papel, 'token': token, 'matricula': mat, 'nome': nome, 'perfil': u.get('perfil') or ('passageiro' if mat else '')}


# ---------- acesso por pessoa (aba Acessos da central; só adm / código da operação) ----------
@app.get('/api/usuarios')
def ver_usuarios():
    return acesso.usuarios()


class Usuario(BaseModel):
    nome: str = ''
    perfil: str


@app.post('/api/usuarios/{mat}')
def salvar_usuario(mat: str, u: Usuario):
    try:
        r = acesso.salvar_usuario(mat, u.nome, u.perfil)
    except ValueError as e:
        raise HTTPException(400, str(e))
    estado.pessoas.registrar(r['nome'], r['matricula'], 'central')
    estado.evento('acesso', f"Acesso de {r['nome']} (mat. {r['matricula']}) salvo como {r['perfil']}")
    return r


@app.post('/api/usuarios/{mat}/remover')
def remover_usuario(mat: str):
    try:
        acesso.remover_usuario(mat)
    except ValueError as e:
        raise HTTPException(400, str(e))
    estado.evento('acesso', f'Acesso da matrícula {mat} removido')
    return {'ok': True}


@app.get('/api/acesso')
def ver_acesso():
    return acesso.ver()


class TrocaCodigos(BaseModel):
    passageiro: str | None = None
    operacao: str | None = None


@app.post('/api/acesso')
def trocar_acesso(tc: TrocaCodigos):
    try:
        acesso.trocar(tc.passageiro, tc.operacao)
    except ValueError as e:
        raise HTTPException(400, str(e))
    estado.evento('acesso', 'Códigos de acesso trocados')
    return acesso.ver()


# ---------- leitura ----------
@app.get('/api/estado')
def ver_estado():
    return visao()


@app.get('/api/locais')
def ver_locais():
    return [{k: v for k, v in l.items() if k not in ('x0', 'y0')} for l in LOCAIS]


class Ponto(BaseModel):
    lat: float
    lon: float


@app.post('/api/locais/{lid}/posicao')
def marcar_local(lid: str, p: Ponto):
    try:
        antes = mapa.definir_ponto(lid, p.lat, p.lon)
    except ValueError as e:
        raise HTTPException(400, str(e))
    l = next(x for x in LOCAIS if x['id'] == lid)
    estado.mover_do_local(antes, l)
    estado.evento('mapa', f"Posição de {l['nome']} marcada no mapa ({p.lat:.5f}, {p.lon:.5f})")
    avisar({'tipo': 'locais'})
    avisar({'tipo': 'estado'})
    return l


@app.post('/api/locais/{lid}/limpar')
def limpar_local(lid: str):
    try:
        antes = mapa.apagar_ponto(lid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    l = next(x for x in LOCAIS if x['id'] == lid)
    estado.mover_do_local(antes, l)
    avisar({'tipo': 'locais'})
    avisar({'tipo': 'estado'})
    return l


class Importacao(BaseModel):
    pontos: dict


@app.post('/api/locais/importar')
def importar_locais(imp: Importacao):
    antes = {l['id']: {'x': l['x'], 'y': l['y']} for l in LOCAIS}
    n = mapa.importar(imp.pontos)
    for l in LOCAIS:
        estado.mover_do_local(antes[l['id']], l)
    estado.evento('mapa', f'{n} posições de locais importadas')
    avisar({'tipo': 'locais'})
    avisar({'tipo': 'estado'})
    return {'importados': n, 'marcados': sum(1 for l in LOCAIS if l.get('real'))}


@app.get('/api/mapa/config')
def ver_mapa_config():
    return {**mapa.config_publica(), 'velocidadeKmh': despacho.CFG['velocidadeKmh'], 'compartilhar': despacho.CFG['compartilhar'],
            'desvioMaxMin': despacho.CFG['desvioMaxMin'], 'toleranciaCompartilharMin': despacho.CFG['toleranciaCompartilharMin']}


class MapaConfig(BaseModel):
    maptilerKey: str | None = None
    velocidadeKmh: float | None = None
    compartilhar: bool | None = None
    desvioMaxMin: float | None = None
    toleranciaCompartilharMin: float | None = None


@app.post('/api/mapa/config')
def mudar_mapa_config(c: MapaConfig):
    if c.maptilerKey is not None:
        mapa.definir_chave(c.maptilerKey)
    if c.velocidadeKmh:
        if not 5 <= c.velocidadeKmh <= 80:
            raise HTTPException(400, 'Velocidade entre 5 e 80 km/h.')
        despacho.CFG['velocidadeKmh'] = c.velocidadeKmh
        mapa.cfg['velocidade_kmh'] = c.velocidadeKmh
        mapa.definir_chave(mapa.cfg.get('maptiler_key'))  # grava o arquivo
    mudou = False
    if c.compartilhar is not None:
        despacho.CFG['compartilhar'] = mapa.cfg['compartilhar'] = c.compartilhar
        mudou = True
    for campo, k_cfg, k_desp in (('desvioMaxMin', 'desvio_max_min', 'desvioMaxMin'), ('toleranciaCompartilharMin', 'tolerancia_compartilhar_min', 'toleranciaCompartilharMin')):
        v = getattr(c, campo)
        if v is not None:
            if not 0 <= v <= 30:
                raise HTTPException(400, 'Minutos entre 0 e 30.')
            despacho.CFG[k_desp] = mapa.cfg[k_cfg] = v
            mudou = True
    if mudou:
        mapa.definir_chave(mapa.cfg.get('maptiler_key'))  # grava o arquivo
    avisar({'tipo': 'locais'})
    return ver_mapa_config()


@app.get('/api/indicadores')
def ver_indicadores():
    return despacho.indicadores(estado.est(), agora())


@app.get('/api/saude')
def saude():
    return {'ok': True, 'agora': agora(), 'configErro': cfg.erro, 'microsoft365': envio.situacao(), 'ia': bool((cfg['ia'] or {}).get('usar_llm') and os.environ.get('ANTHROPIC_API_KEY'))}


# ---------- assistente (rádio e app) ----------
class Mensagem(BaseModel):
    texto: str
    canal: str = 'radio'
    matricula: str | None = None
    nome: str | None = None
    clienteId: str | None = None
    lat: float | None = None       # GPS do celular do passageiro (página do passageiro)
    lon: float | None = None
    precisao: float | None = None


@app.post('/api/assistente')
def assistente(m: Mensagem):
    if not m.texto.strip():
        raise HTTPException(400, 'Texto vazio.')
    return central.mensagem(m.texto.strip(), m.canal, {'matricula': m.matricula, 'nome': m.nome, 'clienteId': m.clienteId,
                                                         'lat': m.lat, 'lon': m.lon, 'precisao': m.precisao})


@app.post('/api/assistente/recarregar')
def recarregar():
    cfg.recarregar(forcar=True)
    return {'ok': cfg.erro is None, 'erro': cfg.erro}


@app.get('/api/conversas')
def conversas(limite: int = 200):
    corrigidas = {e.get('conversa'): e['intencao'] for e in aprendido.exemplos if e.get('conversa')}
    return [{**c, 'corrigidaPara': corrigidas.get(c['n'])} for c in central.e.conversas(limite)]


class Ensino(BaseModel):
    intencao: str
    conversa: int | None = None
    texto: str | None = None


@app.post('/api/assistente/ensinar')
def ensinar(e: Ensino):
    """Corrige o que a IA entendeu: a frase (ou a conversa n) passa a valer como `intencao`."""
    texto = e.texto
    if e.conversa:
        c = estado.conversa(e.conversa)
        if not c:
            raise HTTPException(404, 'Conversa não encontrada.')
        texto = c['texto']
    if not texto:
        raise HTTPException(400, 'Informe a frase.')
    try:
        aprendido.ensinar(texto, e.intencao, agora(), e.conversa)
    except ValueError as x:
        raise HTTPException(400, str(x))
    estado.evento('aprendizado', f'Assistente aprendeu: “{texto}” = {e.intencao}')
    return {'ok': True, 'exemplos': len(aprendido.exemplos)}


@app.post('/api/assistente/esquecer')
def esquecer(e: Ensino):
    texto = e.texto or (estado.conversa(e.conversa) or {}).get('texto')
    return {'removidos': aprendido.esquecer(texto or '')}


@app.get('/api/assistente/analise')
def analise(dias: int = 30):
    """Números para acompanhar a IA: quanto ela entende, por onde entende e o que ainda não entende."""
    desde = agora() - dias * 24 * 3600_000
    cs = [c for c in estado.conversas(20000) if c['em'] >= desde]
    total = len(cs)
    incompletas = [c for c in cs if c['intencao'] == 'pedir' and ((c.get('detalhes') or {}).get('faltando'))]
    desconhecidas = [c for c in cs if c['intencao'] == 'desconhecida']
    corrigidas = {e.get('conversa') for e in aprendido.exemplos if e.get('conversa')}
    erradas = [c for c in cs if c['n'] in corrigidas and c['intencao'] != next(
        (e['intencao'] for e in aprendido.exemplos if e.get('conversa') == c['n']), c['intencao'])]
    grupos = Counter(normalizar(c['texto']) for c in desconhecidas + incompletas)
    exemplo = {normalizar(c['texto']): c['texto'] for c in desconhecidas + incompletas}
    pct = lambda n: round(100 * n / total, 1) if total else None
    return {
        'dias': dias, 'total': total,
        'entendidas': total - len(desconhecidas) - len(incompletas), 'entendidasPct': pct(total - len(desconhecidas) - len(incompletas)),
        'desconhecidas': len(desconhecidas), 'incompletas': len(incompletas), 'corrigidas': len(erradas), 'erradasPct': pct(len(erradas)),
        'porIntencao': dict(Counter(c['intencao'] for c in cs)), 'porFonte': dict(Counter(c['fonte'] for c in cs)),
        'porCanal': dict(Counter(c['canal'] for c in cs)),
        'naoEntendidas': [{'texto': exemplo[k], 'vezes': n} for k, n in grupos.most_common(15)],
        'exemplos': sorted(aprendido.exemplos, key=lambda e: -(e.get('em') or 0)), 'intencoes': list(INTENCOES),
    }


# ---------- corridas por formulário e ações ----------
class Pedido(BaseModel):
    origem: str
    destino: str
    passageiros: int = 1
    solicitante: str = ''
    matricula: str = ''
    setor: str = ''
    urgente: bool = False
    canal: str = 'app'
    clienteId: str = ''
    posPassageiro: dict | None = None  # {lat, lon, precisao} do celular de quem pediu
    pedidoPor: dict | None = None      # {nome, matricula} quando pede para outra pessoa


@app.post('/api/corridas')
def criar(p: Pedido):
    t = agora()
    try:
        res = despacho.criar_corrida({**p.model_dump(), 'criadoPor': p.clienteId}, estado.est(), t)
    except Exception as e:
        raise HTTPException(400, str(e))
    r = res['corrida']
    if not r.get('carro') and r['status'] != 'agendada':
        r['avisarQuandoTiverCarro'] = True
    estado.aplicar(res['ops'])
    r = estado.dados['corridas'][r['id']]
    if p.solicitante and p.matricula:
        estado.pessoas.registrar(p.solicitante, p.matricula, 'app', r['id'])
    fala = central.fala_confirmacao(r, t)
    estado.evento('app', f"Pedido pelo app: {nome_local(r['origem'])} → {nome_local(r['destino'])}", r['id'])
    avisar({'tipo': 'estado'})
    return {'corrida': r, 'fala': fala}


class Acao(BaseModel):
    motivo: str = ''


@app.post('/api/corridas/{cid}/{nome}')
def acao(cid: str, nome: str, a: Acao | None = None):
    if nome == 'checar':  # central pergunta ao motorista como está
        r = estado.dados['corridas'].get(cid)
        if not r or not r.get('carro'):
            raise HTTPException(400, 'Corrida sem carro.')
        return central.checar_motorista(r, 'central', agora())
    if nome not in ('aceitar', 'recusar', 'cheguei', 'embarcou', 'finalizar', 'cancelar', 'desfazer_embarque'):
        raise HTTPException(404, 'Ação desconhecida.')
    try:
        ops = despacho.acao(nome, cid, estado.est(), agora(), {'motivo': a.motivo if a else ''})
    except Exception as e:
        raise HTTPException(400, str(e))
    estado.aplicar(ops)
    estado.evento('acao', f'{nome} ({cid})', cid)
    avisar({'tipo': 'estado'})
    return {'corrida': estado.dados['corridas'].get(cid), 'ops': len(ops)}


# ---------- motoristas ----------
class Motorista(BaseModel):
    id: str
    nome: str
    capacidade: int = 4
    base: str = 'gainf'
    simulado: bool = True


@app.post('/api/motoristas')
def cadastrar(m: Motorista):
    if m.base not in {l['id'] for l in LOCAIS}:
        raise HTTPException(400, 'Local de início inválido.')
    l = next(x for x in LOCAIS if x['id'] == m.base)
    estado.aplicar([{'col': 'motoristas', 'id': m.id.upper(), 'set': {'id': m.id.upper(), 'nome': m.nome, 'capacidade': m.capacidade, 'ativo': True,
                     'modo': 'simulado' if m.simulado else 'real', 'x': l['x'], 'y': l['y'], 'livreDesde': agora()}}])
    avisar({'tipo': 'estado'})
    return estado.dados['motoristas'][m.id.upper()]


@app.post('/api/motoristas/{mid}/{nome}')
def acao_motorista(mid: str, nome: str, dados: dict | None = None):
    m = estado.dados['motoristas'].get(mid)
    if not m:
        raise HTTPException(404, 'Carro não encontrado.')
    t = agora()
    dados = dados or {}
    if nome == 'ativo':
        estado.aplicar(despacho.definir_ativo(mid, bool(dados.get('ativo')), estado.est(), t))
    elif nome == 'modo':  # 'real' = celular do motorista comanda; 'simulado' = anda sozinho
        estado.aplicar([{'col': 'motoristas', 'id': mid, 'patch': {'modo': dados.get('modo', 'real')}}])
    elif nome == 'gps':
        lat, lon = float(dados['lat']), float(dados['lon'])
        patch = {'gps': {'lat': lat, 'lon': lon, 'em': t, 'precisao': dados.get('precisao')}}
        if not despacho.do_carro(mid, estado.dados['corridas'])['atual']:
            patch.update(geo_para_xy(lat, lon))
        estado.aplicar([{'col': 'motoristas', 'id': mid, 'patch': patch}])
        return {'ok': True}
    elif nome == 'condutor':  # quem está dirigindo neste turno (o app do motorista pede a matrícula)
        mat = re.sub(r'\D', '', str(dados.get('matricula') or ''))
        if not mat:
            estado.aplicar([{'col': 'motoristas', 'id': mid, 'patch': {'condutor': None}}])
        else:
            nome_c = (dados.get('nome') or '').strip() or (estado.pessoas.perfis().get(mat) or {}).get('nome', '')
            if not nome_c:
                raise HTTPException(400, 'Matrícula ainda não cadastrada: informe também o nome.')
            if (dados.get('nome') or '').strip():
                estado.pessoas.registrar(nome_c, mat, 'motorista')
            estado.aplicar([{'col': 'motoristas', 'id': mid, 'patch': {'condutor': {'matricula': mat, 'nome': nome_c, 'desde': t}}}])
    elif nome == 'pane':
        central.pane(mid, bool(dados.get('ligar')))
    elif nome == 'remover':
        estado.aplicar([{'col': 'motoristas', 'id': mid, 'apagar': True}])
    else:
        raise HTTPException(404, 'Ação desconhecida.')
    avisar({'tipo': 'estado'})
    return estado.dados['motoristas'].get(mid)


class RespostaChecagem(BaseModel):
    opcao: str
    texto: str = ''
    nome: str = ''        # quem está no carro, quando o motorista informa
    matricula: str = ''


@app.post('/api/checagens/{kid}')
def responder(kid: str, r: RespostaChecagem):
    c = central.responder_checagem(kid, r.opcao, r.texto, {'nome': r.nome, 'matricula': r.matricula})
    if not c:
        raise HTTPException(404, 'Checagem não encontrada.')
    return c


# ---------- voz da central (gerada no servidor) ----------
@app.get('/api/voz/info')
def voz_info():
    return voz.info()


@app.get('/api/voz')
def voz_falar(texto: str, vel: float = 1.0):  # voz única da central (app/voz.py); voz_id de telas antigas é ignorado
    if not texto.strip():
        raise HTTPException(400, 'Texto vazio.')
    try:
        wav = voz.gerar(texto, vel)
    except Exception as e:
        raise HTTPException(503, f'Voz indisponível: {e}')
    return Response(wav, media_type='audio/wav', headers={'Cache-Control': 'max-age=86400'})


# ---------- cadastro de pessoas (nome ↔ matrícula aprendidos) ----------
@app.get('/api/pessoas')
def ver_pessoas():
    return estado.pessoas.lista()


class PessoaCorrigida(BaseModel):
    nome: str


@app.post('/api/pessoas/{mat}')
def corrigir_pessoa(mat: str, p: PessoaCorrigida):
    """A central define o nome certo desta matrícula (vale mais que todas as falas)."""
    if not p.nome.strip():
        raise HTTPException(400, 'Informe o nome.')
    estado.pessoas.registrar(p.nome.strip(), mat, 'central')
    estado.evento('cadastro', f'Central definiu: matrícula {mat} = {p.nome.strip()}')
    return estado.pessoas.perfis().get(''.join(ch for ch in mat if ch.isdigit()))


# ---------- exportação ----------
@app.get('/api/corridas.csv')
def exportar():
    ind = despacho.indicadores(estado.est(), agora())
    cols = ['id', 'canal', 'solicitante', 'matricula', 'setor', 'origem', 'destino', 'passageiros', 'urgente', 'carro', 'status', 'direto',
            'pedido', 'chegou', 'finalizado', 'espera', 'aceite', 'emFila', 'deslocamento', 'embarque', 'viagem', 'total', 'erroEta',
            'kmAteOrigem', 'kmViagem', 'ocorrencia', 'textoFalado']
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    w.writerow(cols)
    for l in ind['linhas']:
        fmt = lambda v: '' if v is None else ('Sim' if v is True else 'Não' if v is False else f'{v:.1f}'.replace('.', ',') if isinstance(v, float) else v)
        w.writerow([fmt(l.get(c)) if c not in ('pedido', 'chegou', 'finalizado', 'origem', 'destino') else
                    {'pedido': hora(l.get('criadoEm')), 'chegou': hora(l.get('chegouOrigemEm')), 'finalizado': hora(l.get('finalizadoEm')),
                     'origem': nome_local(l['origem']), 'destino': nome_local(l['destino'])}[c] for c in cols])
    return StreamingResponse(iter(['﻿' + buf.getvalue()]), media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition': 'attachment; filename=corridas.csv'})


# ---------- integração com o Microsoft 365 (Power Automate lê o feed e grava no SharePoint) ----------
def _chave_integracao(chave):
    certa = os.environ.get('INTEGRACAO_CHAVE', '')
    if not certa or chave != certa:
        raise HTTPException(404)


@app.get('/api/integracao/corridas.json')
def integracao_json(chave: str = ''):
    _chave_integracao(chave)
    lista = integracao.viagens(despacho.indicadores(estado.est(), agora()), estado.dados['motoristas'])
    return [{k: v for k, v in x.items() if k != '_fim'} for x in lista]


@app.get('/api/integracao/corridas.rss')
def integracao_rss(request: Request, chave: str = ''):
    _chave_integracao(chave)
    lista = integracao.viagens(despacho.indicadores(estado.est(), agora()), estado.dados['motoristas'])
    return Response(integracao.rss(lista, str(request.base_url)), media_type='application/rss+xml; charset=utf-8')


@app.get('/api/conversas.csv')
def exportar_conversas():
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    cols = ['n', 'em', 'canal', 'matricula', 'nome', 'texto', 'intencao', 'fonte', 'resposta', 'corrida']
    w.writerow(cols + ['hora'])
    for c in estado.conversas(5000):
        w.writerow([c[k] for k in cols] + [hora(c['em'])])
    return StreamingResponse(iter(['﻿' + buf.getvalue()]), media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition': 'attachment; filename=conversas.csv'})


# ---------- tempo real ----------
@app.websocket('/ws')
async def ws(sock: WebSocket):
    q = sock.query_params
    papel = acesso.papel(q.get('t'))
    if not papel:
        await sock.close(code=4401)
        return
    await sock.accept()
    info = {'papel': papel, 'cliente': q.get('cliente') or '', 'mat': ''.join(ch for ch in (q.get('mat') or '') if ch.isdigit())}
    conexoes[sock] = info
    try:
        est = visao()
        await sock.send_json({'tipo': 'estado', 'estado': est if papel != 'passageiro' else para_passageiro(est, info)})
        while True:
            await sock.receive_text()  # o cliente só manda "ping"
    except WebSocketDisconnect:
        pass
    finally:
        conexoes.pop(sock, None)


# ---------- aplicativos ----------
@app.get('/passageiro')
def so_passageiro():
    # link para mandar ao pessoal: abre direto a página de pedir viagem
    return FileResponse(FRONT / 'passageiro.html')


@app.get('/')
def inicio():
    return FileResponse(FRONT / 'index.html')


if FRONT.exists():
    app.mount('/', StaticFiles(directory=FRONT), name='front')
