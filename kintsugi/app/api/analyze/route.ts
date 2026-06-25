import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

export async function POST(request: Request) {
  const { entry } = await request.json();

  if (!entry || typeof entry !== "string" || entry.trim().length < 10) {
    return Response.json({ error: "Entry too short" }, { status: 400 });
  }

  let message;
  try {
    message = await client.messages.create({
      model: "claude-haiku-4-5-20251001",
      max_tokens: 600,
      messages: [
        {
          role: "user",
          content: `あなたは金継ぎの達人です。壊れたものの中に金を見つける人です。

ユーザーがジャーナルに失敗、挫折、または辛い体験を書きました。あなたの仕事はこの瞬間に「金継ぎ」を施すことです：亀裂の中に隠れた金を見つけ、名前をつけてください。

必ず日本語で、以下のJSON形式で返してください：
{
  "goldLine": "隠れた贈り物や教訓を表す力強い一文（最大30文字）。詩的に、陳腐にならないように。",
  "wisdom": "その金についての2〜3文。ユーザーの状況に具体的に言及してください。実際に書かれた内容を参照して、一般的なアドバイスは避けてください。",
  "strength": "この体験が明らかにした、または鍛えた強さを一言で（2〜8文字。例：「静かな勇気」「しなやかな判断力」）",
  "question": "洞察を深める内省的な質問（本当に役立つ場合のみ。任意）"
}

ユーザーのジャーナルエントリー：
${entry.trim()}

有効なJSONのみを返してください。他は何も返さないでください。`,
        },
      ],
    });
  } catch (e) {
    console.error("Anthropic API error:", e);
    return Response.json(
      { error: "AI API error", detail: String(e) },
      { status: 500 }
    );
  }

  const raw =
    message.content[0].type === "text" ? message.content[0].text : "";
  const text = raw.replace(/^```json\s*/i, "").replace(/```\s*$/i, "").trim();

  try {
    const parsed = JSON.parse(text);
    return Response.json(parsed);
  } catch (e) {
    console.error("Parse error:", e, "Raw text:", text);
    return Response.json(
      { error: "Failed to parse AI response" },
      { status: 500 }
    );
  }
}
