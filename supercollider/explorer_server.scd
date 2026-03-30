// explorer_server.scd
// Real-time SuperCollider server for the Explorer GUI.
// Boots scsynth, defines all 4 module SynthDefs, and listens for OSC
// commands from the Python GUI on port 57120 (sclang default).
//
// Buffer 0: audio source (always mono, always at index 0)
// Buffer 100: FFT analysis buffer (pre-allocated)
//
// Usage:  sclang explorer_server.scd

(
s = Server.default;
s.options.numOutputBusChannels = 2;
s.options.numAudioBusChannels = 128;
s.options.memSize = 65536;
s.options.outDevice = "Mac mini Speakers";

s.waitForBoot({
    var currentSynth = nil;
    var currentModule = nil;

    "explorer: server booted".postln;
    ("explorer: sclang OSC port = " ++ NetAddr.langPort).postln;

    thisProcess.openUDPPort(57120);
    ("explorer: UDP port 57120 open = " ++ thisProcess.openPorts.includes(57120)).postln;

    // Pre-allocate FFT buffer at index 100
    s.sendMsg(\b_alloc, 100, 2048, 1);
    s.sync;

    // -- SynthDef: gardenMicrocosm (granular_sampling) -----------------------

    SynthDef(\gardenMicrocosm, { |out = 0, bufnum = 0, gate = 1,
        algo = 0,
        dens = 20, dur = 0.12,
        pRate = 0.03, pJitter = 0.08,
        rt = 1.0, rtJitter = 0.15,
        pWidth = 0.8,
        lpfFreq = 9000,
        dlMix = 0.25, dlTime = 0.33, fb = 0.35,
        vbMix = 0.22,
        drMix = 0.2,
        amp = 0.5|

        var frames, bufDur, bufScale;
        var algoS;
        var haze, mosaic, tunnel, strum, glide;
        var grains;
        var filtered, delayed, wet, sig, env;

        algoS = algo.lag(0.5);
        frames = BufFrames.kr(bufnum);
        bufDur = BufDur.kr(bufnum);
        bufScale = BufRateScale.kr(bufnum);

        // Algorithm 0: Haze (grain cloud wash)
        haze = {
            var trig = Dust.ar(dens);
            var ptr = Phasor.ar(0, bufScale * pRate, 0, frames) / frames;
            var gPos = (ptr + TRand.ar(pJitter.neg, pJitter, trig)).wrap(0, 1);
            var gRate = rt * TExpRand.ar(1 / (1 + rtJitter), 1 + rtJitter, trig);
            var pan = TRand.ar(pWidth.neg, pWidth, trig);
            TGrains.ar(2, trig, bufnum, gRate, gPos * bufDur, dur, pan, 3.0, 4);
        }.value;

        // Algorithm 1: Mosaic (multi-speed layers)
        mosaic = {
            var trig1 = Dust.ar(dens * 0.5);
            var trig2 = Dust.ar(dens * 0.35);
            var trig3 = Dust.ar(dens * 0.35);
            var ptr = Phasor.ar(0, bufScale * pRate, 0, frames) / frames;
            var gPos1 = (ptr + TRand.ar(pJitter.neg, pJitter, trig1)).wrap(0, 1);
            var gPos2 = (ptr + TRand.ar(pJitter.neg * 1.5, pJitter * 1.5, trig2)).wrap(0, 1);
            var gPos3 = (ptr + TRand.ar(pJitter.neg * 0.5, pJitter * 0.5, trig3)).wrap(0, 1);
            var pan1 = TRand.ar(pWidth.neg, pWidth, trig1);
            var pan2 = TRand.ar(pWidth.neg, pWidth, trig2);
            var pan3 = TRand.ar(pWidth.neg, pWidth, trig3);
            var lo = TGrains.ar(2, trig1, bufnum, rt * 0.5, gPos1 * bufDur, dur * 1.5, pan1, 2.0, 4);
            var mid = TGrains.ar(2, trig2, bufnum, rt, gPos2 * bufDur, dur, pan2, 2.0, 4);
            var hi = TGrains.ar(2, trig3, bufnum, rt * 2.0, gPos3 * bufDur, dur * 0.6, pan3, 1.5, 4);
            (lo + mid + hi) * 0.55;
        }.value;

        // Algorithm 2: Tunnel (tight loop drone)
        tunnel = {
            var trig = Dust.ar(dens * 2);
            var ptr = Phasor.ar(0, bufScale * pRate * 0.08, 0, frames) / frames;
            var gPos = (ptr + TRand.ar(pJitter.neg * 0.15, pJitter * 0.15, trig)).wrap(0, 1);
            var gRate = rt * TExpRand.ar(0.995, 1.005, trig);
            var pan = TRand.ar(pWidth.neg * 0.4, pWidth * 0.4, trig);
            TGrains.ar(2, trig, bufnum, gRate, gPos * bufDur, dur * 2.0, pan, 2.5, 4);
        }.value;

        // Algorithm 3: Strum (rhythmic cascades)
        strum = {
            var trig = Impulse.ar(dens);
            var ptr = Phasor.ar(0, bufScale * pRate * 1.5, 0, frames) / frames;
            var gPos = (ptr + TRand.ar(pJitter.neg * 0.3, pJitter * 0.3, trig)).wrap(0, 1);
            var gRate = rt * TExpRand.ar(1 / (1 + (rtJitter * 0.5)), 1 + (rtJitter * 0.5), trig);
            var pan = TRand.ar(pWidth.neg, pWidth, trig);
            TGrains.ar(2, trig, bufnum, gRate, gPos * bufDur, dur * 0.7, pan, 3.0, 4);
        }.value;

        // Algorithm 4: Glide (pitch-shifting shimmer)
        glide = {
            var trig = Dust.ar(dens * 0.8);
            var ptr = Phasor.ar(0, bufScale * pRate * 1.8, 0, frames) / frames;
            var gPos = (ptr + TRand.ar(pJitter.neg, pJitter, trig)).wrap(0, 1);
            var drift = SinOsc.kr(0.07 + (rtJitter * 0.3)).range(0.85, 1.18);
            var gRate = rt * drift * TExpRand.ar(0.97, 1.03, trig);
            var pan = TRand.ar(pWidth.neg, pWidth, trig);
            TGrains.ar(2, trig, bufnum, gRate, gPos * bufDur, dur * 1.3, pan, 2.8, 4);
        }.value;

        grains = SelectX.ar(algoS.clip(0, 4), [
            haze, mosaic, tunnel, strum, glide
        ]);

        filtered = RLPF.ar(grains, lpfFreq.lag(0.1).clip(80, 18000), 0.5);
        delayed = LocalIn.ar(2);
        wet = filtered + (delayed * fb);
        wet = DelayC.ar(wet, 2.0, dlTime.clip(0.001, 2.0));
        LocalOut.ar(wet);
        wet = XFade2.ar(filtered, wet, (dlMix * 2 - 1).clip(-1, 1));
        wet = FreeVerb2.ar(wet[0], wet[1], mix: vbMix.clip(0, 1),
            room: 0.85, damp: 0.5);

        sig = (filtered * drMix) + (wet * (1.0 - drMix));
        env = EnvGen.kr(Env.asr(0.5, 1, 0.5), gate, doneAction: 2);
        sig = Limiter.ar(sig * env * amp, 0.95);
        Out.ar(out, sig);
    }).add;

    // -- SynthDef: gardenPanharmonium (spectral_resynthesis) ------------------

    SynthDef(\gardenPanharmonium, { |out = 0, bufnum = 0, fftBuf = 100, gate = 1,
        voicesThr = 0.01, blurAmt = 12,
        wStretch = 1.0, wShift = 0.0,
        rt = 1.0, fb = 0.0,
        tiltDb = 0.0, glFreq = 8000,
        vbMix = 0.3, vbRoom = 0.8,
        drMix = 0.2,
        amp = 0.5|

        var src, chain, resynth, processed, wet, sig, env, fbIn;

        src = PlayBuf.ar(1, bufnum, BufRateScale.kr(bufnum) * rt, loop: 1);
        fbIn = LocalIn.ar(1);
        src = src + (fbIn * fb.clip(0, 0.5));
        chain = FFT(fftBuf, src);
        chain = PV_MagAbove(chain, voicesThr.clip(0.0001, 1.0));
        chain = PV_MagSmear(chain, blurAmt.clip(1, 64));
        chain = PV_BinShift(chain, wStretch.clip(0.5, 2.0), wShift.clip(-8, 8));
        resynth = IFFT(chain);
        processed = RLPF.ar(resynth, glFreq.clip(200, 18000), 0.6);
        processed = BLowShelf.ar(processed, 300, 1.0, tiltDb * -0.5);
        processed = BHiShelf.ar(processed, 3000, 1.0, tiltDb * 0.5);
        LocalOut.ar(Limiter.ar(processed, 0.7));

        wet = Pan2.ar(processed, 0);
        wet = FreeVerb2.ar(wet[0], wet[1], mix: vbMix.clip(0, 1),
            room: vbRoom.clip(0.1, 0.99), damp: 0.45);
        sig = (Pan2.ar(processed, 0) * drMix) + (wet * (1.0 - drMix));
        env = EnvGen.kr(Env.asr(0.5, 1, 0.5), gate, doneAction: 2);
        sig = Limiter.ar(sig * env * amp, 0.95);
        Out.ar(out, sig);
    }).add;

    // -- SynthDef: gardenSMR (spectral_resonators) ----------------------------

    SynthDef(\gardenSMR, { |out = 0, bufnum = 0, gate = 1,
        f0 = 55, f1 = 110, f2 = 165, f3 = 220, f4 = 275, f5 = 330,
        rq = 0.01, noiseAmt = 0.2, rt = 1.0, excGain = 1.0,
        mRate = 0.1,
        vbMix = 0.3, vbRoom = 0.8, drMix = 0.2,
        amp = 0.5|

        var src, noise, excitation, env;
        var freqs, bands, left, right, dry, wet, sig;

        src = PlayBuf.ar(1, bufnum, BufRateScale.kr(bufnum) * rt, loop: 1);
        noise = WhiteNoise.ar(noiseAmt.clip(0, 0.5));
        excitation = (src + noise) * excGain.clip(0.1, 4.0);
        freqs = [f0, f1, f2, f3, f4, f5];

        bands = freqs.collect({ |freq, i|
            var drift = SinOsc.kr(
                mRate.clip(0.01, 1.0) * (1.0 + (i * 0.17)),
                i * 1.047
            ) * freq * 0.02;
            Resonz.ar(excitation, (freq + drift).clip(20, 20000),
                rq.clip(0.001, 0.5));
        });

        left = bands[0] + bands[2] + bands[4];
        right = bands[1] + bands[3] + bands[5];
        dry = LeakDC.ar([left, right]);
        wet = FreeVerb2.ar(dry[0], dry[1], mix: vbMix.clip(0, 1),
            room: vbRoom.clip(0.1, 0.99), damp: 0.4);
        sig = (dry * drMix) + (wet * (1.0 - drMix));
        env = EnvGen.kr(Env.asr(0.5, 1, 0.5), gate, doneAction: 2);
        sig = Limiter.ar(sig * env * amp, 0.95);
        Out.ar(out, sig);
    }).add;

    // -- SynthDef: gardenMultiFX (advanced_effects, multi-algorithm FX) ---------

    SynthDef(\gardenMultiFX, { |out = 0, bufnum = 0,
        fftBufL = 100, fftBufR = 101,
        gate = 1,
        algo = 0, mix = 0.5,
        p1 = 0.5, p2 = 0.5, p3 = 0.5,
        width = 0.5, level = 0.8,
        rt = 1.0,
        amp = 0.5|

        var src, dry, env;
        var hall, ppDelay, tapeEcho, chorus, drive, freeze;
        var wet, mid, side, sig;
        var algoS, p1s, p2s, p3s;

        algoS = algo.lag(0.5);
        p1s = p1.lag(0.1);
        p2s = p2.lag(0.1);
        p3s = p3.lag(0.1);

        src = PlayBuf.ar(1, bufnum, BufRateScale.kr(bufnum) * rt.lag(0.2), loop: 1);
        dry = Pan2.ar(src, 0);

        // Algorithm 0: Hall Reverb
        hall = FreeVerb2.ar(dry[0], dry[1],
            mix: 1.0,
            room: p1s.linlin(0, 1, 0.3, 0.98),
            damp: p2s.linlin(0, 1, 0.1, 0.8)
        );
        hall = DelayC.ar(hall, 0.1, p3s.linlin(0, 1, 0.0, 0.08));

        // Algorithm 1: Ping-Pong Delay (self-contained CombC, no LocalIn/LocalOut)
        ppDelay = {
            var t = p1s.linexp(0, 1, 0.05, 1.2);
            var decayT = p2s.linlin(0, 1, 0.2, 6.0);
            var delL = CombC.ar(dry[0], 2, t, decayT);
            var delR = CombC.ar(dry[1], 2, t * 0.75, decayT);
            var crossAmt = p3s.linlin(0, 1, 0.0, 0.5);
            [
                (delL * (1 - crossAmt)) + (delR * crossAmt),
                (delR * (1 - crossAmt)) + (delL * crossAmt)
            ];
        }.value;

        // Algorithm 2: Tape Echo
        tapeEcho = {
            var t = p1s.linexp(0, 1, 0.08, 0.8);
            var decayT = p2s.linlin(0, 1, 0.5, 8.0);
            var wow = SinOsc.kr(
                0.3 + (p3s * 2.0),
                0
            ).range(0.998, 1.002);
            var echoL = CombC.ar(dry[0], 2, t * wow, decayT);
            var echoR = CombC.ar(dry[1], 2, t * 1.05 * wow, decayT);
            var filtered = LPF.ar([echoL, echoR], 4000);
            (dry * 0.3) + (filtered * 0.7);
        }.value;

        // Algorithm 3: Chorus
        chorus = {
            var cRate = p1s.linexp(0, 1, 0.1, 6);
            var depth = p2s.linlin(0, 1, 0.001, 0.015);
            var fb = p3s.linlin(0, 1, 0.0, 0.4);
            var d1 = SinOsc.kr(cRate, 0).range(0.015 - depth, 0.015 + depth);
            var d2 = SinOsc.kr(cRate * 0.93, pi).range(0.018 - depth, 0.018 + depth);
            var cL = DelayC.ar(dry[0] + (DelayC.ar(dry[0], 0.05, d1) * fb), 0.05, d1);
            var cR = DelayC.ar(dry[1] + (DelayC.ar(dry[1], 0.05, d2) * fb), 0.05, d2);
            [cL, cR];
        }.value;

        // Algorithm 4: Overdrive
        drive = {
            var gain = p1s.linexp(0, 1, 1.0, 20.0);
            var tone = p2s.linexp(0, 1, 800, 12000);
            var asym = p3s.linlin(0, 1, 0.0, 0.3);
            var driven = ((dry * gain) + asym).tanh;
            LPF.ar(driven, tone);
        }.value;

        // Algorithm 5: Freezer
        freeze = {
            var chainL, chainR;
            var thr = p1s;
            chainL = FFT(fftBufL, dry[0]);
            chainR = FFT(fftBufR, dry[1]);
            chainL = PV_MagFreeze(chainL, thr > 0.5);
            chainR = PV_MagFreeze(chainR, thr > 0.5);
            chainL = PV_MagSmear(chainL, p3s.linlin(0, 1, 0, 12).round);
            chainR = PV_MagSmear(chainR, p3s.linlin(0, 1, 0, 12).round);
            [IFFT(chainL), IFFT(chainR)];
        }.value;

        wet = SelectX.ar(algoS.clip(0, 5), [
            hall, ppDelay, tapeEcho, chorus, drive, freeze
        ]);

        wet = XFade2.ar(dry, wet, (mix.lag(0.2) * 2 - 1).clip(-1, 1));

        mid = (wet[0] + wet[1]) * 0.5;
        side = (wet[0] - wet[1]) * 0.5;
        side = side * width.linlin(0, 1, 0.2, 1.5);
        sig = [mid + side, mid - side];

        sig = LeakDC.ar(sig);
        env = EnvGen.kr(Env.asr(0.5, 1, 0.5), gate, doneAction: 2);
        sig = Limiter.ar(sig * env * amp * level, 0.95);
        Out.ar(out, sig);
    }).add;

    // Extra FFT buffer for multi-FX freezer (second channel)
    s.sendMsg(\b_alloc, 101, 2048, 1);

    s.sync;
    "explorer: all SynthDefs loaded".postln;

    // -- OSC Handlers ---------------------------------------------------------

    OSCdef(\explorerLoadBuffer, { |msg|
        var filePath = msg[1].asString;
        fork {
            ("explorer: loading buffer from " ++ filePath).postln;
            s.sendMsg(\b_free, 0);
            s.sync;
            s.sendMsg(\b_allocReadChannel, 0, filePath, 0, -1, 0);
            s.sync;
            ("explorer: buffer 0 loaded (mono)").postln;
        };
    }, '/explorer/load_buffer');

    OSCdef(\explorerStart, { |msg|
        var module = msg[1].asString;
        fork {
            if(currentSynth.notNil, {
                currentSynth.set(\gate, 0);
                currentSynth = nil;
            });

            0.1.wait;
            currentModule = module;

            switch(module,
                "granular_sampling", {
                    currentSynth = Synth(\gardenMicrocosm, [
                        \bufnum, 0, \amp, 0.5
                    ]);
                    ("explorer: started gardenMicrocosm on buffer 0").postln;
                },
                "spectral_resynthesis", {
                    currentSynth = Synth(\gardenPanharmonium, [
                        \bufnum, 0, \fftBuf, 100, \amp, 0.8
                    ]);
                    ("explorer: started gardenPanharmonium on buffer 0, fft 100").postln;
                },
                "spectral_resonators", {
                    currentSynth = Synth(\gardenSMR, [
                        \bufnum, 0, \amp, 1.0
                    ]);
                    ("explorer: started gardenSMR on buffer 0").postln;
                },
                "advanced_effects", {
                    currentSynth = Synth(\gardenMultiFX, [
                        \bufnum, 0, \fftBufL, 100, \fftBufR, 101, \amp, 0.5
                    ]);
                    ("explorer: started gardenMultiFX on buffer 0").postln;
                }
            );
            ("explorer: module " ++ module ++ " active").postln;
        };
    }, '/explorer/start');

    OSCdef(\explorerSet, { |msg|
        var paramName = msg[1].asSymbol;
        var value = msg[2].asFloat;

        if(currentSynth.notNil, {
            currentSynth.set(paramName, value);
        });
    }, '/explorer/set');

    OSCdef(\explorerStop, { |msg|
        if(currentSynth.notNil, {
            currentSynth.set(\gate, 0);
            currentSynth = nil;
        });
        currentModule = nil;
        "explorer: stopped".postln;
    }, '/explorer/stop');

    OSCdef(\explorerQuit, { |msg|
        "explorer: quitting".postln;
        fork { 0.5.wait; 0.exit; };
    }, '/explorer/quit');

    "explorer: ready -- listening for OSC commands".postln;
});
)
