import type { Session, User } from "@supabase/supabase-js";
import { createContext, useContext } from "react";

const RETURN_TO_KEY = "open-pxrd-auth-return-to";

export type AuthContextValue = {
  session: Session | null;
  user: User | null;
  loading: boolean;
  error: string | null;
  isConfigured: boolean;
  signInWithGoogle: (returnTo?: string) => Promise<void>;
  signOut: () => Promise<void>;
  clearError: () => void;
};

export const AuthContext = createContext<AuthContextValue | null>(null);

function safeInternalPath(value: string | null | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/digitize";
  if (value.startsWith("/auth/callback")) return "/digitize";
  return value;
}

export function rememberAuthReturnTo(value?: string): void {
  window.sessionStorage.setItem(RETURN_TO_KEY, safeInternalPath(value));
}

export function consumeAuthReturnTo(): string {
  const value = safeInternalPath(window.sessionStorage.getItem(RETURN_TO_KEY));
  window.sessionStorage.removeItem(RETURN_TO_KEY);
  return value;
}

export function peekAuthReturnTo(): string {
  return safeInternalPath(window.sessionStorage.getItem(RETURN_TO_KEY));
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
