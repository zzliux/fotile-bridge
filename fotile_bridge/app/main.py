"""入口：启动最小云 + MQTT 桥。"""
from __future__ import annotations

import asyncio
import logging
import os

from .cloud import CloudServer
from .cloud_mirror import CloudMirror
from .relay import MqttRelay
from .config import load_config
from .mqtt_bridge import MqttBridge
from .profile import ProfileStore
from .tsl import TslStore

LOG = logging.getLogger(__name__)


async def run() -> None:
    cfg = load_config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    here = os.path.dirname(os.path.abspath(__file__))
    profiles = ProfileStore(os.path.join(here, "profiles"))
    tsl = TslStore(cfg.data_dir)
    tsl.seed_from(os.path.join(here, "tsl_seed"))

    cloud = CloudServer(cfg, tsl)
    await cloud.start()

    bridge = MqttBridge(cfg, profiles)

    mirror = CloudMirror(cfg, bridge.publish)
    bridge.mirror = mirror

    relay = MqttRelay(cfg, bridge.publish) if cfg.relay_enabled else None

    try:
        bridge.start()
    except Exception as exc:  # noqa: BLE001
        LOG.error("MQTT bridge failed to start: %s", exc)

    if relay is not None:
        try:
            await relay.start()
        except Exception as exc:  # noqa: BLE001
            LOG.error("mqtt relay failed to start: %s", exc)
    else:
        try:
            mirror.start()
        except Exception as exc:  # noqa: BLE001
            LOG.error("cloud mirror failed to start: %s", exc)

    LOG.info("fotile bridge ready")
    await asyncio.Event().wait()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
