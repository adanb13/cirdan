"""The Always ON process: watch, refresh, detect, export — forever.

Every loop is supervised: an exception inside one loop is logged to the audit
trail and the loop restarts after a backoff; it never takes the daemon down.
"""

from __future__ import annotations

import asyncio
import contextlib

from cirdan.adapters.registry import get_adapters
from cirdan.engine import CirdanEngine
from cirdan.telemetry.events import docker_event_to_event, k8s_event_to_event

SUPERVISOR_BACKOFF = 10.0
SIGNIFICANT_DOCKER_ACTIONS = {"die", "oom", "kill", "health_status: unhealthy", "stop", "start", "restart"}


class CirdanDaemon:
    def __init__(self, engine: CirdanEngine, on_event=None):
        self.engine = engine
        self.on_event = on_event  # optional callback(dict) for foreground watch mode
        self._wake_incidents = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self.running = False

    # -- loop bodies -----------------------------------------------------------

    async def _access_loop(self) -> None:
        while True:
            await asyncio.to_thread(self.engine.refresh_access)
            await asyncio.sleep(self.engine.config.daemon.access_interval)

    async def _fingerprint_loop(self) -> None:
        while True:
            await asyncio.to_thread(self.engine.refresh_fingerprint)
            await asyncio.sleep(self.engine.config.daemon.fingerprint_interval)

    async def _graph_loop(self) -> None:
        while True:
            await asyncio.to_thread(self.engine.discover)
            await asyncio.sleep(self.engine.config.daemon.graph_interval)

    async def _incident_loop(self) -> None:
        from cirdan.incidents.responder import IncidentResponder

        responder = IncidentResponder(self.engine)
        while True:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._wake_incidents.wait(),
                    timeout=self.engine.config.daemon.incident_interval,
                )
            self._wake_incidents.clear()
            touched = await asyncio.to_thread(self.engine.detect_incidents)
            for incident in touched:
                if self.on_event:
                    self.on_event({"kind": "incident", "incident": incident.model_dump()})
                if responder.should_respond(incident):
                    self._tasks.append(
                        asyncio.get_running_loop().create_task(responder.invoke(incident))
                    )
                elif incident.status == "resolved":
                    await asyncio.to_thread(responder.notify, incident, "resolved")

    async def _export_loop(self) -> None:
        while True:
            await asyncio.sleep(self.engine.config.daemon.export_interval)
            await asyncio.to_thread(self.engine.export_artifacts)
            await asyncio.to_thread(self.engine.events.prune)

    async def _watch_loop(self, adapter) -> None:
        async for raw in adapter.watch():
            if adapter.name == "docker":
                event = docker_event_to_event(raw)
                significant = raw.get("action") in SIGNIFICANT_DOCKER_ACTIONS and raw.get("type") == "container"
            elif adapter.name == "kubernetes":
                event = k8s_event_to_event(raw)
                significant = event.severity != "info"
            else:
                continue
            if event.severity != "info" or significant:
                await asyncio.to_thread(self.engine.events.add, event)
            if self.on_event:
                self.on_event({"kind": "event", "event": event.model_dump()})
            if significant:
                self._wake_incidents.set()

    # -- supervision -------------------------------------------------------------

    async def _supervised(self, name: str, coro_factory) -> None:
        while True:
            try:
                await coro_factory()
                return  # loop finished cleanly (watch stream ended, etc.) — restart below
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.engine.audit.write("daemon-error", f"loop '{name}' crashed: {exc}")
            await asyncio.sleep(SUPERVISOR_BACKOFF)

    def _spawn(self, name: str, coro_factory) -> None:
        self._tasks.append(asyncio.get_running_loop().create_task(self._supervised(name, coro_factory)))

    async def start(self) -> None:
        engine = self.engine
        engine.audit.write("daemon", "cirdand starting", root=str(engine.config.root_path))
        await asyncio.to_thread(engine.refresh_access)
        await asyncio.to_thread(engine.refresh_fingerprint)
        await asyncio.to_thread(engine.discover)
        await asyncio.to_thread(engine.export_artifacts)

        self._spawn("access", self._access_loop)
        self._spawn("fingerprint", self._fingerprint_loop)
        self._spawn("graph", self._graph_loop)
        self._spawn("incidents", self._incident_loop)
        self._spawn("export", self._export_loop)
        for adapter in get_adapters(engine.config, engine.access, kind="live"):
            if adapter.name in ("docker", "kubernetes"):
                self._spawn(f"watch-{adapter.name}", lambda a=adapter: self._watch_loop(a))
        self.running = True
        engine.audit.write("daemon", f"cirdand running with {len(self._tasks)} loops")

    async def stop(self) -> None:
        self.running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.engine.audit.write("daemon", "cirdand stopped")

    async def run_forever(self) -> None:
        await self.start()
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
