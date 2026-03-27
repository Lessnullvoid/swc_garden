// granular_sampling.scd
// Microcosm-inspired granular processor: TGrains grain engine with Phasor
// scanning, feedback delay smear, FreeVerb2 reverb, and dry/wet blend.
// Reads ~configPath from the interpreter environment (set by run_daily.scd).

(
var config, params, audioBatchDir, outputDir, duration;
var audioFiles, score, outPath, oscPath;
var grainDensity, grainDuration, posRate, posJitter;
var rate, rateJitter, panWidth;
var lpf, delayMix, delayTime, feedback, reverbMix, dryMix;
var ampAttack, ampDecay;
var numFiles, nrtCmd;

config = File.readAllString(~configPath).parseJSON;
params = config["params"];

audioBatchDir = config["audio_batch_dir"];
outputDir = config["output_dir"];
duration = config["duration"].asFloat;

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

        var trig, ptr, grainPos, grainRate, pan, grains;
        var filtered, delayed, wet, sig, env;
        var frames, bufDur;

        frames = BufFrames.kr(bufnum);
        bufDur = BufDur.kr(bufnum);

        trig = Dust.ar(dens);

        ptr = Phasor.ar(
            trig: 0,
            rate: BufRateScale.kr(bufnum) * pRate,
            start: 0,
            end: frames
        ) / frames;

        grainPos = (ptr + TRand.ar(pJitter.neg, pJitter, trig)).wrap(0, 1);

        grainRate = rt * TExpRand.ar(1 / (1 + rtJitter), 1 + rtJitter, trig);
        pan = TRand.ar(pWidth.neg, pWidth, trig);

        grains = TGrains.ar(
            numChannels: 2,
            trigger: trig,
            bufnum: bufnum,
            rate: grainRate,
            centerPos: grainPos * bufDur,
            dur: dur,
            pan: pan,
            amp: 3.0,
            interp: 4
        );

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
