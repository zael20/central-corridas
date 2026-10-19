"""Roda as frases de teste no interpretador e mostra acertos/erros (equivalente a testar-parser.js).

Uso (a partir de backend/): python3 tests/test_interpretador.py [arquivo.json]
"""
import json
import math
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.interpretador import interpretar_pedido  # noqa: E402
from app.locais import LOCAIS  # noqa: E402


def _js(v):
    # imprime valores como o template string do JS (null, true, false)
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    return str(v)


def main(argv):
    arq = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent / 'frases.json'
    casos = json.loads(arq.read_text(encoding='utf-8'))
    ok = total = 0
    for c in casos:
        r = interpretar_pedido(c['texto'], LOCAIS)
        h = r['horario']
        hor = None
        if h:
            hor = f"{h['hh']}:{h['mm']:02d}" if h['tipo'] == 'agendado' else f"+{h['minutos']}"
        obtido = {'origem': r['origem'], 'destino': r['destino'], 'passageiros': r['passageiros'],
                  'urgente': r['urgente'], 'solicitante': r['solicitante'], 'matricula': r['matricula'],
                  'setor': r['setor'], 'horario': hor}
        erros = [f"{k}: esperado {_js(v)}, veio {_js(obtido.get(k))}"
                 for k, v in c.items() if k != 'texto' and (k not in obtido or type(v) is not type(obtido[k]) or v != obtido[k])]
        total += 1
        if not erros:
            ok += 1
        print(f"{'ERRO' if erros else 'OK  '} [conf {r['confianca']:.2f}] {c['texto']}")
        for e in erros:
            print('       ' + e)
    print(f"\n{ok}/{total} frases corretas ({math.floor(ok / total * 100 + 0.5) if total else 0}%)")
    return 0 if ok == total else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
