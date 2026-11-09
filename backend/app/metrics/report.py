"""Markdown reports and PNG charts built from finished runs (no network access)."""

from __future__ import annotations

import csv
import gzip
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from app.metrics.catalog import AZURE_MONITOR_NOTE, DERIVED, GROUP_ORDER, METRICS
from app.metrics.stats import NO_LEVEL, level_sort_key, percentile

REPORT_NAME = "report.md"
COMPARISON_NAME = "report-comparacao.md"
AZURE_MONITOR_CSV = "azure_monitor.csv"
AZURE_MONITOR_STATUS = "azure_monitor_status.json"
LOOP_LAG_WARN_MS = 50.0
CPU_WARN_PCT = 85.0
SKIPPED_FLUSH_WARN = 0.05
SUSPENSION_WARN_S = 30
SATURATION_MIN_MESSAGES = 100
SATURATION_MIN_SHARE = 0.05  # of the level's accepted batches left in the queue
CDF_POINTS = 400
CDF_AXIS_PERCENTILE = 99.5

SURFACE, INK, INK_SECONDARY, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a")  # validated all-pairs for colour-vision deficiency

STATUS_TEXT = {
    "complete": "completa",
    "partial": "parcial (ver limitações)",
    "interrupted": "interrompida pelo operador",
}
CHARTED_ROUTES = (
    ("http_pulls_ms", "POST /pulls"),
    ("http_pulls_duplicate_ms", "POST /pulls (reenvio)"),
    ("http_me_ms", "GET /me"),
    ("http_public_ms", "GET /public"),
    ("http_join_ms", "POST /players"),
)
CHARTED_SERVICE_BUS = (
    ("sb_rtt_ms", "RTT"),
    ("sb_send_single_ms", "Envio único"),
    ("sb_send_batch_ms", "Envio em lote"),
    ("sb_receive_batch_ms", "Recepção"),
    ("sb_complete_batch_ms", "Complete do lote"),
    ("sb_e2e_ms", "Ponta a ponta"),
)

Chart = tuple[str, str, str]  # file name, title, what it shows


@dataclass
class Run:
    """One finished run, as read back from its directory."""

    path: Path
    manifest: dict[str, Any]
    metrics: dict[tuple[str, str, str], dict[str, Any]]
    counters: dict[tuple[str, str, str], int]
    results: dict[str, Any]

    @property
    def label(self) -> str:
        return self.manifest["label"]

    @property
    def tests(self) -> list[str]:
        return self.manifest["tests"]

    @property
    def app_levels(self) -> dict[str, dict[str, Any]]:
        return self.results.get("app", {}).get("levels", {})

    def levels(self, test: str = "app") -> list[str]:
        return sorted({lv for t, lv, _ in self.metrics if t == test}, key=level_sort_key)

    def stat(self, test: str, level: str, metric: str, field: str) -> float | None:
        entry = self.metrics.get((test, level, metric))
        return None if entry is None else entry[field]

    def samples(self, wanted: set[str]) -> dict[tuple[str, str], list[tuple[float, float]]]:
        """Raw ``(t_s, value)`` pairs of the wanted metrics, by ``(level, metric)``."""
        found: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
        with gzip.open(self.path / "samples.csv.gz", "rt", encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                if row["metric"] in wanted:
                    found[(row["level"], row["metric"])].append(
                        (float(row["t_s"]), float(row["value"]))
                    )
        return found


def load_run(path: Path) -> Run:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    return Run(
        path=path,
        manifest=manifest,
        metrics={(m["test"], m["level"], m["metric"]): m for m in summary["metrics"]},
        counters={(c["test"], c["level"], c["name"]): c["value"] for c in summary["counters"]},
        results=summary["results"],
    )


# ── Formatting ────────────────────────────────────────────────────────────────


def _number(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "–"
    return f"{value:.0f}" if abs(value) >= 1000 else f"{value:.{digits}f}"


def _plain(value: Any) -> str:  # noqa: ANN401 - any scalar from the JSON documents
    return "–" if value is None else str(value)


def _short(value: float) -> str:
    """A parameter such as 0.5, in Brazilian notation."""
    return f"{value:g}".replace(".", ",")


def _level_name(level: str) -> str:
    return "execução única" if level == NO_LEVEL else f"{level} jogadores"


def _table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return [*lines, ""]


def _metric_names(present: Iterable[str]) -> list[str]:
    """Metrics in catalog order; anything uncatalogued goes last and is flagged by the report."""
    available = set(present)
    return [m for m in METRICS if m in available] + sorted(available - set(METRICS))


def _names_in_group(run: Run, group: str) -> list[str]:
    present = {metric for _, _, metric in run.metrics}
    return [m for m in _metric_names(present) if m in METRICS and METRICS[m].group == group]


# ── Sections shared by both reports ───────────────────────────────────────────


def _system_context() -> list[str]:
    return [
        "O Corda Brava é um jogo de cabo de guerra em tempo real. Os celulares enviam lotes de "
        "puxadas por HTTP. O backend, em FastAPI, publica cada lote em uma fila do Azure Service "
        "Bus e só responde ao celular depois de o broker aceitar a mensagem. Por partida, um "
        "laço consumidor lê a fila em ciclos (ticks), aplica as puxadas, grava em SQLite e "
        "transmite o estado ao telão por WebSocket.",
        "",
    ]


def _app_procedure(parameters: dict[str, Any]) -> list[str]:
    app, game = parameters["app"], parameters["game"]
    levels = ", ".join(str(n) for n in app["player_levels"])
    return [
        "### Teste principal: uso simulado pela API do app",
        "",
        "O script executa estes passos, sem navegador, sem frontend e sem Docker:",
        "",
        "1. Cria a fila dedicada `metrics-app`, vazia e com as mesmas propriedades da fila real, "
        "e sobe um processo próprio do backend real (`uvicorn`, um worker, em `127.0.0.1`), "
        "com banco SQLite temporário.",
        f"2. Joga uma partida por nível de carga ({levels} jogadores), em ordem crescente, cada "
        f"uma com {app['match_duration_s']} s. Para cada partida, o apresentador simulado faz "
        "login, cria a partida e abre o WebSocket do telão.",
        f"3. Os jogadores simulados, divididos igualmente entre os dois times, consultam "
        f"`/public` e entram, no máximo {app['join_concurrency']} ao mesmo tempo. A partir daí, "
        f"cada um consulta `/me` a cada {_short(app['me_poll_interval_s'])} s, como o frontend.",
        "4. O apresentador inicia a partida. Cada jogador começa a puxar quando `/me` informa "
        "que a partida está em andamento.",
        f"5. A cada {_short(app['flush_interval_s'])} s, com fase aleatória por jogador, soma "
        f"{app['taps_per_flush']} puxadas ao acumulado e envia o lote em `POST /pulls`. Como no "
        "frontend, dois envios do mesmo jogador nunca se sobrepõem. Um lote que falha por rede, "
        "5xx, 408 ou 429 é reenviado com o mesmo `batchId`, e um 401, 403 ou 409 interrompe "
        "o jogador.",
        f"6. A cada {_short(app['duplicate_every_s'])} s, cada jogador reenvia de propósito um "
        "lote já confirmado, para verificar a idempotência e medir a API sem o broker.",
        f"7. As puxadas param {_short(app['stop_tapping_before_end_s'])} s antes do fim, para "
        "que os lotes em trânsito cheguem ao placar e a contagem final seja exata.",
        "8. Ao fim, o script espera a mensagem `ended` no WebSocket, lê a visão da partida e o "
        "analytics, confere a fila e compara todos os totais.",
        "",
        "Regras do jogo usadas (valores padrão do app):",
        "",
        *_table(["Parâmetro", "Valor"], sorted(game.items())),
        "A carga é igual nos dois times, para a corda não atingir o alvo e encerrar a partida "
        "antes da hora. Cada jogador envia bem menos que o limite anti-cheat, para o descarte "
        "por esse limite não parecer perda.",
        "",
        "Diferenças em relação ao uso real: todos os jogadores rodam em um único processo, na "
        "mesma máquina do backend e por loopback, sem Wi-Fi ou 4G. O nginx não participa. O "
        "ritmo de toques é fixo, e não o de pessoas reais. Cada lote tem no máximo "
        f"{app['max_pulls_per_batch']} puxadas (o frontend permite 200): sob atraso, as puxadas "
        "excedentes esperam no gerador em vez de serem cortadas pelo limite anti-cheat.",
        "",
    ]


def _service_bus_procedure(parameters: dict[str, Any]) -> list[str]:
    sb = parameters["servicebus"]
    return [
        "### Teste simples: só o Service Bus",
        "",
        "Usa o SDK `azure-servicebus` diretamente, sem o app, em uma fila dedicada "
        "(`metrics-sb`) com as propriedades da fila real:",
        "",
        f"1. **Rede:** {sb['rtt_probes']} handshakes TCP na porta AMQP 5671.",
        f"2. **Envio único:** até {sb['single_sends']} mensagens de cerca de "
        f"{sb['payload_bytes']} bytes, uma de cada vez, por no máximo "
        f"{_short(sb['single_sends_max_s'])} s.",
        f"3. **Envio em lote:** {sb['batch_sends']} lotes de {sb['batch_size']} mensagens, em "
        "sequência.",
        f"4. **Recepção e complete:** esvazia a fila acumulada, recebendo até "
        f"{sb['batch_size']} mensagens por vez em modo peek-lock, com `prefetch_count=0`, e "
        "confirmando-as em paralelo, como o adaptador do app.",
        f"5. **Ponta a ponta:** em outra fila (`metrics-sb-e2e`), produtor e consumidor ao mesmo "
        f"tempo, a "
        f"{sb['e2e_rate_msg_s']} msg/s por {_short(sb['e2e_duration_s'])} s, em lotes a cada "
        f"{_short(sb['e2e_tick_s'])} s. O envio é feito em taxa fixa: um produtor atrasado "
        "aparece como latência.",
        "",
    ]


def _method_section(run: Run) -> list[str]:
    lines = ["## 2. Como o teste foi feito", "", *_system_context()]
    if "app" in run.tests:
        lines += _app_procedure(run.manifest["parameters"])
    if "servicebus" in run.tests:
        lines += _service_bus_procedure(run.manifest["parameters"])
    return lines


def _metric_entry(name: str, sample_notes: Iterable[str]) -> list[str]:
    info = METRICS[name]
    return [
        f"#### `{name}`: {info.label}",
        "",
        f"- **O que é:** {info.definition}",
        f"- **Unidade:** {info.unit}",
        f"- **Início da medição:** {info.start}",
        f"- **Fim da medição:** {info.end}",
        f"- **Instrumento:** {info.instrument}",
        f"- **Agregação:** {info.aggregation}",
        f"- **Amostras:** {'; '.join(sample_notes)}",
        f"- **Limitações:** {info.limitations}",
        "",
    ]


def _sample_counts(run: Run, name: str, prefix: str = "") -> list[str]:
    notes = []
    for test, level, metric in sorted(run.metrics, key=lambda k: level_sort_key(k[1])):
        if metric == name:
            notes.append(
                f"{prefix}{_level_name(level)}: n={run.metrics[(test, level, metric)]['n']}"
            )
    return notes


def _extraction_section(runs: Sequence[Run]) -> list[str]:
    lines = [
        "## 3. Como cada métrica foi extraída",
        "",
        "Todas as latências foram medidas no processo que gera a carga, em um único relógio "
        "monotônico. Por isso não dependem de sincronização entre máquinas, e o horário "
        "registrado pelo broker não é usado para calcular latência.",
        "",
    ]
    present = {metric for run in runs for _, _, metric in run.metrics}
    for group in GROUP_ORDER:
        names = [m for m in _metric_names(present) if m in METRICS and METRICS[m].group == group]
        if not names:
            continue
        lines += [f"### {group}", ""]
        for name in names:
            notes = [
                note
                for run in runs
                for note in _sample_counts(run, name, f"{run.label}, " if len(runs) > 1 else "")
            ]
            lines += _metric_entry(name, notes)
    unknown = sorted(present - set(METRICS))
    if unknown:
        lines += [
            "### Sem descrição",
            "",
            "Estas métricas apareceram nos resultados sem entrada no catálogo: "
            + ", ".join(f"`{name}`" for name in unknown),
            "",
        ]
    lines += ["### Contagens e totais", ""]
    lines += [f"- **{name}:** {text}" for name, text in DERIVED]
    lines += [""]
    if any(_monitor_rollup(run) for run in runs):
        lines += ["### Azure Monitor", "", AZURE_MONITOR_NOTE, ""]
    return lines


def _observed_limitations(run: Run) -> list[str]:
    """Problems this specific execution actually showed, derived from its results."""
    notes = []
    if run.manifest["status"] != "complete":
        notes.append(f"A execução ficou com status: {STATUS_TEXT[run.manifest['status']]}.")
    suspended = run.manifest.get("suspended_s", 0)
    if suspended > SUSPENSION_WARN_S:
        notes.append(
            f"A máquina de teste ficou suspensa por cerca de {suspended / 60:.0f} min durante a "
            "execução. Os números desta execução não representam o sistema, e ela deve ser "
            "repetida."
        )
    incomplete = run.results.get("app", {}).get("incomplete", [])
    if incomplete:
        notes.append(f"Níveis incompletos do teste principal: {', '.join(incomplete)} jogadores.")
    for level, outcome in sorted(run.app_levels.items(), key=lambda i: level_sort_key(i[0])):
        notes += _level_limitations(run, level, outcome)
    e2e = run.results.get("servicebus", {}).get("e2e")
    service_bus = run.results.get("servicebus", {})
    if service_bus.get("received", 0) < service_bus.get("sent", 0):
        limit = _short(run.manifest["parameters"]["servicebus"]["drain_max_s"])
        notes.append(
            f"Service Bus direto: a etapa de recepção parou no limite de {limit} s, com "
            f"{service_bus['received']} de {service_bus['sent']} mensagens recebidas."
        )
    if e2e and e2e["lost"]:
        left = e2e.get("left_in_queue")
        where = "" if left is None else f" Ao fim, {left} mensagens continuavam na fila."
        notes.append(
            f"Service Bus direto: no ponta a ponta, {e2e['lost']} das {e2e['sent']} mensagens "
            "enviadas não foram recebidas até o fim da drenagem: o consumidor não acompanhou a "
            f"taxa fixa, e a latência medida só inclui as recebidas.{where}"
        )
    if e2e and e2e["duplicates"]:
        notes.append(f"Service Bus direto: {e2e['duplicates']} mensagens chegaram duplicadas.")
    return notes


def _messages_consumed_per_s(outcome: dict[str, Any]) -> float:
    """Each batch is one message; a batch is consumed once the score covers it."""
    return outcome["e2e"]["resolved"] / outcome["match_duration_s"]


def _saturation_note(run: Run, level: str, outcome: dict[str, Any]) -> str | None:
    """Say once that the consumer fell behind, instead of listing three disconnected symptoms."""
    queue_end, consistency = outcome.get("queue_end"), outcome["consistency"]
    left = queue_end["active"] if queue_end else 0
    threshold = max(SATURATION_MIN_MESSAGES, SATURATION_MIN_SHARE * consistency["batches_accepted"])
    if left < threshold or not consistency["pulls_missing"]:
        return None
    arrived, consumed = outcome["load"]["pull_posts_per_s"], _messages_consumed_per_s(outcome)
    peak = run.stat("app", level, "queue_active_messages", "max") or left
    unresolved = outcome["e2e"]["unresolved"]
    explained = unresolved <= left + max(5, left // 20)
    origin = (
        "estavam nessas mensagens: não foram perdidas pelo broker, a partida acabou antes de "
        "elas serem consumidas"
        if explained
        else "podem estar, em parte, nessas mensagens"
    )
    return (
        f"Com {_level_name(level)}, o consumidor não acompanhou a entrada: chegaram "
        f"{arrived:.0f} mensagens por segundo e foram consumidas {consumed:.0f}. A fila chegou a "
        f"{peak:.0f} mensagens e terminou com {left}. As {consistency['pulls_missing']} puxadas "
        f"aceitas fora do placar {origin}. Neste nível, a latência ponta a ponta só inclui os "
        "lotes aplicados e, por isso, está subestimada."
    )


def _level_limitations(run: Run, level: str, outcome: dict[str, Any]) -> list[str]:
    who = _level_name(level)
    notes = []
    lag = run.stat("app", level, "generator_loop_lag_ms", "p95")
    if lag is not None and lag > LOOP_LAG_WARN_MS:
        notes.append(
            f"Com {who}, o p95 do atraso do laço de eventos do gerador foi {lag:.0f} ms. Acima "
            f"de {LOOP_LAG_WARN_MS:.0f} ms, parte da latência medida pode vir do próprio gerador."
        )
    for metric, name in (("generator_cpu_pct", "gerador"), ("backend_cpu_pct", "backend")):
        cpu = run.stat("app", level, metric, "mean")
        if cpu is not None and cpu > CPU_WARN_PCT:
            notes.append(f"Com {who}, o {name} usou em média {cpu:.0f}% de um núcleo de CPU.")
    if outcome["players_joined"] != outcome["players"]:
        notes.append(f"Com {who}, só {outcome['players_joined']} conseguiram entrar na partida.")
    if outcome["players_stopped"]:
        notes.append(
            f"Com {who}, {outcome['players_stopped']} jogadores pararam de puxar antes do fim, "
            "depois de receber 401, 403 ou 409, como o frontend faz."
        )
    unexpected = _unexpected_statuses(run, level)
    if unexpected:
        details = ", ".join(f"{count} × {code}" for code, count in sorted(unexpected.items()))
        notes.append(
            f"Com {who}, o backend respondeu com erro a requisições válidas ({details}). Não "
            "vieram do Service Bus quando são 403, 404 ou 500 em jogadores e partidas "
            "existentes: indicam falha do próprio backend sob concorrência."
        )
    send_failures = run.counters.get(("app", level, "http_status:pulls:503"), 0)
    if send_failures:
        notes.append(
            f"Com {who}, {send_failures} envios em `POST /pulls` receberam 503: o backend não "
            "conseguiu publicar no Service Bus dentro do limite de 10 s. Os lotes foram "
            "reenviados com o mesmo `batchId`."
        )
    consistency = outcome["consistency"]
    saturation = _saturation_note(run, level, outcome)
    if saturation:
        notes.append(saturation)
    elif consistency["pulls_missing"]:
        cause = ""
        if outcome.get("queue_end") and not outcome["queue_end"]["active"]:
            cause = (
                " A fila terminou vazia; uma causa possível é o limite anti-cheat, quando lotes "
                "atrasados do mesmo jogador chegam no mesmo ciclo."
            )
        notes.append(
            f"Com {who}, {consistency['pulls_missing']} puxadas aceitas não chegaram ao placar."
            + cause
        )
    if consistency["pulls_extra"]:
        notes.append(
            f"Com {who}, o placar teve {consistency['pulls_extra']} puxadas a mais que as aceitas."
        )
    if consistency["pulls_not_confirmed"]:
        notes.append(
            f"Com {who}, {consistency['pulls_not_confirmed']} puxadas geradas não tiveram "
            "confirmação da API ao fim da partida."
        )
    if consistency["duplicates_sent"] != consistency["duplicates_accepted"]:
        notes.append(
            f"Com {who}, nem todo reenvio do mesmo `batchId` foi aceito: "
            f"{consistency['duplicates_accepted']} de {consistency['duplicates_sent']}."
        )
    cycles = consistency["flush_attempts"] + consistency["flushes_skipped"]
    if cycles and consistency["flushes_skipped"] / cycles > SKIPPED_FLUSH_WARN:
        notes.append(
            f"Com {who}, {consistency['flushes_skipped']} de {cycles} ciclos de envio foram "
            "pulados porque o envio anterior ainda não tinha terminado."
        )
    if outcome["e2e"]["unresolved"] and not saturation:
        notes.append(
            f"Com {who}, {outcome['e2e']['unresolved']} lotes não tiveram latência ponta a "
            "ponta calculada, porque o placar não os cobriu."
        )
    queue_end = outcome.get("queue_end")
    if queue_end and queue_end["active"] and not saturation:
        notes.append(f"Com {who}, {queue_end['active']} mensagens ficaram na fila ao fim.")
    if queue_end and queue_end["dead_letter"]:
        notes.append(f"Com {who}, {queue_end['dead_letter']} mensagens foram para a dead-letter.")
    if outcome["end_reason"] not in (None, "timeout"):
        notes.append(f"Com {who}, a partida terminou antes do tempo ({outcome['end_reason']}).")
    failures = sum(
        value
        for (test, lv, name), value in run.counters.items()
        if test == "app" and lv == level and name.startswith("http_failure:")
    )
    if failures:
        notes.append(f"Com {who}, {failures} requisições ficaram sem resposta.")
    logged = outcome["backend_log"]
    if logged:
        details = ", ".join(f"{count} {name}" for name, count in sorted(logged.items()))
        notes.append(f"Com {who}, o backend registrou no log: {details}.")
    return notes


def _unexpected_statuses(run: Run, level: str) -> dict[str, int]:
    """Error responses by status code; a correct run produces none of them."""
    found: dict[str, int] = defaultdict(int)
    for (test, lv, name), value in run.counters.items():
        parts = name.split(":")
        if test != "app" or lv != level or parts[0] != "http_status":
            continue
        # 409 is the right answer once a match ends; 503 means the broker send failed
        if int(parts[2]) >= 400 and parts[2] not in ("409", "503"):
            found[parts[2]] += value
    return dict(found)


def _general_limitations() -> list[str]:
    return [
        "Gerador de carga e backend ficam na mesma máquina e disputam CPU.",
        "Os jogadores simulados não têm a variabilidade de redes Wi-Fi e 4G.",
        "A ponta a ponta do app é dominada pelo ciclo do consumidor (ver `tick_interval_ms`). A "
        "diferença entre regiões aparece melhor em `POST /pulls` e no teste do Service Bus.",
        "A camada Standard é compartilhada e varia com o horário. Com uma passada por região, "
        "diferenças pequenas podem ser ruído.",
        "Janelas curtas produzem p99 pouco estável, principalmente nas cargas menores.",
        "Não houve teste sustentado de longa duração: deriva de latência e reconexões em "
        "execuções longas não foram medidas.",
    ]


def _monitor_rollup(run: Run) -> dict[str, dict[str, Any]]:
    """Azure Monitor series reduced to one line per metric (empty if ``collect`` never ran)."""
    path = run.path / AZURE_MONITOR_CSV
    if not path.exists():
        return {}
    points: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            entry = points.setdefault(
                row["metric"],
                {"unit": row["unit"], "points": 0, "total": 0.0, "averages": [], "maximum": None},
            )
            entry["points"] += 1
            if row["total"]:
                entry["total"] += float(row["total"])
            if row["average"]:
                entry["averages"].append(float(row["average"]))
            if row["maximum"]:
                value = float(row["maximum"])
                entry["maximum"] = (
                    value if entry["maximum"] is None else max(entry["maximum"], value)
                )
    return points


def _monitor_average(entry: dict[str, Any] | None) -> float | None:
    if not entry or not entry["averages"]:
        return None
    return sum(entry["averages"]) / len(entry["averages"])


def _monitor_section(run: Run) -> list[str]:
    rollup = _monitor_rollup(run)
    if not rollup:
        return []
    lines = [
        "### Azure Monitor (lado do servidor)",
        "",
        *_table(
            ["Métrica", "Unidade", "Pontos", "Soma", "Média", "Máximo"],
            (
                [
                    f"`{name}`",
                    entry["unit"],
                    entry["points"],
                    _number(entry["total"]),
                    _number(_monitor_average(entry)),
                    _number(entry["maximum"]),
                ]
                for name, entry in sorted(rollup.items())
            ),
        ),
    ]
    status_path = run.path / AZURE_MONITOR_STATUS
    if status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("empty"):
            empty = ", ".join(f"`{name}`" for name in status["empty"])
            lines += [f"Métricas sem dados na janela: {empty}.", ""]
    return lines


# ── Charts ────────────────────────────────────────────────────────────────────


def _pyplot() -> ModuleType | None:
    """matplotlib's pyplot, styled for the reports, or ``None`` without the ``metrics`` extra."""
    try:
        import matplotlib
    except ImportError:
        return None
    matplotlib.use("Agg")
    from matplotlib import pyplot

    pyplot.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": GRID,
            "axes.labelcolor": INK_SECONDARY,
            "axes.titlecolor": INK,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "axes.grid": True,
            "axes.axisbelow": True,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "xtick.color": INK_SECONDARY,
            "ytick.color": INK_SECONDARY,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "font.size": 9,
        }
    )
    return pyplot


def _save(plt: ModuleType, figure: Any, path: Path, title: str) -> None:  # noqa: ANN401
    figure.suptitle(title, x=0.01, ha="left", fontsize=12, color=INK)
    figure.savefig(path, dpi=150)
    plt.close(figure)


def _legend(figure: Any, handles: list[Any], labels: list[str]) -> None:  # noqa: ANN401
    figure.legend(
        handles, labels, loc="outside lower left", ncols=len(labels), frameon=False, fontsize=9
    )


def _bars(
    plt: ModuleType,
    path: Path,
    title: str,
    xlabel: str,
    grid: list[list[tuple[str, list[str], list[tuple[str, str, list[float | None]]]]]],
) -> None:
    """Grouped horizontal bars; ``grid`` cells are ``(panel, categories, series)``."""
    rows, columns = len(grid), len(grid[0])
    figure, axes = plt.subplots(
        rows,
        columns,
        figsize=(4.6 * columns, 0.6 + 2.6 * rows),
        sharey=True,
        squeeze=False,
        layout="constrained",
    )
    handles: dict[str, Any] = {}
    for row, panels in enumerate(grid):
        for column, (panel, categories, series) in enumerate(panels):
            axis = axes[row][column]
            slot = 0.76 / len(series)
            for index, (label, colour, values) in enumerate(series):
                positions = [
                    len(categories) - 1 - c + 0.38 - slot * (index + 0.5)
                    for c in range(len(categories))
                ]
                bars = axis.barh(
                    positions,
                    [value or 0.0 for value in values],
                    height=slot * 0.86,
                    color=colour,
                    label=label,
                )
                handles[label] = bars
            axis.set_yticks(list(range(len(categories) - 1, -1, -1)), categories)
            axis.set_title(panel, loc="left")
            axis.grid(False, axis="y")
            axis.set_xlabel(xlabel)
            axis.tick_params(length=0)
    _legend(figure, list(handles.values()), list(handles))
    _save(plt, figure, path, title)


def _spread_labels(positions: list[float], minimum: float) -> list[float]:
    """Push apart label positions closer than ``minimum``, keeping their order."""
    order = sorted(range(len(positions)), key=lambda i: positions[i])
    placed = list(positions)
    for before, after in zip(order, order[1:], strict=False):
        placed[after] = max(placed[after], placed[before] + minimum)
    return placed


def _lines(
    plt: ModuleType,
    path: Path,
    title: str,
    xlabel: str,
    ylabel: str,
    panels: list[tuple[str, list[tuple[str, str, list[float], list[float]]]]],
) -> None:
    """Line panels with a shared y axis, each line named at its right end."""
    figure, axes = plt.subplots(
        1, len(panels), figsize=(5.2 * len(panels), 3.8), squeeze=False, layout="constrained"
    )
    handles: dict[str, Any] = {}
    for axis, (panel, series) in zip(axes[0], panels, strict=True):
        for label, colour, xs, ys in series:
            handles[label] = axis.plot(xs, ys, color=colour, linewidth=1.8, marker="o", ms=3.5)[0]
        axis.set_title(panel, loc="left")
        axis.set_xlabel(xlabel)
        axis.set_ylabel(ylabel)
        axis.set_ylim(bottom=0)
        axis.margins(x=0.14)
        low, high = axis.get_ylim()
        drawn = [s for s in series if s[3]]
        ends = _spread_labels([s[3][-1] for s in drawn], (high - low) * 0.05)
        for (label, _, xs, _), end in zip(drawn, ends, strict=True):
            axis.annotate(
                label,
                (xs[-1], end),
                xytext=(7, 0),
                textcoords="offset points",
                va="center",
                fontsize=8,
                color=INK_SECONDARY,
            )
    _legend(figure, list(handles.values()), list(handles))
    _save(plt, figure, path, title)


def _cdf_points(values: Sequence[float]) -> tuple[list[float], list[float]]:
    ordered = sorted(values)
    step = max(1, len(ordered) // CDF_POINTS)
    indices = [*range(0, len(ordered), step), len(ordered) - 1]
    return [ordered[i] for i in indices], [(i + 1) / len(ordered) for i in indices]


def _cdfs(
    plt: ModuleType,
    path: Path,
    title: str,
    xlabel: str,
    panels: list[tuple[str, list[tuple[str, str, list[float]]]]],
    reference: tuple[float, str] | None = None,
) -> None:
    """Cumulative distributions, x axis cut at p99.5 so that outliers do not flatten the curve."""
    figure, axes = plt.subplots(
        1, len(panels), figsize=(5.2 * len(panels), 3.8), squeeze=False, layout="constrained"
    )
    handles: dict[str, Any] = {}
    for axis, (panel, series) in zip(axes[0], panels, strict=True):
        limit, lowest = 0.0, float("inf")
        for position, (label, colour, values) in enumerate(series):
            xs, ys = _cdf_points(values)
            handles[label] = axis.plot(xs, ys, color=colour, linewidth=1.8)[0]
            limit = max(limit, percentile(sorted(values), CDF_AXIS_PERCENTILE))
            lowest = min(lowest, xs[0])
            target = (position + 1) / (len(series) + 1)
            index = min(range(len(ys)), key=lambda i: abs(ys[i] - target))
            axis.annotate(
                label,
                (xs[index], ys[index]),
                xytext=(7, -2),
                textcoords="offset points",
                fontsize=8,
                color=INK_SECONDARY,
            )
        if reference is not None:
            axis.axvline(reference[0], color=INK_SECONDARY, linewidth=1, linestyle=(0, (4, 3)))
            axis.annotate(
                reference[1],
                (reference[0], 0.03),
                xytext=(4, 0),
                textcoords="offset points",
                fontsize=8,
                color=INK_SECONDARY,
            )
            limit = max(limit, reference[0])
            lowest = min(lowest, reference[0])
        margin = (limit - lowest) * 0.05
        axis.set_xlim(max(0.0, lowest - margin), limit + margin)
        axis.set_ylim(0, 1.02)
        axis.set_title(panel, loc="left")
        axis.set_xlabel(xlabel)
        axis.set_ylabel("Proporção das amostras")
    _legend(figure, list(handles.values()), list(handles))
    _save(plt, figure, path, title)


def _values(samples: list[tuple[float, float]]) -> list[float]:
    return [value for _, value in samples]


def _route_bars(run: Run, statistic: str, levels: Sequence[str]) -> Any:  # noqa: ANN401
    categories = [label for _, label in CHARTED_ROUTES]
    series = [
        (
            _level_name(level),
            SERIES[index % len(SERIES)],
            [run.stat("app", level, metric, statistic) for metric, _ in CHARTED_ROUTES],
        )
        for index, level in enumerate(levels)
    ]
    return statistic, categories, series


def _run_charts(run: Run) -> list[Chart]:
    plt = _pyplot()
    levels = run.levels("app") if "app" in run.tests else []
    if plt is None or not levels:
        return []
    charts: list[Chart] = []
    colour = {level: SERIES[i % len(SERIES)] for i, level in enumerate(levels)}
    wanted = {"e2e_pull_ms", "tick_interval_ms", "queue_active_messages"}
    samples = run.samples(wanted)

    _bars(
        plt,
        run.path / "latencia-rotas.png",
        "Latência por rota e carga",
        "ms",
        [[_route_bars(run, "p50", levels), _route_bars(run, "p95", levels)]],
    )
    charts.append(("latencia-rotas.png", "Latência por rota", "p50 e p95 por rota e carga."))

    if len(levels) > 1:
        xs = [float(level) for level in levels]
        series = [
            (name, SERIES[index], xs, [run.stat("app", lv, "http_pulls_ms", name) for lv in levels])
            for index, name in enumerate(("p50", "p95"))
        ]
        _lines(
            plt,
            run.path / "latencia-vs-jogadores.png",
            "Latência de POST /pulls por número de jogadores",
            "Jogadores",
            "ms",
            [("POST /pulls", series)],
        )
        charts.append(
            ("latencia-vs-jogadores.png", "Latência × jogadores", "`POST /pulls`, p50 e p95.")
        )

    e2e = [
        (_level_name(lv), colour[lv], _values(samples[(lv, "e2e_pull_ms")]))
        for lv in levels
        if samples.get((lv, "e2e_pull_ms"))
    ]
    if e2e:
        _cdfs(
            plt,
            run.path / "e2e-cdf.png",
            "Puxada até aparecer no telão",
            "ms",
            [("Distribuição acumulada", e2e)],
        )
        charts.append(("e2e-cdf.png", "Ponta a ponta", "Eixo x cortado em p99,5."))

    ticks = [
        (_level_name(lv), colour[lv], _values(samples[(lv, "tick_interval_ms")]))
        for lv in levels
        if samples.get((lv, "tick_interval_ms"))
    ]
    if ticks:
        configured = float(run.manifest["parameters"]["game"]["TICK_INTERVAL_MS"])
        _cdfs(
            plt,
            run.path / "tick-intervalo.png",
            "Intervalo real entre ticks",
            "ms",
            [("Distribuição acumulada", ticks)],
            reference=(configured, f"configurado: {configured:.0f} ms"),
        )
        charts.append(("tick-intervalo.png", "Intervalo entre ticks", "Eixo x cortado em p99,5."))

    depth = []
    for lv in levels:
        points = samples.get((lv, "queue_active_messages"))
        if points:
            first = points[0][0]
            depth.append(
                (_level_name(lv), colour[lv], [t - first for t, _ in points], _values(points))
            )
    if depth:
        _lines(
            plt,
            run.path / "fila-profundidade.png",
            "Mensagens ativas na fila durante a partida",
            "Segundos desde o início da amostragem",
            "Mensagens",
            [("Fila metrics-app", depth)],
        )
        charts.append(("fila-profundidade.png", "Profundidade da fila", "Amostras a cada 2 s."))
    return charts


def _charts_section(charts: Sequence[Chart]) -> list[str]:
    if not charts:
        return [
            "Gráficos não gerados: instale o extra `metrics` (`pip install -e '.[metrics]'`) ou "
            "rode o teste principal para produzi-los.",
            "",
        ]
    lines: list[str] = []
    for file_name, title, note in charts:
        lines += [f"**{title}.** {note}", "", f"![{title}]({file_name})", ""]
    return lines


# ── Single-run report ─────────────────────────────────────────────────────────


def _environment_rows(run: Run) -> list[tuple[str, Any]]:
    manifest, host = run.manifest, run.manifest["host"]
    namespace = manifest.get("namespace") or {}
    rows: list[tuple[str, Any]] = [
        ("Rótulo da execução (região)", manifest["label"]),
        ("Status", STATUS_TEXT[manifest["status"]]),
        ("Perfil", manifest["profile"]),
        ("Testes executados", ", ".join(manifest["tests"])),
        ("Início (UTC)", manifest["started_at_utc"]),
        ("Fim (UTC)", _plain(manifest.get("finished_at_utc"))),
        ("Sistema operacional", host["platform"]),
        ("Arquitetura", host["machine"]),
        ("Núcleos de CPU", _plain(host["cpu_count"])),
        ("Identificador da máquina (hash)", host["fingerprint"]),
        ("Python", host["python"]),
    ]
    rows += [
        (f"Versão de {name}", version) for name, version in sorted(manifest["versions"].items())
    ]
    rows += [
        ("Namespace", _plain(namespace.get("host"))),
        ("Camada do namespace", _plain(namespace.get("tier"))),
        ("Unidades de mensagens", _plain(namespace.get("messaging_units"))),
    ]
    for test in manifest["tests"]:
        queue = run.results.get(test, {}).get("queue")
        if queue:
            rows.append(
                (
                    f"Fila de teste ({test})",
                    f"`{queue['name']}`: lock de {queue['lock_duration_s']:g} s, máximo de "
                    f"{queue['max_delivery_count']} entregas, TTL de {queue['message_ttl_s']:g} s, "
                    f"detecção de duplicatas de {queue['duplicate_window_s']:g} s",
                )
            )
    return rows


def _metric_rows(run: Run, test: str, names: Iterable[str]) -> list[list[Any]]:
    rows = []
    for name in names:
        info = METRICS[name]
        for level in run.levels(test):
            entry = run.metrics.get((test, level, name))
            if entry is not None:
                rows.append(
                    [
                        info.label,
                        _level_name(level),
                        info.unit,
                        entry["n"],
                        _number(entry["mean"]),
                        _number(entry["stdev"]),
                        _number(entry["p50"]),
                        _number(entry["p95"]),
                        _number(entry["p99"]),
                        _number(entry["max"]),
                    ]
                )
    return rows


METRIC_HEADER = ("Métrica", "Carga", "Unidade", "n", "Média", "Desvio", "p50", "p95", "p99", "Máx.")


def _group_tables(run: Run, test: str, groups: Sequence[str], number: int) -> tuple[list[str], int]:
    lines: list[str] = []
    for group in groups:
        rows = _metric_rows(run, test, _names_in_group(run, group))
        if rows:
            lines += [f"### 4.{number} {group}", "", *_table(METRIC_HEADER, rows)]
            number += 1
    return lines, number


def _load_rows(run: Run) -> list[list[Any]]:
    rows = []
    for level, outcome in sorted(run.app_levels.items(), key=lambda i: level_sort_key(i[0])):
        load = outcome["load"]
        rows.append(
            [
                _level_name(level),
                outcome["players_joined"],
                outcome["players_stopped"],
                _number(load["window_s"]),
                _number(load["requests_per_s"]),
                _number(load["pull_posts_per_s"]),
                _number(_messages_consumed_per_s(outcome)),
                _number(load["pulls_applied_per_s"]),
                outcome["ticks"],
                _plain(outcome["result"]),
                _plain(outcome["end_reason"]),
                _plain(outcome["final_rope"]),
            ]
        )
    return rows


def _consistency_rows(run: Run) -> list[list[Any]]:
    rows = []
    for level, outcome in sorted(run.app_levels.items(), key=lambda i: level_sort_key(i[0])):
        c = outcome["consistency"]
        queue_end = outcome.get("queue_end") or {}
        rows.append(
            [
                _level_name(level),
                c["pulls_generated"],
                c["pulls_accepted"],
                _plain(c["pulls_applied_ws"]),
                _plain(c["pulls_applied_view"]),
                _plain(c["pulls_applied_analytics"]),
                _plain(c["pulls_missing"]),
                _plain(c["pulls_extra"]),
                _plain(queue_end.get("active")),
                _plain(queue_end.get("dead_letter")),
                c["flushes_skipped"],
                c["resends"],
                f"{c['duplicates_accepted']}/{c['duplicates_sent']}",
            ]
        )
    return rows


def _http_status_rows(run: Run) -> list[list[Any]]:
    codes: dict[tuple[str, str], list[str]] = defaultdict(list)
    failures: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for (test, level, name), value in run.counters.items():
        if test != "app":
            continue
        parts = name.split(":")
        if parts[0] == "http_status":
            codes[(level, parts[1])].append(f"{parts[2]}: {value}")
        elif parts[0] == "http_failure":
            failures[(level, parts[1])][parts[2]] += value
    rows = []
    for level, route in sorted({*codes, *failures}, key=lambda k: (level_sort_key(k[0]), k[1])):
        info = METRICS.get(f"http_{route}_ms")
        rows.append(
            [
                info.label if info else route,
                _level_name(level),
                ", ".join(sorted(codes[(level, route)])) or "–",
                failures[(level, route)]["timeout"],
                failures[(level, route)]["network"],
            ]
        )
    return rows


def _error_rows(run: Run) -> list[list[Any]]:
    rows = []
    for (test, level, name), value in sorted(run.counters.items()):
        if name.startswith("error:"):
            parts = name.split(":")
            rows.append([test, _level_name(level), ":".join(parts[1:-1]), parts[-1], value])
    return rows


def _service_bus_outcome(run: Run) -> list[str]:
    e2e = run.results.get("servicebus", {}).get("e2e")
    if not e2e:
        return []
    return [
        "Contagens do ponta a ponta direto:",
        "",
        *_table(
            ["Esperadas", "Enviadas", "Recebidas", "Não recebidas", "Duplicadas", "Fora de ordem"],
            [
                [
                    e2e["expected"],
                    e2e["sent"],
                    e2e["received"],
                    e2e["lost"],
                    e2e["duplicates"],
                    e2e["out_of_order"],
                ]
            ],
        ),
    ]


def _results_section(run: Run, charts: Sequence[Chart]) -> list[str]:
    lines = ["## 4. Resultados", ""]
    number = 1
    if "app" in run.tests:
        lines += [
            f"### 4.{number} Carga e resultado por partida",
            "",
            *_table(
                (
                    "Carga",
                    "Entraram",
                    "Interrompidos",
                    "Janela de puxadas (s)",
                    "Req/s",
                    "POST /pulls por s",
                    "Mensagens consumidas por s",
                    "Puxadas aplicadas por s",
                    "Ticks",
                    "Resultado",
                    "Fim",
                    "Corda final",
                ),
                _load_rows(run),
            ),
            f"### 4.{number + 1} Consistência",
            "",
            *_table(
                (
                    "Carga",
                    "Geradas",
                    "Aceitas",
                    "Aplicadas (WebSocket)",
                    "Aplicadas (partida)",
                    "Aplicadas (analytics)",
                    "Faltantes",
                    "Extras",
                    "Na fila ao fim",
                    "Na DLQ ao fim",
                    "Ciclos pulados",
                    "Reenvios por falha",
                    "Reenvios propositais aceitos",
                ),
                _consistency_rows(run),
            ),
        ]
        number += 2
        groups = ("HTTP", "Ponta a ponta", "Jogo", "Fila", "Recursos")
        tables, number = _group_tables(run, "app", groups, number)
        lines += tables
        lines += [
            f"### 4.{number} Códigos HTTP",
            "",
            *_table(
                ("Rota", "Carga", "Códigos", "Timeouts", "Falhas de rede"), _http_status_rows(run)
            ),
        ]
        number += 1
    if "servicebus" in run.tests:
        tables, number = _group_tables(run, "servicebus", ("Service Bus direto",), number)
        lines += tables
        lines += _service_bus_outcome(run)
    errors = _error_rows(run)
    if errors:
        lines += [
            f"### 4.{number} Erros registrados",
            "",
            *_table(("Teste", "Carga", "Local", "Tipo", "Ocorrências"), errors),
        ]
        number += 1
    if "app" in run.tests:
        lines += [f"### 4.{number} Gráficos", "", *_charts_section(charts)]
    lines += _monitor_section(run)
    return lines


def write_run_report(path: Path) -> Path:
    """Write ``report.md`` (and its charts) for a finished run directory."""
    run = load_run(path)
    charts = _run_charts(run)
    observed = _observed_limitations(run)
    lines = [
        f"# Relatório de métricas: {run.label}",
        "",
        "Este arquivo descreve por completo uma execução da bancada de métricas do Corda Brava: "
        "ambiente, procedimento, origem de cada métrica, resultados e limitações.",
        "",
        "## 1. Ambiente",
        "",
        *_table(["Item", "Valor"], _environment_rows(run)),
        *_method_section(run),
        *_extraction_section([run]),
        *_results_section(run, charts),
        "## 5. Limitações",
        "",
        "### Limitações do método",
        "",
        *[f"- {note}" for note in _general_limitations()],
        "",
        "### Observadas nesta execução",
        "",
        *([f"- {note}" for note in observed] or ["- Nenhuma ocorrência adicional foi observada."]),
        "",
    ]
    report = path / REPORT_NAME
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


# ── Comparison ────────────────────────────────────────────────────────────────


def _difference(first: dict[str, Any], second: dict[str, Any]) -> str:
    keys = sorted(k for k in {*first, *second} if first.get(k) != second.get(k))
    return "igual" if not keys else "difere em: " + ", ".join(f"`{key}`" for key in keys)


def _same(first: Any, second: Any) -> str:  # noqa: ANN401 - any scalar from the manifests
    return "igual" if first == second else "**diferente**"


def _comparability_rows(a: Run, b: Run) -> list[list[Any]]:
    ma, mb = a.manifest, b.manifest
    na, nb = ma.get("namespace") or {}, mb.get("namespace") or {}
    scalar = (
        ("Máquina (hash)", ma["host"]["fingerprint"], mb["host"]["fingerprint"]),
        ("Sistema operacional", ma["host"]["platform"], mb["host"]["platform"]),
        ("Python", ma["host"]["python"], mb["host"]["python"]),
        ("Perfil", ma["profile"], mb["profile"]),
        ("Testes", ", ".join(ma["tests"]), ", ".join(mb["tests"])),
        ("Camada do namespace", _plain(na.get("tier")), _plain(nb.get("tier"))),
        ("Status", ma["status"], mb["status"]),
    )
    rows = [[item, x, y, _same(x, y)] for item, x, y in scalar]
    rows.append(["Versões das bibliotecas", "–", "–", _difference(ma["versions"], mb["versions"])])
    for name in sorted({*ma["parameters"], *mb["parameters"]}):
        rows.append(
            [
                f"Parâmetros ({name})",
                "–",
                "–",
                _difference(ma["parameters"].get(name, {}), mb["parameters"].get(name, {})),
            ]
        )
    return rows


def _relative(first: float | None, second: float | None) -> str:
    if first is None or second is None or first == 0:
        return "–"
    return f"{100 * (second - first) / first:+.1f}%"


def _absolute(first: float | None, second: float | None) -> str:
    if first is None or second is None:
        return "–"
    return f"{second - first:+.1f}"


def _comparison_rows(a: Run, b: Run) -> list[dict[str, Any]]:
    """One record per ``(test, level, metric)`` measured in either run, in catalog order."""
    keys = {*a.metrics, *b.metrics}
    records = []
    for name in _metric_names({metric for _, _, metric in keys}):
        for test, level, metric in sorted(keys, key=lambda k: (k[0], level_sort_key(k[1]))):
            if metric == name:
                records.append(
                    {
                        "test": test,
                        "level": level,
                        "metric": metric,
                        "a": a.metrics.get((test, level, metric)),
                        "b": b.metrics.get((test, level, metric)),
                    }
                )
    return records


def _field(entry: dict[str, Any] | None, name: str) -> float | None:
    return None if entry is None else entry[name]


def _comparison_tables(a: Run, b: Run, records: list[dict[str, Any]]) -> list[str]:
    header = (
        "Métrica",
        "Carga",
        "Unidade",
        f"n ({a.label})",
        f"p50 ({a.label})",
        f"p95 ({a.label})",
        f"n ({b.label})",
        f"p50 ({b.label})",
        f"p95 ({b.label})",
        "Δ p50",
        "Δ p50 %",
        "Δ p95 %",
    )
    lines: list[str] = []
    for group in GROUP_ORDER:
        rows = []
        for record in records:
            info = METRICS.get(record["metric"])
            if info is None or info.group != group:
                continue
            ea, eb = record["a"], record["b"]
            rows.append(
                [
                    info.label,
                    _level_name(record["level"]),
                    info.unit,
                    _plain(_field(ea, "n")),
                    _number(_field(ea, "p50")),
                    _number(_field(ea, "p95")),
                    _plain(_field(eb, "n")),
                    _number(_field(eb, "p50")),
                    _number(_field(eb, "p95")),
                    _absolute(_field(ea, "p50"), _field(eb, "p50")),
                    _relative(_field(ea, "p50"), _field(eb, "p50")),
                    _relative(_field(ea, "p95"), _field(eb, "p95")),
                ]
            )
        if rows:
            lines += [f"### {group}", "", *_table(header, rows)]
    return lines


def _write_comparison_csv(path: Path, a: Run, b: Run, records: list[dict[str, Any]]) -> None:
    fields = ("n", "mean", "stdev", "min", "p50", "p90", "p95", "p99", "max")
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            ["test", "level", "metric"]
            + [f"{field}_{a.label}" for field in fields]
            + [f"{field}_{b.label}" for field in fields]
        )
        for record in records:
            writer.writerow(
                [record["test"], record["level"], record["metric"]]
                + [_field(record["a"], field) for field in fields]
                + [_field(record["b"], field) for field in fields]
            )


def _side_by_side_outcomes(a: Run, b: Run) -> list[str]:
    rows = []
    levels = sorted({*a.app_levels, *b.app_levels}, key=level_sort_key)
    for level in levels:
        for run in (a, b):
            outcome = run.app_levels.get(level)
            if outcome is None:
                continue
            c, load = outcome["consistency"], outcome["load"]
            rows.append(
                [
                    _level_name(level),
                    run.label,
                    outcome["players_joined"],
                    outcome["players_stopped"],
                    _number(load["requests_per_s"]),
                    _number(load["pull_posts_per_s"]),
                    _number(_messages_consumed_per_s(outcome)),
                    c["pulls_accepted"],
                    _plain(c["pulls_applied_ws"]),
                    _plain(c["pulls_missing"]),
                    _plain(c["pulls_extra"]),
                    _plain((outcome.get("queue_end") or {}).get("active")),
                    c["flushes_skipped"],
                    outcome["ticks"],
                ]
            )
    lines: list[str] = []
    if rows:
        lines += [
            "### Carga e consistência",
            "",
            *_table(
                (
                    "Carga",
                    "Região",
                    "Entraram",
                    "Interrompidos",
                    "Req/s",
                    "POST /pulls por s",
                    "Mensagens consumidas por s",
                    "Aceitas",
                    "Aplicadas",
                    "Faltantes",
                    "Extras",
                    "Na fila ao fim",
                    "Ciclos pulados",
                    "Ticks",
                ),
                rows,
            ),
        ]
    e2e_rows = []
    for run in (a, b):
        e2e = run.results.get("servicebus", {}).get("e2e")
        if e2e:
            e2e_rows.append(
                [run.label, e2e["sent"], e2e["received"], e2e["lost"], e2e["duplicates"]]
            )
    if e2e_rows:
        lines += [
            "### Ponta a ponta direto no Service Bus",
            "",
            *_table(("Região", "Enviadas", "Recebidas", "Não recebidas", "Duplicadas"), e2e_rows),
        ]
    return lines


def _comparison_monitor(a: Run, b: Run) -> list[str]:
    rollup_a, rollup_b = _monitor_rollup(a), _monitor_rollup(b)
    if not rollup_a and not rollup_b:
        return []
    rows = []
    for name in sorted({*rollup_a, *rollup_b}):
        entry_a, entry_b = rollup_a.get(name), rollup_b.get(name)
        unit = (entry_a or entry_b)["unit"]
        rows.append(
            [
                f"`{name}`",
                unit,
                _number(entry_a["total"]) if entry_a else "–",
                _number(_monitor_average(entry_a)),
                _number(entry_b["total"]) if entry_b else "–",
                _number(_monitor_average(entry_b)),
            ]
        )
    return [
        "### Azure Monitor (lado do servidor)",
        "",
        *_table(
            (
                "Métrica",
                "Unidade",
                f"Soma ({a.label})",
                f"Média ({a.label})",
                f"Soma ({b.label})",
                f"Média ({b.label})",
            ),
            rows,
        ),
    ]


def _comparison_charts(a: Run, b: Run, out: Path) -> list[Chart]:
    plt = _pyplot()
    if plt is None:
        return []
    runs = ((a, SERIES[0]), (b, SERIES[1]))
    levels = sorted(set(a.levels("app")) & set(b.levels("app")), key=level_sort_key)
    charts: list[Chart] = []

    if levels:
        grid = []
        for statistic in ("p50", "p95"):
            row = []
            for level in levels:
                categories = [label for _, label in CHARTED_ROUTES]
                series = [
                    (
                        run.label,
                        colour,
                        [run.stat("app", level, metric, statistic) for metric, _ in CHARTED_ROUTES],
                    )
                    for run, colour in runs
                ]
                row.append((f"{statistic}, {_level_name(level)}", categories, series))
            grid.append(row)
        _bars(plt, out / "latencia-rotas.png", "Latência por rota e região", "ms", grid)
        charts.append(("latencia-rotas.png", "Latência por rota e região", "p50 e p95."))

    if len(levels) > 1:
        xs = [float(level) for level in levels]
        panels = [
            (
                statistic,
                [
                    (
                        run.label,
                        colour,
                        xs,
                        [run.stat("app", level, "http_pulls_ms", statistic) for level in levels],
                    )
                    for run, colour in runs
                ],
            )
            for statistic in ("p50", "p95")
        ]
        _lines(
            plt,
            out / "latencia-vs-jogadores.png",
            "Latência de POST /pulls por número de jogadores",
            "Jogadores",
            "ms",
            panels,
        )
        charts.append(
            ("latencia-vs-jogadores.png", "Latência × jogadores", "`POST /pulls` por região.")
        )

    if levels:
        wanted = {"e2e_pull_ms", "tick_interval_ms"}
        data = {run.label: run.samples(wanted) for run, _ in runs}
        for metric, file_name, title, note, reference in (
            (
                "e2e_pull_ms",
                "e2e-cdf.png",
                "Puxada até aparecer no telão",
                "Eixo x em p99,5.",
                None,
            ),
            (
                "tick_interval_ms",
                "tick-intervalo.png",
                "Intervalo real entre ticks",
                "Eixo x em p99,5.",
                float(a.manifest["parameters"]["game"]["TICK_INTERVAL_MS"]),
            ),
        ):
            panels = []
            for level in levels:
                series = [
                    (run.label, colour, _values(data[run.label][(level, metric)]))
                    for run, colour in runs
                    if data[run.label].get((level, metric))
                ]
                if series:
                    panels.append((_level_name(level), series))
            if panels:
                marker = (
                    None if reference is None else (reference, f"configurado: {reference:.0f} ms")
                )
                _cdfs(plt, out / file_name, title, "ms", panels, reference=marker)
                charts.append((file_name, title, note))

    if a.levels("servicebus") and b.levels("servicebus"):
        categories = [label for _, label in CHARTED_SERVICE_BUS]
        grid_row = []
        for statistic in ("p50", "p95"):
            series = [
                (
                    run.label,
                    colour,
                    [
                        run.stat("servicebus", NO_LEVEL, metric, statistic)
                        for metric, _ in CHARTED_SERVICE_BUS
                    ],
                )
                for run, colour in runs
            ]
            grid_row.append((statistic, categories, series))
        _bars(plt, out / "service-bus.png", "Service Bus direto, por região", "ms", [grid_row])
        charts.append(("service-bus.png", "Service Bus direto", "p50 e p95 por operação."))
    return charts


def write_comparison(a: Run, b: Run, out: Path) -> Path:
    """Write ``report-comparacao.md``, ``comparacao.csv`` and the charts into ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    records = _comparison_rows(a, b)
    _write_comparison_csv(out / "comparacao.csv", a, b, records)
    charts = _comparison_charts(a, b, out)
    observed = []
    for run in (a, b):
        observed += [f"**{run.label}:** {note}" for note in _observed_limitations(run)]
    lines = [
        f"# Comparação de métricas: {a.label} × {b.label}",
        "",
        "Este arquivo compara duas execuções da bancada de métricas do Corda Brava e descreve o "
        "ambiente, o procedimento, a origem de cada métrica, os resultados e as limitações. "
        f"Nas colunas Δ, a referência é `{a.label}`: valor positivo significa que "
        f"`{b.label}` foi maior.",
        "",
        "## 1. Ambiente e condições de comparação",
        "",
        *_table(
            ["Item", a.label, b.label, "Comparação"],
            _comparability_rows(a, b),
        ),
        "Execuções:",
        "",
        f"- `{a.label}`: `{a.manifest['namespace'].get('host', '–')}`, de "
        f"{a.manifest['started_at_utc']} a {_plain(a.manifest.get('finished_at_utc'))} (UTC).",
        f"- `{b.label}`: `{b.manifest['namespace'].get('host', '–')}`, de "
        f"{b.manifest['started_at_utc']} a {_plain(b.manifest.get('finished_at_utc'))} (UTC).",
        "",
        *_method_section(a),
        *_extraction_section([a, b]),
        "## 4. Resultados lado a lado",
        "",
        *_side_by_side_outcomes(a, b),
        *_comparison_tables(a, b, records),
        *_comparison_monitor(a, b),
        "### Gráficos",
        "",
        *_charts_section(charts),
        "## 5. Limitações",
        "",
        "### Limitações do método",
        "",
        *[f"- {note}" for note in _general_limitations()],
        "",
        "### Observadas nestas execuções",
        "",
        *([f"- {note}" for note in observed] or ["- Nenhuma ocorrência adicional foi observada."]),
        "",
    ]
    report = out / COMPARISON_NAME
    report.write_text("\n".join(lines), encoding="utf-8")
    return report
