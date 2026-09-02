"use client";

import { useRef, useState } from "react";
import { saveRecord } from "./actions";
import { ACCURACY_LABELS, FACT_LABELS } from "@/lib/types";

export function LedgerForm() {
  const formRef = useRef<HTMLFormElement>(null);
  const [status, setStatus] = useState("");

  return (
    <form
      ref={formRef}
      action={async (formData) => {
        const result = await saveRecord(formData);
        if (!result.ok) {
          setStatus(`저장 실패: ${result.error}`);
          return;
        }
        formRef.current?.reset();
        setStatus("저장했습니다. 아래 목록에 바로 쌓입니다.");
      }}
    >
      <label>
        작성자
        <input name="author" required maxLength={80} placeholder="이름" />
      </label>
      <label>
        팀 LLM 대화 (복사해서 붙여넣기)
        <textarea name="transcript" required maxLength={20000} placeholder="질문과 답을 그대로 붙여넣습니다." />
      </label>
      <div className="row">
        <label>
          팩트 판별
          <select name="fact" defaultValue="unknown">
            {Object.entries(FACT_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          정확도 판별
          <select name="accuracy" defaultValue="medium">
            {Object.entries(ACCURACY_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
      </div>
      <label>
        개선할 점
        <textarea name="improvement" maxLength={8000} style={{ minHeight: "5rem" }} placeholder="검색 누락, 정정본, 단위, 거절 실패 등" />
      </label>
      <label>
        메모 / 관련 정보
        <textarea name="notes" maxLength={8000} style={{ minHeight: "5rem" }} placeholder="접수번호가 있으면 자동으로 원문 링크가 생깁니다." />
      </label>
      <button type="submit">원장에 저장</button>
      {status ? <p className="status">{status}</p> : null}
    </form>
  );
}
