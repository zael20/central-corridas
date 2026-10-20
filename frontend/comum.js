// Partes comuns dos 3 aplicativos: conexão com o servidor, tempo real, voz (falar/ouvir), mapa e utilidades.
// Nenhuma regra de negócio fica aqui: o servidor decide e responde; os apps só mostram e falam.
(function () {
  const ls = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };
  // Endereço do servidor: o próprio site, ou o que foi digitado na tela inicial (app Android)
  // No APK a página roda dentro do celular (http://localhost): sem servidor salvo, usa o que veio no APK, nunca o próprio celular.
  const SERVIDOR = (ls.get('servidor') || window.SERVIDOR_PADRAO || (location.protocol.startsWith('http') && !window.Capacitor ? location.origin : '')).replace(/\/$/, '');
  const clienteId = ls.get('clienteId') || (() => { const id = 'cli-' + Math.random().toString(36).slice(2, 10); ls.set('clienteId', id); return id; })();

  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const por = (id, html) => { const el = typeof id === 'string' ? $(id) : id; if (el && el._h !== html) { el.innerHTML = html; el._h = html; } };
  const STATUS = { agendada: 'agendada', sem_carro: 'sem carro', oferecida: 'aguardando aceite', na_fila: 'na fila do motorista', a_caminho: 'carro a caminho',
    no_local: 'carro chegou', em_viagem: 'em viagem', concluida: 'concluída', cancelada: 'cancelada' };
  const pill = (s, txt) => `<span class="pill s-${s}">${esc(txt || STATUS[s] || s)}</span>`;
  const fmtH = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Belem' });
  const hora = t => (t ? fmtH.format(t) : '—');
  const MIN = 60000;

  // ---------- código de acesso ----------
  const token = () => ls.get('acesso') || '';
  const comToken = url => url + (url.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(token());
  let pedindo = null, nivelTela = null;
  // nível que a tela exige: 'operacao' (central), 'motorista' (app do motorista: adm ou motorista), 'codigo' (passageiro)
  const PAPEIS_OK = { operacao: ['operacao'], motorista: ['operacao', 'motorista'], codigo: ['operacao', 'motorista', 'passageiro'] };
  const TITULO = { operacao: 'Acesso da central', motorista: 'Acesso do motorista', codigo: 'Acesso' };
  const DICA = { operacao: 'Matrícula de administrador.', motorista: 'Sua matrícula (cadastrada pela central).', codigo: 'Sua matrícula (cadastrada pela central).' };
  function pedirCodigo(precisa, aviso) {
    const nivel = nivelTela || (precisa === 'operacao' ? 'operacao' : 'codigo');
    if (pedindo) return;
    pedindo = true;
    const d = document.createElement('div');
    d.className = 'codigo-fundo';
    d.innerHTML = `<form class="painel codigo-caixa"><h2>${TITULO[nivel]}</h2>
      <p class="aviso">${esc(aviso && !/^Entre com a sua matrícula/.test(aviso) ? aviso : DICA[nivel])}</p>
      <label>Matrícula<input type="text" inputmode="numeric" autocomplete="username" class="mat-in" value="${esc(ls.get('matricula') || '')}"></label>
      <p class="falta cod-erro" hidden></p><div class="acoes"><button class="btn principal" type="submit">Entrar</button></div></form>`;
    document.body.appendChild(d);
    const mat = d.querySelector('.mat-in');
    mat.focus();
    d.querySelector('form').onsubmit = async ev => {
      ev.preventDefault();
      const erro = d.querySelector('.cod-erro'); erro.hidden = true;
      try {
        const r = await fetch(SERVIDOR + '/api/entrar', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ matricula: mat.value }) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.detail || 'Matrícula não cadastrada.');
        if (!PAPEIS_OK[nivel].includes(j.papel)) throw new Error(nivel === 'operacao' ? 'Este acesso não é de administrador.' : 'Este acesso é de passageiro. Peça à central um acesso de motorista.');
        ls.set('acesso', j.token); ls.set('papel', j.papel);
        if (j.matricula) { ls.set('matricula', j.matricula); ls.set('nome', j.nome || ''); ls.set('perfil', j.perfil || ''); }
        location.reload();
      } catch (e) { erro.hidden = false; erro.textContent = e.message; mat.select(); }
    };
  }
  const sair = () => { ['acesso', 'papel', 'perfil'].forEach(k => ls.set(k, '')); location.reload(); };

  async function api(caminho, dados, metodo) {
    const r = await fetch(SERVIDOR + caminho, { method: metodo || (dados === undefined ? 'GET' : 'POST'),
      headers: { 'Content-Type': 'application/json', 'X-Acesso': token() }, body: dados === undefined ? undefined : JSON.stringify(dados) });
    const j = await r.json().catch(() => ({}));
    if ((r.status === 401 || r.status === 403) && j.precisa) pedirCodigo(j.precisa, j.detail);
    if (!r.ok) { const e = new Error(j.detail || ('Erro ' + r.status)); e.status = r.status; throw e; }
    return j;
  }

  // ---------- tempo real ----------
  const ouvintes = [];
  let estado = null, locais = [], LC = {};
  function conectar(statusEl) {
    let ws, tentativa = 0;
    const abrir = () => {
      ws = new WebSocket(SERVIDOR.replace(/^http/, 'ws') + '/ws?t=' + encodeURIComponent(token()) + '&cliente=' + encodeURIComponent(clienteId) + '&mat=' + encodeURIComponent(ls.get('matricula') || ''));
      ws.onopen = () => { tentativa = 0; statusEl && (statusEl.className = 'modo on', statusEl.innerHTML = '<i></i> Conectado'); };
      ws.onmessage = ev => {
        const msg = JSON.parse(ev.data);
        if (msg.tipo === 'estado') estado = msg.estado;
        for (const f of ouvintes) try { f(msg, estado); } catch (e) { console.error(e); }
      };
      ws.onclose = ev => {
        if (ev.code === 4401) { pedirCodigo(ls.get('papel') === 'operacao' ? 'operacao' : 'codigo'); return; }
        statusEl && (statusEl.className = 'modo', statusEl.innerHTML = '<i></i> Sem conexão, tentando…');
        setTimeout(abrir, Math.min(10000, 1000 * 2 ** tentativa++));
      };
    };
    abrir();
    setInterval(() => { try { ws.readyState === 1 && ws.send('ping'); } catch (e) {} }, 20000);
  }
  async function carregarLocais() {
    locais = await api('/api/locais');
    LC = Object.fromEntries(locais.map(l => [l.id, l]));
    try { cfgMapa = await api('/api/mapa/config'); } catch (e) { cfgMapa = cfgMapa || {}; }
  }
  function semServidor(statusEl, erro, tentativa) {
    let d = document.querySelector('.sem-servidor');
    if (!erro) { d && d.remove(); return; }
    statusEl && (statusEl.className = 'modo', statusEl.innerHTML = '<i></i> Sem conexão, tentando…');
    if (!d) { d = document.createElement('div'); d.className = 'codigo-fundo sem-servidor'; document.body.appendChild(d); }
    const motivo = erro.status ? `O endereço respondeu, mas não parece ser a central (erro ${erro.status}).` : 'Não houve resposta (sem internet, rede bloqueada ou servidor desligado).';
    d.innerHTML = `<div class="painel codigo-caixa"><h2>Sem conexão com a central</h2>
      <p>Servidor: <b>${esc(SERVIDOR)}</b></p><p class="aviso">${esc(motivo)} Tentativa ${tentativa + 1}, tentando de novo sozinho…</p>
      <p class="aviso">A central no Render grátis dorme sem uso e leva até 1 minuto para acordar. Na rede da empresa o site pode estar bloqueado: teste com os dados móveis.</p>
      <div class="acoes"><button class="btn principal" type="button" onclick="location.reload()">Tentar agora</button>
      <a class="btn" href="index.html?config=1">Trocar servidor</a></div></div>`;
  }
  async function iniciar(statusEl, { papel } = {}) {
    if (!SERVIDOR) { location.href = 'index.html'; return; }
    nivelTela = papel || 'codigo';
    if (papel && !PAPEIS_OK[papel].includes(ls.get('papel'))) {
      // sem código ainda (ou com o de passageiro): o servidor diz se a proteção está ligada
      const r = await fetch(SERVIDOR + '/api/estado', { headers: { 'X-Acesso': token() } }).catch(() => null);
      if (r && (r.status === 401 || r.status === 403)) { pedirCodigo('operacao'); return new Promise(() => {}); }
    }
    // sem resposta do servidor: avisa na tela e tenta de novo sozinho (antes ficava em "Conectando…" para sempre)
    for (let tentativa = 0; ; tentativa++) {
      try { await carregarLocais(); break; }
      catch (e) {
        if (e.status === 401 || e.status === 403) return new Promise(() => {}); // pediu o código: espera
        semServidor(statusEl, e, tentativa);
        await new Promise(ok => setTimeout(ok, Math.min(15000, 3000 + tentativa * 2000)));
      }
    }
    semServidor(null);
    // locais marcados/alterados na central: todos os aparelhos recarregam os pontos e a chave do mapa
    ouvintes.unshift(async msg => {
      if (msg.tipo !== 'locais') return;
      const chaveAntes = cfgMapa && cfgMapa.maptilerKey;
      await carregarLocais();
      for (const inst of instancias) { inst.ass = ''; if ((cfgMapa && cfgMapa.maptilerKey) !== chaveAntes) { inst.map.removeLayer(inst.base); inst.base = baseSatelite().addTo(inst.map); } }
      for (const f of ouvintes) try { f({ tipo: 'locais_ok' }, estado); } catch (e) { console.error(e); }
    });
    conectar(statusEl);
  }

  // ---------- voz ----------
  // Plugins nativos do APK. As telas não usam o @capacitor/core, então o Capacitor.Plugins não vem pronto:
  // aqui cada plugin que o app Android declara (Capacitor.PluginHeaders) vira um objeto com os métodos dele.
  let plugins = null;
  const plug = () => {
    const cap = window.Capacitor;
    if (!cap || !cap.nativePromise) return (cap && cap.Plugins) || {};
    if (!plugins) {
      plugins = Object.assign({}, cap.Plugins);
      for (const h of cap.PluginHeaders || []) {
        if (plugins[h.name]) continue;
        const o = {};
        for (const m of h.methods || []) o[m.name] = opcoes => cap.nativePromise(h.name, m.name, opcoes || {});
        o.addListener = async (eventName, f) => {
          const callbackId = cap.nativeCallback(h.name, 'addListener', { eventName }, f);
          return { remove: () => cap.nativePromise(h.name, 'removeListener', { eventName, callbackId }) };
        };
        plugins[h.name] = o;
      }
    }
    return plugins;
  };
  // Voz: prefere as vozes neurais femininas (Edge: "Francisca/Thalita Online (Natural)"), depois a do Google e a Maria do Windows.
  // A escolha e a velocidade ficam salvas neste aparelho (painel "Voz").
  const PREFERIDAS = [/francisca.*natural/i, /thalita.*natural/i, /(brenda|elza|giovanna|leila|leticia|manuela|yara).*natural/i, /natural/i,
    /google portugu[eê]s do brasil/i, /luciana/i, /maria/i, /francisca/i];
  function vozesPt() {
    try { return speechSynthesis.getVoices().filter(v => /^pt[-_]BR/i.test(v.lang) || /portugu.*(bra|brazil)/i.test(v.name)); } catch (e) { return []; }
  }
  const MASCULINAS = /daniel|antonio|ant[oô]nio|donato|fabio|f[aá]bio|julio|j[uú]lio|nicolau|valerio|val[eé]rio|humberto|francisco|ricardo|heitor|male|masculin/i;
  function vozEscolhida() {
    const vs = vozesPt(), salva = ls.get('vozEscolha');
    const femininas = vs.filter(v => !MASCULINAS.test(v.name));
    return vs.find(v => v.name === salva) || PREFERIDAS.map(r => femininas.find(v => r.test(v.name))).find(Boolean) || femininas[0] || vs[0] || null;
  }
  // as vozes do navegador carregam depois da página: espera um pouco para não cair na voz padrão (que pode ser masculina)
  const vozesProntas = () => new Promise(ok => {
    try { if (speechSynthesis.getVoices().length) return ok(); speechSynthesis.addEventListener('voiceschanged', () => ok(), { once: true }); } catch (e) { return ok(); }
    setTimeout(ok, 1500);
  });
  // texto para ouvido: sem abreviações que a voz lê letra por letra
  const paraFala = t => String(t).replace(/\bmat\.\s*/gi, 'matrícula ').replace(/\b(\d+)\s*min\b/g, '$1 minutos').replace(/\s+/g, ' ').trim();

  // Uma fala por vez em TODAS as abas deste aparelho (central + passageiro + motorista abertos juntos):
  // cada aba avisa as outras quando começa e termina de falar, e a mesma frase não é falada duas vezes.
  const canalVoz = 'BroadcastChannel' in window ? new BroadcastChannel('central-voz') : null;
  const ditos = new Map();
  let filaVoz = [], falando = false, outraAteMs = 0;
  if (canalVoz) canalVoz.onmessage = ev => {
    const m = ev.data || {};
    if (m.tipo === 'aviso') ditos.set(m.texto, Date.now());
    if (m.tipo === 'inicio') { outraAteMs = Date.now() + Math.min(30000, 1500 + m.texto.length * 110); ditos.set(m.texto, Date.now()); }
    if (m.tipo === 'fim') { outraAteMs = 0; setTimeout(proximaFala, 300); }
  };
  async function falar(texto, { forcar } = {}) {
    texto = paraFala(texto || '');
    if (!texto) return;
    const agoraMs = Date.now();
    if (!forcar && agoraMs - (ditos.get(texto) || 0) < 20000) return; // esta ou outra aba acabou de falar isso
    ditos.set(texto, agoraMs);
    canalVoz && canalVoz.postMessage({ tipo: 'aviso', texto });
    filaVoz.push(texto);
    setTimeout(proximaFala, Math.random() * 200); // desencontra abas que recebem o mesmo aviso ao mesmo tempo
  }

  // Voz do servidor (Kokoro): a mesma voz natural em todos os aparelhos. Sem ela, usa a voz do navegador/celular.
  let infoSrv = null;
  async function vozServidor() {
    if (infoSrv && (infoSrv.disponivel || Date.now() - infoSrv.em < 15000)) return infoSrv;
    try { infoSrv = { ...(await api('/api/voz/info')), em: Date.now() }; } catch (e) { infoSrv = { disponivel: false, em: Date.now() }; }
    return infoSrv;
  }
  const velVoz = () => parseFloat(ls.get('vozVel')) || 1.0;
  const baixar = u => fetch(u).then(r => { if (!r.ok) throw new Error('voz ' + r.status); return r.blob(); });
  const tocar = blob => new Promise((ok, falha) => {
    const a = new Audio(URL.createObjectURL(blob));
    a.onended = () => { URL.revokeObjectURL(a.src); ok(); };
    a.onerror = () => falha(new Error('áudio'));
    a.play().catch(falha);
  });
  async function falarServidor(frases, id) {
    const url = f => comToken(`${SERVIDOR}/api/voz?texto=${encodeURIComponent(f)}&voz_id=${encodeURIComponent(id)}&vel=${velVoz()}`);
    let prox = baixar(url(frases[0]));
    for (let i = 0; i < frases.length; i++) {
      const blob = await prox;
      if (i + 1 < frases.length) prox = baixar(url(frases[i + 1])); // gera a próxima frase enquanto toca esta
      await tocar(blob);
    }
  }
  function falarNavegador(frases, fim) {
    const voz = vozEscolhida(), vel = velVoz() * 0.95;
    frases.forEach((frase, i) => {
      const u = new SpeechSynthesisUtterance(frase);
      u.lang = 'pt-BR'; if (voz) u.voice = voz;
      u.rate = vel; u.pitch = 1.05; u.volume = 1;
      if (i === frases.length - 1) { u.onend = fim; u.onerror = fim; }
      speechSynthesis.speak(u);
    });
  }
  async function proximaFala() {
    if (falando || !filaVoz.length) return;
    if (Date.now() < outraAteMs) { setTimeout(proximaFala, 400); return; }
    falando = true;
    const texto = filaVoz.shift();
    canalVoz && canalVoz.postMessage({ tipo: 'inicio', texto });
    let terminou = false;
    const fim = () => { if (terminou) return; terminou = true; falando = false; canalVoz && canalVoz.postMessage({ tipo: 'fim' }); setTimeout(proximaFala, 300); };
    // uma frase por vez: pausa natural entre elas e nada é cortado
    const frases = (texto.match(/[^.!?]+[.!?]*/g) || [texto]).map(s => s.trim()).filter(Boolean);
    const escolha = ls.get('vozEscolha') || '';
    try {
      const srv = await vozServidor();
      if (srv.disponivel && (!escolha || escolha.startsWith('srv:'))) {
        try { await falarServidor(frases, escolha.startsWith('srv:') ? escolha.slice(4) : srv.padrao); return fim(); }
        catch (e) { console.warn('voz do servidor falhou, usando a do aparelho', e); }
      }
      const tts = plug().TextToSpeech;
      if (tts) { try { await tts.speak({ text: texto, lang: 'pt-BR', rate: velVoz() * 0.95, pitch: 1.0 }); } catch (e) {} return fim(); }
      await vozesProntas();
      falarNavegador(frases, fim);
      setTimeout(fim, 2000 + texto.length * 120); // garantia, se o navegador não avisar o fim
    } catch (e) { fim(); }
  }
  // Painel de escolha da voz (vai dentro de um elemento qualquer)
  function painelVoz(el) {
    const montar = async () => {
      const srv = await vozServidor(), vs = vozesPt(), escolha = ls.get('vozEscolha') || '';
      const atualNav = vozEscolhida();
      const usandoSrv = srv.disponivel && (!escolha || escolha.startsWith('srv:'));
      const opSrv = srv.disponivel ? srv.vozes.map(v => `<option value="srv:${v.id}" ${(escolha === 'srv:' + v.id || (!escolha && v.id === srv.padrao)) ? 'selected' : ''}>${esc(v.nome)} · voz da central</option>`).join('') : '';
      const opNav = vs.map(v => `<option value="${esc(v.name)}" ${!usandoSrv && atualNav && v.name === atualNav.name ? 'selected' : ''}>${esc(v.name)} · deste aparelho</option>`).join('');
      el.innerHTML = `<div class="form">
        <label>Voz<select class="pv-voz">${opSrv + opNav || '<option>Voz padrão do aparelho</option>'}</select></label>
        <label>Velocidade<input type="range" class="pv-vel" min="0.7" max="1.3" step="0.05" value="${velVoz()}"></label>
        <div class="acoes cheia"><button class="btn pv-testar" type="button">Testar voz</button></div></div>
        <p class="aviso" style="margin:6px 0 0">${srv.disponivel ? 'A “voz da central” é gerada no servidor: todos os aparelhos ouvem a mesma voz, sem internet.'
          : srv.carregando ? 'Carregando a voz da central no servidor…' : 'Voz da central indisponível no servidor; usando a voz deste aparelho.'}</p>`;
      el.querySelector('.pv-voz').onchange = e => ls.set('vozEscolha', e.target.value);
      el.querySelector('.pv-vel').onchange = e => ls.set('vozVel', e.target.value);
      el.querySelector('.pv-testar').onclick = () => falar('Olá! Aqui é a central de corridas. Sua viagem está confirmada, e o veículo chega em três minutos. Câmbio.', { forcar: true });
      if (srv.carregando) setTimeout(() => { infoSrv = null; montar(); }, 4000);
    };
    montar();
    try { speechSynthesis.addEventListener('voiceschanged', montar); } catch (e) {}
  }
  // Microfone estilo rádio: fica aberto até a pessoa mandar parar (pausas para pensar não fecham).
  // O reconhecimento do navegador/celular desliga sozinho no silêncio; aqui ele é religado na hora e o texto vai somando.
  // Devolve { parar() → Promise<texto> }.
  // Junta dois pedaços de fala sem repetir: no celular (Chrome/Android) cada resultado costuma trazer o texto
  // inteiro de novo ("meu nome", "meu nome é Israel", "meu nome é Israel matrícula..."), e isso virava repetição.
  function juntar(a, b) {
    a = (a || '').replace(/\s+/g, ' ').trim(); b = (b || '').replace(/\s+/g, ' ').trim();
    if (!b) return a; if (!a) return b;
    const na = a.toLowerCase(), nb = b.toLowerCase();
    if (nb.includes(na)) return b;          // o novo já contém tudo
    if (na.includes(nb)) return a;          // o novo é repetição de um pedaço
    const pa = a.split(' '), pb = b.split(' ');
    for (let k = Math.min(pa.length, pb.length); k > 0; k--) { // fim de a igual ao começo de b: emenda sem repetir
      if (pa.slice(-k).join(' ').toLowerCase() === pb.slice(0, k).join(' ').toLowerCase()) return pa.concat(pb.slice(k)).join(' ');
    }
    return a + ' ' + b;
  }
  const ANDROID = /Android/i.test(navigator.userAgent);
  async function microfone(aoParcial, aoErro) {
    const sr = plug().SpeechRecognition;
    let ativo = true, juntado = '', atual = '';
    const mostrar = () => aoParcial && aoParcial((juntado + ' ' + atual).replace(/\s+/g, ' ').trim());
    const somar = () => { juntado = juntar(juntado, atual); atual = ''; };
    if (sr) { // app Android
      const p = await sr.requestPermissions().catch(() => null);
      if (p && p.speechRecognition && p.speechRecognition !== 'granted') throw new Error('Permita o microfone para este app.');
      const ouvintes = [];
      try { ouvintes.push(await sr.addListener('partialResults', d => { atual = (d.matches && d.matches[0]) || ''; mostrar(); })); } catch (e) {}
      try { ouvintes.push(await sr.addListener('listeningState', d => { if (d.status === 'stopped') { somar(); mostrar(); if (ativo) setTimeout(abrir, 150); } })); } catch (e) {}
      const abrir = () => sr.start({ language: 'pt-BR', maxResults: 1, partialResults: true, popup: false }).catch(() => {});
      abrir();
      return { async parar() { ativo = false; try { await sr.stop(); } catch (e) {} await new Promise(r => setTimeout(r, 400)); somar(); ouvintes.forEach(o => o && o.remove && o.remove()); return juntado.trim(); } };
    }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) throw new Error('Este navegador não reconhece voz. Use o microfone do teclado (ditado) na caixa de texto.');
    let rec, erro = null, fimAtual = null;
    const abrir = () => {
      // no celular o modo contínuo repete resultados: lá cada sessão é curta e o microfone é religado sozinho
      rec = new SR(); rec.lang = 'pt-BR'; rec.interimResults = true; rec.continuous = !ANDROID; rec.maxAlternatives = 1;
      let finalSessao = '';
      rec.onresult = e => {
        let fin = '', parcial = '';
        for (let i = 0; i < e.results.length; i++) {
          const txt = e.results[i][0].transcript;
          if (e.results[i].isFinal) fin = juntar(fin, txt); else parcial = juntar(parcial, txt);
        }
        finalSessao = fin;
        atual = juntar(finalSessao, parcial); mostrar();
      };
      rec.onerror = e => { if (e.error === 'not-allowed' || e.error === 'service-not-allowed') { erro = new Error('Microfone bloqueado. Libere o microfone para este site (precisa de https ou localhost).'); ativo = false; aoErro && aoErro(erro); } };
      rec.onend = () => { atual = finalSessao; somar(); mostrar(); if (ativo) setTimeout(abrir, 100); else if (fimAtual) fimAtual(); };
      try { rec.start(); } catch (e) {}
    };
    abrir();
    return {
      async parar() {
        if (!ativo) { if (erro) throw erro; return juntado.trim(); }
        ativo = false;
        await new Promise(ok => { fimAtual = ok; try { rec.stop(); } catch (e) { ok(); } setTimeout(ok, 1500); });
        if (erro) throw erro;
        return juntado.trim();
      },
      get erro() { return erro; },
    };
  }
  // compatibilidade: ouve até o primeiro silêncio (usado só por telas antigas)
  async function ouvir(aoParcial) { const m = await microfone(aoParcial); await new Promise(r => setTimeout(r, 6000)); return m.parar(); }
  function bip() {
    try { const a = new (window.AudioContext || window.webkitAudioContext)(); const o = a.createOscillator(); o.frequency.value = 880; o.connect(a.destination); o.start(); setTimeout(() => { o.stop(); a.close(); }, 350); } catch (e) {}
    try { navigator.vibrate && navigator.vibrate([300, 100, 300]); } catch (e) {}
  }
  function toast(txt, ms = 6000) {
    const d = document.createElement('div'); d.className = 'toast'; d.textContent = txt; document.body.appendChild(d); setTimeout(() => d.remove(), ms);
  }

  // Caixa "falar com a central": microfone + texto → servidor → resposta falada
  function caixaVoz(el, { canal, ident, aoResponder, exemplo }) {
    el.innerHTML = `
      <div class="resposta" style="border-left-color:var(--accent);margin-bottom:8px">Padrão: <b>“Eu sou [nome], matrícula [número], preciso de um pool da [origem] para a [destino], para [quantidade] passageiro(s)”</b>.
        Para saber da viagem: <b>“[nome], matrícula [número], onde está meu carro?”</b>
        · Cancelar: <b>“[nome], matrícula [número], quero cancelar a viagem”</b>
        · Se a viagem começou sem você: <b>“[nome], matrícula [número], a viagem começou mas eu não entrei no carro”</b></div>
      <label>O que foi falado<textarea rows="3" class="cv-texto" placeholder="Ex.: Eu sou João Silva, matrícula 12345, preciso de um pool da oficina para a GAINF, para 2 passageiros">${esc(exemplo || '')}</textarea></label>
      <div class="acoes" style="margin-top:8px">
        <button class="btn principal cv-mic" type="button">🎙 Falar</button>
        <button class="btn cv-enviar" type="button">Enviar</button>
        <label class="check"><input type="checkbox" class="cv-voz" checked> Responder em voz alta</label>
      </div>
      <p class="aviso cv-aviso" hidden></p>
      <div class="cv-res" style="margin-top:10px"></div>`;
    const q = s => el.querySelector(s);
    async function enviar() {
      const texto = q('.cv-texto').value.trim(); if (!texto) return;
      q('.cv-res').innerHTML = '<p class="vazio">Enviando para a central…</p>';
      try {
        const r = await api('/api/assistente', { texto, canal, clienteId, ...(ident ? ident() : {}) });
        const cor = r.intencao === 'desconhecida' || (r.interpretacao.faltando || []).length && r.intencao === 'pedir' ? 'var(--bad)' : 'var(--ok)';
        q('.cv-res').innerHTML = `<div class="resposta" style="border-left-color:${cor}"><b>Central:</b> ${esc(r.fala)}</div>
          <p class="aviso" style="margin-top:6px">Entendido como: <b>${esc(r.intencao)}</b> (${esc(r.fonte)})${r.corrida ? ' · viagem ' + esc(r.corrida.id) : ''}</p>`;
        if (q('.cv-voz').checked) falar(r.fala);
        aoResponder && aoResponder(r);
      } catch (e) { q('.cv-res').innerHTML = `<p class="falta">${esc(e.message)}</p>`; }
    }
    q('.cv-enviar').onclick = enviar;
    // Microfone como rádio/WhatsApp: toque para abrir e toque de novo para enviar, OU segure enquanto fala e solte para enviar.
    // Pausas para pensar não fecham o microfone.
    let mic = null, inicioToque = 0, ocupado = false;
    const b = q('.cv-mic'), av = q('.cv-aviso');
    const estadoBotao = on => { b.classList.toggle('gravando', on); b.textContent = on ? '■ Parar e enviar' : '🎙 Falar'; av.hidden = !on; if (on) av.textContent = 'Ouvindo… pode fazer pausas. Toque de novo (ou solte o botão, se estiver segurando) para enviar.'; };
    async function abrirMic() {
      if (mic || ocupado) return;
      ocupado = true;
      try {
        q('.cv-texto').value = '';
        mic = await microfone(p => { q('.cv-texto').value = p; }, e => { fecharMic(false); av.hidden = false; av.textContent = e.message; });
        estadoBotao(true);
      } catch (e) { mic = null; av.hidden = false; av.textContent = e.message; }
      finally { ocupado = false; }
    }
    async function fecharMic(enviarDepois = true) {
      if (!mic || ocupado) return;
      ocupado = true;
      const m = mic; mic = null;
      b.textContent = 'Enviando…';
      try { const t = await m.parar(); estadoBotao(false); if (t) q('.cv-texto').value = t; if (enviarDepois && q('.cv-texto').value.trim()) await enviar(); }
      catch (e) { estadoBotao(false); av.hidden = false; av.textContent = e.message; }
      finally { ocupado = false; }
    }
    b.addEventListener('pointerdown', ev => { ev.preventDefault(); inicioToque = Date.now(); if (mic) fecharMic(); else abrirMic(); });
    b.addEventListener('pointerup', () => { if (mic && Date.now() - inicioToque > 800) fecharMic(); }); // segurou e soltou
    b.addEventListener('contextmenu', ev => ev.preventDefault()); // segurar no celular não abre menu
    b.addEventListener('click', ev => { if (ev.detail === 0) { mic ? fecharMic() : abrirMic(); } }); // teclado (Enter/Espaço)
    return { definir(t) { q('.cv-texto').value = t; }, enviar };
  }

  // ---------- mapa satelital (Leaflet + MapTiler; sem a chave usa o satélite da Esri) ----------
  const CORES = ['#1f6fb2', '#a8432a', '#2e7d4f', '#7a4fb2', '#b26b00', '#00838f', '#c2185b', '#5d6d1e'];
  const corDe = id => CORES[(parseInt(String(id).replace(/\D/g, ''), 10) || 0) % CORES.length];
  const PRINCIPAIS = new Set(['gainf', 'oficina', 'usina', 'canteiro', 'britagem', 'ps1', 'ps3', 'gemin', 'refeitorio', 'portaria', 'calderaria', 'armazem', 'almoxarifado', 'posto', 'mineroduto', 'ambulatorio']);
  let cfgMapa = null;
  const instancias = [];
  function baseSatelite() {
    const k = cfgMapa && cfgMapa.maptilerKey;
    if (k) return L.tileLayer(`https://api.maptiler.com/maps/hybrid/256/{z}/{x}/{y}.jpg?key=${encodeURIComponent(k)}`,
      { maxZoom: 20, attribution: '<a href="https://www.maptiler.com/copyright/" target="_blank">© MapTiler</a> <a href="https://www.openstreetmap.org/copyright" target="_blank">© OpenStreetMap</a>' });
    return L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Imagens © Esri' });
  }
  // margem: espaço coberto por painéis por cima do mapa ({ topo, baixo } em px), para a rota não ficar escondida
  function enquadrar(inst, pontos, margem) {
    const b = pontos.filter(Boolean), m = margem || {};
    const opc = { paddingTopLeft: [30, 30 + (m.topo || 0)], paddingBottomRight: [30, 30 + (m.baixo || 0)], maxZoom: 17 };
    if (b.length === 1) inst.map.fitBounds([b[0], b[0]], opc);
    else if (b.length) inst.map.fitBounds(b, opc);
  }
  function novoMapa(el, aoClicar) {
    const map = L.map(el, { zoomControl: true });
    const inst = { map, base: baseSatelite().addTo(map), grupo: L.layerGroup().addTo(map), ass: '', enquadrado: '' };
    const reais = locais.filter(l => l.real);
    enquadrar(inst, (reais.length > 1 ? reais : locais).map(l => [l.lat, l.lon]));
    if (aoClicar) map.on('click', e => aoClicar(e.latlng));
    // mapa dentro de aba escondida: recalcula o tamanho quando aparece
    if (window.ResizeObserver) new ResizeObserver(() => map.invalidateSize()).observe(el);
    instancias.push(inst);
    return inst;
  }
  // el: <div class="mapa-sat">. carro: mostra só esse carro. alvos: ids de locais em destaque.
  // aoClicar(latlng): clique no mapa (aba Locais). rotulos: nome fixo em todos os locais.
  function mapa(el, { carro, alvos, aoClicar, rotulos, margem } = {}) {
    if (!el || !estado || !locais.length) return;
    if (!window.L) return mapaEsquema(el, { carro, alvos });
    const inst = el._mapa || (el._mapa = novoMapa(el, aoClicar));
    const A = new Set(alvos || []), corr = Object.fromEntries(estado.corridas.map(c => [c.id, c]));
    const carros = estado.motoristas.filter(m => !carro || m.id === carro);
    const ass = JSON.stringify([locais.map(l => [l.id, l.lat, l.lon, l.real]), carros.map(m => [m.id, m.pos.lat.toFixed(5), m.pos.lon.toFixed(5), m.ativo, m.rota]), [...A], !!rotulos]);
    if (ass === inst.ass) return;
    inst.ass = ass;
    const g = inst.grupo; g.clearLayers();
    for (const m of carros) {
      if (!m.rota || !corr[m.atual]) continue;
      if (m.rota.busca) L.polyline(m.rota.busca, { color: corDe(m.id), weight: 4, opacity: .95, dashArray: '8 8' }).addTo(g);
      L.polyline(m.rota.viagem, { color: corDe(m.id), weight: 5, opacity: .8 }).addTo(g);
      // viagem compartilhada: número de cada parada na ordem do roteiro
      (m.rota.paradas || []).forEach((s, i) => L.marker([s.lat, s.lon], { icon: L.divIcon({ className: '', iconSize: [22, 22], iconAnchor: [11, 30],
        html: `<div class="parada-ic ${s.t}" style="border-color:${corDe(m.id)}">${i + 1}</div>` }) })
        .bindTooltip(`${i + 1}. ${s.t === 'buscar' ? 'Buscar' : 'Deixar'} em ${esc((LC[s.l] || {}).nome || s.l)} · ${hora(s.chega)}`, { direction: 'top' }).addTo(g));
    }
    for (const l of locais) {
      const a = A.has(l.id);
      L.circleMarker([l.lat, l.lon], { radius: a ? 9 : 6, color: a ? '#f0b429' : '#ffffff', weight: 2, fillColor: l.real ? '#f0b429' : '#777', fillOpacity: l.real ? .95 : .6 })
        .bindTooltip(esc(l.nome) + (l.real ? '' : ' (posição provisória)'), { permanent: a || (rotulos && l.real) || (!carro && l.real && PRINCIPAIS.has(l.id)), direction: 'right', offset: [8, 0], className: a ? 'rot-alvo' : 'rot' })
        .addTo(g);
    }
    for (const m of carros) {
      const alerta = m.modo === 'pane' || (m.paradoMin >= 4 && m.atual);
      const ic = L.divIcon({ className: '', iconSize: [30, 30], iconAnchor: [15, 15],
        html: `<div class="carro-ic${alerta ? ' alerta' : ''}" style="background:${corDe(m.id)};opacity:${m.ativo ? 1 : .45}">${esc(m.id.replace(/^V0?/, ''))}</div>` });
      L.marker([m.pos.lat, m.pos.lon], { icon: ic, zIndexOffset: 1000 }).bindTooltip(`${esc(m.id)} · ${esc(m.nome)}`, { direction: 'top', offset: [0, -14] }).addTo(g);
    }
    // ao abrir a corrida, enquadra o carro e os locais dela
    const foco = carro ? carro + '|' + [...A].join(',') : '';
    if (foco && inst.enquadrado !== foco) {
      inst.enquadrado = foco;
      enquadrar(inst, [...carros.map(m => [m.pos.lat, m.pos.lon]), ...[...A].map(id => LC[id] && [LC[id].lat, LC[id].lon])], typeof margem === 'function' ? margem() : margem);
    }
  }

  // ---------- plano B sem internet: mapa esquemático em SVG ----------
  function mapaEsquema(el, { carro, alvos } = {}) {
    let svg = el.tagName.toLowerCase() === 'svg' ? el : el.querySelector('svg');
    if (!svg) { el.innerHTML = '<svg class="mapa"></svg>'; svg = el.querySelector('svg'); el.style.height = 'auto'; }
    const xs = locais.map(l => l.x), ys = locais.map(l => l.y), mg = 0.4;
    const X0 = Math.min(...xs) - mg, X1 = Math.max(...xs) + mg * 2, Y0 = Math.min(...ys) - mg, Y1 = Math.max(...ys) + mg, sy = y => Y1 + Y0 - y;
    svg.setAttribute('viewBox', `${X0} ${Y0} ${X1 - X0} ${Y1 - Y0}`);
    const A = new Set(alvos || []), corr = Object.fromEntries(estado.corridas.map(c => [c.id, c]));
    const carros = estado.motoristas.filter(m => !carro || m.id === carro);
    let s = '';
    for (let x = Math.ceil(X0 * 2) / 2; x <= X1; x += 0.5) s += `<line class="grid" x1="${x}" y1="${Y0}" x2="${x}" y2="${Y1}"/>`;
    for (let y = Math.ceil(Y0 * 2) / 2; y <= Y1; y += 0.5) s += `<line class="grid" x1="${X0}" y1="${sy(y)}" x2="${X1}" y2="${sy(y)}"/>`;
    s += `<line class="escala-l" x1="${X1 - 0.7}" y1="${Y1 - 0.12}" x2="${X1 - 0.2}" y2="${Y1 - 0.12}"/><text class="escala" x="${X1 - 0.7}" y="${Y1 - 0.18}">500 m</text>`;
    for (const m of carros) {
      const r = corr[m.atual]; if (!r) continue;
      const p = m.pos, o = LC[r.origem], d = LC[r.destino];
      if (r.status === 'a_caminho') s += `<line class="rota" stroke="${corDe(m.id)}" x1="${p.x}" y1="${sy(p.y)}" x2="${o.x}" y2="${sy(o.y)}"/>`;
      const de = r.status === 'em_viagem' ? p : o;
      s += `<line class="rota" stroke="${corDe(m.id)}" stroke-dasharray="none" opacity=".6" x1="${de.x}" y1="${sy(de.y)}" x2="${d.x}" y2="${sy(d.y)}"/>`;
    }
    for (const l of locais) {
      const a = A.has(l.id);
      s += `<circle class="ponto${a ? ' alvo' : ''}" cx="${l.x}" cy="${sy(l.y)}" r="${a ? .07 : .045}"><title>${esc(l.nome)}</title></circle>`;
      if (a || PRINCIPAIS.has(l.id)) s += `<text class="rotulo${a ? ' alvo' : ''}" x="${l.x + .08}" y="${sy(l.y) + .04}">${esc(l.nome)}</text>`;
    }
    for (const m of carros) {
      const p = m.pos, alerta = m.modo === 'pane' || m.paradoMin >= 4 && m.atual;
      s += `<g opacity="${m.ativo ? 1 : .35}">${alerta ? `<circle cx="${p.x}" cy="${sy(p.y)}" r=".2" fill="none" stroke="var(--bad)" stroke-width=".03"/>` : ''}<circle cx="${p.x}" cy="${sy(p.y)}" r=".12" fill="${corDe(m.id)}" stroke="var(--surface)" stroke-width=".025"/><text class="carro-txt" x="${p.x}" y="${sy(p.y)}">${esc(m.id.replace(/^V0?/, ''))}</text></g>`;
    }
    por(svg, s);
  }

  window.App = { ls, SERVIDOR, clienteId, plug, token, sair, comToken, $, esc, por, pill, hora, MIN, STATUS, api, iniciar, ouvintes, get estado() { return estado; }, get locais() { return locais; }, get LC() { return LC; },
    get cfgMapa() { return cfgMapa; }, carregarLocais, falar, painelVoz, ouvir, bip, toast, caixaVoz, mapa, corDe, nomeLocal: id => (LC[id] ? LC[id].nome : id || '') };
})();
