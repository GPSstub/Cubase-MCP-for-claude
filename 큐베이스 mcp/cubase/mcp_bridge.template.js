// Cubase MIDI Remote driver: bridges Cubase <-> cubase_mcp.py over two loopMIDI ports.
// SOURCE TEMPLATE. Do not install this file directly: run tools/build_bridge.py, which fills in
// every Cubase key command from "Key Commands.xml" and writes mcp_bridge.js.
// Installed at: Documents\Steinberg\Cubase\MIDI Remote\Driver Scripts\Local\mcp\bridge\mcp_bridge.js
//
// Protocol: SysEx  F0 7D 43 4D <ASCII JSON> F7
//   server -> Cubase: {"c":"set","k":"param3","v":0.5} | {"c":"act","n":"nextBank"} | {"c":"time","which":"pos","t":"5.1.1.0"}
//                     {"c":"tempo","bpm":120} | {"c":"dump"} | {"c":"ping"}
//   Cubase -> server: {"e":"value",...} | {"e":"plugin",...} | {"e":"time",...} | {"e":"tempo",...} | {"e":"dump_end",...}
//
// Each setup section runs inside guard() so one failing API call cannot stop the rest of the
// script; failures are reported to the server in dump_end.setupErrors.

var midiremote_api = require('midiremote_api_v1')

var deviceDriver = midiremote_api.makeDeviceDriver('MCP', 'Bridge', 'cubase-mcp')
var midiInput = deviceDriver.mPorts.makeMidiInput()
var midiOutput = deviceDriver.mPorts.makeMidiOutput()

deviceDriver.makeDetectionUnit().detectPortPair(midiInput, midiOutput)
    .expectInputNameContains('CubaseMCP_ToCubase')
    .expectOutputNameContains('CubaseMCP_FromCubase')

var NUM_PARAMS = 16   // plugin parameters per bank
var NUM_MIX = 16      // mixer channels per bank
var NUM_SENDS = 8     // sends of the selected track
var HEADER = [0xF0, 0x7D, 0x43, 0x4D]

// [Category, Command] pairs, filled in by tools/build_bridge.py.
var COMMANDS = /*@@COMMANDS@@*/[['Transport', 'Start'], ['Transport', 'Stop']]

var setupErrors = []
var commandErrors = 0

function guard(name, fn) {
    try { fn() } catch (e) { setupErrors.push(name + ': ' + String(e)) }
}

// ---------- Minimal JSON (Cubase's script engine may not provide a JSON object) ----------

function hex4(n) {
    return ('0000' + n.toString(16)).slice(-4)
}

function toJSON(v) {
    if (v === null || v === undefined) return 'null'
    var t = typeof v
    if (t === 'number') return isFinite(v) ? String(v) : 'null'
    if (t === 'boolean') return v ? 'true' : 'false'
    if (t === 'string') {
        var out = '"'
        for (var i = 0; i < v.length; i++) {
            var ch = v.charAt(i)
            var code = v.charCodeAt(i)
            if (ch === '"' || ch === '\\') out += '\\' + ch
            else if (code < 0x20 || code > 0x7E) out += '\\u' + hex4(code)
            else out += ch
        }
        return out + '"'
    }
    if (v instanceof Array) {
        var parts = []
        for (var j = 0; j < v.length; j++) parts.push(toJSON(v[j]))
        return '[' + parts.join(',') + ']'
    }
    var fields = []
    for (var k in v) fields.push(toJSON(k) + ':' + toJSON(v[k]))
    return '{' + fields.join(',') + '}'
}

function fromJSON(s) {
    var i = 0
    function ws() { while (i < s.length && ' \t\r\n'.indexOf(s.charAt(i)) >= 0) i++ }
    function value() {
        ws()
        var ch = s.charAt(i)
        if (ch === '{') {
            i++; var obj = {}; ws()
            if (s.charAt(i) === '}') { i++; return obj }
            while (true) {
                ws(); var key = value(); ws(); i++ // ':'
                obj[key] = value(); ws()
                if (s.charAt(i++) === '}') return obj
            }
        }
        if (ch === '[') {
            i++; var arr = []; ws()
            if (s.charAt(i) === ']') { i++; return arr }
            while (true) {
                arr.push(value()); ws()
                if (s.charAt(i++) === ']') return arr
            }
        }
        if (ch === '"') {
            i++; var str = ''
            while (s.charAt(i) !== '"') {
                if (i >= s.length) throw 'unterminated string'
                var c = s.charAt(i++)
                if (c === '\\') {
                    var e = s.charAt(i++)
                    if (e === 'u') { str += String.fromCharCode(parseInt(s.substr(i, 4), 16)); i += 4 }
                    else if (e === 'n') str += '\n'
                    else if (e === 't') str += '\t'
                    else str += e
                } else str += c
            }
            i++
            return str
        }
        if (s.substr(i, 4) === 'true') { i += 4; return true }
        if (s.substr(i, 5) === 'false') { i += 5; return false }
        if (s.substr(i, 4) === 'null') { i += 4; return null }
        var start = i
        while (i < s.length && '-+.eE0123456789'.indexOf(s.charAt(i)) >= 0) i++
        if (start === i) throw 'bad json at ' + i
        return Number(s.substring(start, i))
    }
    return value()
}

// ---------- SysEx I/O ----------

var lastDevice = null
var lastMapping = null

function send(activeDevice, obj) {
    var dev = activeDevice || lastDevice
    if (!dev) return
    var json = toJSON(obj)
    var bytes = HEADER.slice()
    for (var i = 0; i < json.length; i++) bytes.push(json.charCodeAt(i) & 0x7F)
    bytes.push(0xF7)
    midiOutput.sendMidi(dev, bytes)
}

function decode(data) {
    var start = 0
    if (data[0] === 0xF0) start = 1
    if (data[start] !== 0x7D || data[start + 1] !== 0x43 || data[start + 2] !== 0x4D) return null
    var s = ''
    for (var i = start + 3; i < data.length && data[i] !== 0xF7; i++) s += String.fromCharCode(data[i])
    try { return fromJSON(s) } catch (e) { return null }
}

// State filled in by the setup sections below.
var values = {}
var actions = {}
var plugin = { name: '', vendor: '', version: '', format: '' }
var times = { pos: { time: '', format: '' }, left: { time: '', format: '' }, right: { time: '', format: '' } }
var locators = {}
var tempo = 0
var timeDisplay = null

// ---------- Incoming commands (registered first so it always exists) ----------

midiInput.mOnSysex = function (activeDevice, data) {
    lastDevice = activeDevice
    try {
        handle(activeDevice, decode(data))
    } catch (e) {
        send(activeDevice, { e: 'error', msg: 'handler: ' + String(e) })
    }
}

function handle(activeDevice, msg) {
    if (!msg) return send(activeDevice, { e: 'error', msg: 'undecodable sysex' })

    if (msg.c === 'ping') {
        return send(activeDevice, { e: 'pong', setupErrors: setupErrors.length })
    } else if (msg.c === 'set') {
        var entry = values[msg.k]
        if (!entry) return send(activeDevice, { e: 'error', msg: 'unknown value ' + msg.k })
        var v = Math.max(0, Math.min(1, Number(msg.v)))
        entry.sv.setProcessValue(activeDevice, v)
    } else if (msg.c === 'act') {
        var sv = actions[msg.n]
        if (!sv) return send(activeDevice, { e: 'error', msg: 'unknown action ' + msg.n })
        // Press now, release on the next idle tick: a press and release in the same callback is
        // swallowed by action bindings.
        sv.setProcessValue(activeDevice, 1)
        pendingRelease.push({ sv: sv, id: msg.id })
        return
    } else if (msg.c === 'time') {
        if (!lastMapping) return send(activeDevice, { e: 'error', msg: 'mapping not active yet' })
        var loc = locators[msg.which]
        if (!loc) return send(activeDevice, { e: 'error', msg: 'unknown locator ' + msg.which })
        loc.setTime(lastMapping, String(msg.t))
    } else if (msg.c === 'tempo') {
        if (!lastMapping || !timeDisplay) return send(activeDevice, { e: 'error', msg: 'mapping not active yet' })
        timeDisplay.setTempoBPM(lastMapping, Number(msg.bpm))
    } else if (msg.c === 'dump') {
        send(activeDevice, { e: 'plugin', name: plugin.name, vendor: plugin.vendor, version: plugin.version, format: plugin.format })
        for (var key in values) report(activeDevice, values[key].state)
        for (var w in times) send(activeDevice, { e: 'time', which: w, time: times[w].time, format: times[w].format })
        send(activeDevice, { e: 'tempo', bpm: tempo })
        for (var sm in stripIdentity) send(activeDevice, { e: 'strip', m: sm, name: stripIdentity[sm].name, vendor: stripIdentity[sm].vendor })
        for (var j = 0; j < setupErrors.length; j++) send(activeDevice, { e: 'error', msg: 'setup ' + setupErrors[j] })
        send(activeDevice, {
            e: 'dump_end', numParams: NUM_PARAMS, numMix: NUM_MIX, numSends: NUM_SENDS,
            numCommands: COMMANDS.length, commandErrors: commandErrors, setupErrors: setupErrors.length,
        })
        return
    }
    send(activeDevice, { e: 'ack' })
}

var pendingRelease = []

deviceDriver.mOnIdle = function (activeDevice) {
    while (pendingRelease.length) {
        var p = pendingRelease.shift()
        p.sv.setProcessValue(activeDevice, 0)
        send(activeDevice, { e: 'ack', id: p.id })
    }
}

deviceDriver.mOnActivate = function (activeDevice) {
    lastDevice = activeDevice
    send(activeDevice, { e: 'hello', numParams: NUM_PARAMS, numMix: NUM_MIX, numCommands: COMMANDS.length, setupErrors: setupErrors.length })
}

// ---------- Values (invisible custom variables bound to host values) ----------

var surface = deviceDriver.mSurface
var page = deviceDriver.mMapping.makePage('MCP')
var host = page.mHostAccess

page.mOnActivate = function (activeDevice, activeMapping) {
    lastDevice = activeDevice
    lastMapping = activeMapping
}

// Each entry caches what Cubase last reported so "dump" can replay it.
function makeValue(key, extra) {
    var sv = surface.makeCustomValueVariable(key)
    var state = { k: key, title: '', name: '', disp: '', units: '', v: 0 }
    if (extra) for (var p in extra) state[p] = extra[p]
    values[key] = { sv: sv, state: state }

    sv.mOnTitleChange = function (activeDevice, objectTitle, valueTitle) {
        state.title = objectTitle
        state.name = valueTitle
        report(activeDevice, state)
    }
    sv.mOnDisplayValueChange = function (activeDevice, value, units) {
        state.disp = value
        state.units = units
        report(activeDevice, state)
    }
    sv.mOnProcessValueChange = function (activeDevice, value) {
        state.v = value
        report(activeDevice, state)
    }
    return sv
}

function report(activeDevice, state) {
    lastDevice = activeDevice
    var msg = { e: 'value' }
    for (var p in state) msg[p] = state[p]
    send(activeDevice, msg)
}

function bindValue(key, hostValue, extra) {
    guard('value ' + key, function () { page.makeValueBinding(makeValue(key, extra), hostValue) })
}

function bindChannelValues(prefix, mixerChannel) {
    var v = mixerChannel.mValue
    bindValue(prefix + 'volume', v.mVolume)
    bindValue(prefix + 'pan', v.mPan)
    bindValue(prefix + 'mute', v.mMute)
    bindValue(prefix + 'solo', v.mSolo)
    bindValue(prefix + 'rec', v.mRecordEnable)
    bindValue(prefix + 'monitor', v.mMonitorEnable)
    bindValue(prefix + 'selected', v.mSelected)
}

function makeAction(key, hostAction) {
    guard('action ' + key, function () {
        var sv = surface.makeCustomValueVariable('act_' + key)
        page.makeActionBinding(sv, hostAction)
        actions[key] = sv
    })
}

// Selected track and its sends
guard('selected track', function () {
    var channel = host.mTrackSelection.mMixerChannel
    bindChannelValues('', channel)
    for (var s = 0; s < NUM_SENDS; s++) {
        var slot = channel.mSends.getByIndex(s)
        bindValue('send' + s + '_level', slot.mLevel, { i: s })
        bindValue('send' + s + '_on', slot.mOn, { i: s })
    }
    makeAction('nextTrack', host.mTrackSelection.mAction.mNextTrack)
    makeAction('prevTrack', host.mTrackSelection.mAction.mPrevTrack)
})

// Insert effects of the selected track
guard('inserts', function () {
    var viewer = host.mTrackSelection.mMixerChannel.mInsertAndStripEffects
        .makeInsertEffectViewer('MCPInserts').excludeEmptySlots()
    var zone = viewer.mParameterBankZone
    for (var i = 0; i < NUM_PARAMS; i++) bindValue('param' + i, zone.makeParameterValue(), { i: i })
    bindValue('insertOn', viewer.mOn)
    bindValue('insertBypass', viewer.mBypass)
    bindValue('insertEdit', viewer.mEdit)
    viewer.mOnChangePluginIdentity = function (activeDevice, activeMapping, name, vendor, version, format) {
        plugin = { name: name, vendor: vendor, version: version, format: format }
        send(activeDevice, { e: 'plugin', name: name, vendor: vendor, version: version, format: format })
    }
    makeAction('nextSlot', viewer.mAction.mNext)
    makeAction('prevSlot', viewer.mAction.mPrev)
    makeAction('resetSlot', viewer.mAction.mReset)
    makeAction('nextBank', zone.mAction.mNextBank)
    makeAction('prevBank', zone.mAction.mPrevBank)
    makeAction('resetBank', zone.mAction.mResetBank)
})

// Channel strip of the selected track: pre-filter, channel EQ and the strip effect modules
guard('pre-filter', function () {
    var pf = host.mTrackSelection.mMixerChannel.mPreFilter
    bindValue('pf_bypass', pf.mBypass)
    bindValue('pf_gain', pf.mGain)
    bindValue('pf_phase', pf.mPhaseSwitch)
    bindValue('pf_hcOn', pf.mHighCutOn)
    bindValue('pf_hcFreq', pf.mHighCutFreq)
    bindValue('pf_hcSlope', pf.mHighCutSlope)
    bindValue('pf_lcOn', pf.mLowCutOn)
    bindValue('pf_lcFreq', pf.mLowCutFreq)
    bindValue('pf_lcSlope', pf.mLowCutSlope)
})

guard('channel eq', function () {
    var eq = host.mTrackSelection.mMixerChannel.mChannelEQ
    var bands = [eq.mBand1, eq.mBand2, eq.mBand3, eq.mBand4]
    for (var b = 0; b < 4; b++) {
        var p = 'eq' + (b + 1) + '_'
        bindValue(p + 'on', bands[b].mOn)
        bindValue(p + 'gain', bands[b].mGain)
        bindValue(p + 'freq', bands[b].mFreq)
        bindValue(p + 'q', bands[b].mQ)
        bindValue(p + 'type', bands[b].mFilterType)
    }
})

var STRIP_MODULES = { gate: 'mGate', comp: 'mCompressor', limiter: 'mLimiter', sat: 'mSaturator', tools: 'mTools' }
var stripIdentity = {}

guard('strip effects', function () {
    var folder = host.mTrackSelection.mMixerChannel.mInsertAndStripEffects.mStripEffects
    for (var mod in STRIP_MODULES) {
        (function (mod) {
            guard('strip ' + mod, function () {
                var slot = folder[STRIP_MODULES[mod]]
                var p = 's_' + mod + '_'
                bindValue(p + 'on', slot.mOn)
                bindValue(p + 'bypass', slot.mBypass)
                bindValue(p + 'edit', slot.mEdit)
                for (var i = 0; i < NUM_PARAMS; i++) bindValue(p + 'p' + i, slot.mParameterBankZone.makeParameterValue(), { i: i })
                makeAction(p + 'nextBank', slot.mParameterBankZone.mAction.mNextBank)
                makeAction(p + 'prevBank', slot.mParameterBankZone.mAction.mPrevBank)
                makeAction(p + 'resetBank', slot.mParameterBankZone.mAction.mResetBank)
                stripIdentity[mod] = { name: '', vendor: '' }
                slot.mOnChangePluginIdentity = function (activeDevice, activeMapping, name, vendor, version, format) {
                    stripIdentity[mod] = { name: name, vendor: vendor }
                    send(activeDevice, { e: 'strip', m: mod, name: name, vendor: vendor })
                }
            })
        })(mod)
    }
})

// Every mixer channel, NUM_MIX at a time
guard('mixer', function () {
    var mixZone = host.mMixConsole.makeMixerBankZone('MCPMix')
    for (var m = 0; m < NUM_MIX; m++) bindChannelValues('mix' + m + '_', mixZone.makeMixerBankChannel())
    makeAction('nextMixBank', mixZone.mAction.mNextBank)
    makeAction('prevMixBank', mixZone.mAction.mPrevBank)
    makeAction('resetMixBank', mixZone.mAction.mResetBank)
})

// Transport
guard('transport', function () {
    var tv = host.mTransport.mValue
    bindValue('play', tv.mStart)
    bindValue('stop', tv.mStop)
    bindValue('record', tv.mRecord)
    bindValue('cycle', tv.mCycleActive)
    bindValue('metronome', tv.mMetronomeActive)
})

guard('time display', function () {
    timeDisplay = host.mTransport.mTimeDisplay
    locators = {
        pos: timeDisplay.mPrimary.mTransportLocator,
        left: timeDisplay.mPrimary.mCycleLocatorLeft,
        right: timeDisplay.mPrimary.mCycleLocatorRight,
    }
    function watchTime(which) {
        locators[which].mOnChange = function (activeDevice, activeMapping, time, format) {
            lastMapping = activeMapping
            times[which] = { time: time, format: format }
            send(activeDevice, { e: 'time', which: which, time: time, format: format })
        }
    }
    watchTime('pos')
    watchTime('left')
    watchTime('right')
    timeDisplay.mOnChangeTempoBPM = function (activeDevice, activeMapping, bpm) {
        lastMapping = activeMapping
        tempo = bpm
        send(activeDevice, { e: 'tempo', bpm: bpm })
    }
})

// Every key command, fired by pulsing its custom variable
for (var c = 0; c < COMMANDS.length; c++) {
    try {
        var cmd = surface.makeCustomValueVariable('cmd_' + c)
        page.makeCommandBinding(cmd, COMMANDS[c][0], COMMANDS[c][1])
        actions['cmd:' + c] = cmd
    } catch (e) {
        commandErrors++
        if (commandErrors <= 3) setupErrors.push('command ' + COMMANDS[c].join(' / ') + ': ' + String(e))
    }
}

// Visible diagnostics in Cubase's MIDI Remote view
guard('status label', function () {
    var label = surface.makeLabelField(0, 0, 24, 3)
    page.setLabelFieldText(label, 'MCP Bridge ' + COMMANDS.length + ' cmds, setup errors: ' + setupErrors.length +
        (setupErrors.length ? ' - ' + setupErrors.slice(0, 2).join(' | ') : ''))
})

// Diagnostic: metronome toggle on CC 21, channel 16, both directions (tests plain MIDI I/O)
guard('diagnostic cc', function () {
    var btn = surface.makeButton(0, 3, 2, 2)
    btn.mSurfaceValue.mMidiBinding.setInputPort(midiInput).setOutputPort(midiOutput).bindToControlChange(15, 21)
    page.makeValueBinding(btn.mSurfaceValue, host.mTransport.mValue.mMetronomeActive).setTypeToggle()
})
