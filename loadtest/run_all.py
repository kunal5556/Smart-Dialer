import argparse
import asyncio
import sys

from loadtest import (
    test_borrower_throughput,
    test_call_creation_throughput,
    test_dialer_tick_latency,
    test_event_throughput,
    test_reservation_throughput,
)
from loadtest.harness import (
    LoadTestResult,
    LOADTEST_DB_NAME,
    fresh_database,
    results_table,
    write_results,
)

DEFAULT_SCALES = (100, 1000)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the SmartDialer load test suite.")
    parser.add_argument(
        "--scales",
        type=int,
        nargs="+",
        default=list(DEFAULT_SCALES),
        help="Agent/borrower counts to measure, for example: --scales 100 1000 10000",
    )
    return parser.parse_args(argv)


async def run(scales: list[int]) -> list[LoadTestResult]:
    client, database, settings = await fresh_database()
    results: list[LoadTestResult] = []
    try:
        for scale in scales:
            print(f"--- scale {scale} ---")
            for label, runner in (
                ("agent reservation", test_reservation_throughput.measure(database, scale)),
                ("borrower reservation", test_borrower_throughput.measure(database, scale)),
                ("call creation", test_call_creation_throughput.measure(database, scale)),
                ("event processing", test_event_throughput.measure(database, scale, settings)),
                ("dialer tick", test_dialer_tick_latency.measure(database, scale, settings)),
            ):
                try:
                    result = await runner
                except Exception as error:
                    print(f"  {label}: FAILED at this scale ({type(error).__name__}: {error})")
                    continue
                print(f"  {label}: {result.ops_per_second} ops/sec, p95 {result.p95_ms} ms")
                results.append(result)
    finally:
        await client.drop_database(LOADTEST_DB_NAME)
        client.close()
    return results


def main() -> int:
    args = parse_args()
    results = asyncio.run(run(args.scales))
    if not results:
        print("No load test produced a result.")
        return 1

    path = write_results(results)
    print()
    print(results_table(results))
    print()
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
