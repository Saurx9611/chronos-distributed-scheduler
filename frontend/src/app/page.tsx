export default function Home() {
  return (
    <main className="mx-auto max-w-3xl px-8 py-24">
      <p className="text-sm uppercase tracking-widest text-emerald-400">Phase 1 · Foundation</p>
      <h1 className="mt-6 text-5xl font-semibold">Chronos</h1>
      <p className="mt-6 text-xl leading-relaxed text-slate-300">
        PostgreSQL-backed distributed task scheduling and execution.
      </p>
      <p className="mt-8 leading-relaxed text-slate-400">
        Architecture and repository tooling are being established. Job submission,
        worker execution, and the operations dashboard are planned for later phases.
      </p>
    </main>
  );
}
