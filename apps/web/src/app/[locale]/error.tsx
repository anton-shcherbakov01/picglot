"use client";

import { useEffect } from "react";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // The digest is a server-side reference; the message itself is never shown.
    console.error("page error", error.digest);
  }, [error]);

  return (
    <div className="container-page grid min-h-[60vh] place-items-center py-20 text-center">
      <div>
        <h1 className="text-2xl font-semibold">Something went wrong</h1>
        <p className="mt-2 text-muted">
          The page could not be loaded. Trying again often fixes it.
        </p>
        {error.digest && (
          <p className="mt-2 text-xs text-muted">Reference: {error.digest}</p>
        )}
        <button type="button" onClick={reset} className="btn-primary mt-6">
          Try again
        </button>
      </div>
    </div>
  );
}
