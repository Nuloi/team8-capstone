---
name: api-probe
description: 노선별 혼잡도 API를 작은 범위로 한 번 호출해 응답 구조·필드·타입이 명세와 맞는지 확인한다. "API 테스트", "응답 확인", "키 되는지 봐줘", "호출해봐" 같은 요청에 사용.
---

# API 응답 점검

`scripts/probe_api.py`로 1회 점검한다. 대량 수집용이 아니다. 점검용 코드를 새로 짜지 않는다.

## 절차
1. 점검 조건을 정한다. 사용자가 말하지 않은 값은 기본값을 쓰거나 물어본다.
   - `--date` 운행일자 YYYYMMDD (필수, 모르면 `20260101`)
   - `--ctpv`/`--sgg` 지역코드 (기본: 원주시 `51`/`51130`)
   - `--rte` 노선아이디, `--sttn` 정류장아이디 (선택)
   - `--rows` 1~10 (기본 3)
2. 실행한다.
   ```
   python scripts/probe_api.py --date 20260101
   ```
   - `python`이 PATH에 없으면 `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`를 쓴다.
   - 스크립트가 `.env`에서 키를 읽는다. `.env`를 직접 열지 않는다.
3. 종료 코드로 판단한다.
   - `0`: 정상, 명세와 일치
   - `1`: 호출 실패 (설정·인증·네트워크·`NO_DATA_FOUND` 등)
   - `2`: 인자 오류 (날짜 누락, `--rows` 범위 초과 등)
   - `3`: 호출은 성공했지만 필드·타입이 명세와 다름
4. 결과를 보고한다. HTTP 상태, resultCode, totalCount, 필드 점검 결과, 샘플 1~3건을 알려준다.
5. 명세와 다른 점이 나오면 사용자에게 확인받은 뒤 `docs/api_spec.md`, `scripts/probe_api.py`의 `EXPECTED_FIELDS`, `CLAUDE.md`를 함께 고친다.

## 실패 시 확인
- `.env` 관련 오류: 파일이 있는지, `SERVICE_KEY=키값` 형식인지 (키 값만 있으면 안 됨)
- `SERVICE_KEY_IS_NOT_REGISTERED_ERROR` 등 인증 오류: Encoding 키를 넣지 않았는지(이중 인코딩), 활용신청 승인 직후라 아직 동기화 전인지(보통 1~2시간)
- `NO_DATA_FOUND`: 지역코드(구코드 42/42130을 쓰지 않았는지), 해당 날짜에 데이터가 있는지
