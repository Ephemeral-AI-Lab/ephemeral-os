import { cn } from "@/lib/cn";

/**
 * A fixed-size inline-SVG sparkline. Plain SVG by design (design.md):
 * uPlot is overkill at this size, and a fixed width/height guarantees
 * zero layout shift across poll refreshes.
 */
export function ResourceSparkline({
  values,
  width = 72,
  height = 18,
  label,
  className,
}: {
  values: number[];
  width?: number;
  height?: number;
  label?: string;
  className?: string;
}) {
  const points = sparklinePoints(values, width, height);
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
      className={cn("shrink-0 text-accent", className)}
    >
      {points ? (
        <polyline
          points={points}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      ) : (
        <line
          x1="1"
          y1={height - 2}
          x2={width - 1}
          y2={height - 2}
          stroke="var(--color-line)"
          strokeWidth="1.2"
        />
      )}
    </svg>
  );
}

function sparklinePoints(
  values: number[],
  width: number,
  height: number,
): string | null {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const stepX = (width - 2) / (values.length - 1);
  return values
    .map((value, index) => {
      const x = 1 + index * stepX;
      const y = height - 2 - ((value - min) / range) * (height - 4);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}
