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
          content: `You are a Kintsugi master — you find the gold in broken things.

A user has written about a failure, setback, or difficult experience in their journal. Your job is to practice "kintsugi" on this moment: find and name the gold hidden within the crack.

Respond in JSON with this exact structure:
{
  "goldLine": "One powerful sentence (max 20 words) that names the hidden gift or lesson — poetic, not platitudinous",
  "wisdom": "2-3 sentences expanding on the gold. Be specific to THEIR situation. Avoid generic advice. Reference something they actually wrote.",
  "strength": "Name one specific strength this experience revealed or forged in them (1-4 words, e.g. 'Resilient discernment' or 'Quiet courage')",
  "question": "One reflective question to deepen their insight (optional — only if genuinely useful)"
}

The user's journal entry:
${entry.trim()}

Return only valid JSON, nothing else.`,
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
