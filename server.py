"""Cirklon MCP Server — control a Sequentix Cirklon over USB MIDI."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from mcp.server.fastmcp import Context, FastMCP

from config import CirklonConfig
from midi_engine import MidiEngine

# ---------------------------------------------------------------------------
# Lifespan: initialize engine + optional auto-connect
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    config = CirklonConfig.from_env()
    engine = MidiEngine()

    if config.auto_connect and config.output_port:
        try:
            result = engine.connect(
                output_port=config.output_port,
                input_port=config.input_port or config.output_port,
            )
            print(f"[cirklon-mcp] Auto-connected: {result}")
        except Exception as e:
            print(f"[cirklon-mcp] Auto-connect failed: {e}")

    yield {"engine": engine, "config": config, "clock_task": None}

    # Cleanup
    engine.disconnect()


mcp = FastMCP(
    "cirklon-midi",
    instructions="Control a Sequentix Cirklon hardware MIDI sequencer",
    lifespan=lifespan,
)


def _engine(ctx: Context) -> MidiEngine:
    return ctx.request_context.lifespan_context["engine"]


def _config(ctx: Context) -> CirklonConfig:
    return ctx.request_context.lifespan_context["config"]


def _require_connection(ctx: Context) -> MidiEngine:
    engine = _engine(ctx)
    if not engine.connected:
        raise ValueError(
            "Not connected to Cirklon. Use connect_cirklon first."
        )
    return engine


# ===================================================================
# 1. CONNECTION TOOLS (3)
# ===================================================================


@mcp.tool()
async def list_midi_ports(ctx: Context) -> str:
    """List all available MIDI input and output ports."""
    engine = _engine(ctx)
    loop = asyncio.get_event_loop()
    out_ports = await loop.run_in_executor(None, engine.list_output_ports)
    in_ports = await loop.run_in_executor(None, engine.list_input_ports)

    lines = ["=== Output Ports ==="]
    for i, p in enumerate(out_ports):
        lines.append(f"  [{i}] {p}")
    if not out_ports:
        lines.append("  (none)")

    lines.append("\n=== Input Ports ===")
    for i, p in enumerate(in_ports):
        lines.append(f"  [{i}] {p}")
    if not in_ports:
        lines.append("  (none)")

    return "\n".join(lines)


@mcp.tool()
async def connect_cirklon(
    ctx: Context,
    output_port: str = "",
    input_port: str = "",
) -> str:
    """Connect to Cirklon MIDI ports.

    Args:
        output_port: Port name substring or index (e.g. "Cirklon" or "0").
            If empty, uses CIRKLON_OUTPUT_PORT from .env.
        input_port: Port name substring or index. If empty, matches output.
    """
    engine = _engine(ctx)
    config = _config(ctx)

    out = output_port or config.output_port
    inp = input_port or config.input_port or out

    # Try numeric index
    try:
        out = int(out)
    except (ValueError, TypeError):
        pass
    try:
        inp = int(inp)
    except (ValueError, TypeError):
        pass

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: engine.connect(output_port=out, input_port=inp)
    )
    return result


@mcp.tool()
async def disconnect_cirklon(ctx: Context) -> str:
    """Disconnect from Cirklon MIDI ports."""
    engine = _engine(ctx)
    # Stop clock if running
    clock_task = ctx.request_context.lifespan_context.get("clock_task")
    if clock_task and not clock_task.done():
        clock_task.cancel()
        ctx.request_context.lifespan_context["clock_task"] = None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, engine.disconnect)


# ===================================================================
# 2. TRANSPORT TOOLS (4)
# ===================================================================


@mcp.tool()
async def transport_start(ctx: Context) -> str:
    """Send MIDI Start (0xFA) to begin Cirklon playback from the top."""
    engine = _require_connection(ctx)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, engine.send_start)
    return "Sent MIDI Start (0xFA)"


@mcp.tool()
async def transport_stop(ctx: Context) -> str:
    """Send MIDI Stop (0xFC) to stop Cirklon playback."""
    engine = _require_connection(ctx)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, engine.send_stop)
    return "Sent MIDI Stop (0xFC)"


@mcp.tool()
async def transport_continue(ctx: Context) -> str:
    """Send MIDI Continue (0xFB) to resume Cirklon from current position."""
    engine = _require_connection(ctx)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, engine.send_continue)
    return "Sent MIDI Continue (0xFB)"


@mcp.tool()
async def send_clock(ctx: Context, bpm: float = 0, bars: int = 0) -> str:
    """Start sending MIDI clock at given BPM.

    The Cirklon is usually clock master, but this lets Claude be master
    if needed. Sends 24 pulses per quarter note.

    Args:
        bpm: Tempo in BPM. 0 = stop clock. Default uses config BPM.
        bars: Number of bars to send (0 = continuous until stopped).
    """
    engine = _require_connection(ctx)
    config = _config(ctx)
    lc = ctx.request_context.lifespan_context

    # Cancel existing clock
    existing = lc.get("clock_task")
    if existing and not existing.done():
        existing.cancel()
        lc["clock_task"] = None

    if bpm == 0 and config.default_bpm:
        bpm = config.default_bpm
    if bpm <= 0:
        return "Clock stopped"

    interval = 60.0 / (bpm * 24)
    total_ticks = bars * 24 * 4 if bars > 0 else 0  # assuming 4/4

    async def _clock_loop() -> None:
        ticks = 0
        next_tick = time.perf_counter()
        loop = asyncio.get_event_loop()
        try:
            while True:
                await loop.run_in_executor(None, engine.send_clock)
                ticks += 1
                if total_ticks and ticks >= total_ticks:
                    break
                next_tick += interval
                sleep_for = next_tick - time.perf_counter()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_clock_loop())
    lc["clock_task"] = task

    duration = f" for {bars} bars" if bars else " (continuous)"
    return f"Clock running at {bpm} BPM{duration}"


# ===================================================================
# 3. SCENE / SONG TOOLS (3)
# ===================================================================


@mcp.tool()
async def select_scene(ctx: Context, scene: int) -> str:
    """Select a Cirklon scene (0-127) via Program Change on the remote channel.

    Args:
        scene: Scene number 0-127.
    """
    engine = _require_connection(ctx)
    config = _config(ctx)
    ch = config.remote_channel
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: engine.send_program_change(ch, scene)
    )
    return f"Selected scene {scene} (PC {scene} on ch {ch})"


@mcp.tool()
async def set_transpose(ctx: Context, semitones: int = 0) -> str:
    """Set scene transpose via Note On on the remote channel.

    Args:
        semitones: Semitones offset. 0 = middle C (note 60). Range: -60 to +67.
    """
    engine = _require_connection(ctx)
    config = _config(ctx)
    ch = config.remote_channel
    note = max(0, min(127, 60 + semitones))
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: engine.send_note_on(ch, note, 100)
    )
    return f"Transpose set to {semitones:+d} (note {note} on ch {ch})"


@mcp.tool()
async def send_program_change(
    ctx: Context,
    channel: int,
    program: int,
    bank_msb: int = -1,
    bank_lsb: int = -1,
) -> str:
    """Send Program Change to an instrument channel, with optional bank select.

    Args:
        channel: MIDI channel 1-16.
        program: Program number 0-127.
        bank_msb: Bank select MSB (CC#0). -1 to skip.
        bank_lsb: Bank select LSB (CC#32). -1 to skip.
    """
    engine = _require_connection(ctx)
    loop = asyncio.get_event_loop()

    if bank_msb >= 0:
        lsb = bank_lsb if bank_lsb >= 0 else None
        await loop.run_in_executor(
            None, lambda: engine.send_bank_select(channel, bank_msb, lsb)
        )

    await loop.run_in_executor(
        None, lambda: engine.send_program_change(channel, program)
    )

    parts = [f"PC {program} on ch {channel}"]
    if bank_msb >= 0:
        parts.append(f"bank MSB={bank_msb}")
    if bank_lsb >= 0:
        parts.append(f"LSB={bank_lsb}")
    return "Sent " + ", ".join(parts)


# ===================================================================
# 4. NOTE / CC TOOLS (5)
# ===================================================================


@mcp.tool()
async def send_note(
    ctx: Context,
    channel: int,
    note: int,
    velocity: int = 100,
    duration_ms: int = 500,
) -> str:
    """Send a note (Note On, wait, Note Off) to an instrument channel.

    Args:
        channel: MIDI channel 1-16.
        note: MIDI note number 0-127 (60 = middle C).
        velocity: Note velocity 0-127.
        duration_ms: Note duration in milliseconds.
    """
    engine = _require_connection(ctx)
    loop = asyncio.get_event_loop()

    await loop.run_in_executor(
        None, lambda: engine.send_note_on(channel, note, velocity)
    )
    await asyncio.sleep(duration_ms / 1000.0)
    await loop.run_in_executor(
        None, lambda: engine.send_note_off(channel, note)
    )
    return f"Note {note} vel={velocity} dur={duration_ms}ms on ch {channel}"


@mcp.tool()
async def send_cc(
    ctx: Context, channel: int, cc: int, value: int
) -> str:
    """Send a MIDI CC message to an instrument channel.

    Args:
        channel: MIDI channel 1-16.
        cc: CC number 0-127.
        value: CC value 0-127.
    """
    engine = _require_connection(ctx)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: engine.send_cc(channel, cc, value)
    )
    return f"CC {cc}={value} on ch {channel}"


@mcp.tool()
async def send_pitch_bend(
    ctx: Context, channel: int, value: int = 8192
) -> str:
    """Send pitch bend to an instrument channel.

    Args:
        channel: MIDI channel 1-16.
        value: Pitch bend value 0-16383 (8192 = center/no bend).
    """
    engine = _require_connection(ctx)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: engine.send_pitch_bend(channel, value)
    )
    return f"Pitch bend {value} on ch {channel}"


@mcp.tool()
async def send_aftertouch(
    ctx: Context, channel: int, value: int
) -> str:
    """Send channel aftertouch (pressure) to an instrument channel.

    Args:
        channel: MIDI channel 1-16.
        value: Pressure value 0-127.
    """
    engine = _require_connection(ctx)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: engine.send_aftertouch(channel, value)
    )
    return f"Aftertouch {value} on ch {channel}"


@mcp.tool()
async def send_poly_aftertouch(
    ctx: Context, channel: int, note: int, value: int
) -> str:
    """Send polyphonic aftertouch (per-note pressure).

    Args:
        channel: MIDI channel 1-16.
        note: MIDI note number 0-127.
        value: Pressure value 0-127.
    """
    engine = _require_connection(ctx)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: engine.send_poly_aftertouch(channel, note, value)
    )
    return f"Poly aftertouch note={note} val={value} on ch {channel}"


# ===================================================================
# 5. RECORDING TOOLS (2)
# ===================================================================


@mcp.tool()
async def record_notes(
    ctx: Context,
    channel: int,
    notes: list[dict],
) -> str:
    """Send a sequence of notes with timing for real-time recording into Cirklon.

    Put Cirklon in record mode first (CK/P3 track arm + transport).

    Each note dict has: note (int), velocity (int, default 100),
    duration_ms (int, default 250), gap_ms (int, default 0 = legato).

    Args:
        channel: MIDI channel 1-16.
        notes: List of note dicts, e.g. [{"note": 60}, {"note": 64, "velocity": 80}].
    """
    engine = _require_connection(ctx)
    loop = asyncio.get_event_loop()
    count = 0

    for n in notes:
        pitch = n["note"]
        vel = n.get("velocity", 100)
        dur = n.get("duration_ms", 250)
        gap = n.get("gap_ms", 0)

        await loop.run_in_executor(
            None, lambda p=pitch, v=vel: engine.send_note_on(channel, p, v)
        )
        await asyncio.sleep(dur / 1000.0)
        await loop.run_in_executor(
            None, lambda p=pitch: engine.send_note_off(channel, p)
        )
        if gap > 0:
            await asyncio.sleep(gap / 1000.0)
        count += 1

    return f"Recorded {count} notes on ch {channel}"


@mcp.tool()
async def record_cc_stream(
    ctx: Context,
    channel: int,
    cc: int,
    values: list[int],
    interval_ms: int = 50,
) -> str:
    """Send a stream of CC values at regular intervals for automation recording.

    Args:
        channel: MIDI channel 1-16.
        cc: CC number 0-127.
        values: List of CC values to send in sequence.
        interval_ms: Time between each CC message in milliseconds.
    """
    engine = _require_connection(ctx)
    loop = asyncio.get_event_loop()

    for val in values:
        await loop.run_in_executor(
            None, lambda v=val: engine.send_cc(channel, cc, v)
        )
        await asyncio.sleep(interval_ms / 1000.0)

    return f"Sent {len(values)} CC{cc} values on ch {channel} at {interval_ms}ms intervals"


# ===================================================================
# 6. SYSEX TOOLS (3)
# ===================================================================


@mcp.tool()
async def send_sysex(ctx: Context, data: list[int]) -> str:
    """Send a raw SysEx message. Auto-frames with F0/F7 if needed.

    Args:
        data: List of byte values, e.g. [0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7].
    """
    engine = _require_connection(ctx)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: engine.send_sysex(data))
    return f"Sent SysEx ({len(data)} bytes)"


@mcp.tool()
async def receive_sysex(ctx: Context, timeout: float = 5.0) -> str:
    """Listen for a single incoming SysEx message with timeout.

    Args:
        timeout: Seconds to wait (default 5).
    """
    engine = _require_connection(ctx)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: engine.wait_for_sysex(timeout)
    )
    if result is None:
        return f"No SysEx received within {timeout}s"
    hex_str = " ".join(f"{b:02X}" for b in result)
    return f"Received SysEx ({len(result)} bytes): {hex_str}"


@mcp.tool()
async def sysex_dump_receive(
    ctx: Context,
    filename: str = "cirklon_dump.syx",
    timeout: float = 120.0,
    idle_timeout: float = 5.0,
) -> str:
    """Receive a full Cirklon SysEx memory dump and save to file.

    Initiate the dump from Cirklon's GLOBAL > SYSEX > SEND menu first.
    Collects all SysEx messages until no new data arrives for idle_timeout seconds.

    Args:
        filename: Output filename (saved in working directory).
        timeout: Maximum total wait time in seconds.
        idle_timeout: Stop after this many seconds of no new SysEx.
    """
    engine = _require_connection(ctx)
    engine.start_sysex_collection()

    start = time.time()
    last_count = 0
    last_activity = time.time()

    try:
        while True:
            await asyncio.sleep(0.5)
            buf = engine._sysex_buffer
            current_count = len(buf)

            if current_count > last_count:
                last_count = current_count
                last_activity = time.time()

            elapsed = time.time() - start
            idle = time.time() - last_activity

            if elapsed > timeout:
                break
            if current_count > 0 and idle > idle_timeout:
                break
    finally:
        messages = engine.stop_sysex_collection()

    if not messages:
        return "No SysEx data received"

    # Write binary .syx file
    raw = bytearray()
    for msg in messages:
        raw.extend(msg)

    path = Path(filename)
    path.write_bytes(raw)
    size_kb = len(raw) / 1024

    return (
        f"Saved {len(messages)} SysEx messages ({size_kb:.1f} KB) to {path}"
    )


# ===================================================================
# 7. MONITOR TOOLS (3)
# ===================================================================


@mcp.tool()
async def start_midi_monitor(ctx: Context) -> str:
    """Start logging incoming MIDI messages to a ring buffer (1000 max)."""
    engine = _require_connection(ctx)
    engine.start_monitor()
    return f"MIDI monitor started on {engine.port_info}"


@mcp.tool()
async def stop_midi_monitor(ctx: Context) -> str:
    """Stop logging incoming MIDI messages."""
    engine = _require_connection(ctx)
    engine.stop_monitor()
    return "MIDI monitor stopped"


@mcp.tool()
async def get_midi_log(
    ctx: Context, count: int = 50, type_filter: str = ""
) -> str:
    """Get recent MIDI messages from the monitor log.

    Args:
        count: Number of messages to return (default 50, max 200).
        type_filter: Filter by message type substring (e.g. "note", "cc", "sysex").
    """
    engine = _require_connection(ctx)
    count = min(count, 200)
    filt = type_filter if type_filter else None
    msgs = engine.get_log(count=count, type_filter=filt)

    if not msgs:
        status = "monitoring" if engine.is_monitoring else "not monitoring"
        return f"No messages in log ({status})"

    lines = [f"=== MIDI Log ({len(msgs)} messages) ==="]
    for m in msgs:
        t = time.strftime("%H:%M:%S", time.localtime(m.timestamp))
        ms = int((m.timestamp % 1) * 1000)
        lines.append(f"  [{t}.{ms:03d}] {m.description}")

    return "\n".join(lines)


# ===================================================================
# Entry point
# ===================================================================


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
