"""명령 입구: python -m bus_congestion <명령> [옵션]

    check      설정 파일 점검
    collect    혼잡도 raw 수집          예) collect --start 20250101 --end 20250131 --max-calls 900
    reference  노선·정류장 목록 스냅샷  예) reference --start 20250101 --end 20251231 --day-of-month 15
    holidays   공휴일 목록              예) holidays --year 2025
    clean      행 단위 정제             예) clean --year 2025
    aggregate  집계표                   예) aggregate --year 2025
    pattern    1단계 혼잡 패턴 지수     예) pattern --year 2025
"""

import argparse
import sys
from datetime import date, datetime

import pandas as pd

from bus_congestion.aggregate import AggregateError, aggregate_year
from bus_congestion.api import ApiClient, ApiError, CallBudgetExceededError, QuotaExceededError
from bus_congestion.clean import CleanError, clean_year
from bus_congestion.collect import collect_range, iter_dates
from bus_congestion.config import ConfigError, load_service_key, load_settings
from bus_congestion.pattern import PatternError, run_pattern
from bus_congestion.reference import snapshot_holidays, snapshot_routes, snapshot_stops
from bus_congestion.storage import SchemaError


def parse_ymd(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"날짜 형식은 YYYYMMDD 입니다: {text}") from None


def date_range_args(p: argparse.ArgumentParser):
    p.add_argument("--start", type=parse_ymd, required=True, help="시작일 YYYYMMDD")
    p.add_argument("--end", type=parse_ymd, required=True, help="종료일 YYYYMMDD (포함)")
    p.add_argument("--overwrite", action="store_true", help="이미 있는 파일도 다시 받기")
    p.add_argument("--max-calls", type=int, help="이번 실행의 최대 호출 수 (일일 한도 1000회 관리용)")


def make_client(args):
    settings = load_settings()
    return settings, ApiClient(settings, load_service_key(), max_calls=args.max_calls)


def cmd_check(_args) -> int:
    """설정 파일을 읽어 요약을 보여준다 (키 값은 출력하지 않음)."""
    s = load_settings()
    print(f"지역: ctpv_cd={s['region']['ctpv_cd']} sgg_cd={s['region']['sgg_cd']}")
    print(f"데이터 폴더: {s.data_dir}")
    print(f"캠퍼스 정류장 {len(s.campus_stop_ids)}개, 수동 포함 노선 {len(s.campus_manual_route_ids)}개, "
          f"제외 노선번호 {sorted(s.excluded_route_nos)}")
    print(f"혼잡 기준값: {s.congested_threshold if s.congested_threshold is not None else '미정'}")
    return 0


def cmd_collect(args) -> int:
    settings, client = make_client(args)
    if args.start > args.end:
        print("[오류] --start가 --end보다 늦습니다.", file=sys.stderr)
        return 2

    def progress(ymd, status, calls):
        print(f"  {ymd} {status:8s} (누적 호출 {calls})", flush=True)

    print(f"혼잡도 수집 {args.start} ~ {args.end} → {settings.data_dir / 'raw' / settings['region']['sgg_cd']}")
    s = collect_range(client, settings, settings.data_dir, args.start, args.end, args.overwrite, progress)
    print(f"\n저장 {len(s.saved)} / 건너뜀 {len(s.skipped)} / 데이터 없음 {len(s.no_data)} / 실패 {len(s.failed)}"
          f" / 이번 실행 호출 {client.calls}회")
    for ymd, msg in s.failed:
        print(f"  [실패] {ymd}: {msg}")
    if s.stopped_reason:
        print(f"\n[중단] {s.stopped_reason}")
        print(f"  {s.stopped_at}부터 받지 못했습니다. 같은 명령을 다시 실행하면 받은 날짜는 건너뛰고 이어서 받습니다.")
        return 3
    return 1 if s.failed else 0


def cmd_reference(args) -> int:
    settings, client = make_client(args)
    dates = [d for d in iter_dates(args.start, args.end) if args.day_of_month is None or d.day == args.day_of_month]
    jobs = [("routes", snapshot_routes), ("stops", snapshot_stops)]
    if args.only:
        jobs = [j for j in jobs if j[0] == args.only]
    print(f"목록 스냅샷 {len(dates)}일 × {[j[0] for j in jobs]}")
    failed = 0
    for d in dates:
        ymd = d.strftime("%Y%m%d")
        for name, fn in jobs:
            try:
                status = fn(client, settings, settings.data_dir, ymd, args.overwrite)
            except (QuotaExceededError, CallBudgetExceededError) as e:
                print(f"\n[중단] {e} — {ymd} {name}부터 받지 못했습니다. 같은 명령으로 이어 받을 수 있습니다.")
                return 3
            except (ApiError, SchemaError) as e:
                status, failed = f"failed: {e}", failed + 1
            print(f"  {ymd} {name:6s} {status} (누적 호출 {client.calls})")
    return 1 if failed else 0


def cmd_holidays(args) -> int:
    settings = load_settings()
    client = ApiClient(settings, load_service_key())
    try:
        status = snapshot_holidays(client, settings, settings.data_dir, args.year, args.overwrite)
    except ApiError as e:
        print(f"[실패] {args.year} 공휴일: {e}", file=sys.stderr)
        return 1
    print(f"{args.year} 공휴일 {status} (호출 {client.calls}회) → {settings.data_dir / 'ref' / 'holidays'}")
    return 0


def cmd_clean(args) -> int:
    settings = load_settings()
    stats = clean_year(settings, settings.data_dir, args.year)
    total = {k: sum(getattr(s, k) for s in stats)
             for k in ("raw_rows", "duplicates", "non_campus", "excluded", "bad_tzon", "clean_rows")}
    missing = set().union(*(s.missing_route_name for s in stats))
    print(f"{args.year} 정제: {len(stats)}일 → {settings.data_dir / 'clean' / settings['region']['sgg_cd']}")
    print(f"  raw {total['raw_rows']:,}행 → 완전 중복 -{total['duplicates']:,} / 캠퍼스 외 노선 -{total['non_campus']:,}"
          f" / 제외 노선 -{total['excluded']:,} / 시간대 오류 -{total['bad_tzon']:,} → 정제 {total['clean_rows']:,}행")
    if missing:
        print(f"  [주의] 노선 목록에 없는 rte_id {sorted(missing)} — rte_no가 비어 있음. reference 스냅샷 추가 필요")
    return 0


def cmd_aggregate(args) -> int:
    settings = load_settings()
    written = aggregate_year(settings, settings.data_dir, args.year)
    print(f"{args.year} 집계표 → {settings.data_dir / 'agg' / settings['region']['sgg_cd'] / str(args.year)}")
    for name, n in written.items():
        print(f"  {name}: {n:,}행")
    if settings.congested_threshold is None:
        print("  (혼잡 기준값 미정 → congested_ratio 컬럼 없음. config/settings.toml에서 정하면 다시 실행)")
    return 0


def cmd_pattern(args) -> int:
    settings = load_settings()
    written = run_pattern(settings, settings.data_dir, args.year)
    out_dir = settings.data_dir / "result" / settings["region"]["sgg_cd"] / str(args.year)
    print(f"{args.year} 1단계 혼잡 패턴 → {out_dir}")
    for name, n in written.items():
        print(f"  {name}: {n:,}행")
    ths = pd.read_csv(out_dir / "grade_thresholds.csv", encoding="utf-8-sig")
    print(f"  등급 경계(임시, {ths['date_min'].iloc[0]}~{ths['date_max'].iloc[0]} {ths['n_days'].iloc[0]}일 기준):")
    for th in ths.itertuples():
        print(f"    {th.scope}: T1 여유 < {th.t_low} ≤ T2 보통 < {th.t_high} ≤ T3 혼잡")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bus_congestion", description="원주 버스 혼잡도 수집·정제")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="설정 파일 점검").set_defaults(func=cmd_check)

    p = sub.add_parser("collect", help="혼잡도 raw 수집")
    date_range_args(p)
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("reference", help="노선·정류장 목록 스냅샷")
    date_range_args(p)
    p.add_argument("--day-of-month", type=int, help="매월 이 날짜만 (예: 15). 생략하면 기간 내 모든 날")
    p.add_argument("--only", choices=["routes", "stops"], help="한 종류만 받기")
    p.set_defaults(func=cmd_reference)

    p = sub.add_parser("holidays", help="공휴일 목록 (한국천문연구원 특일정보)")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_holidays)

    p = sub.add_parser("clean", help="행 단위 정제 (raw → clean, 항상 다시 만듦)")
    p.add_argument("--year", type=int, required=True)
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("aggregate", help="집계표 (clean → agg)")
    p.add_argument("--year", type=int, required=True)
    p.set_defaults(func=cmd_aggregate)

    p = sub.add_parser("pattern", help="1단계 혼잡 패턴 지수 (clean → result)")
    p.add_argument("--year", type=int, required=True)
    p.set_defaults(func=cmd_pattern)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"[설정 오류] {e}", file=sys.stderr)
        return 1
    except (CleanError, AggregateError, PatternError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
