import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { serverFetch } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { absoluteUrl, isLocale, localePath, type Locale } from "@/lib/i18n";

interface Post {
  slug: string;
  title: string;
  excerpt: string;
  body_markdown: string;
  author_name: string;
  tags: string[];
  published_at: string | null;
  reading_minutes: number;
}

async function loadPost(locale: string, slug: string) {
  return serverFetch<Post>(`/api/v1/content/blog/${locale}/${slug}`, {
    revalidate: 900,
  });
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}): Promise<Metadata> {
  const { locale, slug } = await params;
  if (!isLocale(locale)) return {};
  const post = await loadPost(locale, slug);
  if (!post) return {};
  return {
    title: post.title,
    description: post.excerpt,
    alternates: { canonical: absoluteUrl(localePath(locale, `blog/${slug}`)) },
    openGraph: {
      type: "article",
      title: post.title,
      description: post.excerpt,
      publishedTime: post.published_at ?? undefined,
    },
  };
}

/**
 * Minimal Markdown rendering.
 *
 * Deliberately not `dangerouslySetInnerHTML` with a parser: posts come from the
 * CMS, and rendering to React elements means a stray `<script>` in editable
 * content can never execute.
 */
function renderMarkdown(markdown: string) {
  const blocks = markdown.split(/\n\n+/);
  return blocks.map((block, index) => {
    const trimmed = block.trim();
    if (trimmed.startsWith("### ")) {
      return <h3 key={index}>{trimmed.slice(4)}</h3>;
    }
    if (trimmed.startsWith("## ")) {
      return <h2 key={index}>{trimmed.slice(3)}</h2>;
    }
    if (trimmed.startsWith("- ")) {
      return (
        <ul key={index}>
          {trimmed.split("\n").map((line, lineIndex) => (
            <li key={lineIndex}>{line.replace(/^- /, "")}</li>
          ))}
        </ul>
      );
    }
    return <p key={index}>{trimmed}</p>;
  });
}

export default async function BlogPost({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}) {
  const { locale, slug } = await params;
  if (!isLocale(locale)) notFound();
  const post = await loadPost(locale, slug);
  if (!post) notFound();

  const typed = locale as Locale;

  return (
    <article className="container-page max-w-3xl py-12">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            "@context": "https://schema.org",
            "@type": "Article",
            headline: post.title,
            description: post.excerpt,
            datePublished: post.published_at,
            author: { "@type": "Organization", name: post.author_name },
          }),
        }}
      />
      <h1 className="text-3xl font-bold">{post.title}</h1>
      <p className="mt-2 text-sm text-muted">
        {post.published_at ? formatDate(post.published_at, typed) : ""} ·{" "}
        {post.reading_minutes} min · {post.author_name}
      </p>
      <div className="prose-content mt-8">
        {renderMarkdown(post.body_markdown)}
      </div>
    </article>
  );
}
