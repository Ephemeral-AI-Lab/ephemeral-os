import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "outline" | "ghost" | "danger";
type Size = "sm" | "md";

const variantClasses: Record<Variant, string> = {
  primary:
    "bg-accent text-white border border-accent hover:bg-accent/90 disabled:bg-accent/50 disabled:border-accent/30",
  outline:
    "bg-surface text-ink border border-line hover:bg-surface-hover disabled:text-ink-faint",
  ghost: "text-ink border border-transparent hover:bg-surface-hover disabled:text-ink-faint",
  danger:
    "bg-surface text-danger border border-line hover:border-danger/60 hover:bg-danger-soft disabled:text-ink-faint",
};

const sizeClasses: Record<Size, string> = {
  sm: "h-6 px-2 text-xs rounded",
  md: "h-8 px-3 text-[13px] rounded-md",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "outline", size = "md", type, ...props }, ref) => (
    <button
      ref={ref}
      type={type ?? "button"}
      className={cn(
        "inline-flex items-center gap-1.5 font-medium transition-colors cursor-pointer disabled:cursor-not-allowed whitespace-nowrap",
        variantClasses[variant],
        sizeClasses[size],
        className,
      )}
      {...props}
    />
  ),
);
Button.displayName = "Button";
