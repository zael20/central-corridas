"""Servidor só de voz da central (voz Bella), para rodar num Hugging Face Space grátis.

A central no Render chama  GET /falar?texto=...&vel=1.0  com o cabeçalho  x-segredo: <VOZ_SEGREDO>
e recebe o áudio WAV. GET /saude responde sem segredo (serve para acordar o Space).
Bella = Kokoro pf_dora 50% + af_bella 50%, falando português (igual a backend/app/voz.py).
"""
import hmac
import io
import os
import threading
import wave

import numpy as np
from fastapi import FastAPI, Header, HTTPException, Response
from kokoro_onnx import Kokoro

MODELOS = os.environ.get('VOZ_MODELOS', '/modelos')
SEGREDO = os.environ.get('VOZ_SEGREDO', '')
MISTURA = {'pf_dora': 0.5, 'af_bella': 0.5}

modelo = Kokoro(f'{MODELOS}/kokoro-v1.0.onnx', f'{MODELOS}/voices-v1.0.bin')
estilo = sum(modelo.get_voice_style(v) * p for v, p in MISTURA.items())
modelo.create('Central pronta.', voice=estilo, speed=1.0, lang='pt-br')  # aquece
trava = threading.Lock()
app = FastAPI(title='Voz da central')


@app.get('/saude')
def saude():
    return {'ok': True, 'voz': 'Bella', 'protegido': bool(SEGREDO)}


@app.get('/falar')
def falar(texto: str, vel: float = 1.0, x_segredo: str = Header('')):
    if not SEGREDO or not hmac.compare_digest(x_segredo, SEGREDO):
        raise HTTPException(401, 'Segredo inválido (configure VOZ_SEGREDO igual no Space e no Render).')
    texto = ' '.join(texto.split())[:600]
    if not texto:
        raise HTTPException(400, 'Texto vazio.')
    vel = min(1.4, max(0.7, float(vel or 1.0)))
    with trava:
        amostras, taxa = modelo.create(texto, voice=estilo, speed=vel, lang='pt-br')
    pcm = (np.clip(amostras, -1, 1) * 32767).astype('<i2')
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes(pcm.tobytes())
    return Response(buf.getvalue(), media_type='audio/wav')
