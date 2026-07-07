export function PlaceholderTab({ title, phase }: { title: string; phase: string }) {
  return (
    <div className="mx-auto mt-16 max-w-md rounded-lg border border-line bg-surface p-8 text-center">
      <div className="text-sm font-semibold">{title}</div>
      <p className="mt-2 text-xs text-ink-mid">Arrives in {phase}.</p>
    </div>
  );
}
