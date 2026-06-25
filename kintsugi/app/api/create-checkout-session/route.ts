import Stripe from "stripe";

export async function POST() {
  try {
    const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);
    const session = await stripe.checkout.sessions.create({
      payment_method_types: ["card"],
      mode: "subscription",
      line_items: [
        {
          price_data: {
            currency: "usd",
            product_data: {
              name: "Kintsugi Gold",
              description: "Unlimited AI analysis, cloud sync, weekly wisdom digest",
            },
            unit_amount: 500,
            recurring: { interval: "month" },
          },
          quantity: 1,
        },
      ],
      success_url: `${process.env.NEXT_PUBLIC_BASE_URL}/gold/success?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${process.env.NEXT_PUBLIC_BASE_URL}/`,
    });

    return Response.json({ url: session.url });
  } catch (e) {
    console.error("Stripe error:", e);
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
