"""Audit completed SP replay results and extract second-iteration kernel evidence."""
import argparse
import collections
import json
import hashlib
import math
from pathlib import Path
import sqlite3
import statistics
import shutil


def analyse(run):
    records = json.loads((run / "confirmation.json").read_text())
    groups = collections.defaultdict(list)
    for record in records:
        assert len(record["rows"]) == 10
        assert all(r["blocks"] == 530 and math.isfinite(r["seconds"]) and r["max_error"] == 0
                   for r in record["rows"])
        assert record["median"] == statistics.median(r["seconds"] for r in record["rows"][1:])
        groups[(record["mode"], record["batch"], record["boundary"])].append(record["median"])
    assert all(len(values) == 5 for values in groups.values())
    checksums = {r["checksum"] for record in records for r in record["rows"]}
    assert len(checksums) == 1, checksums
    summary = []
    for (mode, batch, boundary), values in sorted(groups.items(), key=lambda x: (x[0][2], x[0][0], x[0][1])):
        old = groups[(0, 1, boundary)]
        one = groups[(1, 1, boundary)]
        median = statistics.median(values)
        summary.append(dict(mode=mode, batch=batch, boundary=boundary, median=median,
            min=min(values), max=max(values), speedup_vs_existing=statistics.median(old)/median,
            reduction_vs_existing=100*(1-median/statistics.median(old)),
            reduction_vs_pilot_one=100*(1-median/statistics.median(one)),
            faster_vs_existing=sum(a < b for a, b in zip(values, old)),
            faster_vs_pilot_one=sum(a < b for a, b in zip(values, one))))
    profiles = []
    for path in sorted(run.glob("profile-m*-K*.sqlite")):
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            columns = {r[1] for r in connection.execute("PRAGMA table_info(CUPTI_ACTIVITY_KIND_KERNEL)")}
            if not columns:
                continue
            name_column = "demangledName" if "demangledName" in columns else "shortName"
            query = (f"SELECT k.start,k.end,s.value,k.gridX,k.blockX FROM CUPTI_ACTIVITY_KIND_KERNEL k "
                     f"JOIN StringIds s ON s.id=k.{name_column} ORDER BY k.start")
            rows = list(connection.execute(query))
        mode = int(path.stem.split("-m")[1].split("-")[0])
        batch = int(path.stem.split("-K")[1])
        launches = 11*530 if mode == 0 else 6*math.ceil(530/batch)
        assert len(rows) == 2*launches, (path, len(rows), launches)
        by_name = collections.defaultdict(list)
        for start, end, name, grid, block in rows[launches:]:
            by_name[name].append((end-start, grid, block))
        profiles.append(dict(mode=mode, batch=batch, launches=launches,
            second_iteration_gpu_seconds=sum(r[1]-r[0] for r in rows[launches:])/1e9,
            kernels=[dict(name=name, instances=len(values), seconds=sum(r[0] for r in values)/1e9,
                          grid_x=sorted({r[1] for r in values}), block_x=sorted({r[2] for r in values}))
                     for name, values in by_name.items()]))
    result = dict(analysis_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  confirmations=len(records), validated_iterations=10*len(records),
                  max_flux_error=0, checksum=next(iter(checksums)), summary=summary, profiles=profiles)
    (run / "audited-analysis.json").write_text(json.dumps(result, indent=2))
    if Path(__file__).resolve() != (run / Path(__file__).name).resolve():
        shutil.copy2(__file__, run / Path(__file__).name)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    analyse(parser.parse_args().run)
