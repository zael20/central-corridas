# Central de Corridas Internas (pool) — versão com servidor

Sistema de pool interno da operação: pedido por rádio ou pelo app, escolha automática do carro, previsão de chegada, acompanhamento, alerta quando o carro para e registro de todos os tempos.

```
uber-radio/
├── backend/            Servidor em Python (FastAPI). TODA a regra e a "IA" ficam aqui.
│   ├── app/            despacho.py (escolha do carro), interpretador.py (texto do rádio → pedido),
│   │                   assistente.py (intenção e respostas), central.py (fluxos), main.py (API)
│   ├── config/assistente.yaml   ← onde a IA é treinada (frases, respostas, limites)
│   ├── tests/          testes automáticos
│   └── iniciar.bat / iniciar.sh
├── frontend/           Telas (HTML/JS): passageiro.html, motorista.html, central.html
├── android/            Casca Android (Capacitor) que vira os APKs
└── .github/workflows/apk.yml    Gera os APKs no GitHub
```

## 1. Rodar o servidor

Requisitos: Python 3.11 ou mais novo.

**Windows:** dê dois cliques em `backend/iniciar.bat`. Na primeira vez ele instala as dependências.
**VS Code:** abra a pasta `uber-radio`, vá em *Executar e Depurar* e escolha **Servidor da central**.
**Linux / Raspberry Pi:** `./backend/iniciar.sh`

Abra `http://localhost:8000` no PC. Nos celulares, use `http://IP-DO-PC:8000`, com o celular na mesma rede. Para ver o IP no Windows, rode `ipconfig`.
Se o celular não abrir, libere a porta 8000 no Firewall do Windows. Libere só para a rede interna e não abra essa porta para a internet.

O banco fica em `backend/dados/central.db` (SQLite). Para começar do zero, apague esse arquivo com o servidor parado.

Para conferir se o servidor está no ar, abra `http://localhost:8000/api/saude`. A resposta mostra a hora do servidor, se o `assistente.yaml` tem erro e se a IA paga está ligada.

**Rodar à mão** (sem os scripts):
```
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt        (Linux: .venv/bin/pip ...)
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Variáveis de ambiente (todas opcionais):**

| Variável | Padrão | Para que serve |
|---|---|---|
| `BANCO` | `backend/dados/central.db` | Caminho do banco SQLite |
| `FRONTEND_DIR` | `frontend/` | Pasta das telas servidas pelo servidor |
| `ANTHROPIC_API_KEY` | — | Chave da IA de linguagem (ver seção 3) |

## 2. Os aplicativos

| App | Para quem | O que faz |
|---|---|---|
| Passageiro | quem pede | Fala ou digita o pedido, acompanha o carro e o tempo, pergunta "onde está meu carro?" e recebe avisos falados |
| Motorista | motorista | Recebe a corrida (direto quando está livre; aceita ou recusa quando está ocupado), toca Cheguei → Embarcou → Finalizar, envia o GPS e responde às perguntas da central |
| Central | operador do rádio | Chamadas ("fulano está pedindo um pool, o veículo tal vai atender"), alertas, mapa, rádio, conversas da IA, gestão e frota |

**Rádio:** o PC da central fica ligado ao rádio. O operador fala ou digita o que ouviu na aba Rádio, e a resposta sai em voz pelo PC. Os avisos que chegam depois (carro definido, resposta do motorista) também são falados.

### Padrão de fala
- Pedir: *"Eu sou [nome], matrícula [número], preciso de um pool da [origem] para a [destino]"*
- Saber da viagem: *"[nome], matrícula [número], onde está meu carro?"*, ou *"…o carro não chegou, aconteceu alguma coisa?"*
- Cancelar: *"cancela meu pool, matrícula [número]"*

### Carro parado ou atrasado
1. O servidor acompanha a posição dos carros (GPS do app do motorista; nos carros simulados, a simulação).
2. Se um carro em atendimento fica parado mais de `parado_min` minutos, ou passa `atraso_min` minutos da previsão, o servidor pergunta ao motorista: *"Você está bem? Aconteceu algo? Vai conseguir chegar no horário?"*. A pergunta aparece em tela cheia, com bip e voz, e o motorista responde com um toque.
3. Se o passageiro perguntar antes disso, a central responde *"o veículo está parado há N minutos perto de X, estou perguntando ao motorista"* e manda a pergunta na hora.
4. A resposta do motorista é repassada em voz ao passageiro.
   - Se o motorista responder "tive um problema", o carro sai de operação e a corrida vai para outro carro.
   - Sem resposta em `sem_resposta_min` minutos, a central recebe um alerta para chamar pelo rádio.

Para testar sem um carro real, use **Central → Operação → Simular pane** num carro que está atendendo.

## 3. Treinar a IA sem mexer no app

Edite `backend/config/assistente.yaml` e salve. O servidor relê o arquivo sozinho em até 5 segundos.
- `intencoes`: expressões que indicam pedir, pedir informação ou cancelar.
- `respostas`: o que a central fala. Cada resposta pode ter várias versões.
- `checagem_motorista`: a pergunta e as opções do motorista.
- `monitor`: os limites de carro parado (`parado_min`, `parado_metros`) e de atraso (`atraso_min`), se a pergunta ao motorista é automática (`perguntar_automatico`) e se a corrida vai para outro carro quando há problema (`redespachar_se_problema`).
- `ia`: liga a IA de linguagem (`usar_llm`) e escolhe o modelo (`modelo`).

Se preferir não esperar a releitura automática, use **Central → Assistente → Recarregar**.

Nomes de locais e apelidos falados ficam em `backend/app/locais.py`. A interpretação das frases fica em `backend/app/interpretador.py`.
Se o arquivo YAML tiver erro, a central mostra o erro e continua usando a versão anterior.

**Material de treino:**
- Central → Assistente lista todas as conversas: o que foi falado, o que foi entendido e o que foi respondido.
- "Baixar conversas (CSV)" exporta essa lista.
- Para medir acertos com os áudios da empresa do rádio: transcreva os áudios e coloque no formato de `backend/tests/frases.json`, depois rode `python tests/test_interpretador.py arquivo.json`.

**IA de linguagem (opcional e paga):** com `ia.usar_llm: true` e a variável de ambiente `ANTHROPIC_API_KEY` definida, o Claude passa a entender frases fora do padrão. O uso é cobrado por mensagem. Desligada, a central usa só as regras, que são gratuitas e funcionam sem internet.

## 3b. Mapa satelital, locais e rotas

- **Satélite:** as telas usam o MapTiler (camada *hybrid*). A chave fica no servidor, em `backend/dados/mapa.json`, e pode ser trocada em **Central → Locais → Mapa e rotas**. Sem chave, as telas usam o satélite da Esri.
- **Locais:** em **Central → Locais**, escolha o local e clique no satélite onde ele fica. A posição vai para `backend/dados/pontos.json` e vale para todos os aparelhos. O campo "Importar coordenadas" aceita o texto do botão "Copiar coordenadas" do painel antigo.
- **Rotas:** a distância pela estrada vem do OSRM (OpenStreetMap, gratuito, precisa de internet no servidor) e fica guardada em `backend/dados/rotas.json`. Tempo do trecho = distância pela estrada ÷ velocidade média das vias internas (padrão 30 km/h, ajustável na mesma tela). Trechos com tempo medido na planilha usam o tempo medido. Via interna que não existe no OpenStreetMap fica em linha reta × 1,3.
- Para rodar sem internet: variável `ROTAS_OFFLINE=1`.

## 3c. Cancelamento, "não embarquei" e aprendizado

- **Cancelar:** *"[nome], matrícula [número], quero cancelar a viagem"*. Vale até o carro chegar ao local. Depois que a viagem começa, a central explica que não dá para cancelar.
- **Viagem começou sem o passageiro:** *"[nome], matrícula [número], a viagem começou mas eu não entrei no carro"* (ou o botão **Não entrei no carro** no app do passageiro). A central avisa o operador, pergunta ao motorista quem está no carro e responde ao passageiro:
  - *O passageiro está comigo*: a central é alertada para confirmar pelo rádio.
  - *Embarcou outra pessoa*: abre um novo pedido urgente para o passageiro e informa o carro e o tempo.
  - *Iniciei por engano*: a viagem volta para "carro chegou" e o passageiro é avisado que o carro está esperando.
  A corrida fica marcada com a ocorrência (Gestão e CSV).
- **Aprendizado:** em **Central → Assistente**, a coluna **Corrigir** ensina o que a pessoa queria. Frases iguais ou parecidas passam a ser entendidas assim (fonte "aprendido"). Os exemplos ficam em `backend/dados/aprendido.json`, e os limites de semelhança ficam em `aprendizado` no `assistente.yaml`. A mesma aba mostra a taxa de entendimento, as frases não entendidas mais repetidas e o que já foi aprendido.

## 3d. Cadastro de pessoas, situações e voz

- **Nome ↔ matrícula:** cada vez que alguém fala nome e matrícula, isso conta como um voto (tabela `pessoas_obs` no banco). O nome que mais se repete para a matrícula vira o oficial. A transcrição pode errar ("Sabio"/"Sávio", "Misael"/"Mizael", um dígito trocado), e mesmo assim a pessoa é reconhecida. Dados digitados no app valem 2 votos, a confirmação do motorista vale 3 e a correção da central (Assistente → Pessoas → Corrigir nome) decide. Depois a pessoa pode falar só a matrícula, só o nome, ou os dois.
- **Situações reconhecidas:** pedir · informação/demora · cancelar · não embarquei (o carro saiu sem mim) · não encontro o carro / local errado · reclamação. As frases ficam em `intencoes` no `assistente.yaml`. Palavras longas toleram 1 letra errada.
- **Pela situação da viagem:** se a viagem já começou e a pessoa diz que ainda está esperando, vira "não embarquei". Se a pessoa já tem viagem aberta e pede de novo, a central responde a viagem que já existe e não cria outra.
- **Não embarquei:** o motorista vê os dados do passageiro registrado e confirma ("é Fulano, matrícula X") ou informa nome e matrícula de quem está no carro. A resposta fica gravada na corrida (`embarcouDeFato`) e no cadastro.
- **Voz da central (servidor):** o servidor gera a fala com o Kokoro (código aberto, offline), uma voz feminina só, fixa: *Bella* (mistura da Dora brasileira com a Bella do Kokoro, falando português; escolhida em 2026-10-20). Não há escolha de voz nas telas, só a velocidade. Todos os aparelhos ouvem a mesma voz. Modelo em `%LOCALAPPDATA%\central-corridas\modelos` (`kokoro-v1.0.onnx` ou o menor `kokoro-v1.0.int8.onnx`, e `voices-v1.0.bin`, das releases *model-files-v1.0* de github.com/thewh1teagle/kokoro-onnx), pacote `kokoro-onnx` (instale com `pip install -r backend/requirements-voz.txt`). Sem o modelo, as telas usam uma voz feminina do aparelho. Memória medida: ~560 MB com o modelo completo, ~340 MB com o int8 (que gasta mais processador); por isso no Render grátis a voz fica desligada (`VOZ=0`).
- **Voz do aparelho:** feminina por padrão. No Microsoft Edge a central usa as vozes neurais "Francisca/Thalita (Natural)". Cada tela tem o painel **Voz** para escolher a voz e a velocidade.

## 3e. Viagem compartilhada

Um carro que já está em rota, com lugar sobrando, pode buscar outro passageiro no caminho. A ordem das paradas é: busca o novo, deixa quem já estava, depois deixa o novo. Quem tem o mesmo destino desce junto.
- Só compartilha se o atraso para quem já está no carro for até **6 min** e se o novo passageiro for buscado no máximo **4 min** depois do que levaria o melhor carro sozinho. Os dois limites e o liga/desliga ficam em **Central → Locais → Mapa e rotas**.
- A conta respeita os lugares: capacidade menos os passageiros a bordo e os que ainda vão ser buscados, trecho a trecho.
- Urgente nunca é compartilhada.
- Enquanto o carro leva mais de uma viagem, ele tem um **roteiro** (paradas com horário previsto). O app do motorista mostra as paradas em ordem, cada uma com o seu botão. O mapa mostra as paradas numeradas.
- "Por que este carro?" mostra a opção compartilhada ao lado das outras, com o atraso causado e os lugares livres.
- Desligar no servidor: variável `COMPARTILHAR=0`.

## 4. APK Android

O APK é só uma casca: as telas vêm de `frontend/` e toda a regra vem do servidor. Na primeira abertura, o app pede o endereço do servidor.
O APK gerado é o **Pool Motorista**. O passageiro usa a página `passageiro.html` no navegador (o `preparar.js` ainda sabe montar um APK de passageiro, se precisar).

Ao abrir, o app do motorista pede: localização, localização o tempo todo, notificações, bateria sem restrição e sobrepor a outros apps (para abrir por cima quando chega corrida). Dá para rever em Menu → Permissões do app.

**Pelo GitHub (grátis, sem instalar nada):**
1. Coloque a pasta `uber-radio` num repositório do GitHub.
2. Abra Actions → **Gerar APKs** → Run workflow.
3. Quando terminar, baixe o `pool-motorista.apk` na página *Releases* (`apk-main`): https://github.com/zael20/central-corridas/releases/download/apk-main/pool-motorista.apk

**No PC (Android Studio instalado):**
```
cd android
npm install
set APP=motorista            (Linux: export APP=motorista)
node scripts/preparar.js
npx cap add android
node scripts/preparar.js
npx cap sync android
npx cap open android          → Build → Build APK
```

Para instalar no celular, copie o `.apk` e permita "instalar apps desconhecidos". No app, o microfone usa o reconhecimento de voz do Google, e a voz usa o TTS do celular.

**Acessos por pessoa (aba Acessos da central).** Cada pessoa entra só com a matrícula (sem código), e na central e no app do motorista só entra quem está cadastrado na aba, com perfil Adm (central e cadastro), Motorista (app do motorista) ou Passageiro. A matrícula `ADM_MATRICULA` (padrão 8015) é sempre adm. Os códigos gerais antigos ainda são aceitos pelo servidor, mas não aparecem mais nas telas. No Render grátis o disco é apagado ao reiniciar: os acessos cadastrados somem e só o adm principal volta sozinho.

**Link do passageiro: `/passageiro`.** Abre direto a tela de pedir pool, sem a escolha de telas. Qualquer matrícula entra como passageiro (para exigir cadastro, use `PASSAGEIRO_LIVRE=0`); se a central já conhece o nome daquela matrícula, ele vem preenchido. A página pede a localização do celular: o formulário já escolhe o local mais perto (até 400 m) como origem, e um pedido falado ou digitado sem dizer onde a pessoa está usa essa posição como origem. O pedido sai no nome da matrícula que entrou; para pedir para outra pessoa, marque **Para outra pessoa** (nome e matrícula de quem vai) ou fale o nome e a matrícula dela. A viagem guarda quem pediu (`pedidoPor`) e a posição de quem pediu (`posPassageiro`).

**Tela do motorista.** Mapa na tela inteira, barra com o motorista do turno (matrícula; o nome vem do cadastro), painel de baixo que recolhe para ver só o mapa, pop-up para aceitar/recusar corrida e cartão da próxima corrida prevista.

**GPS do Pool Motorista em segundo plano.** No APK do motorista, a posição é enviada por um serviço do Android (código em `android/nativo/`, instalado pelo `preparar.js`), e não pela página. Ele continua com a tela desligada ou o app minimizado e deixa uma notificação fixa "Pool Motorista em operação".
- Liga sozinho quando o carro está **assumido** neste celular e **em operação**; desliga em "Sair de operação".
- Manda a posição a cada 10 s para `POST /api/motoristas/{carro}/gps` (também com o carro parado, para a central saber que o celular está vivo).
- Em celulares que economizam bateria (Samsung, Xiaomi, Motorola), toque em "Não deixar o Android pausar o GPS" na tela do motorista.
- O endereço que vem preenchido na primeira abertura é `https://central-corridas.onrender.com`; para outro, gere com `SERVIDOR=http://... node scripts/preparar.js` ou troque no app em "Servidor".

## 5. Testes

```
cd backend
python tests/test_interpretador.py   # 23 frases de rádio
python tests/test_despacho.py        # turno simulado (mesmos números do protótipo)
python tests/test_central.py         # conversa completa: pedido, carro parado, resposta do motorista, cancelamento
python tests/test_novidades.py       # cancelar por voz, "não embarquei", aprendizado, pontos do mapa
python tests/test_compartilhada.py   # carona no caminho: paradas, lugares, desvio, cancelamento
python tests/test_pessoas.py         # cadastro nome ↔ matrícula, frases reais, confirmação do motorista
```

Use o Python do `.venv` (por exemplo, `.venv\Scripts\python tests/test_central.py`) se as dependências não estiverem instaladas no Python do sistema.

## 6. API (para integração)

As telas usam só esta API. Com o servidor rodando, a documentação interativa fica em `http://localhost:8000/docs`.

| Método | Caminho | O que faz |
|---|---|---|
| GET | `/api/saude` | Servidor no ar, erro de configuração, IA ligada |
| GET | `/api/estado` | Situação atual: corridas, carros, alertas |
| GET | `/api/locais` | Locais conhecidos |
| GET | `/api/indicadores` | Números da operação |
| POST | `/api/assistente` | Envia uma frase (rádio ou app) e recebe a resposta da central |
| POST | `/api/assistente/recarregar` | Relê o `assistente.yaml` |
| GET | `/api/conversas` | Últimas conversas do assistente (`?limite=200`) |
| POST | `/api/corridas` | Cria uma corrida |
| POST | `/api/corridas/{id}/{acao}` | Ação numa corrida: `aceitar`, `recusar`, `cheguei`, `embarcou`, `finalizar`, `cancelar`, `checar` |
| POST | `/api/motoristas` | Cadastra um motorista/carro |
| POST | `/api/motoristas/{id}/{acao}` | Ação de um motorista: `ativo`, `modo`, `gps`, `pane`, `remover` |
| POST | `/api/checagens/{id}` | Resposta do motorista à pergunta "você está bem?" |
| GET | `/api/corridas.csv` | Exporta as corridas com todos os tempos |
| GET | `/api/conversas.csv` | Exporta as conversas do assistente |
| WS | `/ws` | Avisos em tempo real (o servidor envia o estado; o cliente só manda `ping`) |

As ações aceitas em cada caminho estão em `backend/app/main.py`.

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| O celular não abre a página | Confira se o celular está na mesma rede, use o IP do PC (não `localhost`) e libere a porta 8000 no Firewall do Windows |
| O microfone não funciona no navegador do celular | Navegadores só liberam o microfone em HTTPS ou `localhost`. Use o APK ou digite o pedido |
| A central mostra erro de configuração | Há um erro no `assistente.yaml` (geralmente indentação). Corrija e salve; até lá vale a versão anterior |
| `iniciar.bat` falha na primeira vez | Instale o Python 3.11+ marcando "Add to PATH", apague a pasta `backend/.venv` e rode de novo |
| A IA paga não liga | Confira `ia.usar_llm: true` e a variável `ANTHROPIC_API_KEY` no ambiente onde o servidor roda. Veja em `/api/saude` |

## Limitações conhecidas

- As posições dos locais ainda são fictícias. Quando chegar o KML com os pontos reais, elas vão para `locais.py`. Até lá, o GPS real dos motoristas não casa com o mapa.
- As rotas usam os tempos da planilha (25 pares). Os outros pares usam linha reta × 1,3 a 30 km/h.
- Não há login: qualquer pessoa na rede abre a Central. Antes de usar em produção, é preciso colocar senha e HTTPS.
