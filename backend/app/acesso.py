"""Códigos de acesso: necessário antes de deixar o sistema na internet.

Dois papéis:
- passageiro: pede viagem, pergunta da própria viagem, cancela. Só recebe as próprias viagens em tempo real.
- operacao:   central e motorista (tudo).
Os códigos ficam em dados/acesso.json (gerados na primeira vez) e podem ser trocados na central
(Locais → Acesso). Trocar um código derruba quem entrou com o código antigo.
Variável ACESSO=0 desliga a proteção (só para uso na rede interna).

Acesso por pessoa (aba Acessos da central): só a matrícula, com perfil adm, motorista ou passageiro.
Só entra quem a central cadastrou (decisão do usuário: sem código/senha).
- adm: tudo (igual ao código da operação) e cadastra os acessos.
- motorista: app do motorista (ver a operação, GPS, botões da corrida, responder a central).
- passageiro: igual ao código de passageiro, mas já identificado pela matrícula.
A matrícula ADM_MATRICULA (padrão 8015) é sempre adm.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path


PERFIS = ['adm', 'motorista', 'passageiro']
PAPEL = {'adm': 'operacao', 'motorista': 'motorista', 'passageiro': 'passageiro'}


def so_digitos(m):
    return re.sub(r'\D', '', str(m or ''))


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
        for k, env in (('codigo_passageiro', 'CODIGO_PASSAGEIRO'), ('codigo_operacao', 'CODIGO_OPERACAO'), ('segredo', 'ACESSO_SEGREDO')):
            if os.environ.get(env):
                self.cfg[k] = os.environ[env]
        for k in ('codigo_passageiro', 'codigo_operacao'):
            if not self.cfg.get(k):
                self.cfg[k] = f'{secrets.randbelow(900000) + 100000}'
                mudou = True
        if not self.cfg.get('segredo'):
            self.cfg['segredo'] = secrets.token_hex(16)
            mudou = True
        self.cfg.setdefault('usuarios', {})
        adm = so_digitos(os.environ.get('ADM_MATRICULA', '8015'))
        if adm:
            u = self.cfg['usuarios'].get(adm)
            if not u or u.get('perfil') != 'adm':
                self.cfg['usuarios'][adm] = self._novo(adm, (u or {}).get('nome') or 'Administrador', 'adm', (u or {}).get('versao', 0) + 1)
                mudou = True
        self.adm = adm
        if mudou:
            self._gravar()

    def _gravar(self):
        tmp = self.arquivo.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=1)
        tmp.replace(self.arquivo)

    def _assinar(self, papel):
        return hmac.new(self.cfg['segredo'].encode(), papel.encode(), hashlib.sha256).hexdigest()[:32]

    # ---------- acesso por pessoa ----------
    @staticmethod
    def _novo(mat, nome, perfil, versao=1):
        return {'matricula': mat, 'nome': nome, 'perfil': perfil, 'versao': versao}

    def _token_usuario(self, u):
        assin = hmac.new(self.cfg['segredo'].encode(), f"u:{u['matricula']}:{u['perfil']}:{u['versao']}".encode(), hashlib.sha256).hexdigest()[:32]
        return f"u.{u['matricula']}.{assin}"

    def usuarios(self):
        return sorted(({k: u[k] for k in ('matricula', 'nome', 'perfil')} | {'principal': u['matricula'] == self.adm}
                       for u in self.cfg['usuarios'].values()), key=lambda u: (PERFIS.index(u['perfil']), u['nome'].lower()))

    def salvar_usuario(self, matricula, nome, perfil):
        mat = so_digitos(matricula)
        nome = (nome or '').strip()
        if len(mat) < 2:
            raise ValueError('Informe a matrícula.')
        if perfil not in PERFIS:
            raise ValueError('Perfil inválido.')
        if mat == self.adm and perfil != 'adm':
            raise ValueError(f'A matrícula {mat} é o administrador principal e continua adm.')
        u = self.cfg['usuarios'].get(mat)
        if not u:
            if not nome:
                raise ValueError('Para cadastrar, informe o nome.')
            u = self._novo(mat, nome, perfil)
        else:
            if perfil != u['perfil']:
                u['versao'] += 1  # quem entrou com o perfil antigo entra de novo
            u['perfil'] = perfil
            u['nome'] = nome or u['nome']
        self.cfg['usuarios'][mat] = u
        self._gravar()
        return {k: u[k] for k in ('matricula', 'nome', 'perfil')}

    def remover_usuario(self, matricula):
        mat = so_digitos(matricula)
        if mat == self.adm:
            raise ValueError('O administrador principal não pode ser removido.')
        if self.cfg['usuarios'].pop(mat, None) is None:
            raise ValueError('Matrícula não cadastrada.')
        self._gravar()

    def quem(self, token):
        """Token de pessoa → {matricula, nome, perfil}; None para os códigos gerais."""
        if not token or not token.startswith('u.'):
            return None
        _, mat, assin = (token.split('.', 2) + ['', ''])[:3]
        u = self.cfg['usuarios'].get(mat)
        if u and hmac.compare_digest(assin, self._token_usuario(u).split('.', 2)[2]):
            return u
        return None

    def entrar(self, codigo, matricula=None):
        """Matrícula cadastrada (ou um dos códigos gerais, para quem já usava) → (papel, token) ou (None, None)."""
        codigo = (codigo or '').strip()
        if so_digitos(matricula):
            u = self.cfg['usuarios'].get(so_digitos(matricula))
            return (PAPEL[u['perfil']], self._token_usuario(u)) if u else (None, None)
        for papel, k in (('operacao', 'codigo_operacao'), ('passageiro', 'codigo_passageiro')):
            if codigo and hmac.compare_digest(codigo, self.cfg[k]):
                return papel, f'{papel}.{self._assinar(papel)}'
        return None, None

    def papel(self, token):
        if not self.ligado:
            return 'operacao'
        if not token or '.' not in token:
            return None
        if token.startswith('u.'):
            u = self.quem(token)
            return PAPEL[u['perfil']] if u else None
        papel, assin = token.split('.', 1)
        if papel in ('operacao', 'passageiro') and hmac.compare_digest(assin, self._assinar(papel)):
            return papel
        return None

    def trocar(self, passageiro=None, operacao=None):
        for k, v in (('codigo_passageiro', passageiro), ('codigo_operacao', operacao)):
            if v is not None:
                v = str(v).strip()
                if len(v) < 4:
                    raise ValueError('O código precisa ter pelo menos 4 caracteres.')
                self.cfg[k] = v
        if self.cfg['codigo_passageiro'] == self.cfg['codigo_operacao']:
            raise ValueError('Os dois códigos precisam ser diferentes.')
        self.cfg['segredo'] = secrets.token_hex(16)  # derruba quem entrou com os códigos antigos
        self._gravar()

    def ver(self):
        return {'ligado': self.ligado, 'passageiro': self.cfg['codigo_passageiro'], 'operacao': self.cfg['codigo_operacao']}
