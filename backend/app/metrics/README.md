# Bancada de métricas

Mede o Corda Brava de ponta a ponta e o Azure Service Bus isoladamente, e gera relatórios em
Markdown para comparar regiões. Cada região leva cerca de 10 minutos.

| Parte | O que faz | Tempo |
|---|---|---|
| Teste principal (`app`) | Sobe um backend próprio, sem frontend e sem Docker, e simula apresentador e jogadores pela API real, com partidas de 120 s para 10, 50 e 200 jogadores | ~8 min |
| Teste simples (`servicebus`) | Usa só o SDK: RTT, envio, recepção com `complete` e ponta a ponta a 100 msg/s | ~2 min |

A bancada cria as filas `metrics-app` e `metrics-sb` no namespace testado e as apaga ao final.
A fila real (`corda-brava-pulls`) e a stack em Docker não são tocadas, então podem continuar no ar.

## Como rodar

Na pasta `backend/`, com o ambiente virtual ativo:

```bash
pip install -e '.[metrics]'

# Região atual: lê AZURE_SERVICEBUS_CONNECTION_STRING do ambiente ou do .env
python -m app.metrics run --label brazil-south

# Outra região: passe o nome da variável que guarda a connection string do novo recurso
python -m app.metrics run --label internacional --connection-env AZURE_SERVICEBUS_CONNECTION_STRING_INTERNATIONAL

# Comparação (usa a execução mais recente de cada rótulo)
python -m app.metrics compare results/brazil-south results/internacional
```

A variável do novo recurso pode ficar no `.env` da raiz do repositório, com a connection string
do namespace, sem `EntityPath`, e com permissão de gerenciamento (`RootManageSharedAccessKey`),
porque a bancada cria e apaga filas.

| Opção de `run` | Efeito |
|---|---|
| `--profile smoke` | Validação rápida, de cerca de 1 minuto. Não use para comparar regiões |
| `--only app` ou `--only servicebus` | Roda só uma das partes |
| `--port N` | Porta do backend de teste (padrão 18000) |
| `--keep-queues` | Não apaga as filas ao final |
| `--results-dir PASTA` | Grava em outra pasta |

## O que é gerado

Em `results/<rótulo>/<execução>/`:

| Arquivo | Conteúdo |
|---|---|
| `report.md` | Relatório completo: ambiente, procedimento, como cada métrica foi extraída, resultados e limitações. É o arquivo para incorporar ao relatório do GB |
| `*.png` | Gráficos citados no relatório |
| `summary.csv`, `summary.json` | Estatísticas por métrica e por carga, contadores e consistência |
| `samples.csv.gz` | Todas as amostras brutas |
| `errors.jsonl`, `backend_warnings.log` | Erros do teste e avisos do backend |
| `manifest.json` | Máquina, versões, parâmetros e janela UTC |

`compare` grava `report-comparacao.md`, `comparacao.csv` e os gráficos em
`results/comparacoes/<a>_vs_<b>/`. Connection strings, senhas e tokens nunca são gravados.

## Métricas do lado do Azure (opcional)

O Azure Monitor publica as métricas do namespace com alguns minutos de atraso, por isso a coleta
é um comando separado. Requer o Azure CLI (`brew install azure-cli`), `az login` e o resource ID
do namespace (portal > namespace > Propriedades):

```bash
python -m app.metrics collect --run results/brazil-south --resource-id <id>
```

Os dados entram em `azure_monitor.csv`, e o `report.md` é atualizado. Rode `compare` depois disso
para incluí-los na comparação.

## Para os números serem comparáveis

- Rode as duas regiões na mesma máquina e na mesma rede. O relatório do GB prevê a VPS, então o
  ideal é rodar lá.
- Não feche a tampa do notebook durante a execução: ele suspende, a rede cai e a medição se perde.
  O relatório avisa quando isso acontece.
- Rode as duas em sequência. A camada Standard é compartilhada e varia com o horário, e com uma
  passada por região diferenças pequenas podem ser ruído.
- O novo namespace deve ser Standard, como o atual. Apague-o depois, porque a taxa base corre
  enquanto ele existir.
- Não rode outras cargas pesadas na máquina: gerador e backend ficam nela e disputam CPU. Se o
  atraso do laço de eventos do gerador passar de 50 ms, o relatório avisa.
- A ponta a ponta do app é dominada pelo ciclo do consumidor (veja o intervalo entre ticks no
  relatório). A diferença entre regiões aparece melhor em `POST /pulls` e no teste simples.
