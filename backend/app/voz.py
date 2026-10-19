"""Voz da central gerada no servidor (Kokoro, código aberto, roda offline).

Todos os aparelhos ouvem a mesma voz (navegador, celular, APK), sem depender das vozes de cada um.
Modelo em %LOCALAPPDATA%/central-corridas/modelos (ou na variável VOZ_MODELOS):
  kokoro-v1.0.onnx e voices-v1.0.bin  (github.com/thewh1teagle/kokoro-onnx, releases "model-files-v1.0")
Sem o modelo ou sem o pacote kokoro-onnx, /api/voz/info diz "indisponível" e as telas usam a voz do navegador.
"""
import hashlib
import io
import os
import threading
import wave
from collections import OrderedDict
from pathlib import Path

PASTA = Path(os.environ.get('VOZ_MODELOS') or Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'central-corridas' / 'modelos')
VOZES = {'pf_dora': 'Dora (feminina)', 'pm_alex': 'Alex (masculina)', 'pm_santa': 'Santa (masculina)'}
PADRAO = 'pf_dora'


class Voz:
    def __init__(self):
        self.modelo = None
        self.erro = None
        self.carregando = True
        self.lock = threading.Lock()
        self.cache = OrderedDict()  # frases repetidas ("Câmbio.") saem na hora
        threading.Thread(target=self._carregar, daemon=True).start()

    def _carregar(self):
        try:
            if os.environ.get('VOZ') == '0':
                raise RuntimeError('voz do servidor desligada (VOZ=0): as telas usam a voz do aparelho')
            from kokoro_onnx import Kokoro
            onnx, vozes = PASTA / 'kokoro-v1.0.onnx', PASTA / 'voices-v1.0.bin'
            if not onnx.exists() or not vozes.exists():
                raise FileNotFoundError(f'modelo de voz não encontrado em {PASTA}')
            m = Kokoro(str(onnx), str(vozes))
            m.create('Central pronta.', voice=PADRAO, speed=1.0, lang='pt-br')  # aquece
            self.modelo = m
        except Exception as e:
            self.erro = f'{type(e).__name__}: {e}'
        finally:
            self.carregando = False

    def info(self):
        return {'disponivel': self.modelo is not None, 'carregando': self.carregando, 'erro': self.erro,
                'vozes': [{'id': k, 'nome': v} for k, v in VOZES.items()], 'padrao': PADRAO}

    def gerar(self, texto, voz=PADRAO, vel=1.0):
        """Texto → WAV (bytes)."""
        if not self.modelo:
            raise RuntimeError(self.erro or 'voz ainda carregando')
        voz = voz if voz in VOZES else PADRAO
        vel = min(1.4, max(0.7, float(vel or 1.0)))
        texto = ' '.join(str(texto).split())[:600]
        chave = hashlib.sha1(f'{voz}|{vel:.2f}|{texto}'.encode()).hexdigest()
        if chave in self.cache:
            self.cache.move_to_end(chave)
            return self.cache[chave]
        import numpy as np
        with self.lock:
            amostras, taxa = self.modelo.create(texto, voice=voz, speed=vel, lang='pt-br')
        pcm = (np.clip(amostras, -1, 1) * 32767).astype('<i2')
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(taxa)
            w.writeframes(pcm.tobytes())
        dados = buf.getvalue()
        self.cache[chave] = dados
        while len(self.cache) > 300:
            self.cache.popitem(last=False)
        return dados
