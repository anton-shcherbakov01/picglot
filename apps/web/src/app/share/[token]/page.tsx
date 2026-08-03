import type { Metadata } from "next";

import { SharedProject } from "@/components/SharedProject";

// A shared result is private-by-link and must never be indexed or cached.
export const metadata: Metadata = {
  title: "Shared result",
  robots: { index: false, follow: false, nocache: true },
};

export const dynamic = "force-dynamic";

export default async function SharePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  return (
    <div className="min-h-dvh bg-bg">
      <div className="container-page max-w-4xl py-10">
        <SharedProject token={token} />
      </div>
    </div>
  );
}
