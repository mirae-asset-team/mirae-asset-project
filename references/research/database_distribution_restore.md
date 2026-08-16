# Semantic DB v1 팀 배포 및 복원

## 이 파일의 의미

`disclosure_corpus_semantic_v1.sqlite.zst`는 정정 계보, 검증 상태, 근거 사용 규칙과
재무 사실용 스키마를 포함한 SQLite 스냅샷입니다. Dense embedding과 vector index는
포함하지 않습니다. OneDrive 파일을 직접 열거나 여러 명이 동시에 수정하지 않습니다.

## 다운로드 후 검증

OneDrive에서 패키지 전체를 내려받고 PowerShell에서 다음을 실행합니다.

```powershell
Get-FileHash -Algorithm SHA256 .\disclosure_corpus_semantic_v1.sqlite.zst
```

압축본의 기대 SHA-256은 다음과 같습니다.

```text
7a46819dde876dd9dedb6a9b8330ef4af4b9a34759e659013fd3a8bac4d2dd0e
```

## 압축 해제

`zstd`가 설치된 환경에서 OneDrive 밖의 작업 폴더로 압축을 풉니다.

```powershell
zstd -d .\disclosure_corpus_semantic_v1.sqlite.zst -o .\disclosure_corpus_semantic_v1.sqlite
```

해제된 DB의 기대 SHA-256은 다음과 같습니다.

```text
b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563
```

프로젝트에서는 파일을 다음 경로에 둡니다.

```text
data/derived/disclosure_corpus_semantic_v1.sqlite
```

## DB 검증

저장소 루트에서 다음을 실행합니다.

```powershell
$env:PYTHONPATH='src'
python scripts\validate_database.py `
  --database data\derived\disclosure_corpus_semantic_v1.sqlite `
  --output data\derived\database_validation_local.json `
  --gold data\derived\gold_qa.jsonl `
  --require-semantic-v1 `
  --integrity-mode quick
```

## 버전 규칙

- 이 스냅샷은 Git 커밋 `129f5b0e6096d35b285322d1bf046fff54ff9cc3`과 대응합니다.
- 기존 파일을 덮어쓰지 않고 `semantic-v2`, `retrieval-index-v1`처럼 새 버전을 배포합니다.
- OneDrive 공유본에는 팀 DB 담당자만 쓰기 권한을 갖고, 나머지 팀원은 보기 권한만 갖습니다.
- 새 DB를 만들 때는 압축본과 해제본의 SHA-256을 모두 새 manifest에 기록합니다.
