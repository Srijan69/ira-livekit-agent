import type { UserProfile } from "@shared/types";

export function profileIdentity(profile?: UserProfile | null) {
  const name = profile?.name.trim() || "Your profile";
  const initials =
    profile?.name
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .map((part) => Array.from(part)[0])
      .slice(0, 2)
      .join("")
      .toLocaleUpperCase() || "";
  return { name, initials };
}
