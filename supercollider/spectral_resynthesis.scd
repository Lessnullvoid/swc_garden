// spectral_resynthesis.scd
// Panharmonium-inspired spectral processor: FFT analysis with voice limiting
// (PV_MagAbove), spectral blur (PV_MagSmear), spectral warp (PV_BinShift),
// feedback loop, glide smoothing, tilt EQ, FreeVerb2, and dry/wet blend.
// Reads ~configPath from the interpreter environment (set by run_daily.scd).

(
var config, params, audioBatchDir, outputDir, duration;
var audioFiles, score, outPath, oscPath;
var voicesThreshold, blur, warpStretch, warpShift;
var rate, feedback, tiltDb, glideFreq;
var reverbMix, reverbRoom, dryMix;
var ampAttack, ampDecay;
var numFiles, nrtCmd;

config = File.readAllString(~configPath).parseJSON;
params = config["params"];

audioBatchDir = config["audio_batch_dir"];
outputDir = config["output_dir"];
duration = config["duration"].asFloat;

voicesThreshold = params["voices_threshold"].asFloat;
blur = params["blur"].asFloat;
warpStretch = params["warp_stretch"].asFloat;
warpShift = params["warp_shift"].asFloat;
rate = params["rate"].asFloat;
feedback = params["feedback"].asFloat;
tiltDb = params["tilt_db"].asFloat;
glideFreq = params["glide_freq"].asFloat;
reverbMix = params["reverb_mix"].asFloat;
reverbRoom = params["reverb_room"].asFloat;
dryMix = params["dry_mix"].asFloat;
ampAttack = params["amp_attack"].asFloat;
ampDecay = params["amp_decay"].asFloat;

audioFiles = (audioBatchDir +/+ "*.wav").pathMatch
    ++ (audioBatchDir +/+ "*.aif").pathMatch
    ++ (audioBatchDir +/+ "*.aiff").pathMatch;

if(audioFiles.size == 0, {
    ("spectral_resynthesis: no audio files found in " ++ audioBatchDir).postln;
    nil;
}, {
    numFiles = audioFiles.size;
    outPath = outputDir +/+ "spectral_resynthesis.aiff";

    score = Score.new;

    audioFiles.do({ |path, i|
        score.add([0.0, [\b_allocReadChannel, i, path, 0, -1, 0]]);
    });

    audioFiles.do({ |path, i|
        score.add([0.0, [\b_alloc, 100 + i, 2048]]);
    });

    score.add([0.0, [\d_recv, SynthDef(\gardenPanharmonium, { |out = 0,
        bufnum = 0, fftBuf = 100,
        voicesThr = 0.01, blurAmt = 12,
        wStretch = 1.0, wShift = 0.0,
        rt = 1.0, fb = 0.0,
        tiltDb = 0.0, glFreq = 8000,
        vbMix = 0.3, vbRoom = 0.8,
        drMix = 0.2,
        attack = 2.0, sustain = 116.0, release = 2.0,
        amp = 0.5|

        var src, chain, resynth, processed, wet, sig, env;
        var fbIn;

        src = PlayBuf.ar(1, bufnum, BufRateScale.kr(bufnum) * rt, loop: 1);

        fbIn = LocalIn.ar(1);
        src = src + (fbIn * fb.clip(0, 0.5));

        chain = FFT(fftBuf, src);

        chain = PV_MagAbove(chain, voicesThr.clip(0.0001, 1.0));

        chain = PV_MagSmear(chain, blurAmt.clip(1, 64));

        chain = PV_BinShift(chain,
            wStretch.clip(0.5, 2.0),
            wShift.clip(-8, 8)
        );

        resynth = IFFT(chain);

        processed = RLPF.ar(resynth, glFreq.clip(200, 18000), 0.6);

        processed = BLowShelf.ar(processed, 300, 1.0, tiltDb * -0.5);
        processed = BHiShelf.ar(processed, 3000, 1.0, tiltDb * 0.5);

        LocalOut.ar(Limiter.ar(processed, 0.7));

        wet = Pan2.ar(processed, 0);
        wet = FreeVerb2.ar(
            wet[0], wet[1],
            mix: vbMix.clip(0, 1),
            room: vbRoom.clip(0.1, 0.99),
            damp: 0.45
        );

        sig = (Pan2.ar(processed, 0) * drMix) + (wet * (1.0 - drMix));

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

        score.add([startTime, [\s_new, \gardenPanharmonium, 2000 + i, 0, 0,
            \bufnum, i,
            \fftBuf, 100 + i,
            \voicesThr, voicesThreshold,
            \blurAmt, blur,
            \wStretch, warpStretch,
            \wShift, warpShift,
            \rt, rate,
            \fb, feedback,
            \tiltDb, tiltDb,
            \glFreq, glideFreq,
            \vbMix, reverbMix,
            \vbRoom, reverbRoom,
            \drMix, dryMix,
            \attack, 2.0,
            \sustain, sustainDur,
            \release, 2.0,
            \amp, 3.0 / numFiles.max(1).sqrt
        ]]);
    });

    score.add([duration, [\c_set, 0, 0]]);
    score.sort;

    ("spectral_resynthesis: rendering " ++ duration ++ "s to " ++ outPath).postln;

    oscPath = outputDir +/+ "spectral_resynthesis_osc.osc";

    Score.write(score.score, oscPath);
    nrtCmd = (thisProcess.platform.resourceDir +/+ "scsynth").shellQuote
        ++ " -N " ++ oscPath.shellQuote
        ++ " _ " ++ outPath.shellQuote
        ++ " 48000 AIFF float -o 2";
    ("spectral_resynthesis: " ++ nrtCmd).postln;
    nrtCmd.unixCmdGetStdOut;
    "spectral_resynthesis: render complete.".postln;
});
)
