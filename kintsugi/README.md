# Kintsugi Journal — 金継ぎ

> *The Japanese art of repairing broken pottery with gold — making it more beautiful for having been broken.*

An AI-powered failure journal that finds the hidden wisdom in your setbacks.

## Revenue Model

- **Free tier**: 5 AI analyses/month, local storage
- **Gold tier**: $5/month or $45/year — unlimited AI, cloud sync, weekly digest
- **Target**: 250 Gold subscribers = **$15,000/year**

Why it works: No other journaling app combines Japanese wabi-sabi philosophy + AI reframing + beautiful dark aesthetics. Journaling apps have 3-12% conversion to paid. With 3,000 free users, 250 conversions is achievable.

## Stack

- Next.js 15 (App Router)
- Tailwind CSS v4
- Claude Haiku (AI analysis — cheap: ~$0.001/analysis)
- localStorage (free tier) / Supabase (Gold tier, next step)

## Getting Started

```bash
cp .env.example .env.local
# Add your ANTHROPIC_API_KEY

npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Monetization Roadmap

1. **Now**: Free journal with AI analysis (localStorage)
2. **Month 2**: Add Stripe + Supabase for Gold tier
3. **Month 3**: Weekly email digest of golden wisdom from entries
4. **Month 6**: Pattern analysis — "You keep failing at X because Y"
5. **Year 2**: Team/therapist edition ($20/seat)

## Cost Structure

- Hosting: ~$20/month (Vercel)
- AI: ~$0.001/analysis × 5,000/month = $5/month
- Total costs: ~$25/month
- At 250 Gold subscribers: $1,250/month revenue = **$1,225/month profit**
