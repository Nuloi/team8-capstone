# 노선별 혼잡도 OpenAPI 명세 정리

원본: `OPENAPI 활용자가이드_노선별 혼잡도_v1.2.pdf` (한국교통안전공단 / 위니텍 컨소시엄)
사업명: 인공지능(AI)기반 비수도권 교통카드 이용내역 합성데이터 구축 및 시스템 고도화

## 1. 데이터 성격
- 교통카드 이용내역을 바탕으로 만든 **합성데이터**(익명화·통계적 변형). 실측값이 아니다.
- 원본의 구조·분포는 최대한 유지하므로 **통계적 경향·패턴 분석에 적합**하고, 개별 수준의 정밀 분석에는 한계가 있다.
- 대상 지역: 비수도권. **원주시(51/51130) 제공 확인됨.**

## 2. 서비스 개요
| 항목 | 값 |
|---|---|
| 서비스 ID | IF-APR-018 |
| 서비스명 | 노선별 혼잡도 (Route Congestion Level) |
| 오퍼레이션 | `getRouteCongestionLevel` (노선별 혼잡도 조회) |
| 방식 | REST (GET) |
| 응답 형식 | JSON / XML (`dataType`) |
| 인증 | 서비스 Key (공공데이터포털 발급) |
| 평균 응답시간 | 500 ms |
| 초당 최대 트랜잭션 | 30 tps |
| 서비스 시작일 | 2025-02-24 |

## 3. 요청
```
GET https://apis.data.go.kr/1613000/RouteCongestionLevel/getRouteCongestionLevel
```

| 파라미터 | 국문 | 크기 | 필수 | 예시 | 비고 |
|---|---|---|---|---|---|
| `serviceKey` | 서비스 키 | 512 | 필수 | 인증키 | Decoding 키를 params로 전달 |
| `pageNo` | 페이지 번호 | 4 | 필수 | 1 | |
| `numOfRows` | 한 페이지 결과 수 | 4 | 필수 | 10 | **최대 1000** (1001 이상은 조회 불가) |
| `opr_ymd` | 운행일자 | 8 | 필수 | 20260101 | YYYYMMDD |
| `ctpv_cd` | 시도코드 | 2 | 필수 | 11 | |
| `sgg_cd` | 시군구코드 | 5 | 필수 | 11140 | |
| `rte_id` | 노선아이디 | 10 | 선택 | 41002069 | |
| `sttn_id` | 정류장아이디 | 10 | 선택 | 4199587 | |
| `dataType` | 데이터타입 | 4 | 필수 | JSON | JSON / XML |

> 가이드의 요청 표는 대문자(`OPR_YMD`)로 적혀 있지만, **소문자**(`opr_ymd`)로 정상 동작함을 확인했다(2026-10-01).

### 지역코드 (실제 호출로 확인)
| 지역 | `ctpv_cd` | `sgg_cd` | 결과 |
|---|---|---|---|
| 강원특별자치도 원주시 | `51` | `51130` | 정상 (2026-01-01 기준 10,628건) |
| (구코드) 강원도 원주시 | `42` | `42130` | `NO_DATA_FOUND` — 사용하지 않음 |

예시:
```
https://apis.data.go.kr/1613000/RouteCongestionLevel/getRouteCongestionLevel?serviceKey=인증키&pageNo=1&numOfRows=10&opr_ymd=20260101&ctpv_cd=11&sgg_cd=11140&dataType=JSON
```

## 4. 응답

> 가이드에는 필드명이 대문자(`OPR_YMD`)로 적혀 있지만, **실제 응답은 소문자**(`opr_ymd`)다. 코드는 실제 응답 기준으로 작성한다.

### 4.1 성공 응답 구조 (JSON, 실제 호출로 확인)
```json
{
  "Response": {
    "header": { "resultCode": "200", "resultMsg": "SUCCESS" },
    "body": {
      "items": { "item": [ { ... }, { ... } ] },
      "dataType": "JSON",
      "pageNo": 1,
      "numOfRows": 3,
      "totalCount": 10628
    }
  }
}
```
- 최상위 키는 대문자 `R`의 `Response`.
- 전체 건수는 `Response.body.totalCount` → 페이지 수 = `ceil(totalCount / numOfRows)`.
- `item`이 1건일 때 리스트가 아닌 객체로 오는지는 아직 확인 안 됨 → 코드에서 둘 다 처리한다.

### 4.2 오류 응답 (HTTP 상태는 200)
```json
{ "Error": { "code": "50", "message": "NO_DATA_FOUND" } }
```
- **오류여도 HTTP 200**이 온다. 성공 여부는 HTTP 상태가 아니라 본문으로 판단한다 (`Response.header.resultCode == "200"` 이고 `Error` 키가 없을 것).
- `NO_DATA_FOUND`(code 50): 조건에 맞는 데이터 없음 (예: 구 지역코드 42/42130, 데이터 없는 날짜).

### 4.3 응답 필드 (`item` 1건)
| 필드 | 국문 | 실제 타입 | 예시 (원주, 2026-01-01) |
|---|---|---|---|
| `opr_ymd` | 운행일자 | 문자열 | `"20260101"` |
| `dow_cd` | 요일코드 | 문자열 | `"5"` |
| `dow_nm` | 요일명 | 문자열 | `"목요일"` |
| `ctpv_cd` | 시도코드 | 문자열 | `"51"` |
| `ctpv_nm` | 시도명 | 문자열 | `"강원특별자치도"` |
| `sgg_cd` | 시군구코드 | 문자열 | `"51130"` |
| `sgg_nm` | 시군구명 | 문자열 | `"원주시"` |
| `emd_cd` | 읍면동코드 | 문자열 | `"5113010100"` |
| `emd_nm` | 읍면동명 | 문자열 | `"중앙동"` |
| `rte_id` | 노선아이디 | 문자열 | `"43700100"` |
| `opr_trntm` | 운행회차 | **숫자** | `1` |
| `sttn_seq` | 정류장순서 | **숫자** | `29` |
| `sttn_id` | 정류장아이디 | 문자열 | `"4374311"` |
| `trfc_mns_se_cd` | 교통수단구분코드 | 문자열 | `"B"` (버스) |
| `tzon` | 시간대 | 문자열 | `"09"` (앞자리 0 포함) |
| `cgst` | 혼잡도 | **숫자** | `24` |

- 2026-01-01(목)이 `dow_cd="5"` → 1=일요일 기준으로 추정(다른 요일로 추가 확인 필요).
- `tzon`은 두 자리 시(00~23)로 추정. 문자열로 보관(앞자리 0 유지).
- `cgst`의 단위·산식은 가이드에 없음(확인 필요). 0인 값도 존재.
- 데이터는 같은 정류장이라도 `opr_trntm`(운행회차)·`tzon`별로 여러 행이 있다.

## 5. 코드 정보 (공공데이터포털 「대중교통 이용통계정보」)
| 코드 | URL |
|---|---|
| 지역코드(시도·시군구·읍면동) | https://www.data.go.kr/data/15142029/openapi.do |
| 이용자유형 | https://www.data.go.kr/data/15142083/openapi.do |
| 교통카드유형 | https://www.data.go.kr/data/15142086/openapi.do |
| 교통수단 | https://www.data.go.kr/data/15142085/openapi.do |
| 버스정류장 | https://www.data.go.kr/data/15142032/openapi.do |
| 버스노선 | https://www.data.go.kr/data/15142030/openapi.do |
| 도시철도 노선 | https://www.data.go.kr/data/15142033/openapi.do |

## 6. 인증키 발급 요약
공공데이터포털 로그인 → 데이터찾기 → 국가중점데이터 → 분야 「국토관리」, 검색어 「국토교통부」 → 「대중교통 이용 통계 정보」 → 노선별 혼잡도 활용신청 → 마이페이지에서 **일반 인증키(Decoding)** 확인.
