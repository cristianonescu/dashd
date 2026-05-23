"""End-to-end install pipeline with a fake transport."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import zipfile

import httpx
import pytest
from PIL import Image

from dashd.pets import CACHE_DIR, install


def _make_zip(states: list[str], rows: int, cols: int, cell: int) -> bytes:
    sheet = Image.new("RGBA", (cols * cell, rows * cell), (0, 0, 0, 0))
    for r in range(rows):
        for c in range(cols):
            pad = cell // 4
            block = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
            for y in range(pad, cell - pad):
                for x in range(pad, cell - pad):
                    block.putpixel((x, y), (200, 100, 50, 255))
            sheet.paste(block, (c * cell, r * cell), block)
    sheet_buf = io.BytesIO(); sheet.save(sheet_buf, format="WEBP")

    manifest = {"id": "synthetic", "displayName": "Synthetic",
                "grid": {"rows": rows, "cols": cols}, "states": states}
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("pet.json", json.dumps(manifest))
        zf.writestr("spritesheet.webp", sheet_buf.getvalue())
    return z.getvalue()


@pytest.mark.asyncio
async def test_install_streams_chunks_to_device(tmp_path, monkeypatch):
    monkeypatch.setattr("dashd.pets.CACHE_DIR", tmp_path / "pets")
    monkeypatch.setattr("dashd.pets.install.CACHE_DIR", tmp_path / "pets")

    zip_bytes = _make_zip(["idle", "wave"], rows=2, cols=3, cell=64)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=zip_bytes)

    sent: list[dict] = []

    async def send_cmd(cmd):
        sent.append(cmd)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        slug = await install.install("synthetic", send_cmd, client=client)

    assert slug == "synthetic"
    names = [c["name"] for c in sent]
    assert names[0] == "pet_install_start"
    assert names[-1] == "pet_install_end"
    chunk_msgs = [c for c in sent if c["name"] == "pet_install_chunk"]
    assert len(chunk_msgs) >= 1
    # Chunks are in sequence and base64-decode cleanly.
    for i, c in enumerate(chunk_msgs):
        assert c["seq"] == i
        base64.b64decode(c["data"])  # raises if malformed


@pytest.mark.asyncio
async def test_install_reuses_cache(tmp_path, monkeypatch):
    cache = tmp_path / "pets"
    monkeypatch.setattr("dashd.pets.CACHE_DIR", cache)
    monkeypatch.setattr("dashd.pets.install.CACHE_DIR", cache)
    cache.mkdir(parents=True)
    # Hand-craft a valid .dpet on disk so the network never gets touched.
    from dashd.pets.converter import convert
    from dashd.pets.downloader import PetBundle
    zip_bytes = _make_zip(["idle"], rows=1, cols=2, cell=32)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        manifest = json.loads(zf.read("pet.json"))
        sheet = zf.read("spritesheet.webp")
    bundle = PetBundle(slug="cached", manifest=manifest, spritesheet_bytes=sheet)
    dpet = convert(bundle, frame_w=16, frame_h=16)
    (cache / "cached.dpet").write_bytes(dpet.raw)

    # Network must NOT be contacted — if it is, the test fails loudly.
    def fail_handler(req): raise AssertionError("network was used")
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail_handler)) as client:
        entry, hit = await install.fetch_and_convert("cached", client=client)
    assert entry.slug == "cached"
    assert hit.raw == dpet.raw


@pytest.mark.asyncio
async def test_install_rejects_unknown_url():
    with pytest.raises(ValueError):
        await install.fetch_and_convert("https://elsewhere.example/foo", client=None)


@pytest.mark.asyncio
async def test_stream_ack_windowed_waits_for_acks():
    """With wait_ack supplied, the sender keeps at most `window` chunks
    outstanding and resolves each via the ACK awaitable."""
    raw = bytes(range(256)) * 40   # ~10 KB → several chunks
    sent: list[dict] = []
    acked: list[int] = []

    async def send_cmd(cmd):
        sent.append(cmd)

    async def _ack(seq: int):
        acked.append(seq)
        return {"name": "pet_install_chunk_ack", "seq": seq, "ok": True}

    def wait_ack(seq: int):
        return _ack(seq)

    await install.stream_to_device("synthetic", raw, send_cmd,
                                   wait_ack=wait_ack, window=4, chunk_bytes=2048)

    chunks = [c for c in sent if c["name"] == "pet_install_chunk"]
    assert [c["seq"] for c in chunks] == list(range(len(chunks)))
    # Every chunk's ACK was awaited.
    assert sorted(acked) == list(range(len(chunks)))
    assert sent[0]["name"] == "pet_install_start"
    assert sent[-1]["name"] == "pet_install_end"


@pytest.mark.asyncio
async def test_stream_ack_window_bounds_outstanding_chunks():
    """At most `window` chunks may be in flight before an ACK is awaited."""
    raw = b"x" * (2048 * 10)        # 10 chunks
    order: list[str] = []

    async def send_cmd(cmd):
        if cmd["name"] == "pet_install_chunk":
            order.append(f"send{cmd['seq']}")

    pending_unblocked: list[int] = []

    def wait_ack(seq: int):
        async def _a():
            order.append(f"ack{seq}")
            pending_unblocked.append(seq)
            return {"seq": seq}
        return _a()

    await install.stream_to_device("p", raw, send_cmd,
                                   wait_ack=wait_ack, window=3, chunk_bytes=2048)
    # With window=3, the 4th send must come only after ack0, etc. Check that
    # ack0 appears before send3 in the interleaving.
    assert order.index("ack0") < order.index("send3")
    assert sorted(pending_unblocked) == list(range(10))


@pytest.mark.asyncio
async def test_stream_aborts_when_pet_install_started_ok_false():
    """Reproducer for the v0.1.8 bug: when the device's LittleFS mount
    fails, it replies to `pet_install_start` with ok=false. The streamer
    must abort BEFORE sending any chunks — previously it slept 50 ms,
    blindly streamed every chunk, and flooded the device with "chunk
    without start" rejects."""
    raw = b"x" * (2048 * 5)
    sent: list[dict] = []

    async def send_cmd(cmd):
        sent.append(cmd)

    async def wait_started():
        return {"name": "pet_install_started", "slug": "p", "ok": False}

    with pytest.raises(RuntimeError, match="rejected pet_install_start"):
        await install.stream_to_device("p", raw, send_cmd,
                                       wait_started=wait_started,
                                       chunk_bytes=2048)

    # Only the START cmd was sent — no chunks, no end.
    assert [c["name"] for c in sent] == ["pet_install_start"]


@pytest.mark.asyncio
async def test_stream_eagerly_aborts_on_chunk0_failure():
    """When chunk 0's ACK comes back ok=false, the streamer must abort
    fast — not wait for the window (default 8) to fill before noticing.
    With eager done-waiter drain, the failure surfaces by the time we'd
    send chunk 2 or 3, not 8."""
    raw = b"x" * (2048 * 20)   # 20 chunks
    sent_chunks: list[int] = []

    async def send_cmd(cmd):
        if cmd["name"] == "pet_install_chunk":
            sent_chunks.append(cmd["seq"])

    # Simulate: chunk 0 fails immediately, all others would succeed.
    # The "immediately" matters — the fail-ACK is available before chunk 1
    # is even queued, so the eager drain should catch it.
    async def _ack(seq: int):
        return {"name": "pet_install_chunk_ack", "seq": seq,
                "ok": (seq != 0)}

    def wait_ack(seq: int):
        return _ack(seq)

    with pytest.raises(RuntimeError, match="rejected pet chunk 0"):
        await install.stream_to_device("p", raw, send_cmd,
                                       wait_ack=wait_ack,
                                       window=8, chunk_bytes=2048)

    # With eager drain the failure is caught well before the window
    # fills. Allow some slack for scheduler ordering but require it's
    # nowhere near the old 8-chunk threshold.
    assert len(sent_chunks) <= 4, (
        f"expected ≤4 chunks before abort, got {len(sent_chunks)}: "
        f"{sent_chunks}")


@pytest.mark.asyncio
async def test_stream_aborts_when_wait_started_times_out():
    """If the device never replies to pet_install_start (e.g. firmware
    crashed before emitting the event, or link dropped), wait_started
    raises TimeoutError. The streamer must propagate it without
    sending any chunks."""
    raw = b"x" * 2048
    sent: list[dict] = []

    async def send_cmd(cmd):
        sent.append(cmd)

    async def wait_started():
        # Caller wraps in asyncio.wait_for; simulate by raising directly.
        raise asyncio.TimeoutError("no pet_install_started")

    with pytest.raises(asyncio.TimeoutError):
        await install.stream_to_device("p", raw, send_cmd,
                                       wait_started=wait_started,
                                       chunk_bytes=2048)

    assert [c["name"] for c in sent] == ["pet_install_start"]


def test_purge_tx_queue_drops_only_matching_commands():
    """`_purge_tx_queue` must drop only commands matching the predicate,
    preserving order of survivors. Used to stop a failed pet install
    from flooding the device with already-queued chunks."""
    import asyncio
    from dashd.main import AgentRuntime  # type: ignore

    # We don't need a real runtime — just enough state to exercise the
    # method. Build a stub that has a real asyncio.Queue.
    class Stub:
        _purge_tx_queue = AgentRuntime._purge_tx_queue

    s = Stub()
    s._tx_queue = asyncio.Queue(maxsize=10)

    # Mix install chunks with unrelated state/cmds.
    cmds = [
        {"type": "cmd", "name": "set_brightness", "value": 80},
        {"type": "cmd", "name": "pet_install_chunk", "seq": 0},
        {"type": "cmd", "name": "pet_install_chunk", "seq": 1},
        {"type": "state", "system": {}},
        {"type": "cmd", "name": "pet_install_end"},
        {"type": "cmd", "name": "show_page", "page": "Pet"},
    ]
    for c in cmds:
        s._tx_queue.put_nowait(c)

    purged = s._purge_tx_queue(
        lambda c: isinstance(c, dict)
        and str(c.get("name") or "").startswith("pet_install_"))

    assert purged == 3
    survivors = []
    while not s._tx_queue.empty():
        survivors.append(s._tx_queue.get_nowait())
    # Survivors keep their relative order; no pet_install_* present.
    assert [c.get("name") or c.get("type") for c in survivors] == [
        "set_brightness", "state", "show_page",
    ]
