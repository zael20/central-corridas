FROM python:3.12-slim
# modelo da voz (Kokoro v1.0), baixado das releases do GitHub na hora de montar o Space
RUN mkdir /modelos && python -c "import urllib.request as u; b='https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/'; [u.urlretrieve(b + f, '/modelos/' + f) for f in ('kokoro-v1.0.onnx', 'voices-v1.0.bin')]"
RUN useradd -m -u 1000 user
USER user
WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt
COPY --chown=user app.py .
ENV PATH=/home/user/.local/bin:$PATH
EXPOSE 7860
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
