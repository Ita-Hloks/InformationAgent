import { type RefObject, useEffect, useRef } from "react";

export function useClickOutside(
  targetRef: RefObject<HTMLElement | null>,
  onOutside: () => void,
  enabled = true,
) {
  const onOutsideRef = useRef(onOutside);
  onOutsideRef.current = onOutside;

  useEffect(() => {
    if (!enabled) return;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node) || targetRef.current?.contains(target)) return;
      onOutsideRef.current();
    };

    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [enabled, targetRef]);
}
