"use client";

import { useState } from "react";
import data from "@/data/impact.json";

type View = (typeof data.windows)[number];
type Engineer = View["top"][number];

const PR_URL = "https://github.com/PostHog/posthog/pull/";
const METHOD = "#method"; // "#" can't appear in a GitHub login
const SNAPSHOT = new Date(data.snapshot * 1000).toLocaleDateString("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

function heldLabel(held: number, measured: boolean) {
  if (!measured) return "Too early to tell";
  if (held >= 1.05) return "Better than peers";
  if (held <= 0.95) return "Below peers";
  return "In line with peers";
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 text-sm">
      <span className="text-zinc-500">{label}</span>
      <span className="text-right">{value}</span>
    </div>
  );
}

function PersonDetail({ engineer }: { engineer: Engineer }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-zinc-400">
        Top 5 under {engineer.top5}% of weightings. With agent-only PRs removed: #{engineer.rankWithoutAutonomous}.
      </p>
      <div className="divide-y divide-zinc-800 border-y border-zinc-800">
        <Row label="Built on by" value={`${engineer.builders} people`} />
        <Row label="Later fixes" value={`${engineer.corrected.toLocaleString()} lines vs ${Math.round(engineer.expectedCorrected).toLocaleString()} expected`} />
        <Row label="Areas" value={`${engineer.breadth} with real depth`} />
        <Row label="Written reviews" value={`${engineer.reviews}, top ${Math.max(100 - engineer.attention, 1)}% for trust`} />
        <Row label="Merged PRs" value={engineer.prs ? `${engineer.prs.toLocaleString()} (#${engineer.volumeRank} by count, not scored)` : "None"} />
        <Row label="Marked fully autonomous" value={engineer.autonomous.toLocaleString()} />
      </div>
      {engineer.evidence.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-sm text-zinc-500">Most built-on work</p>
          {engineer.evidence.map((pr) => (
            <a
              key={pr.number}
              href={`${PR_URL}${pr.number}`}
              target="_blank"
              rel="noopener noreferrer"
              className="truncate text-sm text-zinc-300 hover:text-white"
            >
              #{pr.number} {pr.title}
            </a>
          ))}
        </div>
      )}
      <p className="text-xs text-zinc-500">Data as of {SNAPSHOT}</p>
    </div>
  );
}

function Method({ view }: { view: View }) {
  return (
    <div className="flex flex-col gap-3 text-sm text-zinc-400">
      <p>Nothing here counts volume. Each measure looks at what happened after code merged.</p>
      <div className="divide-y divide-zinc-800 border-y border-zinc-800">
        <Explain title="Built on" text="Later work by other people right next to this code. Each person gets one vote, so shipping more PRs, by hand or by agent, adds nothing." />
        <Explain title="Held up" text="Lines replaced by a later fix, compared with peers on code of the same age. Idle code and moved code don't count." />
        <Explain title="Areas" text="An h-index: 8 means 8 areas with at least 8 built-on PRs each." />
        <Explain title="Reviews" text="Who people turn to, from written human reviews. Reviews that disclose an agent are removed." />
      </div>
      <p>
        These can&apos;t be fairly weighted by hand, so we tried {data.samples.toLocaleString()} random weightings. The big
        number is how often someone made the top 5: 100% means under every weighting, 0% means under none. It&apos;s a
        share, not a grade.
      </p>
      <p>
        {view.sameTop5WithoutBiggestPr
          ? "Removing everyone's biggest PR leaves the same top 5."
          : "Removing everyone's biggest PR changes the top 5."}
      </p>
      <p className="text-zinc-500">
        Based on {view.humanPrs.toLocaleString()} PRs from {view.people} people. Not visible here: incidents, mentoring,
        planning, and work outside this repo. Data as of {SNAPSHOT}.
      </p>
    </div>
  );
}

function Explain({ title, text }: { title: string; text: string }) {
  return (
    <div className="py-1.5">
      <span className="text-zinc-100">{title}.</span> {text}
    </div>
  );
}

export default function Home() {
  const [days, setDays] = useState(data.windows[0].days);
  const [panel, setPanel] = useState<string | null>(null); // a login, METHOD, or closed

  const view = data.windows.find((w) => w.days === days) ?? data.windows[0];
  const engineer = view.top.find((e) => e.login === panel);
  const open = Boolean(engineer) || panel === METHOD;
  const leftWidth = `w-11/12 ${open ? "lg:w-3/4" : "lg:w-2/4"}`;

  return (
    <div className="flex min-h-screen md:h-screen md:overflow-hidden p-8">
      <div className="grid grid-cols-1 md:grid-cols-2 flex-1 items-center justify-center gap-8 w-full">
        <div className="flex flex-col items-center gap-6 animate-in fade-in-0 duration-500">
          <div className={`${leftWidth} transition-all ease-in-out duration-500`}>
            <div className="flex gap-1">
              {data.windows.map((option) => (
                <button
                  type="button"
                  key={option.days}
                  onClick={() => setDays(option.days)}
                  aria-pressed={days === option.days}
                  className={`px-2 py-0.5 rounded-md text-sm cursor-pointer transition-colors
                  ${days === option.days ? "bg-zinc-800 text-zinc-100" : "text-zinc-500 hover:text-zinc-300"}`}
                >
                  {option.days} days
                </button>
              ))}
            </div>
            <h1 className="text-4xl tracking-tighter mt-2">Who moved PostHog forward</h1>
            <p className="text-zinc-400 mt-3">
              {Math.round(view.declaredAgent)}% of people&apos;s PRs declare an agent and {Math.round(view.writtenReviewPrs)}% get
              a written human review, so we rank what happened after merge.
            </p>
            {!view.heldMeasured && (
              <p className="text-sm text-zinc-500 mt-2">
                Early read. This work is too recent to tell who held up, and these names change a lot week to week.
              </p>
            )}
          </div>

          <div className={`${leftWidth} ${open ? "h-auto" : "h-12"} border border-zinc-800 bg-zinc-900 rounded-md overflow-hidden transition-all ease-in-out duration-500`}>
            <button
              type="button"
              onClick={() => setPanel(open ? null : METHOD)}
              className="flex w-full h-12 items-center justify-between px-4 cursor-pointer"
            >
              <span>{engineer ? engineer.name : "How this is calculated"}</span>
              <span className="text-zinc-500">{open ? "×" : "+"}</span>
            </button>
            <div className={`${open ? "h-auto opacity-100 px-4 pb-4" : "h-0 opacity-0 p-0"} overflow-hidden transition-all ease-in-out duration-500`}>
              {engineer ? <PersonDetail engineer={engineer} /> : panel === METHOD ? <Method view={view} /> : null}
            </div>
          </div>
        </div>

        <div className="flex flex-col gap-3 [@media(max-height:760px)]:gap-1.5 w-full lg:w-4/5 h-full px-2 md:overflow-y-auto [scrollbar-width:none] justify-center
        animate-in fade-in-0 slide-in-from-bottom-5 duration-500 ease-out delay-300 fill-mode-backwards">
          <p className="text-sm text-zinc-500">How often each person makes the top 5, across every weighting</p>
          {view.top.map((person, i) => (
            <button
              type="button"
              key={person.login}
              onClick={() => setPanel(person.login === panel ? null : person.login)}
              style={{ animationDelay: `${300 + i * 60}ms` }}
              className={`@container text-left bg-zinc-900 w-full px-4 py-3 border rounded-lg cursor-pointer transition-colors
              ${panel === person.login ? "border-zinc-500" : "border-zinc-800 hover:border-zinc-700"}
              animate-in fade-in-0 slide-in-from-top-5 duration-500 ease-out fill-mode-backwards`}
            >
              <div className="flex items-baseline justify-between gap-4">
                <p className="text-xl truncate">{person.name}</p>
                <p className="text-[9cqw] leading-none tracking-tighter">{Math.round(person.top5)}%</p>
              </div>
              <div className="grid grid-cols-[1fr_1.4fr_auto] border-t border-zinc-800 mt-2 pt-2 text-sm">
                <span>
                  <p className="text-zinc-500">Built on by</p>
                  <p>{person.builders} people</p>
                </span>
                <span>
                  <p className="text-zinc-500">Held up</p>
                  <p>{heldLabel(person.held, view.heldMeasured)}</p>
                </span>
                <span className="text-right">
                  <p className="text-zinc-500">Areas</p>
                  <p>{person.breadth}</p>
                </span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
