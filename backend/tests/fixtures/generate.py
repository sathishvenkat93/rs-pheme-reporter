"""Deterministic fixture generator: ``uv run python -m tests.fixtures.generate``.

Writes every fixture plus ``manifest.json`` (expected format, canonical kind and row count),
which tests use as the independent source of truth.
"""

from __future__ import annotations

import csv
import io
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import quoteattr

HERE = Path(__file__).parent
SEED = 42

SUITES = {
    "api": [
        f"test_api_{n}"
        for n in (
            "login",
            "logout",
            "create_user",
            "delete_user",
            "list_users",
            "rate_limit",
            "pagination",
            "search",
            "export",
            "import",
            "auth_refresh",
            "permissions",
        )
    ],
    "ui": [
        f"test_ui_{n}"
        for n in (
            "home",
            "dashboard",
            "settings",
            "profile",
            "upload",
            "download",
            "dark_mode",
            "keyboard_nav",
            "search_box",
            "notifications",
            "help",
        )
    ],
    "db": [
        f"test_db_{n}"
        for n in (
            "migrate",
            "rollback",
            "backup",
            "restore",
            "index",
            "vacuum",
            "replication",
            "failover",
            "pool",
            "deadlock",
            "encoding",
        )
    ],
}
ERRORS = [
    ("AssertionError", "expected 200 but got 500"),
    ("TimeoutError", "operation timed out after 30s"),
    ("ConnectionError", "connection refused: db:5432"),
    ("NullPointerException", "user was null"),
]
FLAKY = {"test_ui_upload", "test_api_rate_limit", "test_db_pool"}


def _junit(build: int, rng: random.Random, *, with_property: bool, nested: bool) -> tuple[str, int]:
    start = datetime(2026, 10, 1, 9, 0) + timedelta(days=build - 101)
    rows = 0
    suites_xml: list[str] = []
    for s_i, (suite, cases) in enumerate(SUITES.items()):
        parts: list[str] = []
        for case in cases:
            attempts = ["failed", "passed"] if case in FLAKY and rng.random() < 0.6 else [None]
            for forced in attempts:
                roll = rng.random()
                status = forced or (
                    "failed"
                    if roll < 0.12
                    else "error"
                    if roll < 0.16
                    else "skipped"
                    if roll < 0.20
                    else "passed"
                )
                t = round(rng.lognormvariate(-0.5, 0.8), 3)
                attrs = f'name="{case}" classname="tests.{suite}" time="{t}"'
                if status == "passed":
                    parts.append(f"      <testcase {attrs}/>")
                elif status == "skipped":
                    parts.append(
                        f'      <testcase {attrs}><skipped message="not on this platform"/></testcase>'
                    )
                else:
                    etype, msg = rng.choice(ERRORS)
                    tag = "failure" if status == "failed" else "error"
                    parts.append(
                        f"      <testcase {attrs}><{tag} type={quoteattr(etype)} message={quoteattr(msg)}>"
                        f"Traceback (most recent call last): ...</{tag}></testcase>"
                    )
                rows += 1
        ts = (start + timedelta(minutes=10 * s_i)).isoformat()
        suites_xml.append(
            f'    <testsuite name="{suite}" tests="{len(parts)}" timestamp="{ts}">\n'
            + "\n".join(parts)
            + "\n    </testsuite>"
        )
    body = "\n".join(suites_xml)
    if nested:
        body = f'  <testsuite name="all">\n{body}\n  </testsuite>'
    props = (
        f'  <properties><property name="build" value="{build}"/></properties>\n'
        if with_property
        else ""
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuites name="regression">\n{props}{body}\n</testsuites>\n',
        rows,
    )


LABELS = [
    "GET /",
    "GET /login",
    "POST /login",
    "GET /api/users",
    "POST /api/orders",
    "GET /api/orders/{id}",
    "GET /search",
    "GET /static/app.js",
]
JTL_HEADER = [
    "timeStamp",
    "elapsed",
    "label",
    "responseCode",
    "responseMessage",
    "threadName",
    "dataType",
    "success",
    "failureMessage",
    "bytes",
    "sentBytes",
    "grpThreads",
    "allThreads",
    "URL",
    "Latency",
    "IdleTime",
    "Connect",
]


def _jtl_rows(n: int, rng: random.Random) -> list[list[str]]:
    t0 = int(datetime(2026, 10, 5, 14, 0, tzinfo=UTC).timestamp() * 1000)
    rows = []
    for i in range(n):
        label = rng.choice(LABELS)
        base = 80 + LABELS.index(label) * 40
        elapsed = int(rng.lognormvariate(0, 0.5) * base) + (i // 400) * 15  # slow upward drift
        ok = rng.random() > (0.08 if label == "POST /api/orders" else 0.02)
        code = "200" if ok else rng.choice(["500", "502", "504"])
        threads = 1 + min(50, i // 40)
        rows.append(
            [
                str(t0 + i * 300),
                str(elapsed),
                label,
                code,
                "OK" if ok else "Server Error",
                f"Load 1-{threads}",
                "text",
                "true" if ok else "false",
                "" if ok else "Response code 5xx",
                str(rng.randint(500, 50000)),
                "200",
                str(threads),
                str(threads),
                f"https://shop.example.test{label.split(' ')[1]}",
                str(int(elapsed * 0.7)),
                "0",
                str(rng.randint(1, 20)),
            ]
        )
    return rows


def generate() -> dict[str, dict[str, object]]:
    rng = random.Random(SEED)
    manifest: dict[str, dict[str, object]] = {}

    def put(name: str, data: str | bytes, fmt: str, kind: str | None, rows: int | None) -> None:
        path = HERE / name
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        manifest[name] = {"format": fmt, "kind": kind, "rows": rows}

    # JUnit: builds 101-105; 101-103 carry the build in <properties>, 104-105 only in the name.
    for build in range(101, 106):
        xml, rows = _junit(build, rng, with_property=build <= 103, nested=build == 105)
        put(f"junit-build-{build}.xml", xml, "junit_xml", "test_results", rows)
        manifest[f"junit-build-{build}.xml"]["build"] = str(build)

    # JMeter CSV (~2,000 rows) and a small XML variant.
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(JTL_HEADER)
    w.writerows(_jtl_rows(2000, rng))
    put("jmeter-load.jtl", buf.getvalue(), "jtl_csv", "perf_samples", 2000)

    samples = []
    for i, r in enumerate(_jtl_rows(50, rng)):
        if i % 10 == 3:  # guarantee some failed samples in the small file
            r[3], r[4], r[7] = "500", "Server Error", "false"
        samples.append(
            f'  <httpSample t="{r[1]}" lt="{r[14]}" ts="{r[0]}" s="{r[7]}" lb={quoteattr(r[2])} rc="{r[3]}" '
            f'rm={quoteattr(r[4])} tn={quoteattr(r[5])} by="{r[9]}" ng="{r[11]}" na="{r[12]}"/>'
        )
    put(
        "jmeter-small-xml.jtl",
        '<?xml version="1.0" encoding="UTF-8"?>\n<testResults version="1.2">\n'
        + "\n".join(samples)
        + "\n</testResults>\n",
        "jtl_xml",
        "perf_samples",
        50,
    )

    # Generic CSV with daily dates.
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["date", "region", "orders", "revenue", "avg_latency_ms"])
    day0 = datetime(2026, 7, 1)
    n = 0
    for d in range(90):
        for region in ("emea", "amer", "apac"):
            w.writerow(
                [
                    (day0 + timedelta(days=d)).date().isoformat(),
                    region,
                    rng.randint(80, 400),
                    round(rng.uniform(1000, 9000), 2),
                    round(rng.uniform(90, 400), 1),
                ]
            )
            n += 1
    put("daily-metrics.csv", buf.getvalue(), "csv", "generic", n)

    deploys = [
        {
            "service": rng.choice(["api", "web", "worker"]),
            "env": rng.choice(["staging", "prod"]),
            "deployed_at": (datetime(2026, 9, 1, 8) + timedelta(hours=7 * i)).isoformat()
            + "+05:30",
            "duration_s": rng.randint(30, 900),
            "status": rng.choice(["success", "success", "success", "failed"]),
            "meta": {"triggered_by": rng.choice(["ci", "manual"])},
        }
        for i in range(40)
    ]
    put("deployments.json", json.dumps(deploys, indent=1), "json", "generic", 40)

    events = [
        json.dumps(
            {
                "ts": (datetime(2026, 10, 2, 0, 0, tzinfo=UTC) + timedelta(seconds=37 * i))
                .isoformat()
                .replace("+00:00", "Z"),
                "level": rng.choice(["INFO", "INFO", "WARN", "ERROR"]),
                "service": rng.choice(["api", "web"]),
                "latency_ms": rng.randint(5, 900),
                "message": "request handled",
            }
        )
        for i in range(300)
    ]
    put("events.ndjson", "\n".join(events) + "\n", "ndjson", "generic", 300)

    import xlsxwriter

    xbuf = io.BytesIO()
    wb = xlsxwriter.Workbook(xbuf, {"in_memory": True})
    wb.set_properties({"created": datetime(2026, 10, 1)})  # byte-stable output
    ws = wb.add_worksheet("inventory")
    ws.write_row(0, 0, ["sku", "warehouse", "stock", "unit_cost"])
    for i in range(25):
        ws.write_row(
            i + 1,
            0,
            [
                f"SKU-{1000 + i}",
                rng.choice(["north", "south"]),
                rng.randint(0, 500),
                round(rng.uniform(1, 80), 2),
            ],
        )
    wb.close()
    put("inventory.xlsx", xbuf.getvalue(), "xlsx", "generic", 25)

    # Malformed files: one per format.
    junit_ok, _ = _junit(101, random.Random(1), with_property=True, nested=False)
    put("malformed-junit.xml", junit_ok[: len(junit_ok) // 2], "junit_xml", None, None)
    put(
        "malformed-jmeter.jtl",
        ",".join(JTL_HEADER) + "\n1730815200000,abc,GET /,200\n"
        "1730815200300,120,GET /,200,OK,t,text,true,,10,10,1,1,u,1,0,1,EXTRA,MORE\n",
        "jtl_csv",
        None,
        None,
    )
    put("malformed.csv", 'id,name,score\n1,"alice,10\n2,bob,20,extra\n3', "csv", None, None)
    put("malformed.json", '[{"a": 1}, {"a": 2},', "json", None, None)
    put("malformed.ndjson", '{"a": 1}\n{"a": 2}\n{"a": 3\n{"a": 4}\n', "ndjson", None, None)
    put(
        "malformed.xlsx",
        b"PK\x03\x04" + bytes(rng.getrandbits(8) for _ in range(512)),
        "xlsx",
        None,
        None,
    )
    put(
        "malformed-jmeter-xml.jtl",
        '<?xml version="1.0"?>\n<testResults version="1.2">\n'
        '  <httpSample t="12" ts="1730815200000" s="true" lb="GET /"/>\n  <httpSample t="1',
        "jtl_xml",
        None,
        None,
    )

    # Unknown text log.
    put(
        "app.log",
        "".join(
            f"2026-10-02 10:00:{i:02d} INFO request {i} handled in {i * 3}ms\n" for i in range(40)
        ),
        "unknown",
        None,
        None,
    )

    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


if __name__ == "__main__":
    for name, meta in generate().items():
        print(f"{name:28} {meta}")
