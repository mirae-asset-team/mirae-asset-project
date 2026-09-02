import assert from "node:assert/strict";
import test from "node:test";
import { dartUrl, extractReceipts } from "./receipts.ts";

test("extracts unique 14-digit receipt numbers", () => {
  const text = "근거 20260310002820 그리고 다시 20260310002820, 다른 건 20250318000001";
  assert.deepEqual(extractReceipts(text), ["20260310002820", "20250318000001"]);
  assert.equal(dartUrl("20260310002820"), "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260310002820");
});
