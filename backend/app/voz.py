"""Voz da central gerada no servidor (Kokoro, código aberto, roda offline).

Uma voz só, fixa: "Bella" (escolhida pelo usuário em 2026-10-20), mistura da Dora (brasileira) com a Bella do Kokoro,
falando em português. Todos os aparelhos ouvem a mesma voz (navegador, celular, APK), sem escolha na tela.
Modelo em %LOCALAPPDATA%/central-corridas/modelos (ou na variável VOZ_MODELOS):
  kokoro-v1.0.onnx (ou o menor kokoro-v1.0.int8.onnx) e voices-v1.0.bin
  (github.com/thewh1teagle/kokoro-onnx, releases "model-files-v1.0")
Sem o modelo ou sem o pacote kokoro-onnx, /api/voz/info diz "indisponível" e as telas usam a voz do navegador.

Voz em outro servidor (Render grátis não aguenta o modelo): com VOZ_URL (ex.: https://usuario-voz-central.hf.space)
e VOZ_SEGREDO, este servidor não carrega o modelo e busca o áudio lá (pasta voz-servico/ do repositório).
"""
import hashlib
import io
import os
import threading
import time
import wave
from collections import OrderedDict
from pathlib import Path

PASTA = Path(os.environ.get('VOZ_MODELOS') or Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'central-corridas' / 'modelos')
NOME = 'Bella'
MISTURA = {'pf_dora': 0.5, 'af_bella': 0.5}  # pesos das vozes do Kokoro que formam a Bella
REMOTO = (os.environ.get('VOZ_URL') or '').strip().rstrip('/')
SEGREDO = os.environ.get('VOZ_SEGREDO', '')


class Voz:
    def __init__(self):
        self.modelo = None
        self.estilo = None
        self.erro = None
        self.falhou_em = 0.0
        self.carregando = True
        self.lock = threading.Lock()
        self.cache = OrderedDict()  # frases repetidas ("Câmbio.") saem na hora
        threading.Thread(target=self._carregar, daemon=True).start()

    def _carregar(self):
        if REMOTO:
            return self._acordar_remoto()
        try:
            if os.environ.get('VOZ') == '0':
                raise RuntimeError('voz do servidor desligada (VOZ=0): as telas usam a voz do aparelho')
            from kokoro_onnx import Kokoro
            vozes = PASTA / 'voices-v1.0.bin'
            onnx = next((f for f in (PASTA / 'kokoro-v1.0.onnx', PASTA / 'kokoro-v1.0.int8.onnx') if f.exists()), None)
            if not onnx or not vozes.exists():
                raise FileNotFoundError(f'modelo de voz não encontrado em {PASTA}')
            m = Kokoro(str(onnx), str(vozes))
            self.estilo = sum(m.get_voice_style(v) * p for v, p in MISTURA.items())
            m.create('Central pronta.', voice=self.estilo, speed=1.0, lang='pt-br')  # aquece
            self.modelo = m
        except Exception as e:
            self.erro = f'{type(e).__name__}: {e}'
        finally:
            self.carregando = False

    def _acordar_remoto(self):
        # o Space grátis dorme quando fica parado: a primeira chamada acorda (pode levar alguns minutos)
        import httpx
        try:
            httpx.get(f'{REMOTO}/saude', timeout=120).raise_for_status()
        except Exception as e:
            self.erro = f'servidor de voz não respondeu ainda: {type(e).__name__}'
        finally:
            self.carregando = False

    def _gerar_remoto(self, texto, vel):
        # falhou há pouco (Space dormindo ou acordando): responde na hora para a tela usar a voz do aparelho
        if time.time() - self.falhou_em < 60:
            raise RuntimeError(self.erro or 'servidor de voz indisponível')
        import httpx
        try:
            r = httpx.get(f'{REMOTO}/falar', params={'texto': texto, 'vel': f'{vel:.2f}'}, headers={'x-segredo': SEGREDO}, timeout=20)
            if r.status_code != 200:
                raise RuntimeError(f'servidor de voz respondeu {r.status_code}')
        except Exception as e:
            self.erro, self.falhou_em = f'servidor de voz: {e}', time.time()
            if not isinstance(e, RuntimeError):
                threading.Thread(target=self._acordar_remoto, daemon=True).start()
            raise
        self.erro = None
        return r.content

    def info(self):
        if REMOTO:  # as telas tentam a voz da central e, se falhar, usam a do aparelho
            return {'disponivel': True, 'carregando': False, 'erro': self.erro, 'voz': NOME, 'remoto': True}
        return {'disponivel': self.modelo is not None, 'carregando': self.carregando, 'erro': self.erro,
                'voz': NOME}

    def gerar(self, texto, vel=1.0):
        """Texto → WAV (bytes), sempre na voz da central."""
        if not self.modelo and not REMOTO:
            raise RuntimeError(self.erro or 'voz ainda carregando')
        vel = min(1.4, max(0.7, float(vel or 1.0)))
        texto = ' '.join(str(texto).split())[:600]
        chave = hashlib.sha1(f'{NOME}|{vel:.2f}|{texto}'.encode()).hexdigest()
        if chave in self.cache:
            self.cache.move_to_end(chave)
            return self.cache[chave]
        if REMOTO:
            dados = self._gerar_remoto(texto, vel)
            self._guardar(chave, dados)
            return dados
        import numpy as np
        with self.lock:
            amostras, taxa = self.modelo.create(texto, voice=self.estilo, speed=vel, lang='pt-br')
        pcm = (np.clip(amostras, -1, 1) * 32767).astype('<i2')
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(taxa)
            w.writeframes(pcm.tobytes())
        dados = buf.getvalue()
        self._guardar(chave, dados)
        return dados

    def _guardar(self, chave, dados):
        self.cache[chave] = dados
        while len(self.cache) > 300:
            self.cache.popitem(last=False)
