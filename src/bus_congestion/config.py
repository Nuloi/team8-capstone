"""설정 읽기.

- config/settings.toml: 일반 설정
- config/*.csv: 팀이 관리하는 목록 (캠퍼스 정류장, 수동 포함 노선, 제외 노선)
- .env: 인증키 (SERVICE_KEY). 값은 절대 출력하지 않는다.
"""

import csv
import os
import tomllib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


class ConfigError(Exception):
    """설정 파일이나 .env가 잘못된 경우."""


@dataclass(frozen=True)
class Settings:
    raw: dict
    campus_stop_ids: frozenset[str]
    campus_manual_route_ids: frozenset[str]
    excluded_route_nos: frozenset[str]

    @property
    def data_dir(self) -> Path:
        return PROJECT_ROOT / self.raw["paths"]["data_dir"]

    @property
    def congested_threshold(self) -> int | None:
        """혼잡 기준값. 아직 정하지 않았으면 None."""
        return self.raw["aggregate"].get("congested_threshold")

    def __getitem__(self, section: str) -> dict:
        return self.raw[section]


def _read_column(path: Path, column: str) -> frozenset[str]:
    """CSV에서 한 컬럼을 문자열 집합으로 읽는다 (앞자리 0 유지, 엑셀 BOM 허용)."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if column not in (reader.fieldnames or []):
            raise ConfigError(f"{path.name}에 '{column}' 컬럼이 없습니다.")
        return frozenset(row[column].strip() for row in reader if row[column].strip())


def load_settings(config_dir: Path = CONFIG_DIR) -> Settings:
    with (config_dir / "settings.toml").open("rb") as f:
        raw = tomllib.load(f)
    return Settings(
        raw=raw,
        campus_stop_ids=_read_column(config_dir / "campus_stops.csv", "sttn_id"),
        campus_manual_route_ids=_read_column(config_dir / "campus_manual_routes.csv", "rte_id"),
        excluded_route_nos=_read_column(config_dir / "excluded_route_nos.csv", "rte_no"),
    )


def load_academic_calendar(config_dir: Path = CONFIG_DIR) -> dict[str, str]:
    """학사일정 → {YYYYMMDD: 기간}. 기간이 겹치는 날이 있으면 ConfigError."""
    out: dict[str, str] = {}
    with (config_dir / "academic_calendar.csv").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            d = date.fromisoformat(row["start"].strip())
            end = date.fromisoformat(row["end"].strip())
            while d <= end:
                ymd = d.strftime("%Y%m%d")
                if ymd in out:
                    raise ConfigError(f"academic_calendar.csv: {ymd}이 두 기간에 겹칩니다.")
                out[ymd] = row["period"].strip()
                d += timedelta(days=1)
    return out


def load_service_key() -> str:
    """.env의 SERVICE_KEY를 읽는다. 값은 반환만 하고 출력하지 않는다."""
    load_dotenv(PROJECT_ROOT / ".env")
    key = (os.getenv("SERVICE_KEY") or "").strip().strip('"').strip("'")
    if not key:
        raise ConfigError(".env에 SERVICE_KEY가 없습니다. 형식: SERVICE_KEY=키값 (.env.example 참고)")
    return key
