export const FACT_LABELS = {
  correct: "팩트 맞음",
  partial: "부분 맞음",
  incorrect: "팩트 틀림",
  unknown: "확인 불가",
} as const;

export const ACCURACY_LABELS = {
  high: "정확",
  medium: "부분 정확",
  low: "부정확",
  abstain_ok: "거절이 맞음",
} as const;

export type FactVerdict = keyof typeof FACT_LABELS;
export type AccuracyVerdict = keyof typeof ACCURACY_LABELS;

export type LedgerRecord = {
  id: string;
  created_at: string;
  author: string;
  transcript: string;
  fact: FactVerdict;
  accuracy: AccuracyVerdict;
  improvement: string;
  notes: string;
  receipts: string[];
};
