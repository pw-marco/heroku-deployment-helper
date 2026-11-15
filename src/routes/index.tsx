import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "AkkiUp Bot – Heroku Deploy Guide" },
      { name: "description", content: "AkkiUp Telegram bot ko GitHub se Heroku pe deploy karne ka step-by-step guide." },
      { property: "og:title", content: "AkkiUp Bot – Heroku Deploy Guide" },
      { property: "og:description", content: "GitHub se Heroku pe AkkiUp bot deploy karne ke steps." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Index,
});

const steps: { title: string; body: string[] }[] = [
  {
    title: "GitHub connect karo",
    body: [
      "Lovable me upar + menu → Project → GitHub → Connect dabao.",
      "Ek naya private repo ban jayega jisme bot ka code (bot/ folder) aur Heroku files hongi.",
    ],
  },
  {
    title: "Heroku app banao",
    body: ["heroku.com → New → Create new app → naam do → region Europe/US → Create app."],
  },
  {
    title: "Stack ko Container pe set karo (sabse zaroori)",
    body: [
      "Heroku CLI install karo, phir chalao: heroku login",
      "Phir: heroku stack:set container -a <app-name>",
      "Isse Heroku Python error nahi dega, Dockerfile se build karega.",
    ],
  },
  {
    title: "Config Vars bharo",
    body: [
      "Settings → Reveal Config Vars me ye daalo:",
      "API_ID, API_HASH, BOT_TOKEN, MONGO_URL, OWNER_ID, API_SECRET_KEY, LOG_CHANNEL_ID, AUTH_LOG_CHANNEL_ID",
      "HOST_URL = https://<app-name>.herokuapp.com (Settings → Domains me dikhega)",
    ],
  },
  {
    title: "GitHub se deploy karo",
    body: [
      "Deploy tab → Deployment method: GitHub → repo search karke Connect.",
      "Branch main chuno → Deploy Branch. (Chaaho to Enable Automatic Deploys bhi.)",
    ],
  },
  {
    title: "Dyno ON karo",
    body: [
      "Resources tab → web dyno ON. Sirf web dyno chahiye — bot aur API dono isi me chalte hain.",
      "Eco dyno 30 min baad so jata hai, isliye Basic dyno lo taaki bot 24x7 chale.",
    ],
  },
  {
    title: "Check karo",
    body: ["More → View logs me 'Bot commands set successfully!' dikhe to bot chal raha hai. Telegram pe /start bhejo."],
  },
];

function Index() {
  return (
    <main className="min-h-screen bg-background px-5 py-12 text-foreground">
      <div className="mx-auto max-w-2xl">
        <p className="text-sm font-medium uppercase tracking-widest text-muted-foreground">AkkiUp Bot</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight">Heroku pe deploy kaise karein</h1>
        <div className="mt-6 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm">
          <strong>Zaroori:</strong> Purane code me bot token aur MongoDB password likhe hue the. Maine unhe code se hata diya hai.
          BotFather me /revoke karke naya BOT_TOKEN lo, aur MongoDB ka password badlo, phir naye values Config Vars me daalo.
        </div>
        <ol className="mt-8 space-y-4">
          {steps.map((s, i) => (
            <li key={s.title} className="rounded-lg border bg-card p-5 text-card-foreground">
              <div className="flex items-center gap-3">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">
                  {i + 1}
                </span>
                <h2 className="font-semibold">{s.title}</h2>
              </div>
              <ul className="mt-3 space-y-1.5 pl-11 text-sm text-muted-foreground">
                {s.body.map((b) => (
                  <li key={b} className="break-words">{b}</li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      </div>
    </main>
  );
}
