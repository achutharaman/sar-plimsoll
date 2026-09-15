import { useCallback, useState } from "react";
import { useAuth } from "./auth";
import { Button, Spinner } from "./components/ui";
import { CostView } from "./views/CostView";
import { HistoryView } from "./views/HistoryView";
import { InsightsView } from "./views/InsightsView";
import { ReviewView } from "./views/ReviewView";
import { RulesView } from "./views/RulesView";
import { Brand, SignIn } from "./views/SignIn";

type Tab = "review" | "history" | "insights" | "cost" | "rules";

export function App() {
  const { ready, session, signOut } = useAuth();
  const [tab, setTab] = useState<Tab>("review");
  const [openReview, setOpenReview] = useState<string | null>(null);
  const opened = useCallback(() => setOpenReview(null), []);

  if (!ready) return <div className="grid min-h-screen place-items-center"><Spinner label="Loading…" /></div>;
  if (!session) return <SignIn />;

  const tabs: { id: Tab; label: string }[] = [
    { id: "review", label: "Review" },
    { id: "history", label: "History" },
    { id: "insights", label: "Insights" },
    { id: "cost", label: "Cost" },
    ...(session.admin ? [{ id: "rules" as Tab, label: "Rules" }] : []),
  ];

  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <Brand />
          <nav aria-label="Sections" className="order-3 flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto">
            {tabs.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                aria-current={tab === t.id ? "page" : undefined}
                className={`h-9 rounded-lg px-3 text-sm whitespace-nowrap ${tab === t.id ? "bg-surface-2 font-semibold text-ink" : "text-ink-2 hover:text-ink"}`}
              >
                {t.label}
              </button>
            ))}
          </nav>
          <div className="flex items-center gap-2">
            <span className="hidden text-sm text-ink-2 md:inline">{session.email ?? session.uid}{session.admin ? " · admin" : ""}</span>
            <Button variant="ghost" onClick={signOut}>Sign out</Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        {tab === "review" && <ReviewView openReviewId={openReview} onOpened={opened} />}
        {tab === "history" && <HistoryView onOpen={(id) => { setOpenReview(id); setTab("review"); }} />}
        {tab === "insights" && <InsightsView />}
        {tab === "cost" && <CostView />}
        {tab === "rules" && session.admin && <RulesView />}
      </main>
      <footer className="mx-auto max-w-6xl px-4 pb-8 text-xs text-muted">
        Scores are computed by a versioned rubric, not by the model. Submitted code is private to your account and never used for training.
      </footer>
    </div>
  );
}
