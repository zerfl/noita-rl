// CPU Usage (Precise) summary of an ETL for chosen processes: per thread CPU, waits, readying
// threads and the switch-in stacks where the thread blocked. Writes JSON.
// Usage: etwcpu <etl> --pids 1,2 [--from <unix s> --to <unix s>] [--symbols <dir>] --out <json>

using System.Text.Json;
using Microsoft.Windows.EventTracing;
using Microsoft.Windows.EventTracing.Cpu;
using Microsoft.Windows.EventTracing.Symbols;

var etl = args[0];
string Arg(string name, string def = "") { var i = Array.IndexOf(args, name); return i >= 0 ? args[i + 1] : def; }
var pids = Arg("--pids").Split(',', StringSplitOptions.RemoveEmptyEntries).Select(int.Parse).ToHashSet();
var from = Arg("--from") is var f && f != "" ? DateTimeOffset.FromUnixTimeMilliseconds((long)(double.Parse(f, System.Globalization.CultureInfo.InvariantCulture) * 1000)) : DateTimeOffset.MinValue;
var to = Arg("--to") is var t && t != "" ? DateTimeOffset.FromUnixTimeMilliseconds((long)(double.Parse(t, System.Globalization.CultureInfo.InvariantCulture) * 1000)) : DateTimeOffset.MaxValue;
var symDir = Arg("--symbols");
var outPath = Arg("--out");
var topThreads = int.Parse(Arg("--threads", "4"));

using var tp = TraceProcessor.Create(etl, new TraceProcessorSettings { AllowLostEvents = true });
var sched = tp.UseCpuSchedulingData();
var syms = tp.UseSymbols();
tp.Process();
if (symDir != "")
    await syms.Result.LoadSymbolsAsync(new SymCachePath(Path.Combine(symDir, "symcache")),
        new SymbolPath($"srv*{Path.Combine(symDir, "sym")}*https://msdl.microsoft.com/download/symbols"));

// Frames that only say "the thread waited", skipped when naming the caller that waited.
string[] plumbing = ["ntoskrnl.exe", "ntdll.dll", "wow64.dll", "wow64cpu.dll", "wow64win.dll", "wow64base.dll",
    "kernelbase.dll", "kernel32.dll", "win32u.dll", "hal.dll", "win32kfull.sys", "win32kbase.sys", "win32k.sys"];

string Name(StackFrame fr)
{
    if (!fr.HasValue) return "?";
    var img = fr.Image?.FileName ?? "?";
    var fn = fr.Symbol?.FunctionName;
    return fn != null ? $"{img}!{fn}" : $"{img}+0x{fr.RelativeVirtualAddress.Value:x}";
}

// Frames[0] is the innermost frame.
(string reason, string waitFn, string[] top) Blocking(IStackSnapshot? s)
{
    if (s == null || s.Frames.Count == 0) return ("(no stack)", "(no stack)", []);
    var names = s.Frames.Select(Name).ToArray();
    var waitFn = "(kernel)";
    string? caller = null;
    for (int i = 0; i < s.Frames.Count; i++)
    {
        var img = (s.Frames[i].Image?.FileName ?? "?").ToLowerInvariant();
        if (img == "ntdll.dll" && waitFn == "(kernel)") waitFn = names[i];
        if (!plumbing.Contains(img)) { caller = names[i]; break; }
    }
    return (caller ?? waitFn, waitFn, names.Take(30).ToArray());
}

var rows = sched.Result.ThreadActivity
    .Where(a => a.Process != null && pids.Contains(a.Process.Id))
    .Where(a => a.StartTime.DateTimeOffset >= from && a.StartTime.DateTimeOffset <= to)
    .ToList();

double Ms(TraceDuration? d) => d.HasValue ? (double)d.Value.TotalMilliseconds : 0;

var procs = new List<object>();
foreach (var pg in rows.GroupBy(a => a.Process.Id).OrderBy(g => g.Key))
{
    var threads = new List<object>();
    var byThread = pg.GroupBy(a => a.Thread.Id)
        .Select(g => (tid: g.Key, rows: g.ToList(), cpu: g.Sum(a => Ms(a.Duration))))
        .OrderByDescending(x => x.cpu).ToList();
    foreach (var (tid, trows, cpu) in byThread)
    {
        var detail = threads.Count < topThreads || args.Contains($"--tid={tid}");
        var th = trows[0].Thread;
        var d = new Dictionary<string, object?>
        {
            ["tid"] = tid,
            ["start"] = th.StartFrame.HasValue ? Name(th.StartFrame) : null,
            ["cpu_ms"] = Math.Round(cpu, 1),
            ["switch_ins"] = trows.Count,
            ["wait_ms"] = Math.Round(trows.Sum(a => Ms(a.WaitingDuration)), 1),
            ["ready_ms"] = Math.Round(trows.Sum(a => Ms(a.ReadyDuration)), 1),
        };
        if (detail)
        {
            d["readying"] = trows.GroupBy(a => a.ReadyingProcess == null ? "(none)" :
                    $"{a.ReadyingProcess.ImageName} ({a.ReadyingProcess.Id}){(a.ReadyingProcess.Id == pg.Key ? " same" : "")} tid {a.ReadyingThread?.Id}")
                .Select(g => new { who = g.Key, count = g.Count(), wait_ms = Math.Round(g.Sum(a => Ms(a.WaitingDuration)), 1) })
                .OrderByDescending(x => x.wait_ms).Take(8).ToList();
            d["readying_process"] = trows.GroupBy(a => a.ReadyingProcess == null ? "(none)" :
                    a.ReadyingProcess.Id == pg.Key ? "(same process)" : a.ReadyingProcess.ImageName)
                .Select(g => new { who = g.Key, count = g.Count(), wait_ms = Math.Round(g.Sum(a => Ms(a.WaitingDuration)), 1) })
                .OrderByDescending(x => x.wait_ms).ToList();
            var blocked = trows.Select(a => (a, b: Blocking(a.SwitchIn.Stack))).ToList();
            d["wait_reasons"] = blocked.GroupBy(x => $"{x.b.waitFn} <- {x.b.reason}")
                .Select(g => new { reason = g.Key, count = g.Count(), wait_ms = Math.Round(g.Sum(x => Ms(x.a.WaitingDuration)), 1) })
                .OrderByDescending(x => x.wait_ms).Take(8).ToList();
            d["wait_stacks"] = blocked.GroupBy(x => string.Join(" / ", x.b.top))
                .Select(g => new { count = g.Count(), wait_ms = Math.Round(g.Sum(x => Ms(x.a.WaitingDuration)), 1), stack = g.First().b.top })
                .OrderByDescending(x => x.wait_ms).Take(4).ToList();
        }
        threads.Add(d);
    }
    procs.Add(new
    {
        pid = pg.Key,
        image = pg.First().Process.ImageName,
        cpu_ms = Math.Round(pg.Sum(a => Ms(a.Duration)), 1),
        threads_seen = byThread.Count,
        threads,
    });
}

double Unix(TraceTimestamp ts) => ts.DateTimeOffset.ToUnixTimeMilliseconds() / 1000.0;
var fromUnix = rows.Count > 0 ? rows.Min(a => Unix(a.StartTime)) : 0;
var toUnix = rows.Count > 0 ? rows.Max(a => Unix(a.StopTime)) : 0;
var json = JsonSerializer.Serialize(new { etl, from_unix = fromUnix, to_unix = toUnix, symbols = symDir != "", processes = procs },
    new JsonSerializerOptions { WriteIndented = true });
if (outPath != "") File.WriteAllText(outPath, json + "\n"); else Console.WriteLine(json);
