import { Link } from "react-router";

export function NotFound() {
  return (
    <div className="mx-auto mt-24 max-w-md rounded-lg border border-line bg-surface p-8 text-center">
      <div className="text-lg font-semibold">Nothing here</div>
      <p className="mt-2 text-sm text-ink-mid">
        This route doesn&apos;t exist.
      </p>
      <Link to="/" className="mt-4 inline-block text-sm text-accent hover:underline">
        Back to the Fleet Board
      </Link>
    </div>
  );
}
