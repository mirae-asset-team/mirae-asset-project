# 팀 대화 원장

공모전 제출 서버(`:8000`)와 팀 LLM 링크는 **건드리지 않습니다.**
이 폴더만 Vercel에 올려서, 팀원들이 대화를 붙여넣고 팩트·정확도·개선점을 쌓습니다.

## 로컬

```powershell
cd team-qa
npm install
npm run dev
```

브라우저: `http://localhost:3100`
로컬은 `data/ledger.json`에 저장됩니다.

## Vercel

서버리스에서는 파일이 남지 않으므로 Neon Postgres가 필요합니다.

1. [Neon](https://console.neon.tech) 또는 Vercel Marketplace에서 Postgres를 만듭니다.
2. `DATABASE_URL`을 프로젝트 Environment Variable에 넣습니다.
3. 이 폴더를 Vercel Root Directory `team-qa`로 배포합니다.

```powershell
cd team-qa
npx vercel login
npx vercel
npx vercel --prod
```

배포 뒤 팀원에게 URL만 공유하면 됩니다. 로그인/암호는 없습니다.
14자리 접수번호는 `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=` 원문 링크로 바뀝니다.
