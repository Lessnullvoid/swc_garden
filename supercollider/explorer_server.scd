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
        dens = 20, dur = 0.12,
        pRate = 0.03, pJitter = 0.08,
        rt = 1.0, rtJitter = 0.15,
        pWidth = 0.8,
        lpfFreq = 9000,
        dlMix = 0.25, dlTime = 0.33, fb = 0.35,
        vbMix = 0.22,
        drMix = 0.2,
        amp = 0.5|

        var trig, ptr, grainPos, grainRate, pan, grains;
        var filtered, delayed, wet, sig, env;
        var frames, bufDur;

        frames = BufFrames.kr(bufnum);
        bufDur = BufDur.kr(bufnum);
        trig = Dust.ar(dens);
        ptr = Phasor.ar(0, BufRateScale.kr(bufnum) * pRate, 0, frames) / frames;
        grainPos = (ptr + TRand.ar(pJitter.neg, pJitter, trig)).wrap(0, 1);
        grainRate = rt * TExpRand.ar(1 / (1 + rtJitter), 1 + rtJitter, trig);
        pan = TRand.ar(pWidth.neg, pWidth, trig);

        grains = TGrains.ar(2, trig, bufnum, grainRate,
            grainPos * bufDur, dur, pan, 3.0, 4);

        filtered = RLPF.ar(grains, lpfFreq.clip(80, 18000), 0.5);
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

        src = PlayBuf.ar(1, bufnum, BufRateScale.kr(bufnum) * rt, loop: 1);
        dry = Pan2.ar(src, 0);

        // Algorithm 0: Hall Reverb
        hall = FreeVerb2.ar(dry[0], dry[1],
            mix: 1.0,
            room: p1.linlin(0, 1, 0.3, 0.98),
            damp: p2.linlin(0, 1, 0.1, 0.8)
        );
        hall = DelayC.ar(hall, 0.1, p3.linlin(0, 1, 0.0, 0.08));

        // Algorithm 1: Ping-Pong Delay
        ppDelay = {
            var t = p1.linexp(0, 1, 0.05, 1.2);
            var fb = p2.linlin(0, 1, 0.05, 0.85);
            var ppL, ppR, ppFb;
            ppFb = LocalIn.ar(2);
            ppL = CombC.ar(dry[0] + (ppFb[1] * fb), 2, t, 4);
            ppR = CombC.ar(dry[1] + (ppFb[0] * fb), 2, t * 1.07, 4);
            LocalOut.ar([ppL, ppR]);
            XFade2.ar(
                [ppL, ppR],
                [ppR, ppL],
                p3.linlin(0, 1, -1, 1)
            );
        }.value;

        // Algorithm 2: Tape Echo
        tapeEcho = {
            var t = p1.linexp(0, 1, 0.08, 0.8);
            var fb = p2.linlin(0, 1, 0.1, 0.8);
            var wow = SinOsc.kr(
                0.3 + (p3 * 2.0),
                0
            ).range(0.998, 1.002);
            var echoL = CombC.ar(dry[0], 2, t * wow, fb * 6);
            var echoR = CombC.ar(dry[1], 2, t * 1.05 * wow, fb * 6);
            var filtered = LPF.ar([echoL, echoR], 4000);
            (dry * 0.3) + (filtered * 0.7);
        }.value;

        // Algorithm 3: Chorus
        chorus = {
            var cRate = p1.linexp(0, 1, 0.1, 6);
            var depth = p2.linlin(0, 1, 0.001, 0.015);
            var fb = p3.linlin(0, 1, 0.0, 0.4);
            var d1 = SinOsc.kr(cRate, 0).range(0.015 - depth, 0.015 + depth);
            var d2 = SinOsc.kr(cRate * 0.93, pi).range(0.018 - depth, 0.018 + depth);
            var cL = DelayC.ar(dry[0] + (DelayC.ar(dry[0], 0.05, d1) * fb), 0.05, d1);
            var cR = DelayC.ar(dry[1] + (DelayC.ar(dry[1], 0.05, d2) * fb), 0.05, d2);
            [cL, cR];
        }.value;

        // Algorithm 4: Overdrive
        drive = {
            var gain = p1.linexp(0, 1, 1.0, 20.0);
            var tone = p2.linexp(0, 1, 800, 12000);
            var asym = p3.linlin(0, 1, 0.0, 0.3);
            var driven = ((dry * gain) + asym).tanh;
            LPF.ar(driven, tone);
        }.value;

        // Algorithm 5: Freezer
        freeze = {
            var chainL, chainR;
            var thr = p1;
            chainL = FFT(fftBufL, dry[0]);
            chainR = FFT(fftBufR, dry[1]);
            chainL = PV_MagFreeze(chainL, thr > 0.5);
            chainR = PV_MagFreeze(chainR, thr > 0.5);
            chainL = PV_MagSmear(chainL, p3.linlin(0, 1, 0, 12).round);
            chainR = PV_MagSmear(chainR, p3.linlin(0, 1, 0, 12).round);
            [IFFT(chainL), IFFT(chainR)];
        }.value;

        wet = SelectX.ar(algo.clip(0, 5), [
            hall, ppDelay, tapeEcho, chorus, drive, freeze
        ]);

        wet = XFade2.ar(dry, wet, (mix * 2 - 1).clip(-1, 1));

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
