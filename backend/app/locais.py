# Pontos de embarque/desembarque — NOMES REAIS tirados da planilha "CONTROLE DO ATENDIMENTO POOL"
# (os ~40 mais usados em 2025/2026, cobrindo ~93% dos registros).
# ATENÇÃO: as posições x/y (km) são FICTÍCIAS. Para ETA real, troque por lat/lon de cada ponto
# (ou pelas cercas já cadastradas na plataforma de rastreamento).
# "apelidos" = como o nome aparece escrito/falado (sem acento, minúsculo). Inclui variações vistas na planilha.
# Copiado de app-corridas/locais.js (x/y inteiros ficam int para o JSON sair igual ao do JS).

LOCAIS = [
{'id': 'gainf', 'nome': 'GAINF', 'x': 1.2, 'y': 1, 'apelidos': ['gainf', 'ga inf', 'gaimf', 'gainfe', 'gaing', 'gaim', 'gain f', 'gaiinf']},
    {'id': 'oficina', 'nome': 'Oficina', 'x': 2, 'y': 1.4, 'apelidos': ['oficina', 'oficina mecanica']},
    {'id': 'usina', 'nome': 'Usina', 'x': 2.6, 'y': 0.6, 'apelidos': ['usina', 'usina 1', 'usina um', 'planta']},
    {'id': 'usina2', 'nome': 'Usina 2', 'x': 3.1, 'y': 0.4, 'apelidos': ['usina 2', 'usina dois']},
    {'id': 'canteiro', 'nome': 'Canteiro Contratadas', 'x': 0.4, 'y': 1.8, 'apelidos': ['canteiro contratadas', 'canteiro das contratadas', 'canteiro', 'contratadas']},
    {'id': 'fiuza', 'nome': 'Canteiro da Fiuza', 'x': 0.2, 'y': 2.3, 'apelidos': ['canteiro da fiuza', 'fiuza']},
    {'id': 'britagem', 'nome': 'Britagem', 'x': 3.4, 'y': 1.2, 'apelidos': ['britagem', 'britador', 'britag', 'arbitragem', 'britania', 'britanha', 'brita']},
    {'id': 'ps1', 'nome': 'PS1', 'x': 1.6, 'y': 0.2, 'apelidos': ['ps1', 'ps 1', 'ps um', 'pe esse um']},
    {'id': 'ps2', 'nome': 'PS2', 'x': 2.1, 'y': 0, 'apelidos': ['ps2', 'ps 2', 'ps dois', 'pe esse dois']},
    {'id': 'ps3', 'nome': 'PS3', 'x': 3, 'y': 1.9, 'apelidos': ['ps3', 'ps 3', 'ps tres', 'pe esse tres']},
    {'id': 'gemin', 'nome': 'GEMIN', 'x': 1.4, 'y': 1.6, 'apelidos': ['gemin', 'gemim', 'ge min']},
    {'id': 'gemmi', 'nome': 'GEMMI', 'x': 1.7, 'y': 1.9, 'apelidos': ['gemmi']},
    {'id': 'geman', 'nome': 'GEMAN', 'x': 2.3, 'y': 1.8, 'apelidos': ['geman']},
    {'id': 'refeitorio', 'nome': 'Refeitório', 'x': 0.9, 'y': 1.3, 'apelidos': ['refeitorio', 'restaurante', 'rancho']},
    {'id': 'portaria', 'nome': 'Portaria', 'x': 0, 'y': 0.6, 'apelidos': ['portaria', 'portaria principal', 'guarita']},
    {'id': 'portaria2', 'nome': 'Portaria Secundária', 'x': 3.6, 'y': 0.2, 'apelidos': ['portaria secundaria', 'portaria segundaria', 'portaria 2', 'portaria dois']},
    {'id': 'calderaria', 'nome': 'Calderaria', 'x': 2.4, 'y': 1.1, 'apelidos': ['calderaria', 'caldeiraria', 'galderaria', 'caldeira']},
    {'id': 'armazem', 'nome': 'Armazém', 'x': 1.9, 'y': 0.7, 'apelidos': ['armazem']},
    {'id': 'automacao', 'nome': 'Automação', 'x': 1, 'y': 0.7, 'apelidos': ['automacao']},
    {'id': 'cmd', 'nome': 'CMD', 'x': 0.6, 'y': 0.9, 'apelidos': ['cmd', 'c m d']},
    {'id': 'cdm', 'nome': 'CDM', 'x': 0.7, 'y': 0.4, 'apelidos': ['cdm', 'c d m']},
    {'id': 'almoxarifado', 'nome': 'Almoxarifado', 'x': 1.5, 'y': 1.2, 'apelidos': ['almoxarifado', 'almox', 'amox']},
    {'id': 'posto', 'nome': 'Posto', 'x': 2.8, 'y': 1.5, 'apelidos': ['posto', 'posto d', 'posto de combustivel', 'abastecimento']},
    {'id': 'galpao', 'nome': 'Galpão de Lona', 'x': 2.7, 'y': 0.9, 'apelidos': ['galpao de lona', 'g lona', 'galpao']},
    {'id': 'p172', 'nome': '172', 'x': 3.9, 'y': 1.6, 'apelidos': ['172', 'bub 172', 'cento e setenta e dois']},
    {'id': 'mineroduto', 'nome': 'Mineroduto', 'x': 3.8, 'y': 0.8, 'apelidos': ['mineroduto', 'minero duto', 'mineiro duto', 'mineiroduto', 'mineiro adulto', 'minerioduto', 'minerio duto']},
    {'id': 'ambulatorio', 'nome': 'Ambulatório', 'x': 1.1, 'y': 1.9, 'apelidos': ['ambulatorio', 'enfermaria']},
    {'id': 'tenda', 'nome': 'Tenda Integridade', 'x': 2.2, 'y': 2.2, 'apelidos': ['tenda integridade', 'tenda']},
    {'id': 'cth', 'nome': 'CTH', 'x': 0.3, 'y': 1.3, 'apelidos': ['cth', 'c t h']},
    {'id': 'mina4', 'nome': 'Mina 04', 'x': 4.4, 'y': 2.6, 'apelidos': ['mina 04', 'mina 4', 'mina quatro']},
    {'id': 'mina5', 'nome': 'Mina 05', 'x': 4.8, 'y': 2.2, 'apelidos': ['mina 05', 'mina 5', 'mina cinco', 'miltonia 5']},
    {'id': 'mina6', 'nome': 'Mina 06', 'x': 5, 'y': 1.6, 'apelidos': ['mina 06', 'mina 6', 'mina seis']},
    {'id': 'laboratorio', 'nome': 'Laboratório', 'x': 2.9, 'y': 0.2, 'apelidos': ['laboratorio', 'laborat0rio', 'lab']},
    {'id': 'expansao', 'nome': 'Expansão', 'x': 4.2, 'y': 0.4, 'apelidos': ['expansao']},
    {'id': 'diretoria', 'nome': 'Diretoria', 'x': 0.8, 'y': 0.1, 'apelidos': ['diretoria']},
    {'id': 'sotreq', 'nome': 'Sotreq', 'x': 2.5, 'y': 2.4, 'apelidos': ['sotreq', 'sotrec']},
    {'id': 'retomadora', 'nome': 'Retomadora', 'x': 3.5, 'y': 2.1, 'apelidos': ['retomadora', 'retoma']},
    {'id': 'litoteca', 'nome': 'Litoteca', 'x': 3.2, 'y': 2.5, 'apelidos': ['litoteca']},
    {'id': 'captacao', 'nome': 'Captação', 'x': 4.6, 'y': 0.9, 'apelidos': ['captacao', 'capitacao']},
    {'id': 'barragem', 'nome': 'Barragem', 'x': 4, 'y': 3, 'apelidos': ['barragem', 'rejeito', 'rejeitoduto']},
]

# Tempo de carro por rota (minutos) — aba TEMPO da planilha. Vale nos dois sentidos quando só um foi medido.
# Rotas fora desta lista usam distância em linha reta x fator de estrada.
ROTAS_TEMPO = [
    ['ps1', 'calderaria', 2], ['ps1', 'usina', 1], ['usina', 'calderaria', 1], ['oficina', 'p172', 2], ['usina', 'galpao', 4],
    ['oficina', 'ps1', 4], ['ps1', 'oficina', 4], ['ps1', 'canteiro', 6], ['oficina', 'ambulatorio', 4], ['gainf', 'ps1', 2],
    ['canteiro', 'refeitorio', 6], ['canteiro', 'usina', 7], ['usina', 'gainf', 2], ['canteiro', 'oficina', 8], ['gainf', 'armazem', 5],
    ['usina', 'britagem', 2], ['ps1', 'ps3', 3], ['canteiro', 'cth', 8], ['oficina', 'gainf', 5], ['usina', 'oficina', 4],
    ['oficina', 'usina', 3], ['canteiro', 'gainf', 7], ['gainf', 'usina', 2], ['gainf', 'oficina', 5], ['gainf', 'canteiro', 7],
]
