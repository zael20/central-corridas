// Monta um APK de um app só (Passageiro ou Motorista):
//   APP=passageiro node scripts/preparar.js   (ou APP=motorista)
// 1) copia ../frontend para www  2) fixa o app escolhido  3) gera capacitor.config.json com nome/id próprios
// 4) se a pasta android já existe, garante as permissões (microfone, GPS, http na rede interna).
// 5) no Motorista, instala o código nativo de nativo/ (GPS em segundo plano, com a tela desligada).
// SERVIDOR=https://... muda o endereço que já vem preenchido na primeira abertura.
const fs = require('fs'), path = require('path');
const APP = process.env.APP || 'passageiro';
const NOMES = { passageiro: ['Pool Passageiro', 'br.com.corridas.passageiro'], motorista: ['Pool Motorista', 'br.com.corridas.motorista'] };
if (!NOMES[APP]) throw new Error('APP deve ser passageiro ou motorista');
const raiz = path.join(__dirname, '..'), www = path.join(raiz, 'www');
fs.rmSync(www, { recursive: true, force: true });
fs.cpSync(path.join(raiz, '..', 'frontend'), www, { recursive: true });
// mapa (Leaflet) dentro do APK: não depende do unpkg.com, que pode estar bloqueado na rede da empresa
const leaflet = path.join(raiz, 'node_modules', 'leaflet', 'dist');
if (fs.existsSync(leaflet)) {
  fs.cpSync(leaflet, path.join(www, 'leaflet'), { recursive: true });
  for (const f of fs.readdirSync(www).filter(f => f.endsWith('.html'))) {
    const arq = path.join(www, f);
    fs.writeFileSync(arq, fs.readFileSync(arq, 'utf8').replaceAll('https://unpkg.com/leaflet@1.9.4/dist/', 'leaflet/'));
  }
}
const SERVIDOR = process.env.SERVIDOR || 'https://central-corridas.onrender.com';
fs.writeFileSync(path.join(www, 'fixo.js'), `window.APP_FIXO = ${JSON.stringify(APP)};\nwindow.SERVIDOR_PADRAO = ${JSON.stringify(SERVIDOR)};\n`);
fs.writeFileSync(path.join(raiz, 'capacitor.config.json'), JSON.stringify({
  appId: NOMES[APP][1], appName: NOMES[APP][0], webDir: 'www',
  // servidor da central roda em http na rede interna; sem isso o Android bloqueia
  server: { androidScheme: 'http', cleartext: true },
  android: { allowMixedContent: true },
}, null, 2));
const manifesto = path.join(raiz, 'android', 'app', 'src', 'main', 'AndroidManifest.xml');
if (fs.existsSync(manifesto)) {
  let m = fs.readFileSync(manifesto, 'utf8');
  const perms = ['android.permission.INTERNET', 'android.permission.RECORD_AUDIO', 'android.permission.ACCESS_FINE_LOCATION', 'android.permission.ACCESS_COARSE_LOCATION', 'android.permission.VIBRATE'];
  for (const p of perms) if (!m.includes(p)) m = m.replace('</manifest>', `    <uses-permission android:name="${p}" />\n</manifest>`);
  // reconhecimento de voz do Google precisa ser "visível" para o app (Android 11+)
  if (!m.includes('android.speech.RecognitionService')) m = m.replace('</manifest>', '    <queries><intent><action android:name="android.speech.RecognitionService" /></intent><intent><action android:name="android.intent.action.TTS_SERVICE" /></intent></queries>\n</manifest>');
  if (!m.includes('usesCleartextTraffic')) m = m.replace('<application', '<application android:usesCleartextTraffic="true"');
  if (APP === 'motorista') {
    const extras = ['android.permission.FOREGROUND_SERVICE', 'android.permission.FOREGROUND_SERVICE_LOCATION', 'android.permission.POST_NOTIFICATIONS',
      'android.permission.WAKE_LOCK', 'android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS',
      'android.permission.ACCESS_BACKGROUND_LOCATION', 'android.permission.SYSTEM_ALERT_WINDOW'];
    for (const p of extras) if (!m.includes(p)) m = m.replace('</manifest>', `    <uses-permission android:name="${p}" />\n</manifest>`);
    if (!m.includes('.RastreioServico')) m = m.replace('</application>', '        <service android:name=".RastreioServico" android:foregroundServiceType="location" android:exported="false" />\n    </application>');
    const pasta = path.join(raiz, 'android', 'app', 'src', 'main', 'java', ...NOMES[APP][1].split('.'));
    fs.mkdirSync(pasta, { recursive: true });
    for (const f of fs.readdirSync(path.join(raiz, 'nativo')))
      fs.writeFileSync(path.join(pasta, f), fs.readFileSync(path.join(raiz, 'nativo', f), 'utf8').replace(/__PACOTE__/g, NOMES[APP][1]));
  }
  fs.writeFileSync(manifesto, m);
}
console.log(`Pronto: ${NOMES[APP][0]} (${NOMES[APP][1]})`);
