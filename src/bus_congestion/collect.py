"""노선별 혼잡도 raw 수집.

하루 단위로 원주시 전체를 받아 data/raw/{sgg_cd}/{opr_ymd}.csv로 저장한다.
- 이미 파일이 있는 날짜는 건너뛴다 (overwrite=True일 때만 다시 받음)
- NO_DATA_FOUND는 재시도하지 않고 로그에 "no_data"로 남긴다
- 일일 한도·호출 상한에 걸리면 그 자리에서 멈춘다 → 다음에 같은 명령으로 이어 받기
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from bus_congestion.api import (
    CONGESTION,
    ApiClient,
    ApiError,
    CallBudgetExceededError,
    NoDataError,
    QuotaExceededError,
)
from bus_congestion.config import Settings
from bus_congestion.storage import CONGESTION_COLUMNS, SchemaError, append_log, check_fields, write_csv_atomic


def raw_dir(data_dir: Path, sgg_cd: str) -> Path:
    return data_dir / "raw" / sgg_cd


def iter_dates(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


@dataclass
class CollectSummary:
    saved: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    no_data: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    stopped_reason: str | None = None
    stopped_at: str | None = None


def collect_day(client: ApiClient, settings: Settings, data_dir: Path, ymd: str, overwrite: bool = False) -> str:
    """하루치를 받아 저장한다. 반환값: saved / skipped / no_data.

    한도·상한 초과는 예외를 그대로 올린다(상위에서 멈추도록). 그 밖의 오류도 예외로 올린다.
    """
    region = settings["region"]
    out_dir = raw_dir(data_dir, region["sgg_cd"])
    out = out_dir / f"{ymd}.csv"
    log = out_dir / "_collect_log.csv"
    if out.exists() and not overwrite:
        return "skipped"

    calls_before = client.calls
    try:
        items = client.fetch_all(CONGESTION, {
            "opr_ymd": ymd, "ctpv_cd": region["ctpv_cd"], "sgg_cd": region["sgg_cd"],
        })
    except NoDataError as e:
        append_log(log, {"opr_ymd": ymd, "status": "no_data", "rows": 0,
                         "calls": client.calls - calls_before, "message": str(e)})
        return "no_data"
    except (QuotaExceededError, CallBudgetExceededError):
        raise
    except ApiError as e:
        append_log(log, {"opr_ymd": ymd, "status": "failed", "calls": client.calls - calls_before, "message": str(e)})
        raise

    try:
        extra = check_fields(items, CONGESTION_COLUMNS)
    except SchemaError as e:
        append_log(log, {"opr_ymd": ymd, "status": "failed", "calls": client.calls - calls_before, "message": str(e)})
        raise
    write_csv_atomic(out, CONGESTION_COLUMNS, items)
    message = f"명세에 없는 필드 무시: {sorted(extra)}" if extra else ""
    append_log(log, {"opr_ymd": ymd, "status": "saved", "rows": len(items),
                     "calls": client.calls - calls_before, "message": message})
    return "saved"


def collect_range(client: ApiClient, settings: Settings, data_dir: Path, start: date, end: date,
                  overwrite: bool = False, on_progress=None) -> CollectSummary:
    summary = CollectSummary()
    for d in iter_dates(start, end):
        ymd = d.strftime("%Y%m%d")
        try:
            status = collect_day(client, settings, data_dir, ymd, overwrite)
        except QuotaExceededError as e:
            summary.stopped_reason, summary.stopped_at = f"일일 호출 한도 초과: {e}", ymd
            break
        except CallBudgetExceededError as e:
            summary.stopped_reason, summary.stopped_at = str(e), ymd
            break
        except (ApiError, SchemaError) as e:
            summary.failed.append((ymd, str(e)))
            status = "failed"
        else:
            {"saved": summary.saved, "skipped": summary.skipped, "no_data": summary.no_data}[status].append(ymd)
        if on_progress:
            on_progress(ymd, status, client.calls)
    return summary
