import { useEffect, useState } from "react";

import { ApiError, getMe, type Me } from "./api";
import { isSignedIn } from "./auth";

/** The signed-in account and its onboarding state; null when signed out. */
export function useMe() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(isSignedIn());

  useEffect(() => {
    if (!isSignedIn()) return;
    let live = true;
    getMe()
      .then((value) => live && setMe(value))
      .catch((err: ApiError) => live && setError(err.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, []);

  return { me, error, loading };
}
