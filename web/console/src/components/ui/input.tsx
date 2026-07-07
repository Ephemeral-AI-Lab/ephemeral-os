import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      "h-8 w-full rounded-md border border-line bg-surface px-2.5 text-[13px] text-ink placeholder:text-ink-faint",
      "focus:border-accent focus:outline-none focus-visible:outline-2 focus-visible:outline-accent",
      "disabled:bg-app disabled:text-ink-faint",
      className,
    )}
    {...props}
  />
));
Input.displayName = "Input";
