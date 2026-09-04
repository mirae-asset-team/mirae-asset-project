import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "공모전 팀 대화 원장",
  description: "팀 LLM 대화를 붙여넣고 팩트·정확도·개선점을 쌓는 공유 원장",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
