# cirklon-mcp

MCP server for controlling a [Sequentix Cirklon](https://www.sequentix.com/) hardware MIDI sequencer from [Claude Code](https://claude.ai/code) (or any MCP client) over USB MIDI.

The Cirklon is a class-compliant USB MIDI device with 6 ports — no drivers needed.

## Features

- **Transport** — Start, Stop, Continue, MIDI Clock sender
- **Scene/Song** — Select scenes (0-127), transpose, program change with bank select
- **Notes & CCs** — Send notes, CCs, pitch bend, aftertouch to any instrument channel
- **Recording** — Send timed note sequences and CC automation streams for real-time recording
- **SysEx** — Send/receive raw SysEx, full memory dump backup
- **Monitor** — Log and filter incoming MIDI from the Cirklon

20 tools across 7 categories.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Sequentix Cirklon connected via USB

## Setup

```bash
# Clone
git clone https://github.com/yourusername/cirklon-mcp.git
cd cirklon-mcp

# Install dependencies
uv sync

# Configure (optional — can also connect manually via tools)
cp .env.example .env
# Edit .env with your port names
```

## Register with Claude Code

```bash
claude mcp add --scope user cirklon-midi -- uv run --directory ~/Dev/cirklon-mcp server.py
```

## Usage

Once registered, use natural language in Claude Code:

```
"List available MIDI ports"
"Connect to the Cirklon"
"Start playback"
"Select scene 5"
"Send a C major chord on channel 1"
"Start monitoring MIDI input"
"Show me the last 20 MIDI messages"
"Back up the Cirklon via SysEx dump"
```

## Tools Reference

| Category | Tool | Description |
|----------|------|-------------|
| Connection | `list_midi_ports` | List available MIDI I/O ports |
| | `connect_cirklon` | Connect by port name or index |
| | `disconnect_cirklon` | Disconnect |
| Transport | `transport_start` | MIDI Start (0xFA) |
| | `transport_stop` | MIDI Stop (0xFC) |
| | `transport_continue` | MIDI Continue (0xFB) |
| | `send_clock` | Send MIDI clock at BPM |
| Scene | `select_scene` | Program Change on remote channel |
| | `set_transpose` | Note On on remote channel |
| | `send_program_change` | PC + optional bank select |
| Note/CC | `send_note` | Note On, wait, Note Off |
| | `send_cc` | Control Change message |
| | `send_pitch_bend` | 14-bit pitch bend |
| | `send_aftertouch` | Channel pressure |
| | `send_poly_aftertouch` | Per-note pressure |
| Recording | `record_notes` | Timed note sequence |
| | `record_cc_stream` | CC automation stream |
| SysEx | `send_sysex` | Raw SysEx (auto-frames F0/F7) |
| | `receive_sysex` | Listen for incoming SysEx |
| | `sysex_dump_receive` | Full memory dump to .syx file |
| Monitor | `start_midi_monitor` | Begin logging input |
| | `stop_midi_monitor` | Stop logging |
| | `get_midi_log` | Retrieve recent messages |

## Cirklon Configuration

Set the remote control channel in Cirklon: `GLOBAL > MIDI > REMOTE CH` (default: 16).

For SysEx dumps: `GLOBAL > SYSEX > SEND` on the Cirklon, then use `sysex_dump_receive` tool.

## License

MIT
