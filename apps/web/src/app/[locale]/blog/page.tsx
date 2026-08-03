import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { serverFetch } from "@/lib/api";
import { formatDate } from "@/lib/format";
import {
  absoluteUrl,
  alternates,
  isLocale,
  localePath,
  type Locale,
} from "@/lib/i18n";

interface Post {
  slug: string;
  title: string;
  excerpt: string;
  author_name: string;
  tags: string[];
  published_at: string | null;
  reading_minutes: number;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const { languages } = alternates("/blog");
  return {
    title: locale === "ru" ? "Блог" : "Blog",
    description:
      locale === "ru"
        ? "Как работает распознавание, перевод и вёрстка текста на изображениях."
        : "How recognition, translation and typesetting of text in images actually work.",
    alternates: {
      canonical: absoluteUrl(localePath(locale, "blog")),
      languages,
    },
  };
}

export default async function BlogIndex({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const typed = locale as Locale;

  const posts = await serverFetch<Post[]>(`/api/v1/content/blog/${locale}`, {
    revalidate: 900,
  });

  return (
    <div className="container-page max-w-3xl py-12">
      <h1 className="text-3xl font-bold">{typed === "ru" ? "Блог" : "Blog"}</h1>

      {!posts || posts.length === 0 ? (
        <p className="mt-6 text-muted">
          {typed === "ru"
            ? "Пока нет опубликованных статей."
            : "No posts published yet."}
        </p>
      ) : (
        <ul className="mt-8 grid gap-4">
          {posts.map((post) => (
            <li key={post.slug} className="card p-5">
              <Link
                href={localePath(typed, `blog/${post.slug}`)}
                className="text-lg font-semibold hover:text-accent"
              >
                {post.title}
              </Link>
              <p className="mt-2 text-sm text-muted">{post.excerpt}</p>
              <p className="mt-3 text-xs text-muted">
                {post.published_at ? formatDate(post.published_at, typed) : ""}{" "}
                · {post.reading_minutes} min · {post.author_name}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
