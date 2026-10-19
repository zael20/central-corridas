@echo off
REM Servidor da central de corridas (Windows). Na primeira vez cria o ambiente e instala as dependências.
cd /d %~dp0

REM Ambiente criado com o Python "free-threading" (3.13t) não instala as dependências: apaga e recria.
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python -c "import sys,sysconfig; sys.exit(1 if sysconfig.get_config_var('Py_GIL_DISABLED') else 0)" || rmdir /s /q .venv
)

if not exist .venv (
  echo Criando o ambiente Python...
  python -m venv .venv || py -3.13 -m venv .venv || py -3 -m venv .venv
)

REM Instala as dependências se ainda faltar alguma (também conserta uma instalação que falhou antes).
.venv\Scripts\python -c "import fastapi, uvicorn, yaml, httpx" 2>nul || (
  echo Instalando dependencias, aguarde...
  .venv\Scripts\python -m pip install -r requirements.txt || goto erro
)

echo.
echo Abra no navegador: http://localhost:8000   (nos celulares: http://IP-DESTE-PC:8000)
echo Para desligar, feche esta janela.
echo.
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
echo.
echo O servidor parou.
pause
exit /b

:erro
echo.
echo ERRO ao instalar as dependencias. Copie a mensagem acima e envie para quem cuida do sistema.
pause
