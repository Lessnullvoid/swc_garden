// granular_sampling.scd
// Multi-algorithm granular processor (design inspired by Hologram Microcosm).
// 5 grain algorithms: Haze, Mosaic, Tunnel, Strum, Glide.
// SelectX for smooth scan between neighbors.
// Shared post-processing: RLPF -> delay/feedback -> FreeVerb2 -> dry/wet.
// Reads ~configPath from the interpreter environment (set by run_daily.scd).

(
var config, params, audioBatchDir, outputDir, duration;
var audioFiles, score, outPath, oscPath;
var algo, grainDensity, grainDuration, posRate, posJitter;
var rate, rateJitter, panWidth;
var lpf, delayMix, delayTime, feedback, reverbMix, dryMix;
var ampAttack, ampDecay;
var numFiles, nrtCmd;

config = File.readAllString(~configPath).parseJSON;
params = config["params"];

audioBatchDir = config["audio_batch_dir"];
outputDir = config["output_dir"];
duration = config["duration"].asFloat;

algo = if(params["algo"].notNil, { params["algo"].asFloat }, { 0.0 });
grainDensity = params["grain_density"].asFloat;
grainDuration = params["grain_duration"].asFloat;
posRate = params["pos_rate"].asFloat;
posJitter = params["pos_jitter"].asFloat;
rate = params["rate"].asFloat;
rateJitter = params["rate_jitter"].asFloat;
panWidth = params["pan_width"].asFloat;
lpf = params["lpf"].asFloat;
delayMix = params["delay_mix"].asFloat;
delayTime = params["delay_time"].asFloat;
feedback = params["feedback"].asFloat;
reverbMix = params["reverb_mix"].asFloat;
dryMix = params["dry_mix"].asFloat;
ampAttack = params["amp_attack"].asFloat;
ampDecay = params["amp_decay"].asFloat;

audioFiles = (audioBatchDir +/+ "*.wav").pathMatch
    ++ (audioBatchDir +/+ "*.aif").pathMatch
    ++ (audioBatchDir +/+ "*.aiff").pathMatch;

if(audioFiles.size == 0, {
    ("granular_sampling: no audio files found in " ++ audioBatchDir).postln;
    nil;
}, {
    numFiles = audioFiles.size;
    outPath = outputDir +/+ "granular_sampling.aiff";

    score = Score.new;

    audioFiles.do({ |path, i|
        score.add([0.0, [\b_allocReadChannel, i, path, 0, -1, 0]]);
    });

    score.add([0.0, [\d_recv, SynthDef(\gardenMicrocosm, { |out = 0,
        bufnum = 0,
        algo = 0,
        dens = 20, dur = 0.12,
        pRate = 0.03, pJitter = 0.08,
        rt = 1.0, rtJitter = 0.15,
        pWidth = 0.8,
        lpfFreq = 9000,
        dlMix = 0.25, dlTime = 0.33, fb = 0.35,
        vbMix = 0.22,
        drMix = 0.2,
        attack = 2.0, sustain = 116.0, release = 2.0,
        amp = 0.5|

        var frames, bufDur, bufScale;
        var haze, mosaic, tunnel, strum, glide;
        var grains;
        var filtered, delayed, wet, sig, env;

        frames = BufFrames.kr(bufnum);
        bufDur = BufDur.kr(bufnum);
        bufScale = BufRateScale.kr(bufnum);

        // -- Algorithm 0: Haze (original behavior) --
        haze = {
            var trig = Dust.ar(dens);
            var ptr = Phasor.ar(0, bufScale * pRate, 0, frames) / frames;
            var gPos = (ptr + TRand.ar(pJitter.neg, pJitter, trig)).wrap(0, 1);
            var gRate = rt * TExpRand.ar(1 / (1 + rtJitter), 1 + rtJitter, trig);
            var pan = TRand.ar(pWidth.neg, pWidth, trig);
            TGrains.ar(2, trig, bufnum, gRate, gPos * bufDur, dur, pan, 3.0, 4);
        }.value;

        // -- Algorithm 1: Mosaic (multi-speed overlapping layers) --
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

        // -- Algorithm 2: Tunnel (tight loop drone) --
        tunnel = {
            var trig = Dust.ar(dens * 2);
            var ptr = Phasor.ar(0, bufScale * pRate * 0.08, 0, frames) / frames;
            var gPos = (ptr + TRand.ar(pJitter.neg * 0.15, pJitter * 0.15, trig)).wrap(0, 1);
            var gRate = rt * TExpRand.ar(0.995, 1.005, trig);
            var pan = TRand.ar(pWidth.neg * 0.4, pWidth * 0.4, trig);
            TGrains.ar(2, trig, bufnum, gRate, gPos * bufDur, dur * 2.0, pan, 2.5, 4);
        }.value;

        // -- Algorithm 3: Strum (rhythmic cascades) --
        strum = {
            var trig = Impulse.ar(dens);
            var ptr = Phasor.ar(0, bufScale * pRate * 1.5, 0, frames) / frames;
            var gPos = (ptr + TRand.ar(pJitter.neg * 0.3, pJitter * 0.3, trig)).wrap(0, 1);
            var gRate = rt * TExpRand.ar(1 / (1 + (rtJitter * 0.5)), 1 + (rtJitter * 0.5), trig);
            var pan = TRand.ar(pWidth.neg, pWidth, trig);
            TGrains.ar(2, trig, bufnum, gRate, gPos * bufDur, dur * 0.7, pan, 3.0, 4);
        }.value;

        // -- Algorithm 4: Glide (pitch-shifting shimmer) --
        glide = {
            var trig = Dust.ar(dens * 0.8);
            var ptr = Phasor.ar(0, bufScale * pRate * 1.8, 0, frames) / frames;
            var gPos = (ptr + TRand.ar(pJitter.neg, pJitter, trig)).wrap(0, 1);
            var drift = SinOsc.kr(0.07 + (rtJitter * 0.3)).range(0.85, 1.18);
            var gRate = rt * drift * TExpRand.ar(0.97, 1.03, trig);
            var pan = TRand.ar(pWidth.neg, pWidth, trig);
            TGrains.ar(2, trig, bufnum, gRate, gPos * bufDur, dur * 1.3, pan, 2.8, 4);
        }.value;

        // -- Algorithm Selection --
        grains = SelectX.ar(algo.clip(0, 4), [
            haze, mosaic, tunnel, strum, glide
        ]);

        // -- Shared Post-Processing (unchanged) --
        filtered = RLPF.ar(grains, lpfFreq.clip(80, 18000), 0.5);

        delayed = LocalIn.ar(2);
        wet = filtered + (delayed * fb);
        wet = DelayC.ar(wet, 2.0, dlTime.clip(0.001, 2.0));
        LocalOut.ar(wet);

        wet = XFade2.ar(
            filtered,
            wet,
            (dlMix * 2 - 1).clip(-1, 1)
        );

        wet = FreeVerb2.ar(
            wet[0], wet[1],
            mix: vbMix.clip(0, 1),
            room: 0.85,
            damp: 0.5
        );

        sig = (filtered * drMix) + (wet * (1.0 - drMix));

        env = EnvGen.kr(
            Env.new([0, 1, 1, 0], [attack, sustain, release], [\sin, \lin, \sin]),
            doneAction: 2
        );

        sig = Limiter.ar(sig * env * amp, 0.95);
        Out.ar(out, sig);
    }).asBytes]]);

    audioFiles.do({ |path, i|
        var startTime = (i / numFiles.max(1)) * 2.0;
        var synthDur = duration - startTime;
        var sustainDur = (synthDur - 4.0).max(1.0);

        score.add([startTime, [\s_new, \gardenMicrocosm, 1000 + i, 0, 0,
            \bufnum, i,
            \algo, algo,
            \dens, grainDensity,
            \dur, grainDuration,
            \pRate, posRate,
            \pJitter, posJitter,
            \rt, rate,
            \rtJitter, rateJitter,
            \pWidth, panWidth,
            \lpfFreq, lpf,
            \dlMix, delayMix,
            \dlTime, delayTime,
            \fb, feedback,
            \vbMix, reverbMix,
            \drMix, dryMix,
            \attack, 2.0,
            \sustain, sustainDur,
            \release, 2.0,
            \amp, 2.0 / numFiles.max(1).sqrt
        ]]);
    });

    score.add([duration, [\c_set, 0, 0]]);
    score.sort;

    ("granular_sampling: rendering " ++ duration ++ "s to " ++ outPath).postln;

    oscPath = outputDir +/+ "granular_sampling_osc.osc";

    Score.write(score.score, oscPath);
    nrtCmd = (thisProcess.platform.resourceDir +/+ "scsynth").shellQuote
        ++ " -N " ++ oscPath.shellQuote
        ++ " _ " ++ outPath.shellQuote
        ++ " 48000 AIFF float -o 2";
    ("granular_sampling: " ++ nrtCmd).postln;
    nrtCmd.unixCmdGetStdOut;
    "granular_sampling: render complete.".postln;
});
)
