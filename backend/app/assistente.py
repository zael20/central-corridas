"""Assistente da central: entende o que a pessoa quer e monta a resposta falada.

Tudo que é "treinável" (expressões, respostas, limites) fica em config/assistente.yaml.
Este módulo só lê o arquivo; mudar o comportamento não exige mexer no aplicativo.
"""
import json
import os
import random
import re
from pathlib import Path

import yaml

from .interpretador import _parecida, interpretar_pedido, normalizar
from .locais import LOCAIS

CAMINHO = Path(__file__).resolve().parent.parent / 'config' / 'assistente.yaml'


class Config:
    def __init__(self, caminho=CAMINHO):
        self.caminho = Path(caminho)
        self.mtime = 0
        self.dados = {}
        self.erro = None
        self.recarregar(forcar=True)

    def recarregar(self, forcar=False):
        """Relê o arquivo se ele mudou. Com erro de sintaxe, mantém a versão anterior e guarda o erro."""
        try:
            mt = self.caminho.stat().st_mtime
            if not forcar and mt == self.mtime:
                return False
            with open(self.caminho, encoding='utf-8') as f:
                novo = yaml.safe_load(f) or {}
            self.dados, self.mtime, self.erro = novo, mt, None
            return True
        except Exception as e:  # arquivo com erro: avisa na central, segue com o anterior
            self.erro = f'{type(e).__name__}: {e}'
            return False

    def __getitem__(self, k):
        return self.dados.get(k, {})

    def resposta(self, chave, **v):
        modelos = self['respostas'].get(chave) or [chave]
        modelo = random.choice(modelos) if isinstance(modelos, list) else modelos
        return preencher(modelo, v)


def preencher(modelo, v):
    # variável ausente fica vazia em vez de quebrar a resposta
    txt = re.sub(r'\{(\w+)\}', lambda m: str(v.get(m.group(1), '') if v.get(m.group(1)) is not None else ''), modelo)
    txt = re.sub(r'^[\s,]+', '', txt.replace('  ', ' ')).strip()  # sem nome: "{nome}, viagem..." vira "Viagem..."
    return txt[:1].upper() + txt[1:]


INTENCOES = ('pedir', 'informacao', 'cancelar', 'nao_embarquei', 'nao_encontrei', 'reclamacao')
# palavras que não ajudam a comparar frases (o nome e a matrícula mudam de pessoa para pessoa)
_VAZIAS = {'eu', 'sou', 'o', 'a', 'os', 'as', 'de', 'do', 'da', 'e', 'um', 'uma', 'meu', 'minha', 'matricula', 'numero', 'aqui',
           'que', 'ja', 'mas', 'no', 'na', 'ai', 'ei', 'oi', 'ola', 'base', 'central', 'cambio', 'por', 'favor'}


def _chave_frase(texto, interp=None):
    """Frase normalizada sem números, nome do solicitante e palavras vazias (para comparar com as aprendidas)."""
    nome = set(normalizar((interp or {}).get('solicitante') or '').split())
    return [w for w in normalizar(texto).split() if not w.isdigit() and w not in _VAZIAS and w not in nome]


def _parecido(a, b):
    """Semelhança entre duas frases (0 a 1) pelas palavras em comum."""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0
    return len(sa & sb) / len(sa | sb)


class Aprendizado:
    """Correções feitas na central (aba Assistente): "esta frase era X". Ficam em dados/aprendido.json.

    Uma frase igual a uma corrigida usa a intenção ensinada, mesmo que as regras digam outra coisa.
    Uma frase parecida (muitas palavras em comum) também usa, e isso aparece como fonte "aprendido".
    """

    def __init__(self, caminho):
        self.caminho = Path(caminho)
        try:
            with open(self.caminho, encoding='utf-8') as f:
                self.exemplos = json.load(f)
        except (OSError, ValueError):
            self.exemplos = []

    def _gravar(self):
        tmp = self.caminho.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.exemplos, f, ensure_ascii=False, indent=1)
        tmp.replace(self.caminho)

    def ensinar(self, texto, intencao, em=None, conversa=None):
        if intencao not in INTENCOES:
            raise ValueError('Intenção inválida.')
        chave = ' '.join(_chave_frase(texto))
        if not chave:
            raise ValueError('Frase sem palavras para aprender.')
        self.exemplos = [e for e in self.exemplos if e['chave'] != chave]
        self.exemplos.append({'texto': texto, 'chave': chave, 'intencao': intencao, 'em': em, 'conversa': conversa})
        self._gravar()

    def esquecer(self, texto):
        chave = ' '.join(_chave_frase(texto))
        antes = len(self.exemplos)
        self.exemplos = [e for e in self.exemplos if e['chave'] != chave and e['texto'] != texto]
        self._gravar()
        return antes - len(self.exemplos)

    def procurar(self, texto, interp):
        """→ (intencao, semelhança) da frase aprendida mais parecida, ou (None, 0)."""
        k = _chave_frase(texto, interp)
        melhor, nota = None, 0
        for e in self.exemplos:
            s = 1.0 if ' '.join(k) == e['chave'] else _parecido(k, e['chave'].split())
            if s > nota:
                melhor, nota = e['intencao'], s
        return melhor, nota


def _tem_parecida(toks, exp):
    """A expressão aparece no texto tolerando 1 letra errada nas palavras de 6+ letras ("embarqei", "cancellar")."""
    k = len(exp)
    for i in range(len(toks) - k + 1):
        if all(_parecida(toks[i + j], exp[j]) >= 0 for j in range(k)):
            return True
    return False


def detectar_intencao(texto, cfg, interp):
    norm = normalizar(texto)
    n = ' ' + norm + ' '
    listas = list((cfg['intencoes'] or {}).items())
    for intencao, expressoes in listas:
        for e in expressoes or []:
            if re.search(r'(?<![a-z0-9])' + re.escape(normalizar(e)) + r'(?![a-z0-9])', n):
                return intencao
    toks = norm.split()
    for intencao, expressoes in listas:
        for e in expressoes or []:
            exp = normalizar(e).split()
            if any(len(w) >= 6 for w in exp) and _tem_parecida(toks, exp):
                return intencao
    if interp.get('origem') or interp.get('destino'):
        return 'pedir'
    return 'desconhecida'


def entender(texto, cfg, aprendido=None):
    """Texto falado → {intencao, interp (pedido estruturado), fonte}."""
    interp = interpretar_pedido(texto, LOCAIS)
    fonte = 'regras'
    if aprendido:
        lim = (cfg['aprendizado'] or {})
        intencao, nota = aprendido.procurar(texto, interp)
        if intencao and nota >= lim.get('semelhanca_minima', 0.75):
            return {'intencao': intencao, 'interp': interp, 'fonte': 'aprendido'}
        regra = detectar_intencao(texto, cfg, interp)
        if regra == 'desconhecida' and intencao and nota >= lim.get('semelhanca_se_nao_entendeu', 0.5):
            return {'intencao': intencao, 'interp': interp, 'fonte': 'aprendido'}
    ia = cfg['ia'] or {}
    if ia.get('usar_llm') and os.environ.get('ANTHROPIC_API_KEY'):
        try:
            j = _perguntar_llm(texto, ia.get('modelo', 'claude-haiku-5-5'))
            intencao = j.get('intencao') or 'desconhecida'
            ids = {l['id'] for l in LOCAIS}
            for k in ('origem', 'destino'):
                interp[k] = j.get(k) if j.get(k) in ids else interp.get(k)
            for k in ('solicitante', 'matricula'):
                if j.get(k):
                    interp[k] = re.sub(r'\D', '', str(j[k])) if k == 'matricula' else j[k]
            if j.get('passageiros'):
                interp['passageiros'] = max(1, int(j['passageiros']))
            interp['urgente'] = bool(j.get('urgente', interp.get('urgente')))
            interp['faltando'] = [k for k in ('origem', 'destino') if not interp.get(k)]
            return {'intencao': intencao, 'interp': interp, 'fonte': 'llm'}
        except Exception as e:
            interp.setdefault('observacoes', []).append(f'IA indisponível ({type(e).__name__}), usei as regras.')
    return {'intencao': detectar_intencao(texto, cfg, interp), 'interp': interp, 'fonte': fonte}


def _perguntar_llm(texto, modelo):
    import ssl

    import httpx
    locais = '\n'.join(f"{l['id']}: {l['nome']} ({', '.join(l.get('apelidos', []))})" for l in LOCAIS)
    prompt = f'''Você atende o rádio da central de transporte interno (pool) de uma mineração em Paragominas.
O texto veio de transcrição de voz e pode ter erros. "pool" = pedido de carro.
Locais válidos (id: nome (apelidos)):
{locais}

Texto: """{texto}"""

Classifique a intenção: "pedir" (quer um carro), "informacao" (quer saber da viagem: onde está o carro, atraso, previsão),
"cancelar", "nao_embarquei" (a viagem aparece como iniciada ou o carro foi embora, mas a pessoa NÃO entrou no carro;
por exemplo outra pessoa embarcou no lugar dela), "nao_encontrei" (o app diz que o carro chegou mas a pessoa não acha
o carro, ou o carro foi para o lugar errado), "reclamacao" (reclamação do motorista ou da viagem), ou "desconhecida".
Extraia os dados ditos.
Responda só JSON: {{"intencao":..., "origem":id|null, "destino":id|null, "passageiros":n|null, "urgente":bool,
"solicitante":texto|null, "matricula":digitos|null}}'''
    r = httpx.post('https://api.anthropic.com/v1/messages', timeout=20, verify=ssl.create_default_context(), headers={
        'x-api-key': os.environ['ANTHROPIC_API_KEY'], 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
        json={'model': modelo, 'max_tokens': 300, 'messages': [{'role': 'user', 'content': prompt}]})
    r.raise_for_status()
    txt = ''.join(b.get('text', '') for b in r.json()['content'])
    return json.loads(txt[txt.index('{'):txt.rindex('}') + 1])
