# 시스템 구조 한눈에 보기

8조 캡스톤 — 원주 미래캠퍼스 버스 혼잡도 **수집 → 정제 → 알고리즘** 파이프라인.
이 문서는 그림 위주 요약이다. 세부 규칙은 [CLAUDE.md](../CLAUDE.md), 알고리즘 설계는 [algorithm_design.md](algorithm_design.md), API 명세는 `docs/api_*.md`.

> 그림은 Mermaid로 작성했다. GitHub·VS Code(Markdown Preview Mermaid Support 확장)에서 자동으로 그려진다.

---

## 1. 전체 파이프라인

```mermaid
flowchart TB
    subgraph S1["1 · 수집 (공공데이터 API → data/ref, data/raw)"]
        direction LR
        A4["특일정보 API"] --> C1["① holidays"] --> D1[("ref/holidays")]
        A2["버스노선·정류장 API"] --> C2["② reference"] --> D2[("ref/routes<br/>ref/stops")]
        A1["노선별 혼잡도 API"] --> C3["③ collect"] --> D3[("raw/51130<br/>원본")]
    end

    subgraph S2["2 · 정제"]
        direction LR
        C4["④ clean"] --> D4[("clean/51130<br/>캠퍼스 노선만")]
    end

    subgraph S3["3 · 분석·알고리즘"]
        direction LR
        C5["⑤ aggregate"] --> D5[("agg/51130/2025<br/>집계표 6종")]
        C6["⑥ pattern<br/>1단계 알고리즘"] --> D6[("result/51130/2025<br/>결과 5종")]
    end

    CFG[["config/<br/>설정 · 캠퍼스 정류장 · 학사일정"]]
    WEB(["웹 파트 (다른 팀원)"])

    S1 --> C4
    D4 --> C5
    D4 --> C6
    CFG -.-> S2
    CFG -.-> C6
    D6 ==> WEB
```

| 단계 | 명령 | 입력 | 출력 | 비고 |
|---|---|---|---|---|
| ① | `holidays --year 2025` | 특일정보 API | `ref/holidays/2025.csv` | 1회 호출 |
| ② | `reference --start … --end … --day-of-month 15` | 버스노선·정류장 API | `ref/routes/*.csv`, `ref/stops/*.csv` | 매월 15일 |
| ③ | `collect --start … --end … --max-calls 900` | 혼잡도 API | `raw/51130/{날짜}.csv` | 일일 한도 1,000회 → 며칠에 나눠 |
| ④ | `clean --year 2025` | raw + ref | `clean/51130/{날짜}.csv` | 항상 전체 재생성 |
| ⑤ | `aggregate --year 2025` | clean | `agg/51130/2025/by_*.csv` 6종 | 단순 통계 |
| ⑥ | `pattern --year 2025` | clean + 학사일정 | `result/51130/2025/*.csv` 5종 | **웹에 넘기는 결과** |

---

## 2. 데이터 단계별 변화

```mermaid
flowchart TB
    R["<b>raw</b> — API 원본 그대로<br/>원주 전체 노선, 컬럼 16개<br/>약 2만 행/일"]
    C["<b>clean</b> — 정제<br/>미래캠퍼스 노선만<br/>+ rte_no, base_no, sttn_nm, est_pax, is_holiday"]
    AG["<b>agg</b> — 집계표<br/>요일/일자유형/월 × 기본번호/변형<br/>× 정류장 × 시간대"]
    RS["<b>result</b> — 알고리즘 결과<br/>등급 · 혼잡 구간 · 추천 · 경계값"]

    R -- "완전 중복 제거<br/>캠퍼스 외 노선 제거<br/>고장·공차·조조 제거<br/>tzon 24 제거" --> C
    C -- "평균·최대·p50·p90" --> AG
    C -- "기간 붙이기 → 날별 값 → 보정 수준 L<br/>→ 등급 → 빈도·지속" --> RS
```

**미래캠퍼스 노선 판정**

```mermaid
flowchart LR
    S["캠퍼스 정류장 10곳<br/>(연세대 7 + 매지 3)<br/>config/campus_stops.csv"]
    RAW[("수집한 모든 날짜의 raw")]
    AUTO["① 자동 추출<br/>캠퍼스 정류장을 지나는 rte_id"]
    MAN["② 수동 포함<br/>111(연세대경유)<br/>config/campus_manual_routes.csv"]
    U{{"① ∪ ②"}}
    OUT["미래캠퍼스 노선<br/>30 · 31 · 34 · 34-1 · 111 계열"]
    S --> AUTO
    RAW --> AUTO --> U
    MAN --> U --> OUT
```

---

## 3. 수집(collect) 동작 — 시퀀스

```mermaid
sequenceDiagram
    autonumber
    actor U as 사용자
    participant M as __main__ (collect)
    participant C as collect.py
    participant A as api.py (ApiClient)
    participant P as 공공데이터포털
    participant F as data/raw

    U->>M: collect --start 20250101 --end 20251231 --max-calls 900
    loop 날짜마다
        C->>F: 파일 있음?
        alt 이미 있음
            F-->>C: 건너뜀 (skipped)
        else 없음
            C->>A: fetch_all(혼잡도, 날짜)
            loop 페이지 (1000행씩)
                A->>P: GET (0.1초 간격, 오류 시 최대 3회 재시도)
                P-->>A: JSON (오류도 HTTP 200)
            end
            alt NO_DATA_FOUND
                A-->>C: NoDataError → 로그 no_data
            else 일일 한도 / --max-calls 도달
                A-->>C: QuotaExceeded / CallBudgetExceeded
                C-->>M: 그 자리에서 중단 (종료코드 3)
            else 성공
                A-->>C: item 목록 (건수 = totalCount 확인)
                C->>F: 임시파일 → 이름 변경 (원자적 저장)
                C->>F: _collect_log.csv 기록
            end
        end
    end
    M-->>U: 저장/건너뜀/없음/실패 요약
    Note over U,F: 다음 날 같은 명령을 다시 실행하면 이어 받는다
```

---

## 4. 코드 구조 — UML 클래스/모듈 다이어그램

```mermaid
classDiagram
    direction LR

    class main["__main__.py"] {
        check()
        collect()
        reference()
        holidays()
        clean()
        aggregate()
        pattern()
    }
    class config["config.py"] {
        +Settings
        +load_settings()
        +load_service_key()
        +load_academic_calendar()
    }
    class Settings {
        +raw: dict
        +campus_stop_ids
        +campus_manual_route_ids
        +excluded_route_nos
        +data_dir
        +congested_threshold
    }
    class api["api.py"] {
        +ApiClient
        ApiError
        NoDataError
        QuotaExceededError
        CallBudgetExceededError
    }
    class ApiClient {
        +calls: int
        +max_calls
        +fetch_all(path, params)
        +request_external(url, params)
        -request_url()
        -_parse()
    }
    class storage["storage.py"] {
        CONGESTION_COLUMNS
        ROUTE_COLUMNS
        STOP_COLUMNS
        HOLIDAY_COLUMNS
        +write_csv_atomic()
        +append_log()
        +check_fields()
    }
    class collect["collect.py"] {
        +collect_day()
        +collect_range()
    }
    class reference["reference.py"] {
        +snapshot_routes()
        +snapshot_stops()
        +snapshot_holidays()
    }
    class matching["matching.py"] {
        +base_no()
        +match_routes()
        +load_route_names()
        +load_stop_names()
        +campus_route_ids()
    }
    class clean["clean.py"] {
        +CleanContext
        +clean_frame()
        +clean_year()
    }
    class aggregate["aggregate.py"] {
        +day_type()
        +load_clean()
        +summarize()
        +aggregate_year()
    }
    class pattern["pattern.py"] {
        +Thresholds
        +daily_values()
        +cell_levels()
        +compute_thresholds()
        +assign_grade()
        +run_lengths()
        +congestion_windows()
        +recommendations()
        +run_pattern()
    }

    config *-- Settings
    api *-- ApiClient
    ApiClient ..> Settings : 설정 읽음
    main --> collect
    main --> reference
    main --> clean
    main --> aggregate
    main --> pattern
    collect --> ApiClient
    reference --> ApiClient
    collect --> storage
    reference --> storage
    clean --> matching
    clean --> storage
    aggregate --> clean
    pattern --> aggregate
    pattern --> config
```

| 모듈 | 역할 |
|---|---|
| `config.py` | `config/settings.toml`·CSV 목록·학사일정·`.env` 키 읽기 (키는 출력 안 함) |
| `api.py` | API 공통 호출: 페이지 넘김, 재시도, 호출 간격, 호출 수 세기, 오류 판정 |
| `storage.py` | CSV 컬럼 순서(팀과의 스키마 계약), 원자적 저장, 수집 로그 |
| `collect.py` | 혼잡도 하루 단위 수집, 이어받기 |
| `reference.py` | 노선·정류장·공휴일 목록 스냅샷 |
| `matching.py` | 기본번호(34연세대→34), 노선번호 검색, 이름 조회, 캠퍼스 노선 판정 |
| `clean.py` | 행 단위 정제 |
| `aggregate.py` | 집계표 6종 |
| `pattern.py` | 1단계 혼잡 패턴 지수 |

---

## 5. 1단계 알고리즘 — 혼잡 패턴 지수

```mermaid
flowchart TB
    IN[("clean 데이터<br/>+ 학사일정")]
    S1["<b>① 칸 나누기</b><br/>노선 × 정류장 × 기간 × 일자유형 × 시간대"]
    S2["<b>② 날별 값</b><br/>칸 안에서 날짜마다 평균 cgst 1개"]
    S3["<b>③ 보정 수준 L</b><br/>L = (d·평균 + k·노선평균) / (d + k)<br/>d = 관측일 수, k = 5"]
    S4["<b>④ 등급 경계</b> (임시)<br/>기본번호 칸들의 L 분포<br/>33분위 = t_low, 67분위 = t_high"]
    S5{"<b>⑤ 등급</b><br/>L 값은?"}
    G1["T1 여유"]
    G2["T2 보통"]
    G3["T3 혼잡"]
    S6["<b>⑥ 빈도·지속</b> (기준 t_high)<br/>F = 혼잡한 날 비율<br/>Dt = 연속 혼잡 시간<br/>Ds = 연속 혼잡 정류장"]
    O1[["혼잡 구간<br/>연속 T3 시간대 + 피크"]]
    O2[["덜 혼잡한 시간 추천<br/>±2시간 중 등급 낮고 L 최저"]]
    O3[["등급·지표 표<br/>pattern_base / pattern_route"]]
    O4[["경계값<br/>grade_thresholds"]]

    IN --> S1 --> S2 --> S3 --> S4 --> S5
    S5 -- "L < t_low" --> G1
    S5 -- "t_low ≤ L < t_high" --> G2
    S5 -- "L ≥ t_high" --> G3
    G1 & G2 & G3 --> S6 --> O3
    G3 --> O1
    G3 --> O2
    S4 --> O4
```

**보정 수준 L이 필요한 이유** — 관측일이 하루뿐인 칸은 그날 우연히 튄 값이 그대로 "혼잡"이 될 수 있다. 관측일이 적을수록 같은 노선 평균 쪽으로 당겨 안정시킨다.

| 관측일 d | 이 칸 평균 | 노선 평균 | L (k=5) |
|---|---|---|---|
| 1일 | 90% | 35% | (1·90 + 5·35) / 6 ≈ **44%** |
| 10일 | 30% | 35% | (10·30 + 5·35) / 15 ≈ **32%** |

**혼잡 구간 예시** — 한 정류장의 시간대별 등급이 아래와 같으면

```mermaid
gantt
    title 34번 원주의료원 · 학기 · 평일 (예시)
    dateFormat HH
    axisFormat %H시
    section 등급
    T1 여유 :done, 07, 1h
    T2 보통 :active, 08, 1h
    T3 혼잡 (구간 1, 피크 12시) :crit, 10, 4h
    T2 보통 :active, 14, 1h
    T3 혼잡 (구간 2) :crit, 16, 2h
```

→ `congestion_windows.csv`에 "10~13시(피크 12시)", "16~17시" 두 구간이 나오고, 13시 혼잡 칸에는 ±2시간 중 덜 붐비는 14시(T2)가 추천된다.

---

## 6. 2단계 알고리즘 — 예측 모델 (설계만, 구현 전)

```mermaid
flowchart LR
    subgraph X["입력 요소"]
        X1["시간: 시간대, 요일, 일자유형, 월"]
        X2["기간: 학기/시험/방학/계절학기"]
        X3["특일: 공휴일, 연휴 전날·다음날"]
        X4["노선: 기본번호, 변형"]
        X5["정류장: 캠퍼스 여부, 노선 내 위치, 방면"]
        X6["이력: 과거 평균 수준 (학습 기간만)"]
    end
    M["그래디언트 부스팅<br/>(scikit-learn)"]
    Y["예상 재차율<br/>→ 추정 인원, 등급"]
    B["1단계 L<br/>(기준선)"]
    V{"기간 분할 검증<br/>1~10월 학습 / 11~12월 평가<br/>MAE가 기준선보다 작은가?"}

    X --> M --> Y --> V
    B --> V
    V -- "예" --> USE["예측값 사용"]
    V -- "아니오" --> KEEP["1단계 L 사용"]
```

---

## 7. 웹 파트에 넘기는 결과 파일

```mermaid
erDiagram
    PATTERN_BASE {
        string base_no PK "34"
        string sttn_id PK
        string period PK "학기/시험/방학/계절학기"
        string day_type PK "평일/토요일/일요일·공휴일"
        string tzon PK "00~23"
        string sttn_nm
        int n_days "관측일 수"
        float level "보정 수준 L (%)"
        int est_pax "추정 인원"
        string grade "T1/T2/T3"
        float congested_days_ratio "F"
        float duration_hours "Dt"
    }
    PATTERN_ROUTE {
        string rte_id PK
        int sttn_seq PK
        string sttn_id PK
        string period PK
        string day_type PK
        string tzon PK
        string base_no FK
        string rte_no
        float level
        string grade
        float segment_stops "Ds"
    }
    CONGESTION_WINDOWS {
        string base_no FK
        string sttn_id FK
        string period
        string day_type
        string start_tzon
        string end_tzon
        int hours
        string peak_tzon
    }
    RECOMMENDATIONS {
        string base_no FK
        string sttn_id FK
        string period
        string day_type
        string from_tzon
        string rec_tzon
        string rec_grade
        int shift_hours
    }
    GRADE_THRESHOLDS {
        float t_low
        float t_high
        string date_min
        string date_max
        bool provisional
    }

    PATTERN_BASE ||--o{ PATTERN_ROUTE : "기본번호 → 변형"
    PATTERN_BASE ||--o{ CONGESTION_WINDOWS : "T3 연속 시간대"
    PATTERN_BASE ||--o{ RECOMMENDATIONS : "T3 칸마다 추천"
    GRADE_THRESHOLDS ||--|| PATTERN_BASE : "등급 경계"
```

- 위치: `data/result/51130/2025/` (CSV, UTF-8 BOM). **git에 포함**되어 레포에서 바로 받을 수 있다 (`data/agg/`, `data/ref/`도 포함, `data/raw/`·`data/clean/`은 용량 때문에 제외).
- 현재 커밋된 결과의 데이터 기간은 `grade_thresholds.csv`의 `date_min`~`date_max`로 확인한다.
- 등급은 **임시 기준**(1/3 분위수)이다. 2025년 수집이 끝나면 최종 기준을 다시 정하며, `grade_thresholds.csv`의 `provisional`로 구분한다.
- 결과 형식(CSV/JSON)·컬럼은 웹 팀원과 합의 후 확정한다.

---

## 8. 현재 진행 상황 (2026-10-06)

```mermaid
flowchart LR
    P1["① holidays<br/>✅ 2025"]:::done
    P2["② reference<br/>🔶 1~5월"]:::part
    P3["③ collect<br/>🔶 1/1~5월 하순<br/>(약 7일 더)"]:::part
    P4["④ clean<br/>✅ 코드 / 부분 데이터"]:::done
    P5["⑤ aggregate<br/>✅ 코드"]:::done
    P6["⑥ 1단계 pattern<br/>✅ 코드 / 임시 등급"]:::done
    P7["2단계 예측<br/>⬜ 설계만"]:::todo
    P1 --> P4
    P2 --> P4
    P3 --> P4 --> P5
    P4 --> P6 --> P7

    classDef done fill:#d4edda,stroke:#28a745
    classDef part fill:#fff3cd,stroke:#d39e00
    classDef todo fill:#f1f1f1,stroke:#999
```
