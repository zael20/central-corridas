# Publica a central no Azure App Service (Linux, Python).
# Uso:  .\deploy\publicar-azure.ps1 [-Nome central-corridas-xyz] [-Plano B1] [-Regiao brazilsouth]
# Antes: Azure CLI instalado e "az login" feito com a conta onde o site vai ficar.
# Chave do mapa e códigos de acesso vêm de backend\dados (mapa.json, acesso.json): não ficam no Git.
param(
  [string]$Nome = '',
  [string]$Grupo = 'central-corridas-rg',
  [string]$Plano = 'B1',
  [string]$Regiao = 'brazilsouth',
  [string]$Az = 'az'
)
$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
$dados = Join-Path $raiz 'backend\dados'
if (-not $Nome) { $Nome = 'central-corridas-' + -join ((48..57) + (97..122) | Get-Random -Count 5 | ForEach-Object { [char]$_ }) }

$mapa = Get-Content (Join-Path $dados 'mapa.json') -Raw | ConvertFrom-Json
$acesso = Get-Content (Join-Path $dados 'acesso.json') -Raw | ConvertFrom-Json

Write-Host "== grupo e plano ($Plano, $Regiao)"
& $Az group create -n $Grupo -l $Regiao -o none
& $Az appservice plan create -g $Grupo -n "$Grupo-plano" --is-linux --sku $Plano -l $Regiao -o none

Write-Host "== site $Nome"
& $Az webapp create -g $Grupo -p "$Grupo-plano" -n $Nome --runtime 'PYTHON:3.12' -o none
& $Az webapp config set -g $Grupo -n $Nome --web-sockets-enabled true --always-on ($Plano -ne 'F1') `
  --startup-file 'cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000' -o none
# /home é o disco que não apaga ao reiniciar: o banco, os pontos e o aprendizado ficam lá
& $Az webapp config appsettings set -g $Grupo -n $Nome -o none --settings `
  SCM_DO_BUILD_DURING_DEPLOYMENT=true WEBSITES_PORT=8000 VOZ=0 BANCO=/home/dados/central.db `
  "MAPTILER_KEY=$($mapa.maptiler_key)" "CODIGO_PASSAGEIRO=$($acesso.codigo_passageiro)" `
  "CODIGO_OPERACAO=$($acesso.codigo_operacao)" "ACESSO_SEGREDO=$($acesso.segredo)"
& $Az webapp update -g $Grupo -n $Nome --https-only true -o none

Write-Host "== enviando o código (versão do Git)"
$zip = Join-Path $env:TEMP "central-corridas-$Nome.zip"
git -C $raiz archive --format=zip -o $zip HEAD
& $Az webapp deploy -g $Grupo -n $Nome --src-path $zip --type zip -o none
Write-Host "PRONTO: https://$Nome.azurewebsites.net"
