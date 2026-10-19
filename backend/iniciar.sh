#!/bin/sh
# Servidor da central de corridas (Linux / Raspberry Pi).
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
echo "Abra: http://localhost:8000  (celulares: http://IP-DESTE-PC:8000)"
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
