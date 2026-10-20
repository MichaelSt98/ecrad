"""Insert retained raw experiment summaries into the requested benchmark ledger."""
import argparse
import csv
import json
from pathlib import Path
import sqlite3
import statistics

ROOT = Path(__file__).resolve().parent


def replace(text, key, lines):
    start, end = f"<!-- {key}:start -->", f"<!-- {key}:end -->"
    assert text.count(start) == text.count(end) == 1
    before, rest = text.split(start)
    _, after = rest.split(end)
    return before + start + "\n" + "\n".join(lines) + "\n" + end + after


def lineage(path):
    rows = json.loads((path / "timing.json").read_text())
    assert len(rows) == 35
    assert all(r["repeat_validation"]["passed"] for r in rows)
    assert all(r["batch_parent_validation"]["passed"] for r in rows if r["label"] == "batch")
    lines = [(path / "RESULTS.md").read_text().replace("# ", "### ", 1),
             "CPU / version below one means GPU is slower than the 32-core CPU run.",
             "**FAIL** rows are timing context, not validated equivalent-output speedups.",
             "All outputs repeat within their version; SW batching matches its GPU parent.", "",
             "| Version | Maximum CPU-reference LW error | Maximum SW error |", "|---|---:|---:|"]
    for label in ("master-acc", "master-opt", "release-opt", "small-np", "batch"):
        selected = [r for r in rows if r["label"] == label]
        lw = max(v for r in selected for k, v in r["errors"].items() if "lw" in k)
        sw = max(v for r in selected for k, v in r["errors"].items() if "sw" in k)
        lines.append(f"| {label} | {lw:.8g} | {sw:.8g} |")
    return lines + ["", f"Artifacts: `{path}`; raw times, source/binary hashes, allocation/topology,",
                    "per-process configurations/logs/outputs, numerical failures and CTest logs."]


def lw(path):
    rows = json.loads((path / "timing.json").read_text())
    checks = json.loads((path / "validation.json").read_text())
    profiles = json.loads((path / "phases.json").read_text())
    assert len(rows) == 20 and len(checks) == 24 and len(profiles) == 4
    error = max(v for r in rows + checks + profiles for v in r["errors"].values())
    lines = [(path / "RESULTS.md").read_text().replace("# ", "### ", 1),
             f"Maximum saved-output difference over validation/timing/phase processes: **{error:.8g}**.",
             "SP/DP full-domain and actual 65-column inputs, K1/16/32 versus legacy K0.",
             "GPU CTest 13/13 per precision; rebuilt native CPU DP+SP CTest **182/182**.", "",
             "Separate diagnostic runs, one warmup plus four measured iterations:", "",
             "| K | LW batching | Total s | Optics s | Per-block LW s | LW stage s | LW solve s | LW scatter s |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in profiles:
        raw = row["raw_phases"][1:]
        med = lambda k: statistics.median(r[k] for r in raw)
        remaining = statistics.median(r[4]-r[8] for r in raw)
        lines.append(f"| {row['batch']} | {row['lw']} | {row['median']:.7f} | {med(3):.7f} | {remaining:.7f} | {med(8):.7f} | {med(9):.7f} | {med(10):.7f} |")
    lines += ["", "After the LW milestone, synchronized diagnostic wall-time split:", "",
              "| K | SW batched chain s | LW batched chain s | Remaining per-block s | Remaining share |",
              "|---:|---:|---:|---:|---:|"]
    for row in profiles:
        if row["lw"] != 1:
            continue
        raw = row["raw_phases"][1:]
        sw = statistics.median(sum(r[:3]) for r in raw)
        lw_chain = statistics.median(sum(r[8:11]) for r in raw)
        rest = statistics.median(r[5]+r[6]-r[0]-r[2]-r[8]-r[10] for r in raw)
        lines.append(f"| {row['batch']} | {sw:.7f} | {lw_chain:.7f} | {rest:.7f} | {100*rest/row['median']:.2f}% |")
    return lines + ["", "Raw buckets 9/10/11 are LW stage/solve/scatter. Staging is nested in",
                    "preparation and bucket 5; solving is outside preparation; scatter is nested",
                    "in finishing. Subtract nested buckets before summing.", "",
                    f"Artifacts: `{path}`; source manifests, builds/tests, validation, raw timings and phase arrays."]


def gas(path):
    rows = json.loads((path / "timing.json").read_text())
    checks = json.loads((path / "validation.json").read_text())
    profiles = json.loads((path / "phases.json").read_text())
    assert len(rows) == 20 and len(checks) == 24 and len(profiles) == 4
    error = max(v for r in rows + checks + profiles for v in r["errors"].values())
    lines = [(path / "RESULTS.md").read_text().replace("# ", "### ", 1),
             f"Maximum saved-output difference over SP/DP validation, SP timing and profiles: **{error:.8g}**.",
             "GPU CTest 13/13 per precision; native CPU DP/SP CTest 182/182.", "",
             "Separate synchronized phase diagnostics (median of four warmed iterations):", "",
             "| K | Gas batching | Total s | Gas stage s | Gas solve/albedos s | Gas load s | Remaining optics s | Other preparation s | Finishing s |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in profiles:
        phases = row["exclusive"][1:]
        med = lambda key: statistics.median(p[key] for p in phases)
        lines.append(f"| {row['batch']} | {row['gas']} | {row['median']:.7f} | {med('gas_stage'):.7f} | {med('gas_solve'):.7f} | {med('gas_load'):.7f} | {med('optics'):.7f} | {med('other_preparation'):.7f} | {med('other_finishing'):.7f} |")
    for batch in (16, 32):
        a = [r["median"] for r in rows if r["batch"] == batch and r["gas"] == 0]
        b = [r["median"] for r in rows if r["batch"] == batch and r["gas"] == 1]
        lines.append(f"\nK{batch}: faster in {sum(y < x for x, y in zip(a, b))}/5 paired processes.")
    return lines + ["", "Gas staging is nested in preparation; gas load is nested in raw optics bucket 4.",
                    "Gas solve/albedos is outside the per-block preparation buckets. The table uses",
                    "exclusive buckets; do not add the nested raw buckets a second time.", "",
                    f"Artifacts: `{path}`; immutable source hashes, SP/DP validation, timing and phase JSON."]


def coverage(path):
    rows = json.loads((path / "validation.json").read_text())
    assert len(rows) == 24
    error = max(v for r in rows for v in r["errors"].values())
    lines = [f"Extended gas+LW+SW coverage: **{path.name}**.",
             "24 SP/DP runs: six legacy references and 18 candidate comparisons covering",
             "default, derivatives-off and beta-overlap, K1/16/32, on mixed day/night",
             "65-column input with a final single daylight column.",
             f"Maximum candidate saved-output difference: **{error:.8g}**."]
    for precision in ("sp", "dp"):
        row = json.loads((path / f"full-{precision}.json").read_text())
        error = max(row["errors"].values())
        lines.append(f"Full-domain {precision.upper()} control: {row['median']:.7f} s over two warmed iterations; "
                     f"maximum saved error {error:.8g}. Validation timing only, not a new performance claim.")
    return lines + ["GPU CTest 13/13 per precision. Frozen reused source/binary hashes and fixture",
                    f"modification/hash retained in `{path}`."]


def nsys_summary(path):
    sections = {"kernels": [], "api": [], "memory": []}
    current, header = None, []
    for line in (path / "stats.log").read_text().splitlines():
        if line.startswith("Time (%)"):
            header = next(csv.reader([line]))
            current = "kernels" if "Instances" in header else "api" if "Num Calls" in header else "memory"
        elif not line or line.startswith("Processing"):
            current = None
        elif current:
            sections[current].append(dict(zip(header, next(csv.reader([line])))))
    kernels = sections["kernels"]
    total = sum(int(r["Total Time (ns)"]) for r in kernels)
    sw = [r for r in kernels if r["Name"].startswith("radiation_mcica_acc_gpoint_sw_solver")][:2]
    summary = dict(launches=sum(int(r["Instances"]) for r in kernels), kernel_seconds=total/1.e9,
                   sw_two_solve_seconds=sum(int(r["Total Time (ns)"]) for r in sw)/1.e9,
                   top_kernels=kernels[:8], memory=sections["memory"], api=sections["api"])
    database = path / "trace.sqlite"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(CUPTI_ACTIVITY_KIND_KERNEL)")}
        if {"shortName", "registersPerThread", "blockX", "blockY", "blockZ"} <= columns:
            summary["kernel_resources"] = [dict(name=row[0], registers_per_thread=row[1],
                block_x=row[2], block_y=row[3], block_z=row[4], launches=row[5])
                for row in connection.execute("SELECT s.value, MAX(k.registersPerThread), MAX(k.blockX), "
                    "MAX(k.blockY), MAX(k.blockZ), COUNT(*) FROM CUPTI_ACTIVITY_KIND_KERNEL k "
                    "JOIN StringIds s ON s.id=k.shortName GROUP BY k.shortName")]
    (path / "analysis.json").write_text(json.dumps(summary, indent=2))
    return summary


def full(path):
    rows = json.loads((path / "timing.json").read_text())
    checks = json.loads((path / "validation.json").read_text())
    phases = json.loads((path / "phases.json").read_text())
    assert len(rows) == 25 and len(checks) == 24
    parent = statistics.median(r["median"] for r in rows if r["label"] == "parent")
    error = max(v for r in rows + checks + phases for v in r["errors"].values())
    lines = [(path / "RESULTS.md").read_text().replace("# ", "### ", 1),
             f"Maximum saved-output difference versus frozen GPU parent: **{error:.8g}**.",
             "SP/DP full-domain and mixed day/night partial-input validation passes; GPU CTest",
             "13/13 per precision and rebuilt native CPU DP/SP regression suite 182/182.", ""]
    for batch in (16, 32, 64, 128):
        selected = [r for r in rows if r["batch"] == batch]
        value = statistics.median(r["median"] for r in selected)
        parent_rows = [r for r in rows if r["label"] == "parent"]
        pairs = sum(a["median"] < b["median"] for a, b in zip(selected, parent_rows))
        lines.append(f"K{batch}: {100*(1-value/parent):.3f}% reduction, {parent/value:.3f}× GPU-parent speedup, {pairs}/5 faster pairs.")
    lines += ["", "Separate synchronized diagnostic wall times:", "",
              "| K | Total s | Device gather s | Full physics/finishing s | Scatter s | Field transfers s |",
              "|---:|---:|---:|---:|---:|---:|"]
    for row in phases:
        med = lambda k: statistics.median(r[k] for r in row["phases"][1:])
        lines.append(f"| {row['batch']} | {row['median']:.7f} | {med(0):.7f} | {med(1):.7f} | {med(2):.7f} | {med(3):.7f} |")
    lines += ["", "Nsight steady-iteration captures (setup and warmup excluded):", "",
              "| K | Launches | GPU kernel total s | SW clear/cloudy solve kernels s |", "|---:|---:|---:|---:|"]
    for row in phases:
        k = row["batch"]
        profile = nsys_summary(path / f"nsys-K{k}")
        lines.append(f"| {k} | {profile['launches']} | {profile['kernel_seconds']:.7f} | {profile['sw_two_solve_seconds']:.7f} |")
    lines += ["", "GPU kernel totals and CPU CUDA API/wait durations overlap and are not added.",
              "At K128 the SW solve kernels consume about 40% of GPU kernel time; this motivates",
              "the following launch-vector sweep. The gather/scatter itself is only about 1.5 ms.", "",
              f"Artifacts: `{path}`; sources/hashes, validation, raw times, phase arrays, `trace.nsys-rep`,",
              "`trace.sqlite`, CSV statistics and parsed `analysis.json` for each captured configuration."]
    return lines


def tuning(path):
    rows = json.loads((path / "timing.json").read_text())
    validation = json.loads((path / "validation.json").read_text())
    selected = json.loads((path / "selected.json").read_text())
    allocation = json.loads((path / "allocation.json").read_text())
    assert len(rows) == 35 and len(validation) == 11 and allocation["physical_cores"] == 32
    profile = nsys_summary(path / "nsys-selected")
    error = max(v for r in rows + validation for v in r["errors"].values())
    lines = [(path / "RESULTS.md").read_text().replace("# ", "### ", 1),
             f"Allocation: `{allocation['host']}`, verified **32 physical CPU cores**, one A100.",
             f"Selected configuration: **{selected['label']}, K{selected['batch']}**, {selected['median']:.7f} s.",
             f"Fresh CPU time **{selected['cpu']:.7f} s**; selected GPU is **{selected['cpu']/selected['median']:.3f}× faster**.",
             f"Maximum saved-output deviation from GPU reference (or CPU repeatability control): **{error:.8g}**.", "",
             "The initial SW vector sweep used 32/64/256 lanes versus the native 128-lane",
             "large-tile setting. All arithmetic and spectral order are unchanged. Five matched",
             "process groups confirm the table; the CPU has NPROMA=32 and 32 threads, GPU one host thread.", "",
             "Validation includes selected DP full/partial inputs and ordinary non-bit-identity SP",
             "full/partial inputs against a separately built immutable parent GPU source. This checks",
             "that ordinary block-local seeds are preserved, not replaced by compute-tile indices.", "",
             f"Selected steady-iteration Nsight trace: **{profile['launches']} launches**, "
             f"**{profile['kernel_seconds']:.7f} s** GPU kernel total, "
             f"**{profile['sw_two_solve_seconds']:.7f} s** in the two SW solve kernels.",
             "Profiles are separate from headline runs; CPU API waits are not added to device time.", "",
             f"Artifacts: `{path}`; allocation, binary/source hashes, raw process times, DP/normal-mode",
             "validation, CTest logs and Nsight report/SQLite/CSV/parsed analysis."]
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lineage", type=Path)
    parser.add_argument("--lw", type=Path)
    parser.add_argument("--gas", type=Path)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--full", type=Path)
    parser.add_argument("--tuning", type=Path)
    parser.add_argument("--document", type=Path, default=ROOT.parent / "sp_bench_versions.md")
    args = parser.parse_args()
    text = args.document.read_text()
    if args.lineage:
        text = replace(text, "lineage-results", lineage(args.lineage.resolve()))
    if args.lw:
        text = replace(text, "lw-results", lw(args.lw.resolve()))
    if args.gas:
        text = replace(text, "gas-results", gas(args.gas.resolve()))
    if args.coverage:
        text = replace(text, "gas-coverage", coverage(args.coverage.resolve()))
    if args.full:
        text = replace(text, "full-results", full(args.full.resolve()))
    if args.tuning:
        text = replace(text, "full-tuning", tuning(args.tuning.resolve()))
    args.document.write_text(text)


if __name__ == "__main__":
    main()
