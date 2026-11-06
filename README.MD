# Cubase MCP

**Control Steinberg Cubase with Claude AI through the Model Context Protocol (MCP).**

Cubase MCP is an open-source integration that enables Claude to interact with Steinberg Cubase 13 using natural language.

It provides access to mixer channels, track controls, VST3 plugin parameters, transport functions, Cubase key commands, and advanced audio analysis tools.

The goal is to streamline music production, mixing, and audio engineering by allowing users to control their DAW through AI-assisted workflows.

> **Development Status:** Experimental — not yet tested in a live Cubase environment.
>
> **Compatibility:** Cubase 13 / Windows
>
> **AI Client:** Claude Code

---

## Features

### 1. Mixer & Track Control

Access and control Cubase mixer channels directly through Claude.

- List all available MixConsole channels
- Select tracks by name
- Adjust volume in decibels (dB)
- Modify panning
- Toggle mute and solo
- Control record-enable and monitoring states
- Read channel information and current settings

### 2. VST3 Plugin Control

Interact with insert effects, including supported third-party VST3 plugins.

- List insert plugins on the selected track
- Select and focus insert effects
- Read plugin parameters
- Modify parameters using normalized values
- Set parameters using displayed values such as Hz or dB
- Enable, disable, or bypass inserts
- Open plugin interfaces

**Limitation:** Inserting an arbitrary plugin by name is not supported through the MIDI Remote API.

### 3. Transport & Project Control

Control essential Cubase transport functions.

- Play and stop
- Start recording
- Enable or disable cycle mode
- Control the metronome
- Change tempo
- Read and modify transport position
- Manage cycle locators

### 4. Cubase Key Commands

Search and execute approximately 2,000 Cubase key commands.

This provides access to a wide range of Cubase operations beyond basic mixer and transport controls.

Commands are executed within the current Cubase context, meaning their behavior depends on the active selection and focused window.

Commands that open dialogs may require additional manual interaction.

### 5. Advanced Audio Analysis

Analyze audio files and retrieve detailed audio measurements.

Supported analysis features include:

- Integrated loudness (LUFS)
- Peak levels
- True peak levels
- Frequency-band energy
- Stereo correlation
- Tempo estimation
- Musical key estimation

### 6. Reference Audio Comparison

Compare your audio against a reference recording using one-third-octave frequency analysis.

The comparison system can provide:

- Frequency-band differences
- Suggested EQ frequencies
- Recommended filter shapes
- Gain and Q-factor suggestions
- Compression recommendations
- Brightness comparison
- Loudness differences

Specific time ranges can be selected for more focused analysis.

### 7. Render and Analyze

Render the selected track using Cubase's Render in Place functionality and analyze the processed audio, including its insert effects.

Optionally provide a reference file for comparison.

After analysis, the render operation is reverted using Undo, while the rendered audio file remains on disk.

---

## Architecture

Cubase MCP communicates with Cubase using two virtual MIDI ports and a custom MIDI Remote bridge.

```text
Claude / Claude Code
        │
        ▼
MCP Server
(server/cubase_mcp.py)
        │
        ▼
loopMIDI
(Two Virtual MIDI Ports)
        │
        ▼
Cubase MIDI Remote Bridge
(mcp_bridge.js)
        │
        ▼
Steinberg Cubase 13
```

The Python MCP server processes tool requests, while the MIDI Remote bridge provides communication with Cubase.

Audio analysis tools handle audio-file measurement and reference comparison.

---

## Requirements

- Windows
- Steinberg Cubase 13
- Python environment
- Claude Code
- loopMIDI
- Cubase MIDI Remote API
- Required Python dependencies

---

## Installation & Setup

The following instructions describe the existing development environment. Local paths may need to be changed for other systems.

### Step 1 — Configure loopMIDI

Install and launch loopMIDI.

Create two virtual MIDI ports with the exact names:

- `CubaseMCP_ToCubase`
- `CubaseMCP_FromCubase`

Increase the SysEx buffer size to `4096`.

Enable **Autostart** through the loopMIDI system tray menu to keep the ports available after restarting Windows.

### Step 2 — Configure MIDI Ports in Cubase

Open Cubase and navigate to:

**Studio → Studio Setup → MIDI Port Setup**

Find both Cubase MCP virtual MIDI ports.

Disable **In 'All MIDI Inputs'** for both ports to prevent unwanted MIDI input behavior.

### Step 3 — Install the MIDI Remote Bridge

The generated bridge script is located at:

`Documents\Steinberg\Cubase\MIDI Remote\Driver Scripts\Local\mcp\bridge\mcp_bridge.js`

Copy or generate the bridge script in the appropriate MIDI Remote driver directory.

Open the MIDI Remote tab in Cubase's Lower Zone and select **Reload Scripts**.

The device should appear as **MCP Bridge**.

If it does not appear automatically, select:

**Add Surface → MCP / Bridge**

To inspect script errors, navigate to:

**Studio → MIDI Remote Manager → Scripting Tools → Script Console**

### Step 4 — Configure Claude Code

The current development environment uses the Python virtual environment at:

`C:\cubase-mcp-venv`

The MCP server is registered as `cubase` in the `mcpServers` section of:

`~/.claude.json`

A backup of the configuration is stored as:

`.claude.json.bak-cubase-mcp`

Configure the server entry point to launch:

`server/cubase_mcp.py`

Start a new Claude Code session after registering the server.

The Cubase MCP tools should then become available to Claude.

### Step 5 — Rebuild the Bridge After Key Command Changes

If Cubase key commands have been modified, regenerate the bridge script using:

```powershell
C:\cubase-mcp-venv\Scripts\python.exe tools\build_bridge.py --install
```

Then return to Cubase and select **Reload Scripts**.

The bridge template is stored at:

`cubase/mcp_bridge.template.js`

The `mcp_bridge.js` file is generated from this template.

---

## Available MCP Tools

| Tool | Description |
|---|---|
| `cubase_status` | Retrieve the selected track, sends, focused plugin parameters, transport state, position, and tempo. |
| `list_tracks` | List MixConsole channels and their current settings. |
| `select_track` | Select a track by name or navigate to the next or previous track. |
| `set_track` | Modify supported mixer channel properties. |
| `set_track_volume_db` | Set a track's volume in decibels. |
| `set_send` | Configure send
