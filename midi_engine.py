"""MIDI I/O abstraction wrapping python-rtmidi for Cirklon control."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import rtmidi


@dataclass
class MidiMessage:
    """Parsed MIDI message with timestamp."""

    raw: list[int]
    timestamp: float
    type: str = ""
    channel: int | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            self.type, self.channel, self.description = _parse_message(self.raw)


def _parse_message(data: list[int]) -> tuple[str, int | None, str]:
    """Parse raw MIDI bytes into type, channel, description."""
    if not data:
        return "unknown", None, "empty"

    status = data[0]

    # System messages
    if status == 0xFA:
        return "start", None, "Start"
    if status == 0xFB:
        return "continue", None, "Continue"
    if status == 0xFC:
        return "stop", None, "Stop"
    if status == 0xF8:
        return "clock", None, "Clock"
    if status == 0xFE:
        return "active_sense", None, "Active Sensing"
    if status == 0xF0:
        return "sysex", None, f"SysEx ({len(data)} bytes)"

    # Channel messages
    msg_type = status & 0xF0
    ch = (status & 0x0F) + 1  # 1-based

    if msg_type == 0x90 and len(data) >= 3:
        vel = data[2]
        if vel == 0:
            return "note_off", ch, f"Note Off ch{ch} note={data[1]}"
        return "note_on", ch, f"Note On ch{ch} note={data[1]} vel={vel}"
    if msg_type == 0x80 and len(data) >= 3:
        return "note_off", ch, f"Note Off ch{ch} note={data[1]} vel={data[2]}"
    if msg_type == 0xB0 and len(data) >= 3:
        return "cc", ch, f"CC ch{ch} cc={data[1]} val={data[2]}"
    if msg_type == 0xC0 and len(data) >= 2:
        return "program_change", ch, f"PC ch{ch} prog={data[1]}"
    if msg_type == 0xE0 and len(data) >= 3:
        val = data[1] | (data[2] << 7)
        return "pitch_bend", ch, f"PitchBend ch{ch} val={val}"
    if msg_type == 0xD0 and len(data) >= 2:
        return "aftertouch", ch, f"Aftertouch ch{ch} val={data[1]}"
    if msg_type == 0xA0 and len(data) >= 3:
        return "poly_aftertouch", ch, f"PolyAT ch{ch} note={data[1]} val={data[2]}"

    return "unknown", None, f"Unknown 0x{status:02X}"


class MidiEngine:
    """Thread-safe MIDI I/O wrapper for Cirklon communication."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._midi_out: rtmidi.MidiOut | None = None
        self._midi_in: rtmidi.MidiIn | None = None
        self._out_port_name: str = ""
        self._in_port_name: str = ""
        self._connected = False

        # Monitoring
        self._monitoring = False
        self._message_log: deque[MidiMessage] = deque(maxlen=1000)

        # SysEx accumulation
        self._sysex_buffer: list[list[int]] = []
        self._sysex_event = threading.Event()
        self._collecting_sysex = False

    # -- Port Discovery --

    def list_output_ports(self) -> list[str]:
        tmp = rtmidi.MidiOut()
        try:
            return tmp.get_ports()
        finally:
            del tmp

    def list_input_ports(self) -> list[str]:
        tmp = rtmidi.MidiIn()
        try:
            return tmp.get_ports()
        finally:
            del tmp

    def _find_port(self, ports: list[str], query: str) -> int | None:
        """Find port index by case-insensitive substring match."""
        q = query.lower()
        for i, name in enumerate(ports):
            if q in name.lower():
                return i
        return None

    # -- Connection --

    def connect(
        self,
        output_port: str | int = "",
        input_port: str | int = "",
    ) -> str:
        """Connect to MIDI ports. Accepts port name (substring) or index."""
        with self._lock:
            if self._connected:
                self.disconnect()

            # Output
            self._midi_out = rtmidi.MidiOut()
            out_ports = self._midi_out.get_ports()

            if isinstance(output_port, int):
                out_idx = output_port
            elif output_port:
                out_idx = self._find_port(out_ports, output_port)
                if out_idx is None:
                    raise ValueError(
                        f"Output port '{output_port}' not found. "
                        f"Available: {out_ports}"
                    )
            else:
                raise ValueError(
                    f"No output port specified. Available: {out_ports}"
                )

            self._midi_out.open_port(out_idx)
            self._out_port_name = out_ports[out_idx]

            # Input
            self._midi_in = rtmidi.MidiIn()
            self._midi_in.ignore_types(
                sysex=False, timing=False, active_sense=True
            )
            in_ports = self._midi_in.get_ports()

            if isinstance(input_port, int):
                in_idx = input_port
            elif input_port:
                in_idx = self._find_port(in_ports, input_port)
                if in_idx is None:
                    raise ValueError(
                        f"Input port '{input_port}' not found. "
                        f"Available: {in_ports}"
                    )
            else:
                # Try same query as output
                in_idx = self._find_port(in_ports, output_port if isinstance(output_port, str) else "")
                if in_idx is None and in_ports:
                    in_idx = 0

            if in_idx is not None:
                self._midi_in.open_port(in_idx)
                self._in_port_name = in_ports[in_idx]
                self._midi_in.set_callback(self._input_callback)
            else:
                self._in_port_name = "(none)"

            self._connected = True
            return (
                f"Connected: out='{self._out_port_name}', "
                f"in='{self._in_port_name}'"
            )

    def disconnect(self) -> str:
        """Disconnect from MIDI ports."""
        with self._lock:
            self._monitoring = False
            if self._midi_out:
                self._midi_out.close_port()
                del self._midi_out
                self._midi_out = None
            if self._midi_in:
                self._midi_in.close_port()
                del self._midi_in
                self._midi_in = None
            self._connected = False
            self._out_port_name = ""
            self._in_port_name = ""
            return "Disconnected"

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def port_info(self) -> str:
        if not self._connected:
            return "Not connected"
        return f"out='{self._out_port_name}', in='{self._in_port_name}'"

    # -- Input Callback --

    def _input_callback(
        self, event: tuple[list[int], float], data: Any = None
    ) -> None:
        message, delta = event

        if self._monitoring:
            msg = MidiMessage(raw=message, timestamp=time.time())
            self._message_log.append(msg)

        # SysEx collection
        if self._collecting_sysex and message and message[0] == 0xF0:
            self._sysex_buffer.append(message)
            self._sysex_event.set()

    # -- Send Helpers --

    def _send(self, message: list[int]) -> None:
        """Send raw MIDI message (thread-safe)."""
        with self._lock:
            if not self._connected or not self._midi_out:
                raise RuntimeError("Not connected to MIDI output")
            self._midi_out.send_message(message)

    # -- Transport --

    def send_start(self) -> None:
        self._send([0xFA])

    def send_stop(self) -> None:
        self._send([0xFC])

    def send_continue(self) -> None:
        self._send([0xFB])

    def send_clock(self) -> None:
        self._send([0xF8])

    # -- Channel Messages --

    def send_note_on(self, channel: int, note: int, velocity: int = 100) -> None:
        """Send Note On. Channel 1-16, note 0-127, velocity 0-127."""
        self._send([0x90 | (channel - 1), note & 0x7F, velocity & 0x7F])

    def send_note_off(self, channel: int, note: int, velocity: int = 0) -> None:
        self._send([0x80 | (channel - 1), note & 0x7F, velocity & 0x7F])

    def send_cc(self, channel: int, cc: int, value: int) -> None:
        self._send([0xB0 | (channel - 1), cc & 0x7F, value & 0x7F])

    def send_program_change(self, channel: int, program: int) -> None:
        self._send([0xC0 | (channel - 1), program & 0x7F])

    def send_pitch_bend(self, channel: int, value: int = 8192) -> None:
        """Send pitch bend. value: 0-16383, center=8192."""
        value = max(0, min(16383, value))
        lsb = value & 0x7F
        msb = (value >> 7) & 0x7F
        self._send([0xE0 | (channel - 1), lsb, msb])

    def send_aftertouch(self, channel: int, value: int) -> None:
        self._send([0xD0 | (channel - 1), value & 0x7F])

    def send_poly_aftertouch(
        self, channel: int, note: int, value: int
    ) -> None:
        self._send([0xA0 | (channel - 1), note & 0x7F, value & 0x7F])

    def send_bank_select(
        self, channel: int, bank_msb: int, bank_lsb: int | None = None
    ) -> None:
        """Send bank select CC#0 (and optionally CC#32) before a PC."""
        self.send_cc(channel, 0, bank_msb)
        if bank_lsb is not None:
            self.send_cc(channel, 32, bank_lsb)

    # -- SysEx --

    def send_sysex(self, data: list[int]) -> None:
        """Send SysEx. Auto-frames with F0/F7 if not present."""
        if data[0] != 0xF0:
            data = [0xF0] + data
        if data[-1] != 0xF7:
            data = data + [0xF7]
        self._send(data)

    def start_sysex_collection(self) -> None:
        """Begin collecting incoming SysEx messages."""
        self._sysex_buffer.clear()
        self._sysex_event.clear()
        self._collecting_sysex = True

    def stop_sysex_collection(self) -> list[list[int]]:
        """Stop collecting and return accumulated SysEx messages."""
        self._collecting_sysex = False
        result = list(self._sysex_buffer)
        self._sysex_buffer.clear()
        return result

    def wait_for_sysex(self, timeout: float = 5.0) -> list[int] | None:
        """Wait for a single incoming SysEx message."""
        self._sysex_buffer.clear()
        self._sysex_event.clear()
        self._collecting_sysex = True
        got = self._sysex_event.wait(timeout=timeout)
        self._collecting_sysex = False
        if got and self._sysex_buffer:
            return self._sysex_buffer[0]
        return None

    # -- Monitor --

    def start_monitor(self) -> None:
        self._message_log.clear()
        self._monitoring = True

    def stop_monitor(self) -> None:
        self._monitoring = False

    @property
    def is_monitoring(self) -> bool:
        return self._monitoring

    def get_log(
        self, count: int = 50, type_filter: str | None = None
    ) -> list[MidiMessage]:
        """Get recent messages from the log, optionally filtered by type."""
        msgs = list(self._message_log)
        if type_filter:
            f = type_filter.lower()
            msgs = [m for m in msgs if f in m.type]
        return msgs[-count:]
