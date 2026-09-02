import { dartUrl } from "@/lib/receipts";
import { listRecords, summarize } from "@/lib/store";
import { ACCURACY_LABELS, FACT_LABELS, type LedgerRecord } from "@/lib/types";
import { LedgerForm } from "./ui-form";

export const dynamic = "force-dynamic";

export default async function Page() {
  let records: LedgerRecord[] = [];
  let error = "";
  try {
    records = await listRecords();
  } catch (caught) {
    error = caught instanceof Error ? caught.message : "load_failed";
  }
  const stats = summarize(records);

  return (
    <main>
      <header className="top">
        <h1>팀 대화 원장</h1>
        <p>
          공모전 제출 서버와 팀 LLM 링크는 그대로 둡니다. 여기서는 대화를 붙여넣고
          팩트·정확도·개선점만 팀 공유 원장에 쌓습니다. 14자리 접수번호는 DART 원문 링크로 바뀝니다.
        </p>
      </header>

      <dl className="stats">
        <div><dt>기록</dt><dd>{stats.total}</dd></div>
        <div><dt>팩트 맞음</dt><dd>{stats.facts.correct}</dd></div>
        <div><dt>팩트 틀림</dt><dd>{stats.facts.incorrect}</dd></div>
        <div><dt>정확</dt><dd>{stats.accuracy.high}</dd></div>
        <div><dt>부정확</dt><dd>{stats.accuracy.low}</dd></div>
      </dl>

      {error ? <p className="status">원장을 열 수 없습니다: {error === "database_url_required" ? "Vercel에 DATABASE_URL(Neon)을 연결하세요." : error}</p> : null}

      <section className="card" style={{ padding: "1rem" }}>
        <h2>대화 붙여넣기</h2>
        <LedgerForm />
      </section>

      <section style={{ marginTop: "1.5rem" }}>
        <h2>쌓인 기록</h2>
        <div className="list">
          {records.length === 0 && !error ? <p className="status">아직 기록이 없습니다.</p> : null}
          {records.map((row) => (
            <article key={row.id} className="item">
              <div className="meta">
                {new Date(row.created_at).toLocaleString("ko-KR")} · {row.author} ·{" "}
                <span className={row.fact === "incorrect" ? "bad" : "good"}>{FACT_LABELS[row.fact]}</span>
                {" · "}
                <span className={row.accuracy === "low" ? "bad" : ""}>{ACCURACY_LABELS[row.accuracy]}</span>
              </div>
              <pre className="transcript">{row.transcript}</pre>
              {row.improvement ? <p>개선점: {row.improvement}</p> : null}
              {row.notes ? <p>메모: {row.notes}</p> : null}
              {row.receipts.length > 0 ? (
                <p className="links">
                  {row.receipts.map((no) => (
                    <a key={no} href={dartUrl(no)} target="_blank" rel="noreferrer">
                      공시 원문 {no}
                    </a>
                  ))}
                </p>
              ) : null}
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
