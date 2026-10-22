"""Monta a central inteira em UM arquivo HTML, sem servidor: o backend Python roda dentro do navegador (Pyodide).

Uso (na raiz do projeto):  python deploy/montar-html.py SAIDA.html [CHAVE_MAPTILER]
- Abre com dois cliques (Edge/Chrome). Precisa de internet na primeira abertura (baixa o Python do navegador e as
  bibliotecas, ~20 MB, depois fica em cache) e para o mapa.
- Telas: Passageiro, Motorista, Central e Painel (TV), as mesmas do servidor.
- Os dados ficam só neste navegador (IndexedDB). Sem login (ACESSO=0), sem voz do servidor, rotas em linha reta.
"""
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.27.2/full/'
PAGINAS = {'passageiro': 'Pedir viagem', 'motorista': 'Motorista', 'central': 'Central', 'painel': 'Painel (TV)'}


def arquivos_backend():
    out = {}
    for p in sorted((RAIZ / 'backend' / 'app').glob('*.py')):
        out[f'/app/backend/app/{p.name}'] = p.read_text(encoding='utf-8')
    for p in sorted((RAIZ / 'backend' / 'config').glob('*')):
        if p.suffix in ('.yaml', '.yml', '.json'):
            out[f'/app/backend/config/{p.name}'] = p.read_text(encoding='utf-8')
    return out


SHIM = """<script>
(function () {
  // Sem servidor: as chamadas para http://central.local vão para o Python que roda na página principal
  var P = window.parent, BASE = 'http://central.local';
  try { localStorage.setItem('servidor', BASE); } catch (e) {}
  var fetchOrig = window.fetch.bind(window);
  window.fetch = function (url, op) {
    var u = String(url && url.url ? url.url : url);
    if (u.indexOf(BASE + '/') !== 0) return fetchOrig(url, op);
    op = op || {};
    var cab = {}; var h = op.headers || {};
    if (h.forEach) h.forEach(function (v, k) { cab[k] = v; }); else for (var k in h) cab[k] = h[k];
    return P.chamarCentral(op.method || 'GET', u.slice(BASE.length), cab, op.body || '').then(function (r) {
      return new Response(r.corpo, { status: r.status, headers: r.cabecalhos });
    });
  };
  // Sem WebSocket: o app passa sozinho a consultar a central a cada 3 s
  window.WebSocket = function () { var o = { readyState: 3, send: function () {}, close: function () {} };
    setTimeout(function () { o.onclose && o.onclose({ code: 1006 }); }, 30); return o; };
  // links entre as telas (passageiro.html, central.html…) trocam a aba da página principal
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]'); if (!a || a.target) return;
    var m = (a.getAttribute('href') || '').match(/^([a-z]+)\\.html/);
    if (m) { e.preventDefault(); if (P.abrirPagina) P.abrirPagina(m[1]); }
  }, true);
})();
</script>"""


def pagina(nome):
    html = (RAIZ / 'frontend' / f'{nome}.html').read_text(encoding='utf-8')
    css = (RAIZ / 'frontend' / 'estilo.css').read_text(encoding='utf-8')
    js = {f: (RAIZ / 'frontend' / f).read_text(encoding='utf-8') for f in ('comum.js', 'fixo.js')}
    html = html.replace('<link rel="stylesheet" href="estilo.css">', f'<style>{css}</style>')
    html = re.sub(r'<link rel="manifest"[^>]*>', '', html)
    for f, codigo in js.items():
        html = html.replace(f'<script src="{f}"></script>', f'<script>{codigo}</script>')
    return html.replace('<head>', '<head>' + SHIM, 1) if '<head>' in html else SHIM + html


def montar(saida, chave=''):
    dados = {'arquivos': arquivos_backend(), 'paginas': {n: pagina(n) for n in PAGINAS}, 'nomes': PAGINAS, 'chave': chave}
    carga = json.dumps(dados, ensure_ascii=False).replace('</', '<\\/')
    html = MOLDE.replace('__PYODIDE__', PYODIDE).replace('__CARGA__', carga)
    Path(saida).write_text(html, encoding='utf-8', newline='\n')
    print(f'{saida}: {len(html) // 1024} KB')


MOLDE = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Central de Corridas</title>
<style>
  :root { --bg: #161917; --fg: #e8e9e6; --muted: #a2a9a4; --accent: #f0b429; --line: #3a403c; color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--bg); color: var(--fg); font: 14px system-ui, sans-serif; }
  .barra { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; padding: 8px 16px; border-bottom: 1px solid var(--line); }
  .barra b { font: 700 18px "Arial Narrow", sans-serif; letter-spacing: .04em; text-transform: uppercase; }
  .barra b span { color: var(--accent); }
  .barra button { background: none; border: 0; border-bottom: 3px solid transparent; color: var(--muted); font: 600 14px system-ui; padding: 6px 8px; cursor: pointer; }
  .barra button[aria-selected="true"] { color: var(--fg); border-bottom-color: var(--accent); }
  .barra .info { margin-left: auto; color: var(--muted); font-size: 12px; }
  .barra .zerar { border: 1px solid var(--line); border-radius: 4px; border-bottom-width: 1px; font-weight: 400; font-size: 12px; }
  #telas { position: absolute; top: 48px; left: 0; right: 0; bottom: 0; }
  #telas iframe { width: 100%; height: 100%; border: 0; background: #fff; }
  .carregando { position: absolute; inset: 48px 0 0 0; display: flex; align-items: center; justify-content: center; text-align: center; padding: 16px; }
  .carregando p { max-width: 460px; color: var(--muted); }
  .carregando b { display: block; font-size: 18px; color: var(--fg); margin-bottom: 8px; }
</style>
</head><body>
<div class="barra"><b>Central de <span>Corridas</span></b><span id="abas"></span>
  <span class="info" id="info">Carregando…</span><button class="zerar" id="zerar" type="button" title="Apaga as corridas e o aprendizado guardados neste navegador">Zerar dados</button></div>
<div class="carregando" id="carregando"><p><b>Preparando a central…</b><span id="passo">Baixando o Python do navegador (só na primeira vez demora ~30 s).</span></p></div>
<div id="telas"></div>
<script type="application/json" id="carga">__CARGA__</script>
<script src="__PYODIDE__pyodide.js"></script>
<script>
const CARGA = JSON.parse(document.getElementById('carga').textContent);
const $ = id => document.getElementById(id);
const passo = t => { $('passo').textContent = t; $('info').textContent = t; };
let py = null, chamar = null, prontaOk;
window.centralPronta = new Promise(ok => { prontaOk = ok; });

// ---------- telas (uma por aba, mantidas abertas) ----------
const telas = {};
$('abas').innerHTML = Object.entries(CARGA.nomes).map(([id, nome]) => `<button type="button" data-tela="${id}" aria-selected="false">${nome}</button>`).join('');
$('abas').onclick = e => { const b = e.target.closest('[data-tela]'); if (b) abrirPagina(b.dataset.tela); };
window.abrirPagina = id => {
  if (!CARGA.paginas[id]) return;
  for (const [k, f] of Object.entries(telas)) f.hidden = k !== id;
  if (!telas[id]) { const f = document.createElement('iframe'); f.title = CARGA.nomes[id]; f.allow = 'microphone; geolocation; autoplay'; f.srcdoc = CARGA.paginas[id]; $('telas').appendChild(f); telas[id] = f; }
  document.querySelectorAll('[data-tela]').forEach(b => b.setAttribute('aria-selected', b.dataset.tela === id));
  try { localStorage.setItem('central-aba', id); } catch (e) {}
};

// ---------- as telas chamam a central (o backend Python rodando aqui) ----------
window.chamarCentral = async (metodo, caminho, cabecalhos, corpo) => {
  await window.centralPronta;
  const r = JSON.parse(await chamar(metodo, caminho, JSON.stringify(cabecalhos || {}), typeof corpo === 'string' ? corpo : ''));
  return r;
};

let salvarAgendado = null;
const salvar = () => { if (salvarAgendado || !py) return; salvarAgendado = setTimeout(() => { salvarAgendado = null; try { py.FS.syncfs(false, () => {}); } catch (e) {} }, 3000); };

$('zerar').onclick = async () => {
  if (!confirm('Apagar as corridas, conversas e o aprendizado guardados neste navegador?')) return;
  try { indexedDB.deleteDatabase('/dados'); } catch (e) {}
  location.reload();
};

(async () => {
  try {
    py = await loadPyodide({ indexURL: '__PYODIDE__' });
    passo('Instalando as bibliotecas da central…');
    await py.loadPackage(['micropip', 'pydantic', 'pyyaml', 'sqlite3', 'ssl', 'httpx']);
    const micropip = py.pyimport('micropip');
    await micropip.install(['fastapi==0.115.6', 'starlette==0.41.3', 'anyio==4.8.0', 'sniffio==1.3.1', 'httpcore==1.0.7', 'h11==0.14.0']);
    passo('Abrindo o banco de dados deste navegador…');
    py.FS.mkdirTree('/dados');
    try { py.FS.mount(py.FS.filesystems.IDBFS, {}, '/dados'); await new Promise(ok => py.FS.syncfs(true, ok)); } catch (e) { console.warn('sem IndexedDB: dados só nesta sessão', e); }
    for (const [caminho, texto] of Object.entries(CARGA.arquivos)) { py.FS.mkdirTree(caminho.slice(0, caminho.lastIndexOf('/'))); py.FS.writeFile(caminho, texto); }
    passo('Ligando a central…');
    py.globals.set('CHAVE_MAPA', CARGA.chave || '');
    await py.runPythonAsync(`
import os, sys, json, asyncio, threading
os.environ.update({'ACESSO': '0', 'VOZ': '0', 'ROTAS_OFFLINE': '1', 'BANCO': '/dados/central.db', 'FRONTEND_DIR': '/sem-telas'})
if CHAVE_MAPA:
    os.environ['MAPTILER_KEY'] = CHAVE_MAPA
threading.Thread.start = lambda self: self.run()   # o navegador não tem threads: roda na hora
import anyio.to_thread, starlette.concurrency, fastapi.routing, fastapi.dependencies.utils
async def _direto(func, *args, **kwargs):   # sem threads: as rotas síncronas rodam direto
    return func(*args, **kwargs)
async def _direto_anyio(func, *args, **kwargs):
    return func(*args)
anyio.to_thread.run_sync = _direto_anyio
for _mod in (starlette.concurrency, fastapi.routing, fastapi.dependencies.utils):
    if hasattr(_mod, 'run_in_threadpool'):
        _mod.run_in_threadpool = _direto
sys.path.insert(0, '/app/backend')
import app.main as central_main
_vida = central_main.app.router.lifespan_context(central_main.app)
await _vida.__aenter__()   # liga o despacho, a simulação dos carros e os avisos

from urllib.parse import urlsplit
async def chamar(metodo, caminho, cab_json, corpo):
    sp = urlsplit(caminho)
    corpo_b = (corpo or '').encode()
    cab = [(k.lower().encode(), str(v).encode()) for k, v in json.loads(cab_json or '{}').items()]
    if corpo_b:
        cab.append((b'content-length', str(len(corpo_b)).encode()))
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': metodo.upper(), 'scheme': 'http',
             'path': sp.path, 'raw_path': sp.path.encode(), 'query_string': sp.query.encode(), 'headers': cab,
             'client': ('127.0.0.1', 0), 'server': ('central.local', 80), 'root_path': ''}
    lido = False
    async def receive():
        nonlocal lido
        if not lido:
            lido = True
            return {'type': 'http.request', 'body': corpo_b, 'more_body': False}
        await asyncio.sleep(3600)
        return {'type': 'http.disconnect'}
    saida = {'status': 500, 'cab': {}, 'corpo': b''}
    async def send(msg):
        if msg['type'] == 'http.response.start':
            saida['status'] = msg['status']
            saida['cab'] = {k.decode(): v.decode() for k, v in msg.get('headers', [])}
        elif msg['type'] == 'http.response.body':
            saida['corpo'] += msg.get('body', b'')
    await central_main.app(scope, receive, send)
    return json.dumps({'status': saida['status'], 'cabecalhos': saida['cab'], 'corpo': saida['corpo'].decode('utf-8', 'replace')})
`);
    const fn = py.globals.get('chamar');
    chamar = async (...a) => { const r = await fn(...a); salvar(); return r; };
    setInterval(salvar, 15000);
    prontaOk();
    $('carregando').remove();
    $('info').textContent = 'Rodando neste navegador · dados só aqui';
    let aba = 'central'; try { aba = localStorage.getItem('central-aba') || aba; } catch (e) {}
    abrirPagina(CARGA.paginas[aba] ? aba : 'central');
  } catch (e) {
    console.error(e);
    passo('Não consegui ligar a central: ' + (e.message || e) + '. Confira a internet (a primeira abertura baixa o Python do navegador) e abra de novo.');
  }
})();
</script>
</body></html>
"""

if __name__ == '__main__':
    montar(sys.argv[1] if len(sys.argv) > 1 else 'central-corridas.html', sys.argv[2] if len(sys.argv) > 2 else '')
