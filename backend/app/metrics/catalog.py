"""What every metric means and how it is extracted; the reports print this text as it is."""

from __future__ import annotations

from dataclasses import dataclass

_AGGREGATION = (
    "Por nível de carga: número de amostras, média, desvio-padrão amostral, mínimo, p50, p90, "
    "p95, p99 e máximo. Percentis por interpolação linear sobre as amostras ordenadas."
)
_CLOCK = "`time.perf_counter_ns()` no processo gerador de carga (relógio monotônico)"
_HTTP_START = "Imediatamente antes de o cliente `httpx` iniciar a requisição."
_HTTP_END = "Quando a resposta completa chega ao cliente."
_HTTP_LIMITS = (
    "Gerador e backend estão na mesma máquina, ligados por loopback, então não há latência de "
    "Wi-Fi ou 4G. Só entram requisições que tiveram resposta; timeouts e falhas de rede são "
    "contados à parte."
)
_ONE_PER_MATCH = " Há uma amostra por partida, então o valor é apenas indicativo."
_SERVICE_BUS_LIMITS = (
    "Inclui a rede entre a máquina de teste e o namespace. A camada Standard é compartilhada "
    "entre clientes, então o resultado varia com o horário."
)


@dataclass(frozen=True)
class MetricInfo:
    label: str
    unit: str
    group: str
    definition: str
    start: str
    end: str
    instrument: str
    limitations: str
    aggregation: str = _AGGREGATION


def _http(label: str, definition: str, limitations: str = _HTTP_LIMITS) -> MetricInfo:
    return MetricInfo(
        label=label,
        unit="ms",
        group="HTTP",
        definition=definition,
        start=_HTTP_START,
        end=_HTTP_END,
        instrument=f"{_CLOCK}, em torno da chamada ao cliente HTTP.",
        limitations=limitations,
    )


def _sdk(label: str, definition: str, start: str, end: str, limits: str = "") -> MetricInfo:
    return MetricInfo(
        label=label,
        unit="ms",
        group="Service Bus direto",
        definition=definition,
        start=start,
        end=end,
        instrument=f"{_CLOCK}, em torno da chamada ao SDK `azure-servicebus` assíncrono.",
        limitations=f"{_SERVICE_BUS_LIMITS} {limits}".strip(),
    )


def _rate(label: str, definition: str, formula: str) -> MetricInfo:
    return MetricInfo(
        label=label,
        unit="msg/s",
        group="Service Bus direto",
        definition=definition,
        start="Início da primeira operação da etapa.",
        end="Fim da última operação da etapa.",
        instrument=f"{_CLOCK} e contagem das mensagens confirmadas pelo SDK.",
        limitations=f"{_SERVICE_BUS_LIMITS} É um único valor por execução.",
        aggregation=f"Valor único: {formula}.",
    )


def _queue(label: str, unit: str, definition: str) -> MetricInfo:
    return MetricInfo(
        label=label,
        unit=unit,
        group="Fila",
        definition=definition,
        start="Leitura instantânea, a cada 2 s, do início ao fim da partida.",
        end="Não se aplica: cada amostra é o valor do momento da leitura.",
        instrument=(
            "`get_queue_runtime_properties`, da API de gestão do SDK `azure-servicebus`, "
            "chamada em uma thread separada."
        ),
        limitations=(
            "O broker atualiza esses contadores com algum atraso, então a amostra é uma "
            "aproximação do valor real naquele instante."
        ),
    )


def _process(label: str, unit: str, who: str, what: str, formula: str) -> MetricInfo:
    return MetricInfo(
        label=label,
        unit=unit,
        group="Recursos",
        definition=f"{what} do {who} durante a partida.",
        start="Primeira leitura logo antes de iniciar a partida; depois, a cada 5 s.",
        end="Última leitura ao fim da partida.",
        instrument=f"Comando `ps -o time= -o rss= -p <pid>`. {formula}",
        limitations=(
            "No Linux, o `ps` informa tempo de CPU em segundos inteiros, então cada amostra "
            "isolada é imprecisa, embora a média da partida seja confiável. O valor de CPU é "
            "uma fração de um núcleo: 100% significa um núcleo inteiro."
        ),
    )


_CPU_FORMULA = "CPU = 100 × (tempo de CPU consumido no intervalo ÷ duração do intervalo)."
_RSS_FORMULA = "Memória residente (RSS) lida diretamente, convertida de KiB para MiB."

METRICS: dict[str, MetricInfo] = {
    # HTTP
    "http_pulls_ms": _http(
        "POST /api/matches/{id}/pulls",
        "Envio de um lote de puxadas por um jogador. A rota só responde depois de o Service Bus "
        "aceitar a mensagem, então o tempo inclui a API e o envio ao broker, com o agrupamento "
        "de mensagens (group commit) do adaptador.",
    ),
    "http_pulls_duplicate_ms": _http(
        "POST /pulls (reenvio do mesmo batchId)",
        "Reenvio proposital de um lote já confirmado. A rota reconhece o `batchId` na memória e "
        "responde sem falar com o Service Bus. Serve de linha de base: a diferença entre "
        "`http_pulls_ms` e esta métrica estima o custo do envio ao broker.",
    ),
    "http_me_ms": _http(
        "GET /api/matches/{id}/me",
        "Consulta periódica que cada celular faz para saber em que tela deve estar.",
    ),
    "http_public_ms": _http(
        "GET /api/matches/{id}/public", "Dados públicos da partida, lidos antes de entrar."
    ),
    "http_join_ms": _http(
        "POST /api/matches/{id}/players", "Entrada de um jogador na partida, durante o lobby."
    ),
    "http_login_ms": _http(
        "POST /api/host/login",
        "Login do apresentador.",
        _HTTP_LIMITS + _ONE_PER_MATCH,
    ),
    "http_create_ms": _http(
        "POST /api/host/matches",
        "Criação da partida pelo apresentador.",
        _HTTP_LIMITS + _ONE_PER_MATCH,
    ),
    "http_start_ms": _http(
        "POST /api/host/matches/{id}/start",
        "Início da partida, que dispara o laço consumidor.",
        _HTTP_LIMITS + _ONE_PER_MATCH,
    ),
    "http_match_view_ms": _http(
        "GET /api/host/matches/{id}",
        "Visão completa da partida, lida ao fim para conferir o placar.",
        _HTTP_LIMITS + _ONE_PER_MATCH,
    ),
    "http_analytics_ms": _http(
        "GET /api/host/matches/{id}/analytics",
        "Estatísticas calculadas ao fim da partida.",
        _HTTP_LIMITS + _ONE_PER_MATCH,
    ),
    # End to end
    "e2e_pull_ms": MetricInfo(
        label="Puxada até aparecer no telão",
        unit="ms",
        group="Ponta a ponta",
        definition=(
            "Tempo entre o jogador enviar um lote e o placar transmitido ao telão incluir esse "
            "lote. Percorre API, Service Bus, laço consumidor, SQLite e WebSocket."
        ),
        start=(
            "Primeira tentativa de envio do lote (`POST /pulls`), antes da resposta da API. "
            "Só lotes confirmados entram."
        ),
        end="Chegada, no cliente WebSocket, do primeiro tick cujo placar cobre o lote.",
        instrument=(
            f"{_CLOCK}. O tick não informa o `batchId`, então a correspondência usa o placar "
            "acumulado de cada time: os lotes são ordenados pelo instante de envio, somados, e "
            "um lote conta como aplicado no primeiro tick cujo placar alcança a soma até ele."
        ),
        limitations=(
            "É uma aproximação: supõe ordem de chegada dentro de cada time, o que a fila "
            "garante apenas aproximadamente. O valor é dominado pelo ciclo do consumidor (ver "
            "`tick_interval_ms`) e por isso mascara parte da diferença de rede entre regiões. "
            "Só entram lotes que chegaram ao placar: se a fila acumula, os que ficaram nela não "
            "são contados e o valor fica subestimado."
        ),
    ),
    # Game
    "tick_interval_ms": MetricInfo(
        label="Intervalo entre ticks",
        unit="ms",
        group="Jogo",
        definition=(
            "Tempo entre duas atualizações de estado consecutivas recebidas pelo telão. O valor "
            "configurado é a espera entre ciclos; o intervalo real soma a esse valor o tempo de "
            "receber, processar e confirmar as mensagens."
        ),
        start="Chegada de um tick ao cliente WebSocket.",
        end="Chegada do tick seguinte.",
        instrument=f"{_CLOCK}, na leitura de cada mensagem `tick` do WebSocket.",
        limitations="Medido no cliente, inclui a entrega pelo WebSocket em loopback.",
    ),
    "ws_connect_ms": MetricInfo(
        label="Conexão do WebSocket do telão",
        unit="ms",
        group="Jogo",
        definition="Tempo para abrir o WebSocket do telão.",
        start="Antes de iniciar a conexão.",
        end="Handshake do WebSocket concluído.",
        instrument=f"{_CLOCK}, em torno de `websockets.asyncio.client.connect`.",
        limitations="Há uma amostra por partida, então o valor é apenas indicativo.",
    ),
    "ws_snapshot_ms": MetricInfo(
        label="WebSocket até o primeiro estado",
        unit="ms",
        group="Jogo",
        definition="Tempo até o telão receber o primeiro retrato completo da partida.",
        start="Antes de iniciar a conexão.",
        end="Chegada da mensagem `snapshot`.",
        instrument=f"{_CLOCK}.",
        limitations="Há uma amostra por partida, então o valor é apenas indicativo.",
    ),
    # Queue
    "queue_active_messages": _queue(
        "Mensagens ativas na fila",
        "mensagens",
        "Mensagens aguardando consumo: mede o acúmulo. Crescimento contínuo indica que a "
        "entrada supera a capacidade de consumo.",
    ),
    "queue_dead_letter_messages": _queue(
        "Mensagens na dead-letter queue",
        "mensagens",
        "Mensagens que o broker ou o consumidor desistiram de processar.",
    ),
    "queue_size_bytes": _queue(
        "Tamanho da fila", "bytes", "Espaço ocupado pelas mensagens armazenadas."
    ),
    # Resources
    "backend_cpu_pct": _process("CPU do backend", "%", "backend", "Uso de CPU", _CPU_FORMULA),
    "backend_rss_mb": _process("Memória do backend", "MiB", "backend", "Memória", _RSS_FORMULA),
    "generator_cpu_pct": _process(
        "CPU do gerador", "%", "gerador de carga", "Uso de CPU", _CPU_FORMULA
    ),
    "generator_rss_mb": _process(
        "Memória do gerador", "MiB", "gerador de carga", "Memória", _RSS_FORMULA
    ),
    "generator_loop_lag_ms": MetricInfo(
        label="Atraso do laço de eventos do gerador",
        unit="ms",
        group="Recursos",
        definition=(
            "Quanto o laço de eventos do gerador atrasa para acordar. Valores altos indicam que "
            "o próprio gerador está saturado e pode inflar as latências medidas."
        ),
        start="Pedido de `asyncio.sleep(0.1)`.",
        end="Retomada da tarefa; a amostra é o tempo real menos os 100 ms pedidos.",
        instrument=f"{_CLOCK} com `time.perf_counter()`, a cada 100 ms durante a partida.",
        limitations="Mede apenas o gerador; o backend é um processo separado.",
    ),
    # Direct Service Bus test
    "sb_rtt_ms": _sdk(
        "RTT até o namespace",
        "Tempo de ida e volta na rede, aproximado pelo handshake TCP na porta AMQP 5671.",
        "Antes de abrir o socket para o endereço já resolvido.",
        "Conexão TCP estabelecida.",
        "Mede apenas a rede, sem TLS nem AMQP, e usa `asyncio.open_connection`.",
    ),
    "sb_rtt_jitter_ms": _sdk(
        "Jitter do RTT",
        "Variação do RTT: média das diferenças absolutas entre sondas consecutivas.",
        "Primeira sonda de RTT.",
        "Última sonda de RTT.",
        "É um único valor por execução.",
    ),
    "sb_send_single_ms": _sdk(
        "Envio de mensagem única",
        "Tempo de `send_messages` com uma mensagem.",
        "Antes de `send_messages`.",
        "Confirmação do broker.",
    ),
    "sb_send_single_msg_s": _rate(
        "Vazão com envio único",
        "Mensagens por segundo enviando uma de cada vez, em sequência.",
        "mensagens confirmadas ÷ tempo total dentro de `send_messages`",
    ),
    "sb_send_batch_ms": _sdk(
        "Envio em lote de 100",
        "Tempo de `send_messages` com um lote de 100 mensagens.",
        "Antes de `send_messages`, com o lote já montado.",
        "Confirmação do broker.",
    ),
    "sb_send_batch_msg_s": _rate(
        "Vazão com envio em lote",
        "Mensagens por segundo enviando lotes de 100, em sequência.",
        "mensagens confirmadas ÷ tempo total dentro de `send_messages`",
    ),
    "sb_receive_batch_ms": _sdk(
        "Recepção de até 100 mensagens",
        "Tempo de uma chamada `receive_messages` em modo peek-lock, com fila cheia.",
        "Antes de `receive_messages`.",
        "Retorno com pelo menos uma mensagem.",
        "O SDK pode devolver menos de 100 mensagens por chamada; ver "
        "`sb_receive_batch_messages`.",
    ),
    "sb_receive_batch_messages": MetricInfo(
        label="Mensagens por recepção",
        unit="mensagens",
        group="Service Bus direto",
        definition="Quantidade de mensagens devolvidas em cada chamada de recepção.",
        start="Não se aplica.",
        end="Não se aplica.",
        instrument="Tamanho da lista devolvida por `receive_messages`.",
        limitations="Depende do ritmo em que o broker entrega as mensagens ao cliente.",
    ),
    "sb_complete_msg_ms": _sdk(
        "Complete de uma mensagem",
        "Tempo de `complete_message` para uma mensagem, com até 100 em paralelo.",
        "Antes de `complete_message`, com a vaga do paralelismo já obtida.",
        "Confirmação do broker.",
    ),
    "sb_complete_batch_ms": _sdk(
        "Complete do lote recebido",
        "Tempo para confirmar todas as mensagens de uma recepção, em paralelo.",
        "Fim da recepção.",
        "Última confirmação do lote.",
    ),
    "sb_receive_complete_msg_s": _rate(
        "Vazão de recepção e complete",
        "Mensagens por segundo no ciclo completo de receber e confirmar.",
        "mensagens recebidas ÷ tempo somado dos ciclos",
    ),
    "sb_e2e_ms": _sdk(
        "Ponta a ponta no Service Bus",
        "Tempo entre o instante programado de envio e o recebimento, com produtor e consumidor "
        "rodando ao mesmo tempo, em taxa fixa.",
        "Instante programado de envio do lote; um produtor atrasado aparece como latência.",
        "Retorno de `receive_messages` com a mensagem.",
        "Inclui a granularidade de consulta do SDK e o tempo de confirmar o lote anterior, porque "
        "o consumidor alterna receber e confirmar, como o app. Produtor e consumidor usam o mesmo "
        "relógio, por isso não há erro de sincronização. Só entram mensagens recebidas até o fim "
        "da drenagem.",
    ),
    "sb_e2e_send_ms": _sdk(
        "Envio durante o ponta a ponta",
        "Tempo de `send_messages` para cada lote enviado na etapa ponta a ponta.",
        "Antes de `send_messages`.",
        "Confirmação do broker.",
    ),
}

GROUP_ORDER = ("HTTP", "Ponta a ponta", "Jogo", "Fila", "Recursos", "Service Bus direto")

# Counts and totals that are not sample series.
DERIVED: tuple[tuple[str, str], ...] = (
    (
        "Jogadores que entraram",
        "Jogadores simulados que obtiveram resposta de sucesso em `POST /players`, com até 5 "
        "tentativas cada. Cada tentativa entra nas latências e nos códigos HTTP.",
    ),
    (
        "Jogadores interrompidos",
        "Jogadores que pararam de enviar antes do fim da janela de puxadas, após 401, 403 ou 409 "
        "em `POST /pulls`, como o frontend. Um 409 depois do fim da partida é a resposta correta "
        "e não conta aqui.",
    ),
    (
        "Puxadas geradas",
        "Soma das puxadas simuladas pelos jogadores, contadas no gerador antes do envio.",
    ),
    (
        "Puxadas aceitas",
        "Soma das puxadas dos lotes que receberam resposta de sucesso em `POST /pulls`, com cada "
        "lote contado uma vez, mesmo se reenviado.",
    ),
    (
        "Puxadas sem confirmação",
        "Geradas menos aceitas, rejeitadas e descartadas por jogador interrompido: puxadas que "
        "ainda aguardavam envio ou resposta de sucesso quando a partida acabou.",
    ),
    (
        "Puxadas aplicadas",
        "Placar acumulado dos dois times no último tick do WebSocket. É comparado à visão da "
        "partida (`GET /api/host/matches/{id}`) e ao analytics.",
    ),
    (
        "Puxadas faltantes",
        "Aceitas menos aplicadas, quando positivo. Pode indicar perda, corte pelo limite "
        "anti-cheat quando lotes atrasados do mesmo jogador chegam no mesmo ciclo, ou mensagens "
        "ainda na fila quando a partida terminou.",
    ),
    (
        "Puxadas extras",
        "Aplicadas menos aceitas, quando positivo. Indicaria duplicidade aplicada ao placar.",
    ),
    (
        "Ciclos de envio pulados",
        "Vezes em que o temporizador de 500 ms disparou com o envio anterior ainda em andamento. "
        "O frontend pula o ciclo nesse caso, e as puxadas seguem acumuladas para o próximo.",
    ),
    (
        "Req/s e POST /pulls por segundo",
        "Requisições iniciadas entre o início da partida e o fim das puxadas, divididas pela "
        "duração planejada dessa janela.",
    ),
    (
        "Mensagens consumidas por segundo",
        "Lotes que chegaram ao placar (cada lote é uma mensagem) divididos pela duração da "
        "partida. Abaixo de `POST /pulls` por segundo, indica que o consumidor não acompanha a "
        "entrada e a fila cresce.",
    ),
    (
        "Na fila e na DLQ ao fim",
        "Contadores do broker para a fila de teste, lidos 3 s depois do fim da partida.",
    ),
    (
        "Puxadas aplicadas por segundo",
        "Puxadas aplicadas divididas pela duração planejada da janela de puxadas.",
    ),
    (
        "Códigos HTTP e falhas",
        "Contagem por rota dos códigos recebidos. Falhas sem resposta são separadas em `timeout` "
        "(limite de 30 s) e `network` (erro de transporte).",
    ),
    (
        "Log do backend",
        "Linhas de nível WARNING ou superior emitidas pelo backend durante a partida.",
    ),
    (
        "Não recebidas no ponta a ponta do Service Bus",
        "Mensagens com envio confirmado que não foram recebidas até o fim da drenagem, "
        "identificadas por número de sequência no corpo da mensagem.",
    ),
)

AZURE_MONITOR_NOTE = (
    "Métricas registradas pelo próprio Azure para o namespace, lidas depois do teste pela API do "
    "Azure Monitor (`MetricsQueryClient`). O script lista as definições disponíveis no recurso e "
    "consulta cada uma na janela UTC da execução, com granularidade de 1 minuto, separando por "
    "entidade (fila) quando a métrica permite. Como o Azure agrega por minuto, a janela inclui "
    "os instantes vizinhos ao teste e qualquer outro uso do namespace nesse período."
)
