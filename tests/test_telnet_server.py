"""Telnet spot server: stalled clients are dropped, reading clients are kept."""

import asyncio

import pytest

from telnet_server import SpotTelnetServer

SMALL_BUFFER = 10_000
MAX_SPOTS = 500_000


async def _start() -> tuple[SpotTelnetServer, int]:
    server = SpotTelnetServer(host='127.0.0.1', port=0, callsign='N0CALL')
    await server.start()
    assert server._server is not None
    port = server._server.sockets[0].getsockname()[1]
    return server, port


async def _login(port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    await reader.readuntil(b"callsign:\r\n")
    writer.write(b"N0TEST\r\n")
    await reader.readuntil(b"CwSkimmer >\r\n")
    return reader, writer


async def _wait_for_login(server: SpotTelnetServer) -> None:
    for _ in range(100):
        if server.client_count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("client never logged in")


def test_stalled_client_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SpotTelnetServer, 'MAX_CLIENT_BUFFER', SMALL_BUFFER)

    async def run() -> int:
        server, port = await _start()
        _, writer = await _login(port)  # logs in, then never reads again
        await _wait_for_login(server)
        sent = 0
        while server.client_count and sent < MAX_SPOTS:
            server.broadcast_spot(14_025.0, 'W1AW', snr=20, wpm=25)
            sent += 1
            if sent % 100 == 0:
                await asyncio.sleep(0)
        remaining = server.client_count
        writer.close()
        await server.stop()
        return remaining

    assert asyncio.run(run()) == 0


def test_reading_client_is_kept() -> None:
    async def run() -> tuple[int, bytes]:
        server, port = await _start()
        reader, writer = await _login(port)
        await _wait_for_login(server)
        for _ in range(50):
            server.broadcast_spot(14_025.0, 'W1AW', snr=20, wpm=25)
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        remaining = server.client_count
        writer.close()
        await server.stop()
        return remaining, line

    remaining, line = asyncio.run(run())
    assert remaining == 1
    assert b"W1AW" in line
