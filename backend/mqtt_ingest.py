"""MQTT ingest worker for AXON Verify.

Subscribes to the organisation-scoped topic tree and turns authorised events
into evidence records. The worker is deliberately dumb about policy: it
validates the transport envelope (topic, device registration, tenant binding),
then hands the payload to :mod:`backend.ingest_service`, which is the same code
the REST ingest endpoint uses.

Run it with::

    python -m backend.mqtt_ingest

Environment (all optional except the broker URL):

    AXON_MQTT_URL            broker host, e.g. tls://localhost
    AXON_MQTT_PORT           default 8883 with TLS, 1883 without
    AXON_MQTT_TOPIC_PREFIX   default axon/v1
    AXON_MQTT_USERNAME / AXON_MQTT_PASSWORD
    AXON_MQTT_CA / AXON_MQTT_CERT / AXON_MQTT_KEY
    AXON_MQTT_INSECURE       1 = allow plaintext or skip certificate checks
    AXON_MQTT_DATABASE       path to the AXON Verify database
    AXON_MQTT_QOS            default 1 (at least once; event_id de-duplicates)
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.ingest_service import (  # noqa: E402  (path bootstrap above)
    IngestRejection,
    handle_transport_event,
    parse_topic,
)
from backend.verify_store import VerifyStore  # noqa: E402

log = logging.getLogger("axon.mqtt")


@dataclass
class MqttSettings:
    host: str = "localhost"
    port: int = 8883
    use_tls: bool = True
    username: str | None = None
    password: str | None = None
    ca_file: str | None = None
    cert_file: str | None = None
    key_file: str | None = None
    insecure: bool = False
    topic_prefix: str = "axon/v1"
    client_id: str = "axon-verify-ingest"
    qos: int = 1
    database: str = field(default_factory=lambda: os.getenv(
        "AXON_VERIFY_DB", str(PROJECT_ROOT / "var" / "axon-verify.db")))
    subscribe_all_tenants: bool = True

    @property
    def url(self) -> str:
        scheme = "tls" if self.use_tls else "tcp"
        return f"{scheme}://{self.host}:{self.port}"

    @property
    def subscription(self) -> str:
        # The worker sees every tenant; it must never trust the topic alone.
        return f"{self.topic_prefix}/+/+/+" if self.subscribe_all_tenants \
            else f"{self.topic_prefix}/+/+/+/+"


def load_settings() -> MqttSettings:
    host = os.getenv("AXON_MQTT_HOST") or os.getenv("AXON_MQTT_URL", "localhost")
    if "://" in host:  # allow a full URL in AXON_MQTT_URL
        host = host.split("://", 1)[1]
    host = host.split("/")[0].split(":")[0]
    use_tls = os.getenv("AXON_MQTT_INSECURE", "0") != "1"
    default_port = 8883 if use_tls else 1883
    return MqttSettings(
        host=host,
        port=int(os.getenv("AXON_MQTT_PORT", str(default_port))),
        use_tls=use_tls,
        username=os.getenv("AXON_MQTT_USERNAME"),
        password=os.getenv("AXON_MQTT_PASSWORD"),
        ca_file=os.getenv("AXON_MQTT_CA"),
        cert_file=os.getenv("AXON_MQTT_CERT"),
        key_file=os.getenv("AXON_MQTT_KEY"),
        insecure=os.getenv("AXON_MQTT_INSECURE", "0") == "1",
        topic_prefix=os.getenv("AXON_MQTT_TOPIC_PREFIX", "axon/v1"),
        client_id=os.getenv("AXON_MQTT_CLIENT_ID", "axon-verify-ingest"),
        qos=int(os.getenv("AXON_MQTT_QOS", "1")),
    )


class MqttIngestWorker:
    """Blocking MQTT subscriber backed by the verification store."""

    def __init__(self, settings: MqttSettings | None = None,
                 store: VerifyStore | None = None,
                 client_factory: Callable | None = None):
        self.settings = settings or load_settings()
        self.store = store or VerifyStore(self.settings.database)
        self._client_factory = client_factory
        self._client = None

    # --------------------------------------------------------------- client

    def build_client(self):
        if self._client_factory is not None:
            return self._client_factory()
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "paho-mqtt is required for the MQTT ingest worker: "
                "pip install paho-mqtt") from exc

        try:
            callback_api = mqtt.CallbackAPIVersion.VERSION2
            client = mqtt.Client(callback_api=callback_api,
                                 client_id=self.settings.client_id,
                                 protocol=mqtt.MQTTv311)
        except AttributeError:  # paho 1.x
            client = mqtt.Client(client_id=self.settings.client_id,
                                 protocol=mqtt.MQTTv311)

        if self.settings.username:
            client.username_pw_set(self.settings.username, self.settings.password)
        if self.settings.use_tls:
            self._configure_tls(client)
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        client.on_disconnect = self.on_disconnect
        self._client = client
        return client

    def _configure_tls(self, client) -> None:
        if self.settings.insecure:
            log.warning("AXON_MQTT_INSECURE=1: TLS certificate validation is DISABLED")
            client.tls_set(ca_certs=self.settings.ca_file,
                           certfile=self.settings.cert_file,
                           keyfile=self.settings.key_file,
                           tls_version=ssl.PROTOCOL_TLS_CLIENT)
            client.tls_insecure_set(True)
            return
        missing = [name for name, value in
                   (("AXON_MQTT_CA", self.settings.ca_file),) if not value]
        if missing:
            raise RuntimeError(
                "TLS is enabled but no CA bundle was configured (set AXON_MQTT_CA), "
                "or set AXON_MQTT_INSECURE=1 for a local plaintext broker")
        client.tls_set(ca_certs=self.settings.ca_file,
                       certfile=self.settings.cert_file,
                       keyfile=self.settings.key_file,
                       tls_version=ssl.PROTOCOL_TLS_CLIENT)
        client.tls_insecure_set(False)

    # ------------------------------------------------------------ callbacks

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        failure = getattr(reason_code, "is_failure", None)
        if callable(failure) and failure:
            log.error("broker refused the connection: %s", reason_code)
            return
        log.info("connected to %s, subscribing to %s (qos %s)",
                 self.settings.url, self.settings.subscription, self.settings.qos)
        client.subscribe(self.settings.subscription, qos=self.settings.qos)

    def on_disconnect(self, client, userdata, *args, **kwargs):
        log.warning("disconnected from the broker")

    def on_message(self, client, userdata, message):
        try:
            result = self.process(message.topic, message.payload)
        except Exception as exc:  # never let one bad message kill the loop
            log.exception("unhandled error processing %s", message.topic)
            result = {"accepted": False, "reason": f"internal error: {exc}"}
        level = log.info if result.get("accepted") else log.warning
        level("message %s -> %s", message.topic, result)

    # -------------------------------------------------------------- ingest

    def process(self, topic: str, payload: bytes) -> dict:
        """Validate and store one MQTT message. Returns a JSON-safe result."""
        address = parse_topic(topic, self.settings.topic_prefix)
        if address is None:
            return {"accepted": False, "reason": "topic is not an AXON evidence topic",
                    "topic": topic}
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {"accepted": False, "reason": f"payload is not valid JSON: {exc}",
                    "topic": topic}
        if not isinstance(document, dict):
            return {"accepted": False, "reason": "payload must be a JSON object",
                    "topic": topic}
        try:
            return handle_transport_event(self.store, address, document, actor="mqtt")
        except IngestRejection as exc:
            return {"accepted": False, "reason": str(exc), "code": exc.code,
                    "topic": topic}
        except Exception as exc:  # pydantic validation, bad numbers, ...
            return {"accepted": False, "reason": f"payload rejected: {exc}",
                    "code": "invalid_payload", "topic": topic}

    # --------------------------------------------------------------- runtime

    def start(self, blocking: bool = True) -> None:
        if self.settings.use_tls and not self.settings.insecure:
            log.info("TLS enabled (CA %s)", self.settings.ca_file or "system defaults")
        client = self.build_client()
        client.connect(self.settings.host, self.settings.port, keepalive=60)
        if blocking:
            try:
                client.loop_forever(retry_first_connection=True)
            except KeyboardInterrupt:  # pragma: no cover - interactive
                log.info("interrupted, disconnecting")
                client.disconnect()
        else:  # pragma: no cover - used by demos
            client.loop_start()

    def stop(self) -> None:  # pragma: no cover - interactive
        if self._client is not None:
            self._client.disconnect()


def main() -> int:  # pragma: no cover - process entry point
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load_settings()
    if not settings.insecure and not settings.use_tls:
        log.warning("plaintext MQTT: set AXON_MQTT_CA and remove AXON_MQTT_INSECURE "
                    "for a production broker")
    worker = MqttIngestWorker(settings)
    log.info("starting AXON Verify MQTT ingest against %s (db %s)",
             settings.url, settings.database)
    worker.start(blocking=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
