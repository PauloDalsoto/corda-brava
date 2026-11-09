"""Bench queue helpers and the simple, direct Azure Service Bus test."""

from __future__ import annotations

import asyncio
import json
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.servicebus import ServiceBusMessage, ServiceBusReceivedMessage, ServiceBusReceiveMode
from azure.servicebus.aio import ServiceBusClient, ServiceBusReceiver, ServiceBusSender
from azure.servicebus.management import ServiceBusAdministrationClient

from app.metrics.stats import NO_LEVEL, Recorder, jitter, say
from app.queue.provision import ensure_queue

TEST = "servicebus"
QUEUE_NAME = "metrics-sb"
E2E_QUEUE_NAME = "metrics-sb-e2e"
QUEUE_SETTLE_S = 3.0  # broker counters lag a little behind the last settle
BENCH_QUEUE_PREFIX = "metrics-"
AMQP_PORT = 5671
QUEUE_DELETE_WAIT_S = 30.0
QUEUE_SETUP_ATTEMPTS = 6
QUEUE_SETUP_RETRY_S = 5.0
SEND_TIMEOUT_S = 10.0
RTT_TIMEOUT_S = 5.0
RTT_PAUSE_S = 0.1
MAX_PARALLEL_COMPLETES = 100  # same cap as the app's adapter
EMPTY_RECEIVES_BEFORE_STOP = 3
RECEIVE_WAIT_S = 1.0
E2E_RECEIVE_WAIT_S = 0.5
E2E_START_DELAY_NS = 200_000_000  # lets the consumer reach its first receive


@dataclass(frozen=True)
class ServiceBusProfile:
    rtt_probes: int
    single_sends: int
    single_sends_max_s: float
    batch_sends: int
    batch_size: int = 100
    payload_bytes: int = 256  # about the size of the app's PullEvent JSON
    drain_max_s: float = 30.0
    e2e_rate_msg_s: int = 100
    e2e_duration_s: float = 30.0
    e2e_tick_s: float = 0.1
    e2e_drain_max_s: float = 10.0


SERVICEBUS_PROFILES = {
    "quick": ServiceBusProfile(
        rtt_probes=30, single_sends=200, single_sends_max_s=15.0, batch_sends=30
    ),
    "smoke": ServiceBusProfile(
        rtt_probes=5,
        single_sends=20,
        single_sends_max_s=5.0,
        batch_sends=3,
        drain_max_s=10.0,
        e2e_duration_s=5.0,
        e2e_drain_max_s=5.0,
    ),
}


def endpoint_host(connection: str) -> str:
    """The namespace's host name; the only part of a connection string that may be stored."""
    for part in connection.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "endpoint":
            host = urlparse(value).hostname
            if host:
                return host
    raise ValueError("A connection string não tem Endpoint.")


def _require_bench_queue(name: str) -> None:
    """Deleting is part of the bench's cleanup; it must never reach the app's real queue."""
    if not name.startswith(BENCH_QUEUE_PREFIX):
        raise ValueError(f"{name!r} não é uma fila da bancada de métricas.")


def _delete_and_wait(admin: ServiceBusAdministrationClient, name: str) -> None:
    try:
        admin.delete_queue(name)
    except ResourceNotFoundError:
        return
    deadline = time.monotonic() + QUEUE_DELETE_WAIT_S
    while time.monotonic() < deadline:
        try:
            admin.get_queue(name)
        except ResourceNotFoundError:
            return
        time.sleep(1)


def recreate_queue(connection: str, name: str, lock_s: int) -> dict[str, Any]:
    """Create an empty bench queue with the real queue's properties and return them.

    A leftover from an interrupted run is deleted first, so old messages cannot pollute the
    new measurement.
    """
    _require_bench_queue(name)
    with ServiceBusAdministrationClient.from_connection_string(connection) as admin:
        _delete_and_wait(admin, name)
        for attempt in range(1, QUEUE_SETUP_ATTEMPTS + 1):
            try:
                ensure_queue(connection, name, timedelta(seconds=lock_s))
                break
            except AzureError:  # the namespace may still be finishing the delete
                if attempt == QUEUE_SETUP_ATTEMPTS:
                    raise
                time.sleep(QUEUE_SETUP_RETRY_S)
        props = admin.get_queue(name)
    return {
        "name": name,
        "lock_duration_s": props.lock_duration.total_seconds(),
        "max_delivery_count": props.max_delivery_count,
        "message_ttl_s": props.default_message_time_to_live.total_seconds(),
        "duplicate_detection": props.requires_duplicate_detection,
        "duplicate_window_s": props.duplicate_detection_history_time_window.total_seconds(),
    }


def drop_queue(connection: str, name: str) -> None:
    _require_bench_queue(name)
    with ServiceBusAdministrationClient.from_connection_string(connection) as admin:
        try:
            admin.delete_queue(name)
        except ResourceNotFoundError:
            pass


def namespace_info(connection: str) -> dict[str, Any]:
    """Host, tier and capacity of the namespace; the region is not exposed by this API."""
    info: dict[str, Any] = {"host": endpoint_host(connection)}
    try:
        with ServiceBusAdministrationClient.from_connection_string(connection) as admin:
            props = admin.get_namespace_properties()
    except AzureError as exc:
        info["error"] = type(exc).__name__
        return info
    info.update(
        name=props.name,
        tier=str(props.messaging_sku),
        namespace_type=str(props.namespace_type),
        messaging_units=props.messaging_units,
    )
    return info


class QueueProbe:
    """Reads a queue's broker-side counters through the management API."""

    def __init__(self, connection: str, name: str) -> None:
        self._admin = ServiceBusAdministrationClient.from_connection_string(connection)
        self._name = name

    def read(self) -> dict[str, int]:
        props = self._admin.get_queue_runtime_properties(self._name)
        return {
            "active": props.active_message_count,
            "dead_letter": props.dead_letter_message_count,
            "size_bytes": props.size_in_bytes,
        }

    def close(self) -> None:
        self._admin.close()


def _active_messages(connection: str) -> int | None:
    """Messages still waiting in the end-to-end queue, or ``None`` if they cannot be read."""
    probe = QueueProbe(connection, E2E_QUEUE_NAME)
    try:
        return probe.read()["active"]
    except AzureError:
        return None
    finally:
        probe.close()


def _body(payload_bytes: int, **fields: Any) -> str:  # noqa: ANN401 - free-form JSON fields
    """A JSON body padded to about ``payload_bytes``."""
    head = json.dumps(fields)
    padding = "x" * max(0, payload_bytes - len(head) - 12)
    return json.dumps({**fields, "pad": padding})


def _message(payload_bytes: int, **fields: Any) -> ServiceBusMessage:  # noqa: ANN401
    # Unique ids: the bench queue has duplicate detection on, like the real one.
    return ServiceBusMessage(_body(payload_bytes, **fields), message_id=str(uuid.uuid4()))


async def _probe_rtt(host: str, profile: ServiceBusProfile, recorder: Recorder) -> None:
    """Time TCP handshakes on the AMQP port, against an already resolved address."""
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(host, AMQP_PORT, type=socket.SOCK_STREAM)
    address = addresses[0][4][0]
    samples: list[float] = []
    for _ in range(profile.rtt_probes):
        started = time.perf_counter_ns()
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(address, AMQP_PORT), RTT_TIMEOUT_S
            )
        except (OSError, TimeoutError) as exc:
            recorder.error(TEST, NO_LEVEL, "rtt", exc)
            continue
        elapsed_ms = (time.perf_counter_ns() - started) / 1e6
        writer.close()
        await writer.wait_closed()
        samples.append(elapsed_ms)
        recorder.add(TEST, NO_LEVEL, "sb_rtt_ms", elapsed_ms, started)
        await asyncio.sleep(RTT_PAUSE_S)
    spread = jitter(samples)
    if spread is not None:
        recorder.add(TEST, NO_LEVEL, "sb_rtt_jitter_ms", spread)


async def _send_singles(
    sender: ServiceBusSender, profile: ServiceBusProfile, recorder: Recorder
) -> int:
    sent = 0
    busy_s = 0.0
    began = time.perf_counter()
    for _ in range(profile.single_sends):
        if time.perf_counter() - began >= profile.single_sends_max_s:
            break
        message = _message(profile.payload_bytes, kind="load")
        started = time.perf_counter_ns()
        try:
            await sender.send_messages(message, timeout=SEND_TIMEOUT_S)
        except AzureError as exc:
            recorder.error(TEST, NO_LEVEL, "send_single", exc)
            continue
        elapsed_ns = time.perf_counter_ns() - started
        recorder.add(TEST, NO_LEVEL, "sb_send_single_ms", elapsed_ns / 1e6, started)
        busy_s += elapsed_ns / 1e9
        sent += 1
    if sent:
        recorder.add(TEST, NO_LEVEL, "sb_send_single_msg_s", sent / busy_s)
    return sent


async def _send_batches(
    sender: ServiceBusSender, profile: ServiceBusProfile, recorder: Recorder
) -> int:
    sent = 0
    busy_s = 0.0
    for _ in range(profile.batch_sends):
        try:
            batch = await sender.create_message_batch()
            for _ in range(profile.batch_size):
                batch.add_message(_message(profile.payload_bytes, kind="load"))
            started = time.perf_counter_ns()
            await sender.send_messages(batch, timeout=SEND_TIMEOUT_S)
        except AzureError as exc:
            recorder.error(TEST, NO_LEVEL, "send_batch", exc)
            continue
        elapsed_ns = time.perf_counter_ns() - started
        recorder.add(TEST, NO_LEVEL, "sb_send_batch_ms", elapsed_ns / 1e6, started)
        busy_s += elapsed_ns / 1e9
        sent += profile.batch_size
    if sent:
        recorder.add(TEST, NO_LEVEL, "sb_send_batch_msg_s", sent / busy_s)
    return sent


async def _complete_all(
    receiver: ServiceBusReceiver,
    messages: list[ServiceBusReceivedMessage],
    recorder: Recorder,
    *,
    timed: bool,
) -> None:
    """Settle in parallel, like the adapter; the caller never overlaps this with a receive."""
    gate = asyncio.Semaphore(MAX_PARALLEL_COMPLETES)

    async def complete(message: ServiceBusReceivedMessage) -> None:
        async with gate:
            started = time.perf_counter_ns()
            try:
                await receiver.complete_message(message)
            except AzureError as exc:
                recorder.error(TEST, NO_LEVEL, "complete", exc)
                return
            if timed:
                elapsed_ms = (time.perf_counter_ns() - started) / 1e6
                recorder.add(TEST, NO_LEVEL, "sb_complete_msg_ms", elapsed_ms, started)

    await asyncio.gather(*(complete(message) for message in messages))


async def _receive_and_complete(
    receiver: ServiceBusReceiver, expected: int, profile: ServiceBusProfile, recorder: Recorder
) -> int:
    """Drain the backlog built by the send steps, measuring receives and completes."""
    received, empty, busy_s = 0, 0, 0.0
    deadline = time.perf_counter() + profile.drain_max_s
    while received < expected and empty < EMPTY_RECEIVES_BEFORE_STOP:
        if time.perf_counter() >= deadline:
            break
        started = time.perf_counter_ns()
        try:
            messages = await receiver.receive_messages(
                max_message_count=profile.batch_size, max_wait_time=RECEIVE_WAIT_S
            )
        except AzureError as exc:
            recorder.error(TEST, NO_LEVEL, "receive", exc)
            continue
        received_at = time.perf_counter_ns()
        if not messages:
            empty += 1
            continue
        empty = 0
        recorder.add(TEST, NO_LEVEL, "sb_receive_batch_ms", (received_at - started) / 1e6, started)
        recorder.add(TEST, NO_LEVEL, "sb_receive_batch_messages", len(messages), started)
        await _complete_all(receiver, messages, recorder, timed=True)
        finished = time.perf_counter_ns()
        recorder.add(
            TEST, NO_LEVEL, "sb_complete_batch_ms", (finished - received_at) / 1e6, received_at
        )
        busy_s += (finished - started) / 1e9
        received += len(messages)
    if received:
        recorder.add(TEST, NO_LEVEL, "sb_receive_complete_msg_s", received / busy_s)
    return received


async def _end_to_end(
    sender: ServiceBusSender,
    receiver: ServiceBusReceiver,
    profile: ServiceBusProfile,
    recorder: Recorder,
) -> dict[str, int]:
    """Send at a fixed rate and time each message from its scheduled send to its receipt.

    Latency starts at the *scheduled* instant, so a sender that falls behind shows up as latency
    instead of hiding behind fewer samples.
    """
    per_tick = round(profile.e2e_rate_msg_s * profile.e2e_tick_s)
    ticks = round(profile.e2e_duration_s / profile.e2e_tick_s)
    tick_ns = round(profile.e2e_tick_s * 1e9)
    began_ns = time.perf_counter_ns() + E2E_START_DELAY_NS
    counts = {"sent": 0, "received": 0, "duplicates": 0, "out_of_order": 0}
    seen: set[int] = set()
    producing = True

    async def produce() -> None:
        nonlocal producing
        sequence = 0
        for tick in range(ticks):
            scheduled_ns = began_ns + tick * tick_ns
            delay_ns = scheduled_ns - time.perf_counter_ns()
            if delay_ns > 0:
                await asyncio.sleep(delay_ns / 1e9)
            try:
                batch = await sender.create_message_batch()
                for offset in range(per_tick):
                    batch.add_message(
                        _message(
                            profile.payload_bytes,
                            kind="e2e",
                            seq=sequence + offset,
                            scheduled_ns=scheduled_ns,
                        )
                    )
                started = time.perf_counter_ns()
                await sender.send_messages(batch, timeout=SEND_TIMEOUT_S)
            except AzureError as exc:
                recorder.error(TEST, NO_LEVEL, "e2e_send", exc)
            else:
                elapsed_ms = (time.perf_counter_ns() - started) / 1e6
                recorder.add(TEST, NO_LEVEL, "sb_e2e_send_ms", elapsed_ms, started)
                counts["sent"] += per_tick
            sequence += per_tick
        producing = False

    async def consume() -> None:
        last_sequence = -1
        drain_until = None
        while True:
            if not producing:
                if counts["received"] >= counts["sent"]:
                    return
                drain_until = drain_until or time.perf_counter() + profile.e2e_drain_max_s
                if time.perf_counter() >= drain_until:
                    return
            try:
                messages = await receiver.receive_messages(
                    max_message_count=profile.batch_size, max_wait_time=E2E_RECEIVE_WAIT_S
                )
            except AzureError as exc:
                recorder.error(TEST, NO_LEVEL, "e2e_receive", exc)
                continue
            received_at = time.perf_counter_ns()
            for message in messages:
                body = json.loads(str(message))
                if body.get("kind") != "e2e":
                    continue  # a leftover from the send steps
                sequence = body["seq"]
                if sequence in seen:
                    counts["duplicates"] += 1
                    continue
                seen.add(sequence)
                counts["received"] += 1
                if sequence < last_sequence:
                    counts["out_of_order"] += 1
                last_sequence = max(last_sequence, sequence)
                latency_ms = (received_at - body["scheduled_ns"]) / 1e6
                recorder.add(TEST, NO_LEVEL, "sb_e2e_ms", latency_ms, received_at)
            await _complete_all(receiver, messages, recorder, timed=False)

    await asyncio.gather(produce(), consume())
    counts["expected"] = per_tick * ticks
    counts["lost"] = counts["sent"] - counts["received"]
    return counts


async def run_servicebus_test(
    *,
    connection: str,
    profile: ServiceBusProfile,
    recorder: Recorder,
    lock_s: int,
    keep_queue: bool = False,
) -> dict[str, Any]:
    """RTT, send, receive/complete and end-to-end latency against a dedicated queue."""
    output: dict[str, Any] = {"queue": None, "e2e": None, "sent": 0, "received": 0}
    try:
        output["queue"] = await asyncio.to_thread(recreate_queue, connection, QUEUE_NAME, lock_s)
        await asyncio.to_thread(recreate_queue, connection, E2E_QUEUE_NAME, lock_s)
        say("[service bus] medindo RTT")
        await _probe_rtt(endpoint_host(connection), profile, recorder)
        async with ServiceBusClient.from_connection_string(connection) as client:
            async with (
                client.get_queue_sender(QUEUE_NAME) as sender,
                client.get_queue_receiver(
                    QUEUE_NAME, receive_mode=ServiceBusReceiveMode.PEEK_LOCK, prefetch_count=0
                ) as receiver,
            ):
                say("[service bus] medindo envio")
                sent = await _send_singles(sender, profile, recorder)
                sent += await _send_batches(sender, profile, recorder)
                say("[service bus] medindo recepção e complete")
                received = await _receive_and_complete(receiver, sent, profile, recorder)
                output.update(sent=sent, received=received)
            say(f"[service bus] ponta a ponta a {profile.e2e_rate_msg_s} msg/s")
            async with (  # its own queue: a slow region may not have drained the one above
                client.get_queue_sender(E2E_QUEUE_NAME) as sender,
                client.get_queue_receiver(
                    E2E_QUEUE_NAME, receive_mode=ServiceBusReceiveMode.PEEK_LOCK, prefetch_count=0
                ) as receiver,
            ):
                output["e2e"] = await _end_to_end(sender, receiver, profile, recorder)
        await asyncio.sleep(QUEUE_SETTLE_S)
        output["e2e"]["left_in_queue"] = await asyncio.to_thread(_active_messages, connection)
    finally:
        if not keep_queue:
            await asyncio.to_thread(drop_queue, connection, QUEUE_NAME)
            await asyncio.to_thread(drop_queue, connection, E2E_QUEUE_NAME)
    return output
