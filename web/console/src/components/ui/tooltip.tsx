import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { cn } from "@/lib/cn";

export const TooltipProvider = TooltipPrimitive.Provider;

export function Tooltip({
  content,
  children,
  ...props
}: ComponentPropsWithoutRef<typeof TooltipPrimitive.Root> & {
  content: ReactNode;
}) {
  return (
    <TooltipPrimitive.Root delayDuration={300} {...props}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          sideOffset={4}
          className={cn(
            "z-50 max-w-xs rounded border border-line bg-ink px-2 py-1 text-xs text-white",
          )}
        >
          {content}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}
