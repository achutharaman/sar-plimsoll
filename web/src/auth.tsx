import { initializeApp, type FirebaseApp } from "firebase/app";
import {
  getAuth,
  onIdTokenChanged,
  signInWithEmailAndPassword,
  signOut as firebaseSignOut,
  type Auth,
} from "firebase/auth";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setTokenProvider } from "./api";
import type { ClientConfig } from "./types";

export interface Session {
  uid: string;
  email: string | null;
  admin: boolean;
}

interface AuthState {
  config: ClientConfig | null;
  session: Session | null;
  ready: boolean;
  error: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);
const DEV_KEY = "plimsoll.dev-session";

function readDevSession(): Session | null {
  try {
    const raw = localStorage.getItem(DEV_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<ClientConfig | null>(null);
  const [auth, setAuth] = useState<Auth | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .config()
      .then((cfg) => {
        setConfig(cfg);
        if (cfg.auth_mode === "dev") {
          const dev = readDevSession();
          setSession(dev);
          setTokenProvider(async () => {
            const current = readDevSession();
            return current ? `dev:${current.uid}${current.admin ? ":admin" : ""}` : null;
          });
          setReady(true);
          return;
        }
        if (!cfg.firebase.apiKey) {
          setError("Sign-in is not configured on this deployment (missing Firebase web API key).");
          setReady(true);
          return;
        }
        const app: FirebaseApp = initializeApp(cfg.firebase);
        const firebaseAuth = getAuth(app);
        setAuth(firebaseAuth);
        setTokenProvider(async () => (firebaseAuth.currentUser ? firebaseAuth.currentUser.getIdToken() : null));
        onIdTokenChanged(firebaseAuth, async (user) => {
          if (!user) {
            setSession(null);
          } else {
            // Admin in the UI only shows or hides tabs; the API enforces it on every admin route.
            const me = await api.me().catch(() => ({ uid: user.uid, email: user.email, admin: false }));
            setSession({ uid: me.uid, email: me.email ?? user.email, admin: me.admin });
          }
          setReady(true);
        });
      })
      .catch(() => {
        setError("Cannot load configuration from the API.");
        setReady(true);
      });
  }, []);

  const signIn = useCallback(
    async (email: string, password: string) => {
      if (config?.auth_mode === "dev") {
        const [uid, role] = email.split(":");
        const dev = { uid: uid || "dev", email: null, admin: role === "admin" };
        localStorage.setItem(DEV_KEY, JSON.stringify(dev));
        setSession(dev);
        return;
      }
      if (!auth) throw new Error("Sign-in is not available");
      await signInWithEmailAndPassword(auth, email, password);
    },
    [auth, config],
  );

  const signOut = useCallback(async () => {
    localStorage.removeItem(DEV_KEY);
    if (auth) await firebaseSignOut(auth);
    setSession(null);
  }, [auth]);

  const value = useMemo(
    () => ({ config, session, ready, error, signIn, signOut }),
    [config, session, ready, error, signIn, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
