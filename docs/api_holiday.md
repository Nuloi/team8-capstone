# 특일정보(공휴일) OpenAPI 명세 정리

원본: `참고자료/OpenAPI활용가이드_한국천문연구원_천문우주정보__특일_정보제공_서비스_v1.4.docx`
제공: 한국천문연구원 (공공데이터포털 "특일 정보", 자동승인)
용도: 집계표의 **일자 유형**(평일 / 토요일 / 일요일·공휴일) 판정, 정제 데이터의 `is_holiday`

## 1. 요청 — 공휴일 정보조회 `getRestDeInfo`
```
GET http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo
```
| 파라미터 | 필수 | 예시 | 비고 |
|---|---|---|---|
| `ServiceKey` | 필수 | | **대문자 S** — 1613000 API(`serviceKey`)와 다름. 같은 키 사용 |
| `solYear` | 필수 | 2025 | |
| `solMonth` | 선택 | 05 | **생략하면 1년 전체** → 1회 호출로 끝 |
| `numOfRows` | 선택 | 100 | 기본 10 → 1년치를 한 번에 받으려면 지정 |
| `_type` | 선택 | json | 기본 XML (`dataType`이 아님) |

다른 오퍼레이션: `getHoliDeInfo`(국경일, 제헌절 포함 isHoliday=N), `getAnniversaryInfo`(기념일), `get24DivisionsInfo`(24절기), `getSundryDayInfo`(잡절). 이 프로젝트는 `getRestDeInfo`만 쓴다.

## 2. 응답 (실제 호출로 확인, 2026-10-02)
1613000 API와 구조가 다르다: 최상위 **`response`(소문자)**, 성공 코드 **`"00"`**.
```json
{"response": {
  "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
  "body": {"items": {"item": [ {...} ]}, "numOfRows": 100, "pageNo": 1, "totalCount": 20}
}}
```
| 필드 | 타입 | 예시 | 비고 |
|---|---|---|---|
| `locdate` | **숫자** | `20250505` | 저장 시 문자열로 변환 |
| `seq` | 숫자 | `1`, `2` | 같은 날 공휴일이 겹치면 2 (예: 2025-05-05 어린이날·부처님오신날) |
| `dateKind` | 문자열 | `"01"` | 01 국경일, 02 기념일, 03 24절기, 04 잡절 |
| `isHoliday` | 문자열 | `"Y"` | 공공기관 휴일 여부 |
| `dateName` | 문자열 | `"대체공휴일"` | |

- 결과가 없으면 `items`가 빈 문자열(`""`)로 올 수 있다(코드에서 처리).
- 데이터는 연 1회 갱신, 임시공휴일은 1일 이내 반영.

## 3. 2025년 결과 (20건, 날짜 19개)
1/1, **1/27(임시공휴일)**, 1/28~30(설날), 3/1, 3/3(대체), 5/5(어린이날·부처님오신날), 5/6(대체), **6/3(임시공휴일, 대통령 선거)**, 6/6, 8/15, 10/3, 10/5~7(추석), 10/8(대체), 10/9, 12/25

저장: `data/ref/holidays/2025.csv` (`python -m bus_congestion holidays --year 2025`)
