"""Códigos de acesso: necessário antes de deixar o sistema na internet.

Dois papéis:
- passageiro: pede viagem, pergunta da própria viagem, cancela. Só recebe as próprias viagens em tempo real.
- operacao:   central e motorista (tudo).
- tv:         painel de gestão ao vivo (só leitura, sem matrícula, só o primeiro nome) — para deixar aberto numa TV.
Os códigos ficam em dados/acesso.json (gerados na primeira vez) e podem ser trocados na central
(Locais → Acesso). Trocar um código derruba quem entrou com o código antigo.
Variável ACESSO=0 desliga a proteção (só para uso na rede interna).
"""
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path


class Acesso:
    def __init__(self, arquivo):
        self.arquivo = Path(arquivo)
        self.ligado = os.environ.get('ACESSO') != '0'
        try:
            with open(self.arquivo, encoding='utf-8') as f:
                self.cfg = json.load(f)
        except (OSError, ValueError):
            self.cfg = {}
        mudou = False
        # na nuvem os códigos vêm das variáveis de ambiente (o disco do servidor grátis é apagado ao reiniciar)
        for k, env in (('codigo_passageiro', 'CODIGO_PASSAGEIRO'), ('codigo_operacao', 'CODIGO_OPERACAO'), ('codigo_tv', 'CODIGO_TV'),
                       ('segredo', 'ACESSO_SEGREDO')):
            if os.environ.get(env):
                self.cfg[k] = os.environ[env]
        for k in ('codigo_passageiro', 'codigo_operacao', 'codigo_tv'):
            if not self.cfg.get(k):
                self.cfg[k] = f'{secrets.randbelow(900000) + 100000}'
                mudou = True
        if not self.cfg.get('segredo'):
            self.cfg['segredo'] = secrets.token_hex(16)
            mudou = True
        if mudou:
            self._gravar()

    def _gravar(self):
        tmp = self.arquivo.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=1)
        tmp.replace(self.arquivo)

    def _assinar(self, papel):
        return hmac.new(self.cfg['segredo'].encode(), papel.encode(), hashlib.sha256).hexdigest()[:32]

    def entrar(self, codigo):
        """Código digitado → (papel, token) ou (None, None)."""
        codigo = (codigo or '').strip()
        for papel, k in (('operacao', 'codigo_operacao'), ('passageiro', 'codigo_passageiro'), ('tv', 'codigo_tv')):
            if codigo and hmac.compare_digest(codigo, self.cfg[k]):
                return papel, f'{papel}.{self._assinar(papel)}'
        return None, None

    def papel(self, token):
        if not self.ligado:
            return 'operacao'
        if not token or '.' not in token:
            return None
        papel, assin = token.split('.', 1)
        if papel in ('operacao', 'passageiro', 'tv') and hmac.compare_digest(assin, self._assinar(papel)):
            return papel
        return None

    def trocar(self, passageiro=None, operacao=None, tv=None):
        for k, v in (('codigo_passageiro', passageiro), ('codigo_operacao', operacao), ('codigo_tv', tv)):
            if v is not None:
                v = str(v).strip()
                if len(v) < 4:
                    raise ValueError('O código precisa ter pelo menos 4 caracteres.')
                self.cfg[k] = v
        if len({self.cfg['codigo_passageiro'], self.cfg['codigo_operacao'], self.cfg['codigo_tv']}) < 3:
            raise ValueError('Os três códigos precisam ser diferentes.')
        self.cfg['segredo'] = secrets.token_hex(16)  # derruba quem entrou com os códigos antigos
        self._gravar()

    def ver(self):
        return {'ligado': self.ligado, 'passageiro': self.cfg['codigo_passageiro'], 'operacao': self.cfg['codigo_operacao'], 'tv': self.cfg['codigo_tv']}
