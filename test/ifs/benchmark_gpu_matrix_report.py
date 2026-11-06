"""Summarize paired branch comparisons, keeping CPU throughput secondary.

Usage: benchmark_gpu_matrix_report.py ACC_TILED_JOB ACC_NORMAL_JOB OMP_JOB OUTPUT.json [VALIDATION_JOB] [BOUNDED_JOB] [CPU_VALIDATION_JOB]
Run benchmark_gpu_matrix_analyse.py on each completed allocation first.
"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re
import subprocess
import sys

from benchmark_gpu import ROOT, dump
from benchmark_gpu_build import PINS


def report(acc_tiled_job, acc_normal_job, omp_job, validation_job=None, bounded_job=None,
           cpu_validation_job=None):
    jobs = dict(acc_tiled=acc_tiled_job, acc_normal=acc_normal_job, omp=omp_job)
    families = {name: [] for name in ("normal_acc", "normal_omp", "tiled_acc", "tiled_omp")}
    cpu = []
    secondary = []
    allocations = {}
    for suite, job in jobs.items():
        directory = ROOT / "results" / job
        analysis = json.loads((directory / "matrix-assessment.json").read_text())
        assert analysis["complete_cases"] == analysis["expected_cases"], job
        assert not analysis["failed_process_groups"], job
        assert all(row["repeats_exact"] for row in analysis["rows"].values()), job
        environment = json.loads((directory / "environment.json").read_text())
        allocations[job] = {"suite": suite, "node": environment.get("SLURM_JOB_NODELIST"),
                            "acc_resident_source_requested": environment.get("ECRAD_ACC_RESIDENT_FIELDS", "0"),
                            "complete_cases": analysis["complete_cases"],
                            "analysis": str(directory.relative_to(ROOT.parents[1]) / "matrix-assessment.json")}
        rows = analysis["rows"]
        for pair in analysis["comparisons"]:
            role = pair["role"]
            current, base = pair["current"], pair["base"]
            a, b = rows[base], rows[current]
            row = {"job": job, "current": current, "base": base,
                   "logical_block": b["block"], "execution_width": b["width"],
                   "base_seconds": a["seconds"], "unified_seconds": b["seconds"],
                   "runtime_change_percent": 100*(pair["time_ratio"]-1),
                   "resolved_slowdown": pair["resolved_slowdown"],
                   "resolved_speedup": pair["resolved_speedup"],
                   "p_slower": pair["p_slower"], "p_faster": pair["p_faster"],
                   "host_change_percent": 100*(pair["host_ratio"]-1),
                   "unified_host_MiB": b["host_MiB"], "base_host_MiB": a["host_MiB"],
                   "unified_device_MiB": b["device_MiB"], "base_device_MiB": a["device_MiB"],
                   "outputs_exact": pair["numerical"]["exact"],
                   "numerical_comparison": pair["numerical"],
                   "binary_sha256": b["binary_sha256"]}
            row["unified_acc_source_resident"] = b.get("acc_source_resident", False)
            if role == "cpu_base":
                cpu.append({key: row[key] for key in (
                    "job", "current", "base", "logical_block", "base_seconds", "unified_seconds",
                    "runtime_change_percent", "resolved_slowdown", "p_slower", "numerical_comparison",
                    "binary_sha256")})
            elif role in ("best_cpu", "cpu9"):
                secondary.append({"job": job, "current": current, "cpu": base, "role": role,
                                  "speedup_over_cpu": 1/pair["time_ratio"],
                                  "resolved_speedup": pair["resolved_speedup"], "p_faster": pair["p_faster"]})
            elif role in ("normal_base", "tiled_base", "equivalent_width_control"):
                backend = "acc" if "optimized-acc" in current else "omp"
                mode = "normal" if role == "normal_base" else "tiled"
                assert pair["valid_base"], (job, current, base)
                row["reference_kind"] = role
                families[mode + "_" + backend].append(row)
    summary = {}
    best = {}
    for family, rows in families.items():
        assert rows, family
        changes = [r["runtime_change_percent"] for r in rows]
        summary[family] = {
            "comparisons": len(rows),
            "runtime_change_percent_range": [min(changes), max(changes)],
            "resolved_slowdowns": sum(r["resolved_slowdown"] for r in rows),
            "resolved_speedups": sum(r["resolved_speedup"] for r in rows),
            "all_outputs_exact": all(r["outputs_exact"] for r in rows),
            "host_memory_no_more_than_5_percent_above_base": all(r["host_change_percent"] <= 5 for r in rows),
            "device_memory_no_more_than_5_percent_above_base": all(
                r["unified_device_MiB"] <= 1.05*r["base_device_MiB"] for r in rows)}
        if family.startswith("tiled"):
            best[family] = {str(block): min((r for r in rows if r["logical_block"] == block),
                                           key=lambda r: r["unified_seconds"])
                            for block in sorted({r["logical_block"] for r in rows})}
    result = {
        "schema_version": 1,
        "assessment": "SP unified-versus-base branch performance matrix",
        "report_date_utc": datetime.now(timezone.utc).date().isoformat(),
        "implementation_parent_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parents[1], text=True).strip(),
        "source_pins": PINS,
        "production_source_sha256": {
            path: hashlib.sha256((ROOT.parents[1] / path).read_bytes()).hexdigest()
            for path in ("driver/ecrad_ifs_driver_blocked_gpu.F90", "ifsrrtm/rrtm_setcoef_140gp_gpu.F90",
                         "ifsrrtm/srtm_setcoef_gpu.F90", "radiation/radiation_flux.F90",
                         "radiation/radiation_single_level.F90", "utilities/radiation_random_numbers_mix.F90")},
        "changes": [
            "ACC weighted SW albedo mapping shares the existing scalar explicit-array helper.",
            "ACC gas coefficient setup separates independent layers from ordered tropopause counts, adapting the tiled ACC donor.",
            "ACC surface aggregation bounds each band's ordered g-point scan, including interleaved maps and empty bands.",
            "Avoid touching unused host tile pages; preserve pressure-ordering checks and bounded device scratch.",
            "NVHPC ACC scatters outputs on device, with pitched downloads per bounded tile or once per resident model step.",
            "Optional driver-owned ACC model-source residency matches the corresponding base; all input refreshes and output downloads remain inside iteration timing.",
            "Block-ordered seed/pressure staging removes repeated integer division while preserving original logical-block seeds.",
            "Native MIX initialization uses an exactly equivalent branch-free Galois shift, preserving its random streams."],
        "priority": "Unified branch versus the corresponding numerically valid base; GPU/CPU throughput is secondary",
        "allocations": allocations,
        "protocol": {"inherited_from": "gpu-performance-sp-optimized.json#/protocol",
                     "cpu_blocks": [8, 9, 16, 32], "normal_gpu_blocks": [1024, 2048, 4096, 8192, 8480],
                     "tiled_logical_blocks": [32, 64, 128], "execution_widths": [1024, 2048, 4096, 8192],
                     "physical_resources": "64 CPU cores/64 threads or two distinct A100-SXM4-40GB GPUs; 16960 columns, 137 levels",
                     "timing": "Synchronized radiation iterations include required transfers and gather/scatter; exclude initialization, process launch and NetCDF I/O",
                     "memory": "Maximum across groups of summed process RSS peaks and 20-ms-sampled NVML device peaks, including context/reservations; rank peaks need not be simultaneous",
                     "acc_source_lifetime": "Default bounded source staging, or ECRAD_ACC_RESIDENT_FIELDS=1 for driver-owned full-model input residency matching the tiled ACC base. Resident mode still refreshes inputs inside every timed iteration; its model storage scales with ncol while radiation scratch remains tile-bounded.",
                     "process_groups": 5, "iterations_per_process": 6, "warmup_iterations_excluded": 1},
        "statistics": "Five independent paired process groups; exact one-sided sign-flip test on log times, p<0.05. No resolved slowdown does not prove equivalence. Inner iterations are not independent samples.",
        "gpu_family_summary": summary,
        "cpu_summary": {"runtime_change_percent_range": [min(r["runtime_change_percent"] for r in cpu),
                                                         max(r["runtime_change_percent"] for r in cpu)],
                        "comparisons": len(cpu),
                        "resolved_slowdowns": sum(r["resolved_slowdown"] for r in cpu)},
        "best_tiled_setting_per_logical_block": best,
        "acceptance": {
            "normal_gpu_all_measured_base_comparisons_no_resolved_slowdown": all(
                not r["resolved_slowdown"] for name in ("normal_acc", "normal_omp") for r in families[name]),
            "tiled_gpu_best_setting_each_logical_block_no_resolved_slowdown": all(
                not r["resolved_slowdown"] for settings in best.values() for r in settings.values()),
            "all_gpu_configurations_no_resolved_slowdown": all(
                not r["resolved_slowdown"] for rows in families.values() for r in rows),
            "cpu_all_allocations_no_resolved_slowdown": not any(r["resolved_slowdown"] for r in cpu)},
        "primary_gpu_comparisons": families,
        "primary_cpu_comparisons": cpu,
        "secondary_cpu_throughput_comparisons": secondary,
        "limits": [
            "OMP tiled comparison uses its valid equivalent-width unbatched control; the original packed fork fails numerical validation and is excluded.",
            "Native CPU/GPU samplers differ. The existing full-domain matched-CPU/GPU LW tolerance miss remains separate; exact GPU/base agreement does not establish full-domain CPU/GPU agreement.",
            "Results cover SP RRTMG/McICA, one input and the tested hardware; SP/DP correctness checks are separate."],
        "rejected_trials": [
            "A full g-point scan per output band did not improve whole-iteration time; retain bounded band scans.",
            "An explicit-shape dense-only wrapper added complexity without a measurable timing improvement and was removed.",
            "256-lane SW launches and a 32-lane source gather regressed performance; retain the original launch policy.",
            "Explicit CUDA host registration failed during cleanup; its code and CUDA link option were removed.",
            "CPU scratch-interface and vector-cosine trials did not consistently improve timings and were removed.",
            "A floating MINSTD reduction preserved test streams but did not affect the default MIX path; it was removed."],
        "checks": {
            "diff_check": "Passed",
            "python_syntax_and_paired_statistics": "Passed; synthetic identical/faster/slower five-pair cases checked",
            "fortran_added_line_quick_check": "Seven S1 END DO/END IF spelling findings follow radiation/CONVENTIONS; no other findings",
            "fortls": "Attempted on changed Fortran sources; launcher exits 1 without diagnostics"},
        "reproduction": {
            "environment": "module load cmake ninja prgenv/nvidia nvidia/24.5 netcdf4 python3",
            "build": "python3 test/ifs/benchmark_gpu_build.py main-cpu optimized-cpu acc-opt acc-tiled master-omp omp-tiled optimized-acc optimized-omp",
            "acc_tiled": "sbatch test/ifs/benchmark_gpu.sbatch matrix-acc cpu tiled32 tiled64 tiled128",
            "acc_tiled_resident": "sbatch --export=ALL,ECRAD_ACC_RESIDENT_FIELDS=1 test/ifs/benchmark_gpu.sbatch matrix-acc cpu tiled32 tiled64 tiled128",
            "acc_normal": "sbatch test/ifs/benchmark_gpu.sbatch matrix-acc cpu normal",
            "omp": "sbatch --time=00:25:00 test/ifs/benchmark_gpu.sbatch matrix-omp cpu normal tiled32 tiled64 tiled128",
            "analyse": "python3 test/ifs/benchmark_gpu_matrix_analyse.py JOB_ID [JOB_ID ...]",
            "report": "python3 test/ifs/benchmark_gpu_matrix_report.py ACC_TILED_JOB ACC_NORMAL_JOB OMP_JOB OUTPUT.json",
            "candidate_builds": "These allocations used isolated candidate-acc/candidate-omp build directories to keep earlier measurements immutable; rebuilding optimized-acc/optimized-omp from this source uses the same flags."}}
    if validation_job:
        evidence = json.loads((ROOT / f"matrix-validation-{validation_job}.json").read_text())
        assert len(evidence) == 6 and sum(r["cases"] for r in evidence) == 292
        log = (ROOT / f"extended-{validation_job}.log").read_text()
        errors = re.findall(r"ERROR SUMMARY:\s+(\d+) errors", log)
        ctests = re.findall(r"100% tests passed, 0 tests failed out of (\d+)", log)
        assert len(errors) == 8 and not any(int(n) for n in errors)
        assert len(ctests) == 2 and sum(int(n) for n in ctests) >= 10
        result["validation"] = {
            "job": validation_job, "per_backend_suites": evidence,
            "saved_output_files_exact": sum(r["files_exact"] for r in evidence),
            "saved_numeric_variables_exact": sum(r["numeric_variables_exact"] for r in evidence),
            "saved_output_scope": "All saved numerical variables in active columns; inactive offset-driver outputs are unspecified and excluded",
            "compute_sanitizer_runs": len(errors), "compute_sanitizer_errors": 0,
            "instrumentation_only_acc_stack_bytes": 32768,
            "targeted_ctest_passed": sum(int(n) for n in ctests),
            "precision_test_fix": "Give the MINSTD test a private module directory and assert the expected PARKIND1 precision; NVHPC otherwise imported the SP module into the DP test",
            "large_input_cpu_gpu_limitation": "The inherited 0.003814697265625 W/m2 LW GPU/matched-CPU discrepancy exceeds the unchanged 0.001 threshold; valid GPU bases share it. Tolerances were not relaxed."}
    if bounded_job:
        bounded = json.loads((ROOT / "results" / bounded_job / "matrix-assessment.json").read_text())
        assert bounded["complete_cases"] == bounded["expected_cases"]
        assert not bounded["failed_process_groups"]
        comparisons = [r for r in bounded["comparisons"] if r["role"] == "tiled_base"]
        assert len(comparisons) == 12 and all(r["valid_base"] for r in comparisons)
        result["bounded_source_mode"] = {
            "job": bounded_job, "default": True,
            "interpretation": "Bounded mode releases each mapped source run before radiation scratch; comparison is against the fork's resident-source mode, so ownership strategies differ.",
            "comparisons": comparisons,
            "no_resolved_slowdown_all_widths": not any(r["resolved_slowdown"] for r in comparisons)}
    if cpu_validation_job:
        log = (ROOT / f"cpu-regression-{cpu_validation_job}.log").read_text()
        count = re.search(r"100% tests passed, 0 tests failed out of (\d+)", log)
        assert count and int(count[1]) >= 188
        continuity = json.loads((ROOT / "cpu-mix-output-continuity.json").read_text())
        assert len(continuity) == 4 and all(r["numeric_variables_exact"] for r in continuity)
        streams = {}
        for precision, width in (("sp", 4), ("dp", 8)):
            before = (ROOT / f"mix-old-{precision}.bin").read_bytes()
            after = (ROOT / f"mix-new-{precision}.bin").read_bytes()
            assert before == after and len(before)//width == 2705000
            streams[precision] = {"samples_exact": len(before)//width,
                                  "sha256": hashlib.sha256(before).hexdigest()}
        result["native_cpu_validation"] = {
            "job": cpu_validation_job, "ctest_passed": int(count[1]), "ctest_failed": 0,
            "mix_streams": streams,
            "stream_scope": "1000 seeds including signed boundaries; uniform chunks spanning buffer boundaries and Gaussian draws, compared against frozen pre-change executables",
            "large_benchmark_continuity": continuity,
            "interpretation": "The branch-free initializer preserves the existing generator and random streams; it does not switch the CPU sampler."}
    return result


if __name__ == "__main__":
    result = report(*sys.argv[1:4], validation_job=sys.argv[5] if len(sys.argv) > 5 else None,
                    bounded_job=sys.argv[6] if len(sys.argv) > 6 else None,
                    cpu_validation_job=sys.argv[7] if len(sys.argv) > 7 else None)
    dump(Path(sys.argv[4]), result)
    print(json.dumps({key: result[key] for key in ("gpu_family_summary", "cpu_summary", "acceptance")}, indent=2))
    for family, settings in result["best_tiled_setting_per_logical_block"].items():
        for block, row in settings.items():
            print(family, block, "width", row["execution_width"], "unified", row["unified_seconds"],
                  "base", row["base_seconds"], "change", row["runtime_change_percent"])
    for family, rows in result["primary_gpu_comparisons"].items():
        best = min(rows, key=lambda row: row["unified_seconds"])
        cpu = next(row for row in result["secondary_cpu_throughput_comparisons"]
                   if row["job"] == best["job"] and row["current"] == best["current"] and row["role"] == "cpu9")
        print("Best", family, best["current"], best["unified_seconds"], "seconds; CPU9 speedup",
              cpu["speedup_over_cpu"], "host MiB", best["unified_host_MiB"], "device MiB", best["unified_device_MiB"])
