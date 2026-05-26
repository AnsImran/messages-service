"""Simple SMS interval/burst probe for microservice_sms.

Fires N SMS one at a time through the synchronous endpoint
``POST /api/v1/sms/send`` at a controlled cadence, so you can watch burst
behavior, latency, ordering, and where BEEtexting starts rate-limiting.

Why /send (not /batch): /send is synchronous -- each call goes straight
through the SMS service to BEEtexting, so rapid calls genuinely burst the
provider. /batch only enqueues to a single ~1-msg/sec worker and can't burst.

What you can see here:
  * HTTP 200  -> BEEtexting accepted it (body has provider_response.result).
  * HTTP 502  -> provider refused; body message is e.g.
                 "BEEtexting responded with HTTP 429." (429 = rate limited).
                 The provider's FULL body is only in the sms-service log.
  * HTTP 503  -> token service problem (creds / token service down).
  * HTTP 422  -> bad request (E.164 / length).

Prereqs: token service on :8100 (with real BEEtexting creds) and the SMS
service on :8200, both via ``uv run python main.py``. See the plan.

WARNING: sends REAL, billable SMS to the real test phone(s). Start small.

Usage
-----
    # smoke: a single text
    uv run python tests/e2e/sms_send_probe.py --count 1

    # 30 texts at a 2s cadence (~60s); then tighten to find the ceiling
    uv run python tests/e2e/sms_send_probe.py --count 30 --interval 2
    uv run python tests/e2e/sms_send_probe.py --count 30 --interval 1
    uv run python tests/e2e/sms_send_probe.py --count 30 --interval 0   # max burst

    # the second test radiologist:
    uv run python tests/e2e/sms_send_probe.py --to +19492720165 --count 1

Recipients (test radiologists): +19493137724 (default), +19492720165.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

LOG_FILE = Path(__file__).resolve().parent / "logs" / "sms_send_probe.log"
SEND_PATH = "/api/v1/sms/send"
# Fixed -7 (PDT, summer) avoids needing tzdata on Windows and matches the
# Pacific timestamps used elsewhere in this project. Valid ~Mar-Nov.
PACIFIC = timezone(timedelta(hours=-7), "PDT")


def _emit(line: str, fh) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    fh.write(line + "\n")
    fh.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--count", type=int, default=10, help="How many messages (default 10).")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="Target seconds between sends (absolute cadence; 0 = max burst). "
                         "Run time ~= count * interval. Default 2.0.")
    ap.add_argument("--text", default="Renew SMS test",
                    help='Base message body. The probe appends " #i/count HH:MM:SS" so each '
                         'text is unique and drops/reordering are visible (default "Renew SMS test").')
    ap.add_argument("--to", default="+19493137724",
                    help="Recipient E.164 (default +19493137724 = test_radiologist_1; "
                         "the 2nd test number is +19492720165).")
    ap.add_argument("--from", dest="from_number", default="+19494248180",
                    help="Sender E.164 (default +19494248180 = company number).")
    ap.add_argument("--url", default="http://127.0.0.1:8200",
                    help="SMS service base URL (default http://127.0.0.1:8200).")
    args = ap.parse_args(argv)

    post_url = args.url.rstrip("/") + SEND_PATH

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fh = LOG_FILE.open("a", encoding="utf-8")

    _emit("=" * 92, fh)
    _emit(f"SMS SEND PROBE  count={args.count}  interval={args.interval}s  "
          f"from={args.from_number}  to={args.to}", fh)
    _emit(f"endpoint={post_url}", fh)
    _emit("HTTP 200 = BEEtexting accepted. 502 'HTTP 429' = provider rate-limited "
          "(full body in sms-service log). 503 = token service.", fh)
    _emit("=" * 92, fh)

    start = time.time()
    statuses: dict[int, int] = {}
    oks: list[float] = []
    errors = 0
    with httpx.Client(timeout=30.0) as client:
        for i in range(args.count):
            target = start + i * args.interval
            rest = target - time.time()
            if rest > 0:
                time.sleep(rest)
            now = datetime.now(PACIFIC)
            ts = now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"
            text = f"{args.text} #{i + 1}/{args.count} {ts}"
            body = {"from_number": args.from_number, "to_number": args.to, "text": text}
            t0 = time.perf_counter()
            try:
                r = client.post(post_url, json=body)
                dt_ms = (time.perf_counter() - t0) * 1000.0
                statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
                if r.status_code < 400:
                    oks.append(dt_ms)
                    try:
                        extra = f"  result={r.json().get('provider_response', {}).get('result')!r}"
                    except Exception:
                        extra = ""
                else:
                    extra = f"  body={r.text[:200]}"
                _emit(f"[{ts}] #{i + 1:>3}/{args.count}  HTTP {r.status_code}  {dt_ms:7.0f}ms{extra}", fh)
            except httpx.HTTPError as exc:
                errors += 1
                _emit(f"[{ts}] #{i + 1:>3}/{args.count}  ERROR {exc!r}", fh)

    _emit("-" * 92, fh)
    _emit(f"DONE in {time.time() - start:.0f}s.  statuses={statuses}  transport_errors={errors}", fh)
    if oks:
        _emit(f"2xx latency ms: min {min(oks):.0f} / median {statistics.median(oks):.0f} "
              f"/ max {max(oks):.0f}", fh)
    _emit("Check the phone: how many texts arrived, and in order? "
          "Tail the sms-service log for BEEtexting bodies on any 502.", fh)
    _emit("=" * 92, fh)
    fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
