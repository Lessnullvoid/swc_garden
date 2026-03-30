// advanced_effects.scd
// Multi-algorithm FX processor (design inspired by Milky Way / Hologram pedals).
// 6 algorithms: Hall Reverb, Ping-Pong Delay, Tape Echo, Chorus,
// Overdrive, Freezer.  SelectX for smooth scan between neighbors.
// Macro controls (p1, p2, p3) map differently per algorithm.
// Dry/wet mix, mid-side stereo width, output VCA, Limiter.
// Reads ~configPath from the interpreter environment (set by run_daily.scd).

(
var config, params, audioBatchDir, outputDir, duration;
var audioFiles, score, outPath, oscPath;
var algo, mix, p1, p2, p3, width, level, rate;
var ampAttack, ampDecay;
var numFiles, nrtCmd;

config = File.readAllString(~configPath).parseJSON;
params = config["params"];

audioBatchDir = config["audio_batch_dir"];
outputDir = config["output_dir"];
duration = config["duration"].asFloat;

algo = params["algo"].asFloat;
mix = params["mix"].asFloat;
p1 = params["param1"].asFloat;
p2 = params["param2"].asFloat;
p3 = params["param3"].asFloat;
width = params["stereo_width"].asFloat;
level = params["level"].asFloat;
rate = params["rate"].asFloat;
ampAttack = params["amp_attack"].asFloat;
ampDecay = params["amp_decay"].asFloat;

audioFiles = (audioBatchDir +/+ "*.wav").pathMatch
    ++ (audioBatchDir +/+ "*.aif").pathMatch
    ++ (audioBatchDir +/+ "*.aiff").pathMatch;

if(audioFiles.size == 0, {
    ("advanced_effects: no audio files found in " ++ audioBatchDir).postln;
    nil;
}, {
    numFiles = audioFiles.size;
    outPath = outputDir +/+ "advanced_effects.aiff";

    score = Score.new;

    audioFiles.do({ |path, i|
        score.add([0.0, [\b_allocReadChannel, i, path, 0, -1, 0]]);
    });

    // FFT buffers for the freezer algorithm (one per voice)
    audioFiles.do({ |path, i|
        score.add([0.0, [\b_alloc, 200 + i, 2048]]);
        score.add([0.0, [\b_alloc, 300 + i, 2048]]);
    });

    score.add([0.0, [\d_recv, SynthDef(\gardenMultiFX, { |out = 0,
        bufnum = 0, fftBufL = 200, fftBufR = 300,
        algo = 0, mix = 0.5,
        p1 = 0.5, p2 = 0.5, p3 = 0.5,
        width = 0.5, level = 0.8,
        rt = 1.0,
        attack = 2.0, sustain = 116.0, release = 2.0,
        amp = 0.5|

        var src, dry, env;
        var hall, ppDelay, tapeEcho, chorus, drive, freeze;
        var wet, mid, side, sig;

        // -- Input Layer --
        src = PlayBuf.ar(1, bufnum, BufRateScale.kr(bufnum) * rt, loop: 1);
        dry = Pan2.ar(src, 0);

        // -- Algorithm 0: Hall Reverb --
        hall = FreeVerb2.ar(dry[0], dry[1],
            mix: 1.0,
            room: p1.linlin(0, 1, 0.3, 0.98),
            damp: p2.linlin(0, 1, 0.1, 0.8)
        );
        hall = DelayC.ar(hall, 0.1, p3.linlin(0, 1, 0.0, 0.08));

        // -- Algorithm 1: Ping-Pong Delay (self-contained CombC) --
        ppDelay = {
            var t = p1.linexp(0, 1, 0.05, 1.2);
            var decayT = p2.linlin(0, 1, 0.2, 6.0);
            var delL = CombC.ar(dry[0], 2, t, decayT);
            var delR = CombC.ar(dry[1], 2, t * 0.75, decayT);
            var crossAmt = p3.linlin(0, 1, 0.0, 0.5);
            [
                (delL * (1 - crossAmt)) + (delR * crossAmt),
                (delR * (1 - crossAmt)) + (delL * crossAmt)
            ];
        }.value;

        // -- Algorithm 2: Tape Echo --
        tapeEcho = {
            var t = p1.linexp(0, 1, 0.08, 0.8);
            var decayT = p2.linlin(0, 1, 0.5, 8.0);
            var wow = SinOsc.kr(
                0.3 + (p3 * 2.0),
                0
            ).range(0.998, 1.002);
            var echoL = CombC.ar(dry[0], 2, t * wow, decayT);
            var echoR = CombC.ar(dry[1], 2, t * 1.05 * wow, decayT);
            var filtered = LPF.ar([echoL, echoR], 4000);
            (dry * 0.3) + (filtered * 0.7);
        }.value;

        // -- Algorithm 3: Chorus --
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

        // -- Algorithm 4: Overdrive --
        drive = {
            var gain = p1.linexp(0, 1, 1.0, 20.0);
            var tone = p2.linexp(0, 1, 800, 12000);
            var asym = p3.linlin(0, 1, 0.0, 0.3);
            var driven = ((dry * gain) + asym).tanh;
            LPF.ar(driven, tone);
        }.value;

        // -- Algorithm 5: Freezer --
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

        // -- Scan / Select --
        wet = SelectX.ar(algo.clip(0, 5), [
            hall, ppDelay, tapeEcho, chorus, drive, freeze
        ]);

        // -- Mix Layer --
        wet = XFade2.ar(dry, wet, (mix * 2 - 1).clip(-1, 1));

        // -- Stereo Width (mid-side) --
        mid = (wet[0] + wet[1]) * 0.5;
        side = (wet[0] - wet[1]) * 0.5;
        side = side * width.linlin(0, 1, 0.2, 1.5);
        sig = [mid + side, mid - side];

        // -- Output VCA + Envelope --
        sig = LeakDC.ar(sig);
        env = EnvGen.kr(
            Env.new([0, 1, 1, 0], [attack, sustain, release], [\sin, \lin, \sin]),
            doneAction: 2
        );
        sig = Limiter.ar(sig * env * amp * level, 0.95);
        Out.ar(out, sig);
    }).asBytes]]);

    audioFiles.do({ |path, i|
        var startTime = (i / numFiles.max(1)) * 2.0;
        var synthDur = duration - startTime;
        var sustainDur = (synthDur - 4.0).max(1.0);

        score.add([startTime, [\s_new, \gardenMultiFX, 4000 + i, 0, 0,
            \bufnum, i,
            \fftBufL, 200 + i,
            \fftBufR, 300 + i,
            \algo, algo,
            \mix, mix,
            \p1, p1,
            \p2, p2,
            \p3, p3,
            \width, width,
            \level, level,
            \rt, rate,
            \attack, 2.0,
            \sustain, sustainDur,
            \release, 2.0,
            \amp, 2.0 / numFiles.max(1).sqrt
        ]]);
    });

    score.add([duration, [\c_set, 0, 0]]);
    score.sort;

    ("advanced_effects: rendering " ++ duration ++ "s to " ++ outPath).postln;

    oscPath = outputDir +/+ "advanced_effects_osc.osc";

    Score.write(score.score, oscPath);
    nrtCmd = (thisProcess.platform.resourceDir +/+ "scsynth").shellQuote
        ++ " -N " ++ oscPath.shellQuote
        ++ " _ " ++ outPath.shellQuote
        ++ " 48000 AIFF float -o 2 -a 128";
    ("advanced_effects: " ++ nrtCmd).postln;
    nrtCmd.unixCmdGetStdOut;
    "advanced_effects: render complete.".postln;
});
)
