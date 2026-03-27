// spectral_resonators.scd
// 4ms SMR-inspired 6-band resonator instrument.
// Six parallel Resonz bands with scale-based frequencies, odd/even stereo
// routing, noise excitation, per-band LFO morph drift, FreeVerb2, dry/wet
// blend, LeakDC, and Limiter.
// Reads ~configPath from the interpreter environment (set by run_daily.scd).

(
var config, params, audioBatchDir, outputDir, duration;
var audioFiles, score, outPath, oscPath;
var bandFreqs, rqVal, noiseMix, rate, exciteGain, morphRate;
var reverbMix, reverbRoom, dryMix, ampAttack, ampDecay;
var numFiles, nrtCmd;

config = File.readAllString(~configPath).parseJSON;
params = config["params"];

audioBatchDir = config["audio_batch_dir"];
outputDir = config["output_dir"];
duration = config["duration"].asFloat;

bandFreqs = config["band_freqs"].collect(_.asFloat);
rqVal = params["rq"].asFloat;
noiseMix = params["noise_mix"].asFloat;
rate = params["rate"].asFloat;
exciteGain = params["excite_gain"].asFloat;
morphRate = params["morph_rate"].asFloat;
reverbMix = params["reverb_mix"].asFloat;
reverbRoom = params["reverb_room"].asFloat;
dryMix = params["dry_mix"].asFloat;
ampAttack = params["amp_attack"].asFloat;
ampDecay = params["amp_decay"].asFloat;

audioFiles = (audioBatchDir +/+ "*.wav").pathMatch
    ++ (audioBatchDir +/+ "*.aif").pathMatch
    ++ (audioBatchDir +/+ "*.aiff").pathMatch;

if(audioFiles.size == 0, {
    ("spectral_resonators: no audio files found in " ++ audioBatchDir).postln;
    nil;
}, {
    numFiles = audioFiles.size;
    outPath = outputDir +/+ "spectral_resonators.aiff";

    score = Score.new;

    audioFiles.do({ |path, i|
        score.add([0.0, [\b_allocReadChannel, i, path, 0, -1, 0]]);
    });

    score.add([0.0, [\d_recv, SynthDef(\gardenSMR, { |out = 0,
        bufnum = 0,
        f0 = 55, f1 = 110, f2 = 165, f3 = 220, f4 = 275, f5 = 330,
        rq = 0.01, noiseAmt = 0.2, rt = 1.0, excGain = 1.0,
        mRate = 0.1,
        vbMix = 0.3, vbRoom = 0.8, drMix = 0.2,
        attack = 3.0, sustain = 114.0, release = 3.0,
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
            var driftedFreq = (freq + drift).clip(20, 20000);
            Resonz.ar(excitation, driftedFreq, rq.clip(0.001, 0.5));
        });

        left  = bands[0] + bands[2] + bands[4];
        right = bands[1] + bands[3] + bands[5];

        dry = LeakDC.ar([left, right]);

        wet = FreeVerb2.ar(
            dry[0], dry[1],
            mix: vbMix.clip(0, 1),
            room: vbRoom.clip(0.1, 0.99),
            damp: 0.4
        );

        sig = (dry * drMix) + (wet * (1.0 - drMix));

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
        var sustainDur = (synthDur - 6.0).max(1.0);

        score.add([startTime, [\s_new, \gardenSMR, 3000 + i, 0, 0,
            \bufnum, i,
            \f0, bandFreqs[0] ? 55,
            \f1, bandFreqs[1] ? 110,
            \f2, bandFreqs[2] ? 165,
            \f3, bandFreqs[3] ? 220,
            \f4, bandFreqs[4] ? 275,
            \f5, bandFreqs[5] ? 330,
            \rq, rqVal,
            \noiseAmt, noiseMix,
            \rt, rate,
            \excGain, exciteGain,
            \mRate, morphRate,
            \vbMix, reverbMix,
            \vbRoom, reverbRoom,
            \drMix, dryMix,
            \attack, 3.0,
            \sustain, sustainDur,
            \release, 3.0,
            \amp, 4.0 / numFiles.max(1).sqrt
        ]]);
    });

    score.add([duration, [\c_set, 0, 0]]);
    score.sort;

    ("spectral_resonators: rendering " ++ duration ++ "s to " ++ outPath).postln;

    oscPath = outputDir +/+ "spectral_resonators_osc.osc";

    Score.write(score.score, oscPath);
    nrtCmd = (thisProcess.platform.resourceDir +/+ "scsynth").shellQuote
        ++ " -N " ++ oscPath.shellQuote
        ++ " _ " ++ outPath.shellQuote
        ++ " 48000 AIFF float -o 2";
    ("spectral_resonators: " ++ nrtCmd).postln;
    nrtCmd.unixCmdGetStdOut;
    "spectral_resonators: render complete.".postln;
});
)
