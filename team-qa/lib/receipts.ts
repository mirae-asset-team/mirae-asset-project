const RECEIPT = /\b(\d{14})\b/g;

export function extractReceipts(text: string): string[] {
  return Array.from(new Set(Array.from(text.matchAll(RECEIPT), (match) => match[1])));
}

export function dartUrl(receiptNo: string): string {
  return `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${receiptNo}`;
}
