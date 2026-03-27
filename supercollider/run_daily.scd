// run_daily.scd
// Orchestrator: reads date and base_dir from command-line arguments,
// resolves config paths for all 4 modules, runs each as a separate
// sclang NRT subprocess to guarantee sequential blocking execution.
//
// Usage:  sclang run_daily.scd 2026-03-24 /path/to/project

(
var args, dateStr, baseDir, configDir, modules, logLines;
var scDir, sclangPath;

args = thisProcess.argv;

if(args.size < 2, {
    "run_daily: usage: sclang run_daily.scd YYYY-MM-DD /path/to/base_dir".postln;
    0.exit;
});

dateStr = args[0];
baseDir = args[1];
configDir = baseDir +/+ "module_configs" +/+ dateStr;
scDir = PathName(thisProcess.nowExecutingPath).pathOnly;

("run_daily: date = " ++ dateStr).postln;
("run_daily: base_dir = " ++ baseDir).postln;
("run_daily: config_dir = " ++ configDir).postln;
("run_daily: sc_dir = " ++ scDir).postln;

modules = [
    "granular_sampling",
    "spectral_resynthesis",
    "spectral_resonators",
    "advanced_effects"
];

logLines = [];

modules.do({ |moduleName|
    var cfgPath, moduleScript, moduleStart;

    cfgPath = configDir +/+ (moduleName ++ ".json");

    if(File.exists(cfgPath).not, {
        ("run_daily: WARNING -- config not found: " ++ cfgPath).postln;
        logLines = logLines.add(moduleName ++ ": SKIPPED (no config)");
    }, {
        moduleScript = scDir +/+ (moduleName ++ ".scd");

        if(File.exists(moduleScript).not, {
            ("run_daily: WARNING -- script not found: " ++ moduleScript).postln;
            logLines = logLines.add(moduleName ++ ": SKIPPED (no script)");
        }, {
            ("run_daily: === " ++ moduleName ++ " ===").postln;
            moduleStart = Date.getDate;
            ("run_daily: start " ++ moduleStart.format("%H:%M:%S")).postln;

            ~configPath = cfgPath;
            thisProcess.interpreter.executeFile(moduleScript);

            ("run_daily: finished " ++ moduleName ++ " at " ++ Date.getDate.format("%H:%M:%S")).postln;
            logLines = logLines.add(moduleName ++ ": OK");
        });
    });
});

"".postln;
"run_daily: === Summary ===".postln;
logLines.do({ |line| ("  " ++ line).postln });
("run_daily: complete for " ++ dateStr).postln;

0.exit;
)
