import { ImageResponse } from "next/og";

export const runtime = "edge";
export const alt = "金継ぎジャーナル";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OgImage() {
  return new ImageResponse(
    (
      <div
        style={{
          background: "#0d0b07",
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: "serif",
        }}
      >
        <div style={{ fontSize: 80, color: "#c9a84c", marginBottom: 24 }}>金継ぎ</div>
        <div style={{ fontSize: 36, color: "#e8e0d0", marginBottom: 16, letterSpacing: "0.1em" }}>
          KINTSUGI JOURNAL
        </div>
        <div style={{ fontSize: 22, color: "#c9a84c", opacity: 0.7, marginBottom: 40 }}>
          失敗の中に、金がある。
        </div>
        <div
          style={{
            fontSize: 16,
            color: "#e8e0d0",
            opacity: 0.4,
            border: "1px solid #c9a84c",
            padding: "8px 24px",
            borderRadius: 2,
          }}
        >
          AIが失敗から金を見つける月額$5のジャーナル
        </div>
      </div>
    ),
    { ...size }
  );
}
