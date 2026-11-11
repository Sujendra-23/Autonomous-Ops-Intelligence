import { Icon } from "./Icon";

// Prices are supplied by the product owner. Checkout and entitlement enforcement
// are not implemented; do not imply that choosing a plan enables billing.
export const pricingPlans = [
  { id: "free", name: "Free", audience: "A little clarity, to get started.", price: "$0", period: "Explore meeting intelligence", members: "For getting to know AOI", features: ["Meeting transcripts and structured notes", "Tasks, decisions, risks, and blockers", "Usage allowance to be confirmed"] },
  { id: "pro", name: "Pro", audience: "A shared rhythm for a growing company.", price: "$50", period: "per company / month", members: "For companies with 10–20 members", features: ["A plan for your company of 10–20 members", "One company price, billed monthly", "Feature and usage limits to be confirmed"] },
  { id: "team", name: "Team", audience: "For the way your whole company works.", price: "Let’s talk", period: "Custom monthly pricing", members: "For larger companies", features: ["Pricing tailored to your company size", "Discuss your member count and needs", "A monthly quote for your organization"] },
];

export function Pricing({ onBack }: { onBack?: () => void }) {
  return <section className="pricing studio-pricing" aria-labelledby="pricing-title">
    {onBack && <button className="text-button" onClick={onBack}>Back to workspace<Icon name="arrow"/></button>}
    <div className="pricing-heading"><h1 id="pricing-title">Good conversations.<br/><em>A plan that fits.</em></h1><p>From your first meeting to your company’s next chapter.</p></div>
    <div className="pricing-plans">{pricingPlans.map(plan => <article className={`pricing-plan${plan.id === "pro" ? " featured-plan" : ""}`} key={plan.id}>
      <div className="plan-title"><h2>{plan.name}</h2>{plan.id === "pro" && <span>10–20 members</span>}</div><p className="plan-audience">{plan.audience}</p>
      <div className="plan-price">{plan.price}</div><p className="plan-period">{plan.period}</p>
      <p className="plan-members">{plan.members}</p>
      <ul>{plan.features.map(feature => <li key={feature}><Icon name="check"/>{feature}</li>)}</ul>
      {plan.id === "team" ? <p className="plan-availability">Talk to us for a quote. Sales contact details coming soon.</p> : <p className="plan-availability">{plan.id === "free" ? "Usage limits to be announced" : "Paid subscriptions coming soon"}</p>}
    </article>)}</div>
    <div className="pricing-notice"><Icon name="book"/><p>Plan details preview. Paid checkout is not available yet. Final feature entitlements and usage limits are being defined; this page does not change your workspace access.</p></div>
  </section>;
}
